# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

from __future__ import unicode_literals
import frappe, erpnext
import frappe.defaults
from frappe.utils import cint, flt, add_months, today, date_diff, getdate, add_days, cstr, nowdate,formatdate, get_last_day
from frappe import _, msgprint, throw
from erpnext.accounts.party import get_party_account, get_due_date
from erpnext.controllers.stock_controller import update_gl_entries_after
from frappe.model.mapper import get_mapped_doc
from erpnext.accounts.doctype.sales_invoice.pos import update_multi_mode_option

from erpnext.controllers.selling_controller import SellingController
from erpnext.accounts.utils import get_account_currency
from erpnext.stock.doctype.delivery_note.delivery_note import update_billed_amount_based_on_so
from erpnext.projects.doctype.timesheet.timesheet import get_projectwise_timesheet_data
from erpnext.assets.doctype.asset.depreciation \
    import get_disposal_account_and_cost_center, get_gl_entries_on_asset_disposal
from erpnext.stock.doctype.batch.batch import set_batch_nos
from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos, get_delivery_note_serial_no
from erpnext.setup.doctype.company.company import update_company_current_month_sales
from erpnext.accounts.general_ledger import get_round_off_account_and_cost_center
from erpnext.accounts.doctype.loyalty_program.loyalty_program import \
    get_loyalty_program_details, get_loyalty_details, validate_loyalty_points
# custom import start #
from frappe.contacts.doctype.address.address import (get_address_display, get_default_address, get_company_address)
from erpnext.controllers.taxes_and_totals import calculate_taxes_and_totals
# custom import end #

from six import iteritems

form_grid_templates = {
    "items": "templates/form_grid/item_grid.html"
}

class SalesInvoice(SellingController):
    def __init__(self, *args, **kwargs):
        super(SalesInvoice, self).__init__(*args, **kwargs)
        self.status_updater = [{
            'source_dt': 'Sales Invoice Item',
            'target_field': 'billed_amt',
            'target_ref_field': 'amount',
            'target_dt': 'Sales Order Item',
            'join_field': 'so_detail',
            'target_parent_dt': 'Sales Order',
            'target_parent_field': 'per_billed',
            'source_field': 'amount',
            'join_field': 'so_detail',
            'percent_join_field': 'sales_order',
            'status_field': 'billing_status',
            'keyword': 'Billed',
            'overflow_type': 'billing'
        }]

    ################################ Custom YTPL ############################
    def autoname(self):
        from erpnext.accounts.utils import get_fiscal_year
        from frappe.utils import nowdate, formatdate

        current_fiscal_year = get_fiscal_year(self.posting_date, company=erpnext.get_default_company(), as_dict=True)
        fiscal_year = formatdate(current_fiscal_year.year_start_date, "YY") + "/" + formatdate(current_fiscal_year.year_end_date, "YY")

        if self.custom_bill_no:
            #if self.bill_number and len(str(self.bill_number)) == 5 and int(self.bill_number):
            if self.bill_number and len(str(self.bill_number)) == 5 and int(self.bill_number):
                custom_naming = "SI-" + fiscal_year + "-" + self.bill_number
                self.name = custom_naming
            else:
                frappe.throw(_('''Bill Number should be 5 digit number'''))
        #else:
        #    last_number= frappe.db.sql("""select name from `tabSales Invoice` where name like '%s%%'"""%("SI-" + fiscal_year), as_dict= True)
        #    print(last_number)
        #    custom_naming = "SI-" + fiscal_year + "-.######"
        #    self.naming_series = custom_naming

    ################################ Custom YTPL ############################

    def set_indicator(self):
        """Set indicator for portal"""
        if self.outstanding_amount > 0:
            self.indicator_color = "orange"
            self.indicator_title = _("Unpaid")
        else:
            self.indicator_color = "green"
            self.indicator_title = _("Paid")

    def validate(self):
        super(SalesInvoice, self).validate()
        self.validate_auto_set_posting_time()
        ################################ Custom YTPL ############################

        aleady_exist = None
        if self.billing_type == "Attendance":
            aleady_exist = frappe.db.sql(""" SELECT si.name
                            FROM
                                `tabSales Invoice` as si
                            INNER JOIN `tabSales Invoice Item` as sii ON (sii.parent = si.name)
                            WHERE
                                si.billing_period= '%s' AND
                                si.customer= '%s' AND
                                sii.attendance= '%s' AND
                                si.billing_type= '%s' AND
                                si.docstatus=1 """ % (self.billing_period, self.customer, self.attendance, self.billing_type), as_dict=1)

        if self.billing_type not in ["Rate Revision", "Attendance"]:
            aleady_exist = frappe.db.sql(
                '''select name from`tabSales Invoice` where billing_period=%s and customer=%s and billing_type=%s and docstatus=1''',
                (self.billing_period, self.customer, self.billing_type), as_dict=1)

        if aleady_exist:
            invoice_list = ", ".join([d.name for d in aleady_exist])
            frappe.throw(
                _('''Sales Invoice Aleady Created For Selected Criteria. Sales Invoice No {0}''').format(invoice_list))

        ################################ Custom YTPL ############################

        if not self.is_pos:
            self.so_dn_required()

        self.validate_proj_cust()
        self.validate_with_previous_doc()
        #self.validate_uom_is_integer("stock_uom", "stock_qty")
        #self.validate_uom_is_integer("uom", "qty")
        self.check_close_sales_order("sales_order")
        self.validate_debit_to_acc()
        self.clear_unallocated_advances("Sales Invoice Advance", "advances")
        self.add_remarks()
        self.validate_write_off_account()
        self.validate_account_for_change_amount()
        self.validate_fixed_asset()
        self.set_income_account_for_fixed_assets()
        validate_inter_company_party(self.doctype, self.customer, self.company, self.inter_company_invoice_reference)

        if cint(self.is_pos):
            self.validate_pos()

        if cint(self.update_stock):
            self.validate_dropship_item()
            self.validate_item_code()
            self.validate_warehouse()
            self.update_current_stock()
            self.validate_delivery_note()

        if not self.is_opening:
            self.is_opening = 'No'

        if self._action != 'submit' and self.update_stock and not self.is_return:
            set_batch_nos(self, 'warehouse', True)

        if self.redeem_loyalty_points:
            lp = frappe.get_doc('Loyalty Program', self.loyalty_program)
            self.loyalty_redemption_account = lp.expense_account if not self.loyalty_redemption_account else self.loyalty_redemption_account
            self.loyalty_redemption_cost_center = lp.cost_center if not self.loyalty_redemption_cost_center else self.loyalty_redemption_cost_center

        self.set_against_income_account()
        self.validate_c_form()
        self.validate_time_sheets_are_submitted()
        self.validate_multiple_billing("Delivery Note", "dn_detail", "amount", "items")
        if not self.is_return:
            self.validate_serial_numbers()
        self.update_packing_list()
        self.set_billing_hours_and_amount()
        self.update_timesheet_billing_for_project()
        self.set_status()
        if self.is_pos and not self.is_return:
            self.verify_payment_amount_is_positive()
        if self.redeem_loyalty_points and self.loyalty_program and self.loyalty_points:
            validate_loyalty_points(self, self.loyalty_points)

    ################################ Custom YTPL ############################
    def set_site_address(self):
        # Site address display
        from frappe.contacts.doctype.address.address import (get_address_display, get_default_address, get_company_address)
        self.site_address = get_default_address('Business Unit', self.site)
        if self.site_address:
            self.site_address_display = get_address_display(self.site_address)
        return self.site_address
    ################################ Custom YTPL ############################

    def before_save(self):
        set_account_for_mode_of_payment(self)
        self.posting_date= get_posting_date(str(self.si_from_date)) 

    ############################ Custom YTPL START#####################################
    def add_service_charges(self, period):
        service_charges= 0.0
        if len(self.items) > 0:
            for item in self.items:
                if item.contract:
                    contract_doc= frappe.get_doc('Site Contract', item.contract)
                    if contract_doc.is_service_charges == 1:
                        if contract_doc.mode_of_service_charges == 'Percentage':
                            service_charges+= ((item.qty * item.rate) * contract_doc.service_charges) / 100
                        else:
                            service_charges+= contract_doc.service_charges
            if service_charges > 0.0:
                company_income_acount, cost_center= frappe.db.get_value('Company', self.company, ['default_income_account', 'cost_center'])
                self.append('items',{   'rate': float(service_charges),
                                            'price_list_rate': float(service_charges),
                                            'item_code': "Service Charges",
                                            'item_name': "Service Charges",
                                            'description': "Service Charges",
                                            'uom': 'Nos',
                                            'qty': 1,
                                            'item_from_date': period.start_date,
                                            'item_to_date': period.end_date,
                                            'income_account': company_income_acount,
                                            'cost_center': cost_center
                                        }
                                )    
            else: pass
    ############################ Custom YTPL END#####################################
    def before_submit(self):
        self.posting_date= get_posting_date(str(self.si_from_date))
                 
    def on_submit(self):
        self.posting_date= get_posting_date(str(self.si_from_date))
        self.validate_pos_paid_amount()

        if not self.auto_repeat:
            frappe.get_doc('Authorization Control').validate_approving_authority(self.doctype,
                self.company, self.base_grand_total, self)

        self.check_prev_docstatus()

        if self.is_return and not self.update_billed_amount_in_sales_order:
            # NOTE status updating bypassed for is_return
            self.status_updater = []

        self.update_status_updater_args()
        self.update_prevdoc_status()
        self.update_billing_status_in_dn()
        self.clear_unallocated_mode_of_payments()
        ################# YTPL CODE END ################################################### 
        ### YTPL CODE Check Bill Duty Of people attendance and billing quantity ##
        update_modified=True
        if self.billing_type == "Standard":
            contract_wise_total_bill_quantity= frappe.db.sql("""select sii.contract, sum(sii.qty) as total_bill_duty from `tabSales Invoice` si 
                                                                inner join `tabSales Invoice Item` sii on si.name= sii.parent 
                                                                where si.name= '%s' group by sii.contract;"""%(self.name), as_dict= True)
            for contract in contract_wise_total_bill_quantity:
                attendance= frappe.db.sql("""select pa.name, sum(atd.bill_duty) as total_bill_duty from `tabPeople Attendance` pa inner join `tabAttendance Details` atd on pa.name= atd.parent where pa.contract= '%s' and pa.attendance_period= '%s'"""%(contract.contract, self.billing_period), as_dict= True)
                if len(attendance) > 0:
                    if attendance[0]["name"] != None and attendance[0]["name"] != 'None': 
                        if attendance[0]["total_bill_duty"] > contract.total_bill_duty:
                            frappe.db.set_value("People Attendance", attendance[0]["name"], "status", 'Partially Completed', update_modified=update_modified)
                        else:
                            frappe.db.set_value("People Attendance", attendance[0]["name"], "status", 'Completed', update_modified=update_modified)
        elif self.billing_type == "Attendance":
            attendance_wise_total_bill_quantity= frappe.db.sql("""select sii.attendance as attendance, sum(sii.qty) as total_bill_duty from `tabSales Invoice` si 
                                                                    inner join `tabSales Invoice Item` sii on si.name= sii.parent 
                                                                    where si.name= '%s' group by sii.attendance;"""%(self.name), as_dict= True)
            for attendance in attendance_wise_total_bill_quantity:
                if attendance.attendance != None:
                    people_attendance= frappe.db.sql("""select pa.name, sum(atd.bill_duty) as total_bill_duty from `tabPeople Attendance` pa 
                                            inner join `tabAttendance Details` atd on pa.name= atd.parent 
                                            where pa.name= '%s'"""%(attendance.attendance), as_dict= True)
                    if len(people_attendance) > 0:
                        if people_attendance[0]["total_bill_duty"] > attendance.total_bill_duty:
                            frappe.db.set_value("People Attendance", attendance.attendance, "status", 'Partially Completed', update_modified=update_modified)
                        else:
                            frappe.db.set_value("People Attendance", attendance.attendance, "status", 'Completed', update_modified=update_modified)
        elif self.billing_type == "Supplementry":
            self.update_attendance('Completed')
        else:
            pass 
        ################# YTPL CODE END ################################################### 
        # Updating stock ledger should always be called after updating prevdoc status,
        # because updating reserved qty in bin depends upon updated delivered qty in SO
        if self.update_stock == 1:
            self.update_stock_ledger()

        # this sequence because outstanding may get -ve
        self.make_gl_entries()

        if not self.is_return:
            self.update_billing_status_for_zero_amount_refdoc("Sales Order")
            self.check_credit_limit()

        self.update_serial_no()

        if not cint(self.is_pos) == 1 and not self.is_return:
            self.update_against_document_in_jv()

        self.update_time_sheet(self.name)

        if frappe.db.get_single_value('Selling Settings', 'sales_update_frequency') == "Each Transaction":
            update_company_current_month_sales(self.company)
            self.update_project()
        update_linked_invoice(self.doctype, self.name, self.inter_company_invoice_reference)

        # create the loyalty point ledger entry if the customer is enrolled in any loyalty program 
        if not self.is_return and self.loyalty_program:
            self.make_loyalty_point_entry()
        elif self.is_return and self.return_against and self.loyalty_program:
            against_si_doc = frappe.get_doc("Sales Invoice", self.return_against)
            against_si_doc.delete_loyalty_point_entry()
            against_si_doc.make_loyalty_point_entry()
        if self.redeem_loyalty_points and self.loyalty_points:
            self.apply_loyalty_points()

    def validate_pos_paid_amount(self):
        if len(self.payments) == 0 and self.is_pos:
            frappe.throw(_("At least one mode of payment is required for POS invoice."))

    def before_cancel(self):
        self.update_time_sheet(None)
        self.check_payroll_entry() ############## Custom YTPL CODE ####################
    ####### YTPL CODE STRAT##################
    def check_payroll_entry(self):
        sales_invoice_contract_list= frappe.db.sql("""select name, payroll_process from `tabProcessed Payroll` 
                                                        where contract in(select distinct sii.contract from `tabSales Invoice` si 
                                                        inner join `tabSales Invoice Item` sii on si.name= sii.parent where si.name= '%s') 
                                                        and period= '%s';"""%(self.name, self.billing_period))
        if len(sales_invoice_contract_list) > 0:
            frappe.throw("Payroll Process Has Been Complete For Selected Bill, Bill Can Not Be Cancel Or Delete")

    def on_trash(self):
        self.update_attendance("To Bill")

    ######## YTPL CODE END ##################
    def update_attendance(self, status, update_modified=True):
        if self.items:
            for items in self.items:
                if items.attendance:
                    frappe.db.set_value("People Attendance", items.attendance, "status", status, update_modified=update_modified)
                if self.billing_period == 'Standard' and items.contract and items.site:
                    attendance= frappe.db.sql("""select name from `tabPeople Attendance` where contract= '%s' and attendance_period= '%s'"""%(items.contract, self.billing_period), as_dict= True)
                    if len(attendance) > 0:
                        frappe.db.set_value("People Attendance", attendance[0]['name'], "status", status, update_modified=update_modified)

    def on_cancel(self):
        self.check_close_sales_order("sales_order")

        from erpnext.accounts.utils import unlink_ref_doc_from_payment_entries
        if frappe.db.get_single_value('Accounts Settings', 'unlink_payment_on_cancellation_of_invoice'):
            unlink_ref_doc_from_payment_entries(self)

        if self.is_return and not self.update_billed_amount_in_sales_order:
            # NOTE status updating bypassed for is_return
            self.status_updater = []

        self.update_status_updater_args()
        self.update_prevdoc_status()
        self.update_billing_status_in_dn()
        ########################## CUSTOM YTPL CODE ################################################# 
        self.update_attendance("To Bill")
        update_modified=True
        if self.billing_type == "Standard":
            contract_wise_total_bill_quantity= frappe.db.sql("""select sii.contract, sum(sii.qty) as total_bill_duty from `tabSales Invoice` si 
                                                                inner join `tabSales Invoice Item` sii on si.name= sii.parent 
                                                                where si.name= '%s' group by sii.contract;"""%(self.name), as_dict= True)
            for contract in contract_wise_total_bill_quantity:
                attendance= frappe.db.sql("""select pa.name, sum(atd.bill_duty) as total_bill_duty from `tabPeople Attendance` pa inner join `tabAttendance Details` atd on pa.name= atd.parent where pa.contract= '%s' and pa.attendance_period= '%s'"""%(contract.contract, self.billing_period), as_dict= True)
                if len(attendance) > 0:
                    frappe.db.set_value("People Attendance", attendance[0]["name"], "status", 'To Bill', update_modified=update_modified)
        elif self.billing_type == "Supplementry":
            self.update_attendance("Partially Completed")
        else:
            self.update_attendance("To Bill")
        ########################## CUSTOM YTPL CODE ################################################# 
        if not self.is_return:
            self.update_billing_status_for_zero_amount_refdoc("Sales Order")
            self.update_serial_no(in_cancel=True)

        self.validate_c_form_on_cancel()

        # Updating stock ledger should always be called after updating prevdoc status,
        # because updating reserved qty in bin depends upon updated delivered qty in SO
        if self.update_stock == 1:
            self.update_stock_ledger()

        self.make_gl_entries_on_cancel()
        frappe.db.set(self, 'status', 'Cancelled') ############ Custom YTPL CODE

        if frappe.db.get_single_value('Selling Settings', 'sales_update_frequency') == "Each Transaction":
            update_company_current_month_sales(self.company)
            self.update_project()
        if not self.is_return and self.loyalty_program:
            self.delete_loyalty_point_entry()
        elif self.is_return and self.return_against and self.loyalty_program:
            against_si_doc = frappe.get_doc("Sales Invoice", self.return_against)
            against_si_doc.delete_loyalty_point_entry()
            against_si_doc.make_loyalty_point_entry()

        unlink_inter_company_invoice(self.doctype, self.name, self.inter_company_invoice_reference)

    def update_status_updater_args(self):
        if cint(self.update_stock):
            self.status_updater.extend([{
                'source_dt':'Sales Invoice Item',
                'target_dt':'Sales Order Item',
                'target_parent_dt':'Sales Order',
                'target_parent_field':'per_delivered',
                'target_field':'delivered_qty',
                'target_ref_field':'qty',
                'source_field':'qty',
                'join_field':'so_detail',
                'percent_join_field':'sales_order',
                'status_field':'delivery_status',
                'keyword':'Delivered',
                'second_source_dt': 'Delivery Note Item',
                'second_source_field': 'qty',
                'second_join_field': 'so_detail',
                'overflow_type': 'delivery',
                'extra_cond': """ and exists(select name from `tabSales Invoice`
                    where name=`tabSales Invoice Item`.parent and update_stock = 1)"""
            },
            {
                'source_dt': 'Sales Invoice Item',
                'target_dt': 'Sales Order Item',
                'join_field': 'so_detail',
                'target_field': 'returned_qty',
                'target_parent_dt': 'Sales Order',
                # 'target_parent_field': 'per_delivered',
                # 'target_ref_field': 'qty',
                'source_field': '-1 * qty',
                # 'percent_join_field': 'sales_order',
                # 'overflow_type': 'delivery',
                'extra_cond': """ and exists (select name from `tabSales Invoice` where name=`tabSales Invoice Item`.parent and update_stock=1 and is_return=1)"""
            }
        ])

    def check_credit_limit(self):
        from erpnext.selling.doctype.customer.customer import check_credit_limit

        validate_against_credit_limit = False
        bypass_credit_limit_check_at_sales_order = cint(frappe.db.get_value("Customer", self.customer,
            "bypass_credit_limit_check_at_sales_order"))
        if bypass_credit_limit_check_at_sales_order:
            validate_against_credit_limit = True

        for d in self.get("items"):
            if not (d.sales_order or d.delivery_note):
                validate_against_credit_limit = True
                break
        if validate_against_credit_limit:
            check_credit_limit(self.customer, self.company, bypass_credit_limit_check_at_sales_order)

    def set_missing_values(self, for_validate=False):
        pos = self.set_pos_fields(for_validate)

        if not self.debit_to:
            self.debit_to = get_party_account("Customer", self.customer, self.company)
        if not self.due_date and self.customer:
            self.due_date = get_due_date(self.posting_date, "Customer", self.customer, self.company)

        super(SalesInvoice, self).set_missing_values(for_validate)

        if pos:
            return {
                "print_format": pos.get("print_format_for_online"),
                "allow_edit_rate": pos.get("allow_user_to_edit_rate"),
                "allow_edit_discount": pos.get("allow_user_to_edit_discount")
            }

    def update_time_sheet(self, sales_invoice):
        for d in self.timesheets:
            if d.time_sheet:
                timesheet = frappe.get_doc("Timesheet", d.time_sheet)
                self.update_time_sheet_detail(timesheet, d, sales_invoice)
                timesheet.calculate_total_amounts()
                timesheet.calculate_percentage_billed()
                timesheet.flags.ignore_validate_update_after_submit = True
                timesheet.set_status()
                timesheet.save()

    def update_time_sheet_detail(self, timesheet, args, sales_invoice):
        for data in timesheet.time_logs:
            if (self.project and args.timesheet_detail == data.name) or \
                (not self.project and not data.sales_invoice) or \
                (not sales_invoice and data.sales_invoice == self.name):
                data.sales_invoice = sales_invoice

    def on_update(self):
        self.set_paid_amount()
        self.posting_date= get_posting_date(str(self.si_from_date))
    def set_paid_amount(self):
        paid_amount = 0.0
        base_paid_amount = 0.0
        for data in self.payments:
            data.base_amount = flt(data.amount*self.conversion_rate, self.precision("base_paid_amount"))
            paid_amount += data.amount
            base_paid_amount += data.base_amount

        self.paid_amount = paid_amount
        self.base_paid_amount = base_paid_amount

    def validate_time_sheets_are_submitted(self):
        for data in self.timesheets:
            if data.time_sheet:
                status = frappe.db.get_value("Timesheet", data.time_sheet, "status")
                if status not in ['Submitted', 'Payslip']:
                    frappe.throw(_("Timesheet {0} is already completed or cancelled").format(data.time_sheet))

    def set_pos_fields(self, for_validate=False):
        """Set retail related fields from POS Profiles"""
        if cint(self.is_pos) != 1:
            return

        from erpnext.stock.get_item_details import get_pos_profile_item_details, get_pos_profile
        if not self.pos_profile:
            pos_profile = get_pos_profile(self.company) or {}
            self.pos_profile = pos_profile.get('name')

        pos = {}
        if self.pos_profile:
            pos = frappe.get_doc('POS Profile', self.pos_profile)

        if not self.get('payments') and not for_validate:
            update_multi_mode_option(self, pos)

        if not self.account_for_change_amount:
            self.account_for_change_amount = frappe.db.get_value('Company', self.company, 'default_cash_account')

        if pos:
            self.allow_print_before_pay = pos.allow_print_before_pay

            if not for_validate and not self.customer:
                self.customer = pos.customer

            self.ignore_pricing_rule = pos.ignore_pricing_rule
            if pos.get('account_for_change_amount'):
                self.account_for_change_amount = pos.get('account_for_change_amount')

            for fieldname in ('territory', 'naming_series', 'currency', 'taxes_and_charges', 'letter_head', 'tc_name',
                'selling_price_list', 'company', 'select_print_heading', 'cash_bank_account',
                'write_off_account', 'write_off_cost_center', 'apply_discount_on'):
                    if (not for_validate) or (for_validate and not self.get(fieldname)):
                        self.set(fieldname, pos.get(fieldname))

            if not for_validate:
                self.update_stock = cint(pos.get("update_stock"))

            # set pos values in items
            for item in self.get("items"):
                if item.get('item_code'):
                    profile_details = get_pos_profile_item_details(pos, frappe._dict(item.as_dict()), pos)
                    for fname, val in iteritems(profile_details):
                        if (not for_validate) or (for_validate and not item.get(fname)):
                            item.set(fname, val)

            # fetch terms
            if self.tc_name and not self.terms:
                self.terms = frappe.db.get_value("Terms and Conditions", self.tc_name, "terms")

            # fetch charges
            if self.taxes_and_charges and not len(self.get("taxes")):
                self.set_taxes()

        return pos

    def get_company_abbr(self):
        return frappe.db.sql("select abbr from tabCompany where name=%s", self.company)[0][0]

    def validate_debit_to_acc(self):
        account = frappe.db.get_value("Account", self.debit_to,
            ["account_type", "report_type", "account_currency"], as_dict=True)

        if not account:
            frappe.throw(_("Debit To is required"))

        if account.report_type != "Balance Sheet":
            frappe.throw(_("Debit To account must be a Balance Sheet account"))

        if self.customer and account.account_type != "Receivable":
            frappe.throw(_("Debit To account must be a Receivable account"))

        self.party_account_currency = account.account_currency

    def clear_unallocated_mode_of_payments(self):
        self.set("payments", self.get("payments", {"amount": ["not in", [0, None, ""]]}))

        frappe.db.sql("""delete from `tabSales Invoice Payment` where parent = %s
            and amount = 0""", self.name)

    def validate_with_previous_doc(self):
        super(SalesInvoice, self).validate_with_previous_doc({
            "Sales Order": {
                "ref_dn_field": "sales_order",
                "compare_fields": [["customer", "="], ["company", "="], ["project", "="], ["currency", "="]]
            },
            "Sales Order Item": {
                "ref_dn_field": "so_detail",
                "compare_fields": [["item_code", "="], ["uom", "="], ["conversion_factor", "="]],
                "is_child_table": True,
                "allow_duplicate_prev_row_id": True
            },
            "Delivery Note": {
                "ref_dn_field": "delivery_note",
                "compare_fields": [["customer", "="], ["company", "="], ["project", "="], ["currency", "="]]
            },
            "Delivery Note Item": {
                "ref_dn_field": "dn_detail",
                "compare_fields": [["item_code", "="], ["uom", "="], ["conversion_factor", "="]],
                "is_child_table": True,
                "allow_duplicate_prev_row_id": True
            },
        })

        if cint(frappe.db.get_single_value('Selling Settings', 'maintain_same_sales_rate')) and not self.is_return:
            self.validate_rate_with_reference_doc([
                ["Sales Order", "sales_order", "so_detail"],
                ["Delivery Note", "delivery_note", "dn_detail"]
            ])

    def set_against_income_account(self):
        """Set against account for debit to account"""
        against_acc = []
        for d in self.get('items'):
            if d.income_account not in against_acc:
                against_acc.append(d.income_account)
        self.against_income_account = ','.join(against_acc)

    def add_remarks(self):
        if not self.remarks: self.remarks = 'No Remarks'

    def validate_auto_set_posting_time(self):
        # Don't auto set the posting date and time if invoice is amended
        if self.is_new() and self.amended_from:
            self.set_posting_time = 1

        self.validate_posting_time()

    def so_dn_required(self):
        """check in manage account if sales order / delivery note required or not."""
        dic = {'Sales Order':['so_required', 'is_pos'],'Delivery Note':['dn_required', 'update_stock']}
        for i in dic:
            if frappe.db.get_value('Selling Settings', None, dic[i][0]) == 'Yes':
                for d in self.get('items'):
                    if frappe.db.get_value('Item', d.item_code, 'is_stock_item') == 1 \
                        and not d.get(i.lower().replace(' ','_')) and not self.get(dic[i][1]):
                        msgprint(_("{0} is mandatory for Item {1}").format(i,d.item_code), raise_exception=1)


    def validate_proj_cust(self):
        """check for does customer belong to same project as entered.."""
        if self.project and self.customer:
            res = frappe.db.sql("""select name from `tabProject`
                where name = %s and (customer = %s or customer is null or customer = '')""",
                (self.project, self.customer))
            if not res:
                throw(_("Customer {0} does not belong to project {1}").format(self.customer,self.project))

    def validate_pos(self):
        if self.is_return:
            if flt(self.paid_amount) + flt(self.write_off_amount) - flt(self.grand_total) < \
                1/(10**(self.precision("grand_total") + 1)):
                    frappe.throw(_("Paid amount + Write Off Amount can not be greater than Grand Total"))

    def validate_item_code(self):
        for d in self.get('items'):
            if not d.item_code:
                msgprint(_("Item Code required at Row No {0}").format(d.idx), raise_exception=True)

    def validate_warehouse(self):
        super(SalesInvoice, self).validate_warehouse()

        for d in self.get_item_list():
            if not d.warehouse and frappe.db.get_value("Item", d.item_code, "is_stock_item"):
                frappe.throw(_("Warehouse required for stock Item {0}").format(d.item_code))

    def validate_delivery_note(self):
        for d in self.get("items"):
            if d.delivery_note:
                msgprint(_("Stock cannot be updated against Delivery Note {0}").format(d.delivery_note), raise_exception=1)

    def validate_write_off_account(self):
        if flt(self.write_off_amount) and not self.write_off_account:
            self.write_off_account = frappe.db.get_value('Company', self.company, 'write_off_account')

        if flt(self.write_off_amount) and not self.write_off_account:
            msgprint(_("Please enter Write Off Account"), raise_exception=1)

    def validate_account_for_change_amount(self):
        if flt(self.change_amount) and not self.account_for_change_amount:
            msgprint(_("Please enter Account for Change Amount"), raise_exception=1)

    def validate_c_form(self):
        """ Blank C-form no if C-form applicable marked as 'No'"""
        if self.amended_from and self.c_form_applicable == 'No' and self.c_form_no:
            frappe.db.sql("""delete from `tabC-Form Invoice Detail` where invoice_no = %s
                    and parent = %s""", (self.amended_from, self.c_form_no))

            frappe.db.set(self, 'c_form_no', '')

    def validate_c_form_on_cancel(self):
        """ Display message if C-Form no exists on cancellation of Sales Invoice"""
        if self.c_form_applicable == 'Yes' and self.c_form_no:
            msgprint(_("Please remove this Invoice {0} from C-Form {1}")
                .format(self.name, self.c_form_no), raise_exception = 1)

    def validate_dropship_item(self):
        for item in self.items:
            if item.sales_order:
                if frappe.db.get_value("Sales Order Item", item.so_detail, "delivered_by_supplier"):
                    frappe.throw(_("Could not update stock, invoice contains drop shipping item."))

    def update_current_stock(self):
        for d in self.get('items'):
            if d.item_code and d.warehouse:
                bin = frappe.db.sql("select actual_qty from `tabBin` where item_code = %s and warehouse = %s", (d.item_code, d.warehouse), as_dict = 1)
                d.actual_qty = bin and flt(bin[0]['actual_qty']) or 0

        for d in self.get('packed_items'):
            bin = frappe.db.sql("select actual_qty, projected_qty from `tabBin` where item_code =   %s and warehouse = %s", (d.item_code, d.warehouse), as_dict = 1)
            d.actual_qty = bin and flt(bin[0]['actual_qty']) or 0
            d.projected_qty = bin and flt(bin[0]['projected_qty']) or 0

    def update_packing_list(self):
        if cint(self.update_stock) == 1:
            from erpnext.stock.doctype.packed_item.packed_item import make_packing_list
            make_packing_list(self)
        else:
            self.set('packed_items', [])

    def set_billing_hours_and_amount(self):
        if not self.project:
            for timesheet in self.timesheets:
                ts_doc = frappe.get_doc('Timesheet', timesheet.time_sheet)
                if not timesheet.billing_hours and ts_doc.total_billable_hours:
                    timesheet.billing_hours = ts_doc.total_billable_hours

                if not timesheet.billing_amount and ts_doc.total_billable_amount:
                    timesheet.billing_amount = ts_doc.total_billable_amount

    def update_timesheet_billing_for_project(self):
        if not self.timesheets and self.project:
            self.add_timesheet_data()
        else:
            self.calculate_billing_amount_for_timesheet()

    def add_timesheet_data(self):
        self.set('timesheets', [])
        if self.project:
            for data in get_projectwise_timesheet_data(self.project):
                self.append('timesheets', {
                        'time_sheet': data.parent,
                        'billing_hours': data.billing_hours,
                        'billing_amount': data.billing_amt,
                        'timesheet_detail': data.name
                    })

            self.calculate_billing_amount_for_timesheet()

    def calculate_billing_amount_for_timesheet(self):
        total_billing_amount = 0.0
        for data in self.timesheets:
            if data.billing_amount:
                total_billing_amount += data.billing_amount

        self.total_billing_amount = total_billing_amount

    def get_warehouse(self):
        user_pos_profile = frappe.db.sql("""select name, warehouse from `tabPOS Profile`
            where ifnull(user,'') = %s and company = %s""", (frappe.session['user'], self.company))
        warehouse = user_pos_profile[0][1] if user_pos_profile else None

        if not warehouse:
            global_pos_profile = frappe.db.sql("""select name, warehouse from `tabPOS Profile`
                where (user is null or user = '') and company = %s""", self.company)

            if global_pos_profile:
                warehouse = global_pos_profile[0][1]
            elif not user_pos_profile:
                msgprint(_("POS Profile required to make POS Entry"), raise_exception=True)

        return warehouse

    def set_income_account_for_fixed_assets(self):
        disposal_account = depreciation_cost_center = None
        for d in self.get("items"):
            if d.is_fixed_asset:
                if not disposal_account:
                    disposal_account, depreciation_cost_center = get_disposal_account_and_cost_center(self.company)

                d.income_account = disposal_account
                if not d.cost_center:
                    d.cost_center = depreciation_cost_center

    def check_prev_docstatus(self):
        for d in self.get('items'):
            if d.sales_order and frappe.db.get_value("Sales Order", d.sales_order, "docstatus") != 1:
                frappe.throw(_("Sales Order {0} is not submitted").format(d.sales_order))

            if d.delivery_note and frappe.db.get_value("Delivery Note", d.delivery_note, "docstatus") != 1:
                throw(_("Delivery Note {0} is not submitted").format(d.delivery_note))

    def make_gl_entries(self, gl_entries=None, repost_future_gle=True, from_repost=False):
        auto_accounting_for_stock = erpnext.is_perpetual_inventory_enabled(self.company)

        if not self.grand_total:
            return

        if not gl_entries:
            gl_entries = self.get_gl_entries()

        if gl_entries:
            from erpnext.accounts.general_ledger import make_gl_entries

            # if POS and amount is written off, updating outstanding amt after posting all gl entries
            update_outstanding = "No" if (cint(self.is_pos) or self.write_off_account or
                cint(self.redeem_loyalty_points)) else "Yes"

            make_gl_entries(gl_entries, cancel=(self.docstatus == 2),
                update_outstanding=update_outstanding, merge_entries=False)

            if update_outstanding == "No":
                from erpnext.accounts.doctype.gl_entry.gl_entry import update_outstanding_amt
                update_outstanding_amt(self.debit_to, "Customer", self.customer,
                    self.doctype, self.return_against if cint(self.is_return) and self.return_against else self.name)

            if repost_future_gle and cint(self.update_stock) \
                and cint(auto_accounting_for_stock):
                    items, warehouses = self.get_items_and_warehouses()
                    update_gl_entries_after(self.posting_date, self.posting_time, warehouses, items)
        elif self.docstatus == 2 and cint(self.update_stock) \
            and cint(auto_accounting_for_stock):
                from erpnext.accounts.general_ledger import delete_gl_entries
                delete_gl_entries(voucher_type=self.doctype, voucher_no=self.name)

    def get_gl_entries(self, warehouse_account=None):
        from erpnext.accounts.general_ledger import merge_similar_entries

        gl_entries = []

        self.make_customer_gl_entry(gl_entries)

        self.make_tax_gl_entries(gl_entries)

        self.make_item_gl_entries(gl_entries)

        # merge gl entries before adding pos entries
        gl_entries = merge_similar_entries(gl_entries)

        self.make_loyalty_point_redemption_gle(gl_entries)
        self.make_pos_gl_entries(gl_entries)
        self.make_gle_for_change_amount(gl_entries)

        self.make_write_off_gl_entry(gl_entries)
        self.make_gle_for_rounding_adjustment(gl_entries)

        return gl_entries

    def make_customer_gl_entry(self, gl_entries):
        grand_total = self.rounded_total or self.grand_total
        if grand_total:
            # Didnot use base_grand_total to book rounding loss gle
            grand_total_in_company_currency = flt(grand_total * self.conversion_rate,
                self.precision("grand_total"))

            gl_entries.append(
                self.get_gl_dict({
                    "account": self.debit_to,
                    "party_type": "Customer",
                    "party": self.customer,
                    "against": self.against_income_account,
                    "debit": grand_total_in_company_currency,
                    "debit_in_account_currency": grand_total_in_company_currency \
                        if self.party_account_currency==self.company_currency else grand_total,
                    "against_voucher": self.return_against if cint(self.is_return) and self.return_against else self.name,
                    "against_voucher_type": self.doctype
                }, self.party_account_currency)
            )

    def make_tax_gl_entries(self, gl_entries):
        for tax in self.get("taxes"):
            if flt(tax.base_tax_amount_after_discount_amount):
                account_currency = get_account_currency(tax.account_head)
                gl_entries.append(
                    self.get_gl_dict({
                        "account": tax.account_head,
                        "against": self.customer,
                        "credit": flt(tax.base_tax_amount_after_discount_amount),
                        "credit_in_account_currency": flt(tax.base_tax_amount_after_discount_amount) \
                            if account_currency==self.company_currency else flt(tax.tax_amount_after_discount_amount),
                        "cost_center": tax.cost_center
                    }, account_currency)
                )

    def make_item_gl_entries(self, gl_entries):
        # income account gl entries
        for item in self.get("items"):
            if flt(item.base_net_amount):
                if item.is_fixed_asset:
                    asset = frappe.get_doc("Asset", item.asset)

                    fixed_asset_gl_entries = get_gl_entries_on_asset_disposal(asset, item.base_net_amount)
                    for gle in fixed_asset_gl_entries:
                        gle["against"] = self.customer
                        gl_entries.append(self.get_gl_dict(gle))

                    asset.db_set("disposal_date", self.posting_date)
                    asset.set_status("Sold" if self.docstatus==1 else None)
                else:
                    account_currency = get_account_currency(item.income_account)
                    gl_entries.append(
                        self.get_gl_dict({
                            "account": item.income_account if not item.enable_deferred_revenue else item.deferred_revenue_account,
                            "against": self.customer,
                            "credit": item.base_net_amount,
                            "credit_in_account_currency": item.base_net_amount \
                                if account_currency==self.company_currency else item.net_amount,
                            "cost_center": item.cost_center
                        }, account_currency)
                    )

        # expense account gl entries
        if cint(self.update_stock) and \
            erpnext.is_perpetual_inventory_enabled(self.company):
            gl_entries += super(SalesInvoice, self).get_gl_entries()

    def make_loyalty_point_redemption_gle(self, gl_entries):
        if cint(self.redeem_loyalty_points):
            gl_entries.append(
                self.get_gl_dict({
                    "account": self.debit_to,
                    "party_type": "Customer",
                    "party": self.customer,
                    "against": "Expense account - " + cstr(self.loyalty_redemption_account) + " for the Loyalty Program",
                    "credit": self.loyalty_amount,
                    "against_voucher": self.return_against if cint(self.is_return) else self.name,
                    "against_voucher_type": self.doctype
                })
            )
            gl_entries.append(
                self.get_gl_dict({
                    "account": self.loyalty_redemption_account,
                    "cost_center": self.loyalty_redemption_cost_center,
                    "against": self.customer,
                    "debit": self.loyalty_amount,
                    "remark": "Loyalty Points redeemed by the customer"
                })
            )

    def make_pos_gl_entries(self, gl_entries):
        if cint(self.is_pos):
            for payment_mode in self.payments:
                if payment_mode.amount:
                    # POS, make payment entries
                    gl_entries.append(
                        self.get_gl_dict({
                            "account": self.debit_to,
                            "party_type": "Customer",
                            "party": self.customer,
                            "against": payment_mode.account,
                            "credit": payment_mode.base_amount,
                            "credit_in_account_currency": payment_mode.base_amount \
                                if self.party_account_currency==self.company_currency \
                                else payment_mode.amount,
                            "against_voucher": self.return_against if cint(self.is_return) and self.return_against else self.name,
                            "against_voucher_type": self.doctype,
                        }, self.party_account_currency)
                    )

                    payment_mode_account_currency = get_account_currency(payment_mode.account)
                    gl_entries.append(
                        self.get_gl_dict({
                            "account": payment_mode.account,
                            "against": self.customer,
                            "debit": payment_mode.base_amount,
                            "debit_in_account_currency": payment_mode.base_amount \
                                if payment_mode_account_currency==self.company_currency \
                                else payment_mode.amount
                        }, payment_mode_account_currency)
                    )

    def make_gle_for_change_amount(self, gl_entries):
        if cint(self.is_pos) and self.change_amount:
            if self.account_for_change_amount:
                gl_entries.append(
                    self.get_gl_dict({
                        "account": self.debit_to,
                        "party_type": "Customer",
                        "party": self.customer,
                        "against": self.account_for_change_amount,
                        "debit": flt(self.base_change_amount),
                        "debit_in_account_currency": flt(self.base_change_amount) \
                            if self.party_account_currency==self.company_currency else flt(self.change_amount),
                        "against_voucher": self.return_against if cint(self.is_return) and self.return_against else self.name,
                        "against_voucher_type": self.doctype
                    }, self.party_account_currency)
                )

                gl_entries.append(
                    self.get_gl_dict({
                        "account": self.account_for_change_amount,
                        "against": self.customer,
                        "credit": self.base_change_amount
                    })
                )
            else:
                frappe.throw(_("Select change amount account"), title="Mandatory Field")

    def make_write_off_gl_entry(self, gl_entries):
        # write off entries, applicable if only pos
        if self.write_off_account and self.write_off_amount:
            write_off_account_currency = get_account_currency(self.write_off_account)
            default_cost_center = frappe.db.get_value('Company', self.company, 'cost_center')

            gl_entries.append(
                self.get_gl_dict({
                    "account": self.debit_to,
                    "party_type": "Customer",
                    "party": self.customer,
                    "against": self.write_off_account,
                    "credit": self.base_write_off_amount,
                    "credit_in_account_currency": self.base_write_off_amount \
                        if self.party_account_currency==self.company_currency else self.write_off_amount,
                    "against_voucher": self.return_against if cint(self.is_return) and self.return_against else self.name,
                    "against_voucher_type": self.doctype
                }, self.party_account_currency)
            )
            gl_entries.append(
                self.get_gl_dict({
                    "account": self.write_off_account,
                    "against": self.customer,
                    "debit": self.base_write_off_amount,
                    "debit_in_account_currency": self.base_write_off_amount \
                        if write_off_account_currency==self.company_currency else self.write_off_amount,
                    "cost_center": self.write_off_cost_center or default_cost_center
                }, write_off_account_currency)
            )

    def make_gle_for_rounding_adjustment(self, gl_entries):
        if self.rounding_adjustment:
            round_off_account, round_off_cost_center = \
                get_round_off_account_and_cost_center(self.company)

            gl_entries.append(
                self.get_gl_dict({
                    "account": round_off_account,
                    "against": self.customer,
                    "credit_in_account_currency": self.rounding_adjustment,
                    "credit": self.base_rounding_adjustment,
                    "cost_center": round_off_cost_center,
                }
            ))

    def update_billing_status_in_dn(self, update_modified=True):
        updated_delivery_notes = []
        for d in self.get("items"):
            if d.dn_detail:
                billed_amt = frappe.db.sql("""select sum(amount) from `tabSales Invoice Item`
                    where dn_detail=%s and docstatus=1""", d.dn_detail)
                billed_amt = billed_amt and billed_amt[0][0] or 0
                frappe.db.set_value("Delivery Note Item", d.dn_detail, "billed_amt", billed_amt, update_modified=update_modified)
                updated_delivery_notes.append(d.delivery_note)
            elif d.so_detail:
                updated_delivery_notes += update_billed_amount_based_on_so(d.so_detail, update_modified)

        for dn in set(updated_delivery_notes):
            frappe.get_doc("Delivery Note", dn).update_billing_percentage(update_modified=update_modified)

    def on_recurring(self, reference_doc, auto_repeat_doc):
        for fieldname in ("c_form_applicable", "c_form_no", "write_off_amount"):
            self.set(fieldname, reference_doc.get(fieldname))

        self.due_date = None

    def update_serial_no(self, in_cancel=False):
        """ update Sales Invoice refrence in Serial No """
        invoice = None if (in_cancel or self.is_return) else self.name
        if in_cancel and self.is_return:
            invoice = self.return_against

        for item in self.items:
            if not item.serial_no:
                continue

            for serial_no in item.serial_no.split("\n"):
                if serial_no and frappe.db.exists('Serial No', serial_no):
                    sno = frappe.get_doc('Serial No', serial_no)
                    sno.sales_invoice = invoice
                    sno.db_update()

    def validate_serial_numbers(self):
        """
            validate serial number agains Delivery Note and Sales Invoice
        """
        self.set_serial_no_against_delivery_note()
        self.validate_serial_against_delivery_note()
        self.validate_serial_against_sales_invoice()

    def set_serial_no_against_delivery_note(self):
        for item in self.items:
            if item.serial_no and item.delivery_note and \
                item.qty != len(get_serial_nos(item.serial_no)):
                item.serial_no = get_delivery_note_serial_no(item.item_code, item.qty, item.delivery_note)

    def validate_serial_against_delivery_note(self):
        """
            validate if the serial numbers in Sales Invoice Items are same as in
            Delivery Note Item
        """

        for item in self.items:
            if not item.delivery_note or not item.dn_detail:
                continue

            serial_nos = frappe.db.get_value("Delivery Note Item", item.dn_detail, "serial_no") or ""
            dn_serial_nos = set(get_serial_nos(serial_nos))

            serial_nos = item.serial_no or ""
            si_serial_nos = set(get_serial_nos(serial_nos))

            if si_serial_nos - dn_serial_nos:
                frappe.throw(_("Serial Numbers in row {0} does not match with Delivery Note".format(item.idx)))

            if item.serial_no and cint(item.qty) != len(si_serial_nos):
                frappe.throw(_("Row {0}: {1} Serial numbers required for Item {2}. You have provided {3}.".format(
                    item.idx, item.qty, item.item_code, len(si_serial_nos))))

    def validate_serial_against_sales_invoice(self):
        """ check if serial number is already used in other sales invoice """
        for item in self.items:
            if not item.serial_no:
                continue

            for serial_no in item.serial_no.split("\n"):
                sales_invoice = frappe.db.get_value("Serial No", serial_no, "sales_invoice")
                if sales_invoice and self.name != sales_invoice:
                    frappe.throw(_("Serial Number: {0} is already referenced in Sales Invoice: {1}".format(
                        serial_no, sales_invoice
                    )))

    def update_project(self):
        if self.project:
            project = frappe.get_doc("Project", self.project)
            project.flags.dont_sync_tasks = True
            project.update_billed_amount()
            project.save()


    def verify_payment_amount_is_positive(self):
        for entry in self.payments:
            if entry.amount < 0:
                frappe.throw(_("Row #{0} (Payment Table): Amount must be positive").format(entry.idx))

    # collection of the loyalty points, create the ledger entry for that.
    def make_loyalty_point_entry(self):
        lp_details = get_loyalty_program_details(self.customer, company=self.company,
            loyalty_program=self.loyalty_program, expiry_date=self.posting_date)
        if lp_details and getdate(lp_details.from_date) <= getdate(self.posting_date) and \
            (not lp_details.to_date or getdate(lp_details.to_date) >= getdate(self.posting_date)):
            returned_amount = self.get_returned_amount()
            eligible_amount = flt(self.grand_total) - cint(self.loyalty_amount) - returned_amount
            points_earned = cint(eligible_amount/lp_details.collection_factor)
            doc = frappe.get_doc({
                "doctype": "Loyalty Point Entry",
                "company": self.company,
                "loyalty_program": lp_details.loyalty_program,
                "loyalty_program_tier": lp_details.tier_name,
                "customer": self.customer,
                "sales_invoice": self.name,
                "loyalty_points": points_earned,
                "purchase_amount": eligible_amount,
                "expiry_date": add_days(self.posting_date, lp_details.expiry_duration),
                "posting_date": self.posting_date
            })
            doc.flags.ignore_permissions = 1
            doc.save()
            frappe.db.set_value("Customer", self.customer, "loyalty_program_tier", lp_details.tier_name)

    # valdite the redemption and then delete the loyalty points earned on cancel of the invoice
    def delete_loyalty_point_entry(self):
        lp_entry = frappe.db.sql("select name from `tabLoyalty Point Entry` where sales_invoice=%s",
            (self.name), as_dict=1)[0]
        against_lp_entry = frappe.db.sql('''select name, sales_invoice from `tabLoyalty Point Entry`
            where redeem_against=%s''', (lp_entry.name), as_dict=1)
        if against_lp_entry:
            invoice_list = ", ".join([d.sales_invoice for d in against_lp_entry])
            frappe.throw(_('''Sales Invoice can't be cancelled since the Loyalty Points earned has been redeemed. 
                First cancel the Sales Invoice No {0}''').format(invoice_list))
        else:
            frappe.db.sql('''delete from `tabLoyalty Point Entry` where sales_invoice=%s''', (self.name))
            # Set loyalty program
            lp_details = get_loyalty_program_details(self.customer, company=self.company,
                loyalty_program=self.loyalty_program, expiry_date=self.posting_date)
            frappe.db.set_value("Customer", self.customer, "loyalty_program_tier", lp_details.tier_name)

    def get_returned_amount(self):
        returned_amount = frappe.db.sql("""
            select sum(grand_total)
            from `tabSales Invoice`
            where docstatus=1 and is_return=1 and ifnull(return_against, '')=%s
        """, self.name)
        return abs(flt(returned_amount[0][0])) if returned_amount else 0

    # redeem the loyalty points.
    def apply_loyalty_points(self):
        from erpnext.accounts.doctype.loyalty_point_entry.loyalty_point_entry \
            import get_loyalty_point_entries, get_redemption_details
        loyalty_point_entries = get_loyalty_point_entries(self.customer, self.loyalty_program, self.company, self.posting_date)
        redemption_details = get_redemption_details(self.customer, self.loyalty_program, self.company)

        points_to_redeem = self.loyalty_points
        for lp_entry in loyalty_point_entries:
            available_points = lp_entry.loyalty_points - flt(redemption_details.get(lp_entry.name))
            if available_points > points_to_redeem:
                redeemed_points = points_to_redeem
            else:
                redeemed_points = available_points
            doc = frappe.get_doc({
                "doctype": "Loyalty Point Entry",
                "company": self.company,
                "loyalty_program": self.loyalty_program,
                "loyalty_program_tier": lp_entry.loyalty_program_tier,
                "customer": self.customer,
                "sales_invoice": self.name,
                "redeem_against": lp_entry.name,
                "loyalty_points": -1*redeemed_points,
                "purchase_amount": self.grand_total,
                "expiry_date": lp_entry.expiry_date,
                "posting_date": self.posting_date
            })
            doc.flags.ignore_permissions = 1
            doc.save()
            points_to_redeem -= redeemed_points
            if points_to_redeem < 1: # since points_to_redeem is integer
                break

    def book_income_for_deferred_revenue(self):
        # book the income on the last day, but it will be trigger on the 1st of month at 12:00 AM
        # start_date: 1st of the last month or the start date
        # end_date: end_date or today-1

        gl_entries = []
        for item in self.get('items'):
            last_gl_entry = False

            booking_start_date = getdate(add_months(today(), -1))
            booking_start_date = booking_start_date if booking_start_date>item.service_start_date else item.service_start_date

            booking_end_date = getdate(add_days(today(), -1))
            if booking_end_date>=item.service_end_date:
                last_gl_entry = True
                booking_end_date = item.service_end_date

            total_days = date_diff(item.service_end_date, item.service_start_date)
            total_booking_days = date_diff(booking_end_date, booking_start_date) + 1

            account_currency = get_account_currency(item.income_account)
            if not last_gl_entry:
                base_amount = flt(item.base_net_amount*total_booking_days/flt(total_days), item.precision("base_net_amount"))
                if account_currency==self.company_currency:
                    amount = base_amount
                else:
                    amount = flt(item.net_amount*total_booking_days/flt(total_days), item.precision("net_amount"))
            else:
                gl_entries_details = frappe.db.sql('''
                    select sum(debit) as total_debit, sum(debit_in_account_currency) as total_debit_in_account_currency, voucher_detail_no
                    from `tabGL Entry` where company=%s and account=%s and voucher_type=%s and voucher_no=%s and voucher_detail_no=%s
                    group by voucher_detail_no
                ''', (self.company, item.deferred_revenue_account, "Sales Invoice", self.name, item.name), as_dict=True)[0]
                base_amount = flt(item.base_net_amount - gl_entries_details.total_debit, item.precision("base_net_amount"))
                if account_currency==self.company_currency:
                    amount = base_amount
                else:
                    amount = flt(item.net_amount - gl_entries_details.total_debit_in_account_currency, item.precision("net_amount"))

            # GL Entry for crediting the amount in the income
            gl_entries.append(
                self.get_gl_dict({
                    "account": item.income_account,
                    "against": self.customer,
                    "credit": base_amount,
                    "credit_in_account_currency": amount,
                    "cost_center": item.cost_center,
                    'posting_date': booking_end_date
                }, account_currency)
            )
            # GL Entry to debit the amount from the deferred account
            gl_entries.append(
                self.get_gl_dict({
                    "account": item.deferred_revenue_account,
                    "against": self.customer,
                    "debit": base_amount,
                    "debit_in_account_currency": amount,
                    "cost_center": item.cost_center,
                    "voucher_detail_no": item.name,
                    'posting_date': booking_end_date
                }, account_currency)
            )

        if gl_entries:
            from erpnext.accounts.general_ledger import make_gl_entries
            make_gl_entries(gl_entries, cancel=(self.docstatus == 2), merge_entries=True)

    #################### CUSTOM YTPL #######################################
    def get_sunday_count(self, start_date, end_date, period):
        from sps.sps.doctype.people_attendance.people_attendance import get_wo_count
        period_doc= frappe.get_doc('Salary Payroll Period', period)
        sunday_count, sunday_count_details= get_wo_count(start_date, end_date, 'Sunday', None, period_doc)
        return sunday_count
    
    def get_total_qty(self, total_bill_duty, sunday_count, contract, work_type):
        bill_duty= total_bill_duty
        total_qty= 0
        if contract.is_relieving_charges_includes:
            for row in contract.contract_details:
                if row.work_type in ["GD1", "GMW"] and work_type in ["GD1", "GMW"]:
                    total_qty= bill_duty + (sunday_count * row.quantity)
                else: total_qty= bill_duty
        else: total_qty= bill_duty
        return total_qty  
                    
    def get_details_to_create_items(self, att_list, billing_period):
        self.items= []
        period = frappe.get_doc('Salary Payroll Period', billing_period)
        if att_list:
            att_data= None
            if len(att_list) > 1:
                #att_data = frappe.db.sql("""select atd.work_type, sum(atd.bill_duty) as total_bill_duty, 
                #                        att.include_relieving_charges, atd.wage_rule, att.name,  
                #                        atd.wage_rule_details, att.contract, att.site, att.site_name, att.weekly_off_included, att.company 
                #                        from `tabPeople Attendance` att inner join `tabAttendance Details` atd on atd.parent= att.name 
                #                        where att.name in %s group by att.name, atd.work_type;"""%(str(tuple(att_list))), as_dict= True)
                att_data = frappe.db.sql("""select atd.work_type, sum(atd.bill_duty) as total_bill_duty,  ctd.quantity, 
                                            att.include_relieving_charges, atd.wage_rule, att.name, atd.wage_rule_details, att.contract, 
                                            att.site, att.site_name, att.weekly_off_included, att.company  from `tabPeople Attendance` att  
                                            inner join `tabAttendance Details` atd on atd.parent= att.name 
                                            inner join `tabContract Details` ctd on att.contract= ctd.parent and atd.work_type= ctd.work_type 
                                            where att.name in %s group by att.name, atd.work_type;"""%(str(tuple(att_list))), as_dict= True)
            else:
                att_data = frappe.db.sql("""select atd.work_type, sum(atd.bill_duty) as total_bill_duty,  ctd.quantity, 
                                            att.include_relieving_charges, atd.wage_rule, att.name, atd.wage_rule_details, att.contract, 
                                            att.site, att.site_name, att.weekly_off_included, att.company  from `tabPeople Attendance` att  
                                            inner join `tabAttendance Details` atd on atd.parent= att.name 
                                            inner join `tabContract Details` ctd on att.contract= ctd.parent and atd.work_type= ctd.work_type 
                                            where att.name= '%s' group by att.name, atd.work_type;"""%(str(att_list[0])), as_dict= True)

            company_income_acount, cost_center= frappe.db.get_value('Company', att_data[0]['company'], ['default_income_account', 'cost_center'])
            sunday_count= self.get_sunday_count(period.start_date, period.end_date, period.name)
            for row in att_data:
                rate, wage_rule_details= self.get_price(row.wage_rule, row.wage_rule_details, period.start_date, period.end_date)
                contract_doc= frappe.get_doc('Site Contract', row.contract)
                if rate == 0.0:
                    frappe.throw(_("WageRule: {0} not valid for Contract : {1} | WT : {2}.").format(row.wage_rule,row.contract,row.work_type))
                else:
                    self.append('items',{   'rate': float(rate),
                                            'price_list_rate': float(rate),
                                            'item_code': row.work_type,
                                            'item_name': row.work_type,
                                            'description': row.work_type,
                                            'uom': 'Nos',
                                            'qty': self.get_total_qty(row.total_bill_duty, sunday_count, contract_doc, row.work_type), 
                                            'contract': row.contract,
                                            'contract_quantity': row.quantity,
                                            'site': row.site,
                                            'attendance': row.name,
                                            'salary_structure': row.wage_rule,
                                            'ss_revision_name': wage_rule_details,
                                            'ss_revision_rate': float(rate),
                                            'item_from_date': period.start_date,
                                            'item_to_date': period.end_date,
                                            'income_account': company_income_acount,
                                            'cost_center': cost_center
                                        }
                                )
        self.add_service_charges(period)
        return "Item Inserted Successfully" 

    def get_price(self, salary_structure, wage_rule_rev_name, start_date, end_date):
        wage_rule_rev_name= wage_rule_rev_name
        total_days= float(date_diff(end_date, start_date)) + 1.0
        rate= 0.0
        wage_rule= frappe.get_doc("Wage Structure", salary_structure)
        for i in range(0, len(wage_rule.wage_rule_details)):
            if getdate(wage_rule.wage_rule_details[i].from_date) <= getdate(start_date) and getdate(wage_rule.wage_rule_details[i].to_date) >= getdate(end_date):
                wage_rule_rev_name= wage_rule.wage_rule_details[i].name
                if wage_rule.wage_rule_details[i].rate_per == "Month":
                    rate= round(wage_rule.wage_rule_details[i].rate / total_days, 2)
                else: rate= round(wage_rule.wage_rule_details[i].rate, 2)
        return rate, wage_rule_rev_name

    def get_items_for_standard_billing(self, contract_list, period_from_date, period_to_date):
        self.items= []
        contract_data= []
        if len(contract_list) > 1:
            contract_data= frappe.db.sql(""" select ctd.work_type, ctd.quantity, ctd.wage_rule, ctd.from_date, ctd.to_date, 
                                            ct.name as contract, ct.bu_site as site, ct.bu_site_name as site_name, ct.company 
                                            from `tabSite Contract` ct 
                                            inner join `tabContract Details` ctd on ctd.parent= ct.name 
                                            inner join my_date_series ON ( dateval >= ctd.from_date and dateval <= ctd.to_date) 
                                            where ct.name in %s and dateval >= '%s' and dateval <= '%s' 
                                            group by ct.name, ctd.work_type;""" %(str(tuple(contract_list)), period_from_date, period_to_date), as_dict= True)
        else:
             contract_data= frappe.db.sql("""select ctd.work_type, ctd.quantity, ctd.wage_rule, ctd.from_date, ctd.to_date, 
                                            ct.name as contract, ct.bu_site as site, ct.bu_site_name as site_name, ct.company 
                                            from `tabSite Contract` ct inner join `tabContract Details` ctd on ctd.parent= ct.name 
                                            inner join my_date_series ON ( dateval >= ctd.from_date and dateval <= ctd.to_date) 
                                            where ct.name = '%s' and dateval >= '%s' and dateval <= '%s' 
                                            group by ct.name, ctd.work_type;""" %(contract_list[0], period_from_date, period_to_date), as_dict= True)

        company_income_acount, cost_center= frappe.db.get_value('Company', contract_data[0]['company'], ['default_income_account', 'cost_center'])
        if len(contract_data) > 0:
            for row in contract_data:
                rate, wage_rule_details= self.get_price(row.wage_rule, None, period_from_date, period_to_date)
                from_date=  row.from_date if str(row.from_date) >= str(period_from_date) else period_from_date
                to_date= row.to_date if str(row.to_date) <= str(period_to_date) else period_to_date
                total_days= date_diff(to_date, from_date) + 1
                print(from_date, to_date, total_days)
                self.append('items',{   'rate': float(rate),
                                        'price_list_rate': float(rate),
                                        'item_code': row.work_type,
                                        'item_name': row.work_type,
                                        'description': row.work_type,
                                        'uom': 'Nos',
                                        'qty': int(row.quantity) * total_days,
                                        'contract': row.contract,
                                        'contract_quantity': row.quantity,
                                        'site': row.site,
                                        'salary_structure': row.wage_rule,
                                        'ss_revision_name': wage_rule_details,
                                        'ss_revision_rate': float(rate),
                                        'item_from_date': period_from_date,
                                        'item_to_date': period_to_date,
                                        'income_account': company_income_acount,
                                        'cost_center': cost_center
                                    }
                            )
        else:
            frappe.throw("No Data Found For Selected Contract")
        period= {'from_date': period_from_date, 'to_date': period_to_date}
        self.add_service_charges(period)
        return "Item Inserted Successfully"

    def rate_revision_si(self, ref_si,item_code, frm_dt,to_dt):
        filters = [['docstatus', '=', 1], ['ref_sales_invoice', '=', ref_si], ['item_code', '=', item_code], ['item_from_date', '=', frm_dt], ['item_to_date', '=', to_dt]]
        rr_si_list = frappe.get_list("Sales Invoice Item", fields=['*'], filters=filters,ignore_permissions=1)
        rate=0.00
        if rr_si_list:
            for rr_item in rr_si_list:
                rate= round(rate,2) + round(rr_item.rate,2)
        return rate

    def get_wage_rule_details(self, docname, period_from_date, period_to_date):
        period_total_days = cint(date_diff(period_to_date, period_from_date) + 1)
        count = wr_revision= wr_rate= 0
        wr_name=None
        if not (period_total_days and period_total_days > 0):
            frappe.throw(_("Billing Period Days Should be Greater Than Zero"))
        sal_struct = frappe.get_doc('Wage Structure', docname)
        if sal_struct:
            if(sal_struct.docstatus == 1 and sal_struct.is_active == "Yes"):
                if sal_struct.wage_rule_details:
                    for wr in sal_struct.wage_rule_details:
                        wage_rv_frmdt = getdate(wr.from_date)  # Wage Rule Details Revision Fron Date
                        wage_rv_todt = getdate(wr.to_date)  # Wage Rule Details Revision To Date
                        period_start_dt = getdate(period_from_date)  # Attendance Period Start Date
                        period_end_dt = getdate(period_to_date)  # Attendance Period End Date
                        if (wage_rv_frmdt <= period_start_dt and wage_rv_todt >= period_end_dt):
                            count = count + 1
                            if count > 1:
                                frappe.throw(_("Rule Code : '{0}', Multiple Wage Rule Revisions found between '{1} - {2}' ").format(sal_struct.rule_code, period_start_dt, period_end_dt))
                            if count == 1:
                                wr_name=wr.name
                                wr_revision=wr.revision
                                days_in_month = cint(formatdate(get_last_day(period_from_date), "dd"))
                                if str(wr.rate_per).upper() == 'MONTH':
                                    wr_rate = wr.rate / days_in_month
                                elif str(wr.rate_per).upper() == 'DUTY':
                                    wr_rate = wr.rate
                                else:wr_rate = 0.00
                            pass
                        pass
                    pass
                if count == 0: frappe.throw(_("Wage Structure : '{0}'. revision not found between '{1} - {2}' ").format(sal_struct.rule_code,period_start_dt, period_end_dt))
            else : frappe.throw(_("Wage Rule Revision Not found in Wage Structure: {0}").format(sal_struct.rule_code))
        else : frappe.throw(_("Wage Structure: {0} should be Active").format(sal_struct.rule_code))
        print({"wr_rate": round(wr_rate,2), "wr_name": wr_name, "wr_revision": wr_revision})
        return {"wr_rate": round(wr_rate,2), "wr_name": wr_name, "wr_revision": wr_revision}

    
    def get_data_to_make_arrears_bill(self):
        if self.arrears_bill_from and self.customer:
            filters = [['docstatus', '=', 1],['is_return', '=', 0],['billing_type', 'in', ['Standard', 'Attendance', 'Supplementary']],['si_from_date', '>=', self.arrears_bill_from],['customer', '=', self.customer]]
            si_list = frappe.get_list('Sales Invoice', fields=['*'], filters=filters)
            si_items_row= {}
            if si_list:
                for prev_bill in si_list:
                    prev_si = frappe.get_doc('Sales Invoice', prev_bill.name)
                    for prev_si_items in prev_si.items:
                        diff_rate = curr_rate = 0.00
                        if prev_si_items.salary_structure:
                            new_details= self.get_wage_rule_details(prev_si_items.salary_structure, prev_si_items.item_from_date, prev_si_items.item_to_date)
                            rr_rate= self.rate_revision_si(prev_bill.name, prev_si_items.item_code, prev_si_items.item_from_date, prev_si_items.item_to_date)
                            curr_rate = round(new_details['wr_rate'],2)
                            base_rate = round(prev_si_items.rate, 2)

                            diff_rate = round(round(curr_rate, 2) - round(base_rate, 2), 2) - round(rr_rate, 2)
                            if(diff_rate > 0.0):
                                prev_si_items.ss_revision_name = new_details['wr_name'];
                                prev_si_items.ss_revision_no = new_details['wr_revision'];
                                prev_si_items.ss_revision_rate = round(curr_rate,2);
                                prev_si_items.rate = round(diff_rate,2)

                                if si_items_row.has_key(prev_bill.name): si_items_row[prev_bill.name].append(prev_si_items)
                                else: si_items_row[prev_bill.name] = [prev_si_items]
                                self.append('items',{   'rate': round(diff_rate,2),
                                                        'price_list_rate': round(diff_rate,2) , 
                                                        'item_code': prev_si_items.item_code,
                                                        'item_name': prev_si_items.item_code,
                                                        'description': prev_si_items.item_code,
                                                        'uom': 'Nos',
                                                        'qty': prev_si_items.qty,
                                                        'contract': prev_si_items.contract,
                                                        'contract_quantity': prev_si_items.contract_quantity,
                                                        'site': prev_si_items.site,
                                                        'attendance': prev_si_items.attendance,
                                                        'salary_structure': prev_si_items.salary_structure,
                                                        'ss_revision_name': new_details['wr_name'],
                                                        'ss_revision_rate': round(curr_rate,2),
                                                        'item_from_date': prev_si_items.item_from_date ,
                                                        'item_to_date':  prev_si_items.item_to_date ,
                                                        'income_account': prev_si_items.income_account,
                                                        'cost_center': prev_si_items.cost_center,
                                                        'ref_sales_invoice': prev_bill.name,
                                                        'ref_invoice_rate': prev_si_items.rate
                                                        }
                                                        )
                        else: frappe.throw(_("Wage Structure not linked."))
                    pass
                period= {'from_date': self.items[0]['item_from_date'], 'to_date': self.items[0]['item_to_date']} 
                self.add_service_charges(period)
            if not si_items_row : frappe.msgprint(_("Rate Diffrence Not Found"))
        else: frappe.throw(_("Bill not generated after '{0}' for Customer : {1}").format(self.arrears_bill_from, self.customer))
        return "Item Fetched"
    
    
    def get_data_to_make_supplementary_bill(self):
        if self.billing_period and self.customer and self.standard_bill:
            #filters  = [['docstatus', '=', 1],['is_return', '=', 0],['billing_type', 'in', ['Standard']],['customer', '=', self.customer]]
            si_doc   = frappe.get_doc("Sales Invoice", self.standard_bill)            
            sqlQuery = """select sum(pds.bill_duty) as qty, pds.work_type as work_type, pa.contract , pa.customer, pa.name
                            from `tabPeople Attendance` pa 
                            inner join `tabAttendance Details` pds on pa.name = pds.parent 
                            where pa.contract in(select distinct sii.contract from `tabSales Invoice` si inner join `tabSales Invoice Item` sii on si.name= sii.parent where sii.parent= '%s' order by sii.contract) and pa.attendance_period = '%s' and pa.customer = '%s'
                            group by pds.work_type, pa.name
                            order by pa.contract, pds.work_type"""%(self.standard_bill, self.billing_period, self.customer)
            
            attd_doc = frappe.db.sql(sqlQuery, as_dict = True)
            print(attd_doc, type(attd_doc))
            #pa_doc   = frappe.get_doc("People Attendance", self.billing_period)
            company_income_acount, cost_center= frappe.db.get_value('Company', self.company, ['default_income_account', 'cost_center'])
            for i in range(len(attd_doc)):
                diff_qty = 0
                if si_doc.items[i].item_code == attd_doc[i]["work_type"] and attd_doc[i]["customer"] == si_doc.customer and si_doc.items[i].contract == attd_doc[i]["contract"]:
                    if si_doc.items[i].qty < attd_doc[i]["qty"]:
                        diff_qty = attd_doc[i]["qty"] - si_doc.items[i].qty
                        self.append('items', {'rate':si_doc.items[i].rate,
                                                            'qty':flt(diff_qty), 
                                                            'price_list_rate':si_doc.items[i].rate,
                                                            'item_code':si_doc.items[i].item_code,
                                                            'item_name': si_doc.items[i].item_code,
                                                            'description': si_doc.items[i].item_code,
                                                            'uom': 'Nos',
                                                            'contract':si_doc.items[i].contract,
                                                            'contract_quantity':si_doc.items[i].contract_quantity,
                                                            'attendance':attd_doc[i]["name"],
                                                            'salary_structure':si_doc.items[i].salary_structure,
                                                            'ss_revision_name':si_doc.items[i].ss_revision_name,
                                                            'ss_revision_rate':si_doc.items[i].ss_revision_rate,
                                                            'item_from_date':si_doc.items[i].item_from_date,
                                                            'item_to_date':si_doc.items[i].item_to_date,
                                                            'income_account': company_income_acount,
                                                            'cost_center': cost_center,
                                                            })
        period= {'from_date': self.items[0]['item_from_date'], 'to_date': self.items[0]['item_to_date']} 
        self.add_service_charges(period)
        return "Done"
    def get_draft_bill(self):
        data= frappe.db.sql("""select distinct sii.attendance from `tabSales Invoice` si inner join `tabSales Invoice Item` sii 
                                on si.name= sii.parent where si.docstatus= 0 
                                and si.billing_period= '%s' and si.customer= '%s' and sii.attendance is not null"""%(self.billing_period, self.customer), as_dict= True)
        result= []
        if len(data) > 0:
            for row in data:
                result.append(row.attendance)
        return result
    
    def get_site_count(self):
        data= frappe.db.sql("select distinct site_name from `tabSales Invoice Item` where parent= '%s'"%(self.name), as_dict= True)
        return len(data)
    #################### CUSTOM YTPL END#######################################

def booked_deferred_revenue():
    # check for the sales invoice for which GL entries has to be done
    invoices = frappe.db.sql_list('''
        select parent from `tabSales Invoice Item` where service_start_date<=%s and service_end_date>=%s
        and enable_deferred_revenue = 1 and docstatus = 1
    ''', (today(), add_months(today(), -1)))

    # ToDo also find the list on the basic of the GL entry, and make another list
    for invoice in invoices:
        doc = frappe.get_doc("Sales Invoice", invoice)
        doc.book_income_for_deferred_revenue()


def validate_inter_company_party(doctype, party, company, inter_company_invoice_reference):
    if doctype == "Sales Invoice":
        partytype, ref_partytype, internal = "Customer", "Supplier", "is_internal_customer"
        ref_doc =  "Purchase Invoice"
    else:
        partytype, ref_partytype, internal = "Supplier", "Customer", "is_internal_supplier"
        ref_doc =  "Sales Invoice"

    if inter_company_invoice_reference:
        doc = frappe.get_doc(ref_doc, inter_company_invoice_reference)
        ref_party = doc.supplier if doctype == "Sales Invoice" else doc.customer
        if not frappe.db.get_value(partytype, {"represents_company": doc.company}, "name") == party:
            frappe.throw(_("Invalid {0} for Inter Company Invoice.").format(partytype))
        if not frappe.db.get_value(ref_partytype, {"name": ref_party}, "represents_company") == company:
            frappe.throw(_("Invalid Company for Inter Company Invoice."))

    elif frappe.db.get_value(partytype, {"name": party, internal: 1}, "name") == party:
        companies = frappe.db.sql("""select company from `tabAllowed To Transact With`
            where parenttype = '{0}' and parent = '{1}'""".format(partytype, party), as_list = 1)
        companies = [d[0] for d in companies]
        if not company in companies:
            frappe.throw(_("{0} not allowed to transact with {1}. Please change the Company.").format(partytype, company))

def update_linked_invoice(doctype, name, inter_company_invoice_reference):
    if inter_company_invoice_reference:
        frappe.db.set_value(doctype, inter_company_invoice_reference,\
            "inter_company_invoice_reference", name)

def unlink_inter_company_invoice(doctype, name, inter_company_invoice_reference):
    ref_doc = "Purchase Invoice" if doctype == "Sales Invoice" else "Sales Invoice"
    if inter_company_invoice_reference:
        frappe.db.set_value(doctype, name,\
            "inter_company_invoice_reference", "")
        frappe.db.set_value(ref_doc, inter_company_invoice_reference,\
            "inter_company_invoice_reference", "")

def get_list_context(context=None):
    from erpnext.controllers.website_list_for_contact import get_list_context
    list_context = get_list_context(context)
    list_context.update({
        'show_sidebar': True,
        'show_search': True,
        'no_breadcrumbs': True,
        'title': _('Invoices'),
    })
    return list_context

@frappe.whitelist()
def get_bank_cash_account(mode_of_payment, company):
    account = frappe.db.get_value("Mode of Payment Account",
        {"parent": mode_of_payment, "company": company}, "default_account")
    if not account:
        frappe.throw(_("Please set default Cash or Bank account in Mode of Payment {0}")
            .format(mode_of_payment))
    return {
        "account": account
    }

@frappe.whitelist()
def make_delivery_note(source_name, target_doc=None):
    def set_missing_values(source, target):
        target.ignore_pricing_rule = 1
        target.run_method("set_missing_values")
        target.run_method("calculate_taxes_and_totals")

    def update_item(source_doc, target_doc, source_parent):
        target_doc.qty = flt(source_doc.qty) - flt(source_doc.delivered_qty)
        target_doc.stock_qty = target_doc.qty * flt(source_doc.conversion_factor)

        target_doc.base_amount = target_doc.qty * flt(source_doc.base_rate)
        target_doc.amount = target_doc.qty * flt(source_doc.rate)

    doclist = get_mapped_doc("Sales Invoice", source_name,  {
        "Sales Invoice": {
            "doctype": "Delivery Note",
            "validation": {
                "docstatus": ["=", 1]
            }
        },
        "Sales Invoice Item": {
            "doctype": "Delivery Note Item",
            "field_map": {
                "name": "si_detail",
                "parent": "against_sales_invoice",
                "serial_no": "serial_no",
                "sales_order": "against_sales_order",
                "so_detail": "so_detail",
                "cost_center": "cost_center"
            },
            "postprocess": update_item,
            "condition": lambda doc: doc.delivered_by_supplier!=1
        },
        "Sales Taxes and Charges": {
            "doctype": "Sales Taxes and Charges",
            "add_if_empty": True
        },
        "Sales Team": {
            "doctype": "Sales Team",
            "field_map": {
                "incentives": "incentives"
            },
            "add_if_empty": True
        }
    }, target_doc, set_missing_values)

    return doclist

@frappe.whitelist()
def make_sales_return(source_name, target_doc=None):
    from erpnext.controllers.sales_and_purchase_return import make_return_doc
    return make_return_doc("Sales Invoice", source_name, target_doc)

def set_account_for_mode_of_payment(self):
    for data in self.payments:
        if not data.account:
            data.account = get_bank_cash_account(data.mode_of_payment, self.company).get("account")

def get_inter_company_details(doc, doctype):
    if doctype == "Sales Invoice":
        party = frappe.db.get_value("Supplier", {"disabled": 0, "is_internal_supplier": 1, "represents_company": doc.company}, "name")
        company = frappe.db.get_value("Customer", {"name": doc.customer}, "represents_company")
    else:
        party = frappe.db.get_value("Customer", {"disabled": 0, "is_internal_customer": 1, "represents_company": doc.company}, "name")
        company = frappe.db.get_value("Supplier", {"name": doc.supplier}, "represents_company")

    return {
        "party": party,
        "company": company
    }


def validate_inter_company_invoice(doc, doctype):

    details = get_inter_company_details(doc, doctype)
    price_list = doc.selling_price_list if doctype == "Sales Invoice" else doc.buying_price_list
    valid_price_list = frappe.db.get_value("Price List", {"name": price_list, "buying": 1, "selling": 1})
    if not valid_price_list:
        frappe.throw(_("Selected Price List should have buying and selling fields checked."))

    party = details.get("party")
    if not party:
        partytype = "Supplier" if doctype == "Sales Invoice" else "Customer"
        frappe.throw(_("No {0} found for Inter Company Transactions.").format(partytype))

    company = details.get("company")
    default_currency = frappe.db.get_value("Company", company, "default_currency")
    if default_currency != doc.currency:
        frappe.throw(_("Company currencies of both the companies should match for Inter Company Transactions."))

    return

@frappe.whitelist()
def make_inter_company_purchase_invoice(source_name, target_doc=None):
    return make_inter_company_invoice("Sales Invoice", source_name, target_doc)

def make_inter_company_invoice(doctype, source_name, target_doc=None):
    if doctype == "Sales Invoice":
        source_doc = frappe.get_doc("Sales Invoice", source_name)
        target_doctype = "Purchase Invoice"
    else:
        source_doc = frappe.get_doc("Purchase Invoice", source_name)
        target_doctype = "Sales Invoice"

    validate_inter_company_invoice(source_doc, doctype)
    details = get_inter_company_details(source_doc, doctype)

    def set_missing_values(source, target):
        target.run_method("set_missing_values")

    def update_details(source_doc, target_doc, source_parent):
        target_doc.inter_company_invoice_reference = source_doc.name
        if target_doc.doctype == "Purchase Invoice":
            target_doc.company = details.get("company")
            target_doc.supplier = details.get("party")
            target_doc.buying_price_list = source_doc.selling_price_list
        else:
            target_doc.company = details.get("company")
            target_doc.customer = details.get("party")
            target_doc.selling_price_list = source_doc.buying_price_list

    doclist = get_mapped_doc(doctype, source_name,  {
        doctype: {
            "doctype": target_doctype,
            "postprocess": update_details,
            "field_no_map": [
                "taxes_and_charges"
            ]
        },
        doctype +" Item": {
            "doctype": target_doctype + " Item",
            "field_no_map": [
                "income_account",
                "expense_account",
                "cost_center",
                "warehouse"
            ]
        }

    }, target_doc, set_missing_values)

    return doclist

@frappe.whitelist()
def get_loyalty_programs(customer):
    ''' sets applicable loyalty program to the customer or returns a list of applicable programs '''
    from erpnext.selling.doctype.customer.customer import get_loyalty_programs

    customer = frappe.get_doc('Customer', customer)
    if customer.loyalty_program: return

    lp_details = get_loyalty_programs(customer)

    if len(lp_details) == 1:
        frappe.db.set(customer, 'loyalty_program', lp_details[0])
        return []
    else:
        return lp_details

################################ Custom YTPL ############################
################# YTPL Code Start ###########################
@frappe.whitelist()
def create_xml_file_for_tally(sales_invoice_list):
    import xml.etree.ElementTree as grg
    from frappe.utils.file_manager import download_file
    from frappe.utils import get_bench_path, get_files_path
    import datetime, os
    sales_invoice_list= sales_invoice_list.replace('"', '')
    sales_invoice_list= sales_invoice_list[1:len(sales_invoice_list)-1]
    sales_invoice_list= sales_invoice_list.split(",") 
    if len(sales_invoice_list) > 0:
        root = grg.Element("ENVELOPE")
        for sales_invoice in sales_invoice_list:
            doc= frappe.get_doc('Sales Invoice', sales_invoice)
            gst_number= frappe.db.get_value('Business Unit', {'bu_name': doc.customer}, ['gst_number'])
            print(gst_number)
            if gst_number:
                DBCFIXED= grg.Element("DBCFIXED")
                root.append(DBCFIXED)
                DBCDATE= grg.SubElement(DBCFIXED, "DBCDATE")
                DBCDATE.text= getdate(doc.posting_date).strftime("X%d-%b-%y").replace('X0', '') 
                DBCPARTY= grg.SubElement(DBCFIXED, "DBCPARTY")
                DBCPARTY.text= doc.customer
                DBCVCHTYPE= grg.Element("DBCVCHTYPE")
                root.append(DBCVCHTYPE)
                DBCVCHTYPE.text= "Sales"
                DBCVCHNO= grg.Element("DBCVCHNO")
                root.append(DBCVCHNO)
                DBCVCHNO.text= doc.name
                DBCVCHREF= grg.Element("DBCVCHREF")
                root.append(DBCVCHREF)
                DBCVCHREF.text= doc.name
                DBCGSTIN= grg.Element("DBCGSTIN")
                root.append(DBCGSTIN)
                DBCGSTIN.text= gst_number 
                DBCNARR= grg.Element("DBCNARR")
                root.append(DBCNARR)
                DBCNARR.text= doc.name + " - " + doc.customer
                DBCAMOUNT= grg.Element("DBCAMOUNT")
                root.append(DBCAMOUNT)
                DBCAMOUNT.text= str(doc.total)
                DBCGROSSAMT= grg.Element("DBCGROSSAMT")
                root.append(DBCGROSSAMT)
                DBCGROSSAMT.text= str(doc.rounded_total)
                DBCLEDAMT= grg.Element("DBCLEDAMT")
                root.append(DBCLEDAMT)
                DBCLEDAMT.text= str(doc.total)
                if len(doc.taxes) > 0:
                    if taxes_and_charges.upper().startswith("IN STATE"):
                        DBCLEDAMT= grg.Element("DBCLEDAMT")
                        root.append(DBCGROSSAMT)
                        DBCLEDAMT.text= str(doc.taxes[0].tax_amount)
                        DBCLEDAMT= grg.Element("DBCLEDAMT")
                        root.append(DBCGROSSAMT)
                        DBCLEDAMT.text= str(doc.taxes[1].tax_amount)
                        DBCLEDAMT= grg.Element("DBCLEDAMT")
                        root.append(DBCGROSSAMT)
                        DBCLEDAMT.text= str(doc.rounding_adjustment)
                        DBCLEDAMT= grg.Element("DBCLEDAMT")
                        root.append(DBCGROSSAMT)
                        DBCLEDAMT.text= ""
                        DBCLEDAMT= grg.Element("DBCLEDAMT")
                        root.append(DBCGROSSAMT)
                        DBCLEDAMT.text= ""
                    else:
                        DBCLEDAMT= grg.Element("DBCLEDAMT")
                        root.append(DBCGROSSAMT)
                        DBCLEDAMT.text= ""
                        DBCLEDAMT= grg.Element("DBCLEDAMT")
                        root.append(DBCGROSSAMT)
                        DBCLEDAMT.text= ""
                        DBCLEDAMT= grg.Element("DBCLEDAMT")
                        root.append(DBCGROSSAMT)
                        DBCLEDAMT.text= str(doc.rounding_adjustment)
                        DBCLEDAMT= grg.Element("DBCLEDAMT")
                        root.append(DBCGROSSAMT)
                        DBCLEDAMT.text= str(doc.taxes[0].tax_amount)
                        DBCLEDAMT= grg.Element("DBCLEDAMT")
                        root.append(DBCGROSSAMT)
                        DBCLEDAMT.text= ""
                else:
                    DBCLEDAMT= grg.Element("DBCLEDAMT")
                    root.append(DBCGROSSAMT)
                    DBCLEDAMT.text= ""
                    DBCLEDAMT= grg.Element("DBCLEDAMT")
                    root.append(DBCGROSSAMT)
                    DBCLEDAMT.text= ""
                    DBCLEDAMT= grg.Element("DBCLEDAMT")
                    root.append(DBCGROSSAMT)
                    DBCLEDAMT.text= str(doc.rounding_adjustment)
                    DBCLEDAMT= grg.Element("DBCLEDAMT")
                    root.append(DBCGROSSAMT)
                    DBCLEDAMT.text= ""
                    DBCLEDAMT= grg.Element("DBCLEDAMT")
                    root.append(DBCGROSSAMT)
                    DBCLEDAMT.text= ""
        tree = grg.ElementTree(root)
        file_name= "Sales-Invoice-"+datetime.datetime.now().strftime("%d%m%Y%H%M%S")+".xml"
        path= get_bench_path()+"/sites"+ get_files_path(is_private= False).replace('.', '')+"/%s"%(file_name)
        tree.write(path)
        file_doc= frappe.new_doc("File")
        file_doc.file_name= file_name
        file_doc.is_private= 0
        file_doc.attached_to_doctype= "Sales Invoice"
        file_doc.attached_to_name= doc.name
        file_doc.file_url=  "/files/%s" %(file_name)
        file_doc.flags.ignore_mandatory= True
        file_doc.flags.ignore_permissions= True
        file_doc.save()
        file_actual_name= file_doc.save()
        return frappe.utils.get_url()+"/files/%s" %(file_name), file_name
        
@frappe.whitelist()
def auto_invoice_creation(billing_period, customer= None):
    import re 
    msg= ""
    pointer= 30001
    data= frappe.db.sql("""select bill_number from `tabSales Invoice` order by bill_number desc limit 1;""", as_dict= True)
    if len(data) > 0:
        if data[0]["bill_number"] is not None and data[0]["bill_number"] != '':
            string_check= re.compile('[@_!#$%^&*()<>?/\|}{~:]')
            if(string_check.search(data[0]["bill_number"]) == None):
                pointer= int(data[0]["bill_number"]) + 1
            else:
                temp= data[0]["bill_number"].split("-")
                pointer= int(temp[len(temp) - 1]) + 1
    all_customers= []
    if customer != None:
        all_customers= frappe.db.sql("""select distinct customer from `tabPeople Attendance` where attendance_period= '%s' and status= 'To Bill' and customer= '%s'""" %(billing_period, customer), as_dict= True)
    else:
        all_customers= frappe.db.sql("""select distinct customer from `tabPeople Attendance` where attendance_period= '%s' and status= 'To Bill'""" %(billing_period), as_dict= True)
        
    if len(all_customers) >  0:
        att_wise_bill_count= cust_wise_bill_count= standard_bill_count= po_bill_count=0
        for i in range(0, len(all_customers)):
            customer= frappe.get_doc('Customer', all_customers[i]["customer"])
            print ("%s Invoicing In Process counter value %s" %(customer.name, i))
            if customer.invoice_process_type == "Attendance Wise":
                att_wise_bill_count += 1
                pointer= attendance_wise_invoicing(customer, billing_period, pointer)
            elif customer.invoice_process_type == "Customer Wise" or customer.invoice_process_type ==  "State Wise":
                cust_wise_bill_count +=1
                pointer= customer_or_state_wise_invoicing(customer, billing_period, pointer)
            elif customer.invoice_process_type == "Standard Billing Wise":
                standard_bill_count += 1
                pointer= standard_invoicing(customer, billing_period, pointer)
            elif customer.invoice_process_type == "PO Wise":
                po_bill_count += 1
                pointer= po_wise_billing(customer, billing_period, pointer)
            else: pass
        msg= """Total Attendance Wise Billing= %s <br> Total Customer Wise Billing = %s <br> Total Standard Billing = %s <br>  PO Wise Billing = %s""" %(att_wise_bill_count, cust_wise_bill_count, standard_bill_count, po_bill_count)
    else: msg= "No Record Found For Billing"
    return msg


def get_draft_bill(customer, billing_period):
    data= frappe.db.sql("""select distinct sii.attendance from `tabSales Invoice` si inner join `tabSales Invoice Item` sii 
                            on si.name= sii.parent where si.docstatus= 0 
                            and si.billing_period= '%s' and si.customer= '%s' and sii.attendance is not null"""%(billing_period, customer), as_dict= True)
    result= []
    if len(data) > 0:
        for row in data:
            result.append(row.attendance)
    return result

def get_draft_bill_contract_wise(customer, billing_period):
    data= frappe.db.sql("""select distinct sii.contract from `tabSales Invoice` si inner join `tabSales Invoice Item` sii 
                            on si.name= sii.parent where si.docstatus= 0 and si.billing_period= '%s' and si.customer= '%s' and 
                            sii.contract is not null"""%(billing_period, customer), as_dict= True)
    result= []
    if len(data) > 0:
        for row in data:
            result.append(row.contract)
    return result        

def add_service_charges(doc):
    service_charges= 0.0
    if len(doc.items) > 0:
        for item in doc.items:
            if item.contract:
                contract_doc= frappe.get_doc('Site Contract', item.contract)
                if contract_doc.is_service_charges == 1:
                    if contract_doc.mode_of_service_charges == 'Percentage':
                        service_charges+= (item.amount * contract_doc.service_charges) / 100
                    else:
                        service_charges+= contract_doc.service_charges
        if service_charges > 0.0:
            company_income_acount, cost_center= frappe.db.get_value('Company', doc.company, ['default_income_account', 'cost_center'])
            doc.append('items',{    'rate': float(service_charges),
                                    'price_list_rate': float(service_charges),
                                    'item_code': "Service Charges",
                                    'item_name': "Service Charges",
                                    'description': "Service Charges",
                                    'uom': 'Nos',
                                    'qty': 1,
                                    'item_from_date': doc.si_from_date,
                                    'item_to_date': doc.si_to_date,
                                    'income_account': company_income_acount,
                                    'cost_center': cost_center
                                    }
                            )
    else: pass
    return doc
    

def get_customer_address(customer):
    #address= get_default_address('Customer', customer)
    address= get_default_address('Business Unit', customer)
    #address1= frappe.get_doc('Address', customer)
    address_details= None
    if address:
        address_details= frappe.get_doc("Address", address)
    return address_details

def get_customer_attendances(billing_period, customer):
    draft_bill= get_draft_bill(billing_period, customer)
    all_attendance= []       
    if len(draft_bill) > 0:
        if len(draft_bill) == 1:
            all_attendance= frappe.db.sql(""" select name, start_date, end_date, company from `tabPeople Attendance`
                                                where attendance_period= '%s' and status= 'To Bill'
                                                and customer= '%s' and name != '%s'""" %(billing_period, customer, draft_bill[0]), as_dict= True)
        else:
            all_attendance= frappe.db.sql(""" select name, start_date, end_date, company from `tabPeople Attendance` 
                                                where attendance_period= '%s' and status= 'To Bill' 
                                                and customer= '%s' and name in %s""" %(billing_period, customer, str(tuple(draft_bill))), as_dict= True)
    else:
        all_attendance= frappe.db.sql(""" select name, start_date, end_date, company from `tabPeople Attendance` 
                                            where attendance_period= '%s' and status= 'To Bill' 
                                            and customer= '%s'""" %(billing_period, customer), as_dict= True)
    return all_attendance

def get_attendance_details(attendance_name):
    attendance_details= frappe.db.sql("""select atd.work_type, sum(atd.bill_duty) as total_bill_duty,  ctd.quantity, 
                                        att.include_relieving_charges, atd.wage_rule, att.name, atd.wage_rule_details, att.contract, 
                                        att.site, att.site_name, att.weekly_off_included, att.company  from `tabPeople Attendance` att  
                                        inner join `tabAttendance Details` atd on atd.parent= att.name 
                                        inner join `tabContract Details` ctd on att.contract= ctd.parent and atd.work_type= ctd.work_type 
                                        where att.name= '%s' group by att.name, atd.work_type""" %(attendance_name), as_dict= True)
    return attendance_details

def get_price(salary_structure, wage_rule_rev_name, start_date, end_date):
    from frappe.utils import date_diff
    wage_rule_rev_name= wage_rule_rev_name
    total_days= float(date_diff(end_date, start_date)) + 1.0
    rate= 0.0
    wage_rule= frappe.get_doc("Wage Structure", salary_structure)
    for i in range(0, len(wage_rule.wage_rule_details)):
        if getdate(wage_rule.wage_rule_details[i].from_date) <= getdate(start_date) and getdate(wage_rule.wage_rule_details[i].to_date) >= getdate(end_date):
            if wage_rule.wage_rule_details[i].rate_per == "Month":
                rate= round(wage_rule.wage_rule_details[i].rate / total_days, 2)
            else: rate= round(wage_rule.wage_rule_details[i].rate, 2)
    return rate

def get_sunday_count(start_date, end_date, period):
    from sps.sps.doctype.people_attendance.people_attendance import get_wo_count
    period_doc= frappe.get_doc('Salary Payroll Period', period)
    sunday_count, sunday_count_details= get_wo_count(start_date, end_date, 'Sunday', None, period_doc)
    return sunday_count

def get_total_qty(total_bill_duty, sunday_count, contract, work_type):
    bill_duty= total_bill_duty
    total_qty= 0
    if contract.is_relieving_charges_includes:
        for row in contract.contract_details:
            if row.work_type in ["GD1", "GMW"] and work_type in ["GD1", "GMW"]:
                total_qty= bill_duty + (sunday_count * row.quantity)
            else: total_qty= bill_duty
    else: total_qty= bill_duty
    return total_qty

def get_posting_date(start_date):
    import datetime
    start_date_day= datetime.datetime.strptime(start_date, "%Y-%m-%d").day
    start_date_month= datetime.datetime.strptime(start_date, "%Y-%m-%d").month
    start_date_year= datetime.datetime.strptime(start_date, "%Y-%m-%d").year
    next_year= datetime.datetime.strptime(start_date, "%Y-%m-%d").year + 1
    next_month= datetime.datetime.strptime(start_date, "%Y-%m-%d").month + 1
    posting_date= ""
    if start_date_day == 1:
        if next_month == 13:
            posting_date= str(next_year)+"-"+"01"+"-"+"03"
        else:
            posting_date= str(start_date_year)+"-"+("0" if next_month < 10 else "")+str(next_month)+"-"+"03"
    else:
        posting_date= str(start_date_year)+"-"+("0" if start_date_month < 10 else "")+str(start_date_month)+"-"+"24"
    return posting_date

def attendance_wise_invoicing(customer, billing_period, pointer):
    #address= get_customer_address(customer.name) # address display pending
    att_data= get_customer_attendances(billing_period, customer.name)
    my_pointer= pointer
    posting_date= get_posting_date(str(att_data[0]["start_date"]))
    for i in range(0, len(att_data)):
        bill_data= get_attendance_details(att_data[i]["name"])
        si_doc= frappe.new_doc("Sales Invoice")
        si_doc.billing_type= "Attendance"
        #si_doc.custom_bill_no= 1
        si_doc.custom_bill_no= 0
        #si_doc.bill_number= str(my_pointer)
        si_doc.billing_period= billing_period
        si_doc.customer= customer.name
        si_doc.posting_date= posting_date
        si_doc.si_from_date= att_data[0]["start_date"]
        si_doc.si_to_date= att_data[0]["end_date"]
        #si_doc.customer_name= customer.customer_code   
        #si_doc.site_address_display = get_address_display(address) # address display pending 
        ################## child table items entry @@@ ###########################
        address= None
        sunday_count= get_sunday_count(att_data[0]["start_date"], att_data[0]["end_date"], billing_period)
        for data in bill_data:
            rate= get_price(data["wage_rule"], data["wage_rule_details"], att_data[0]["start_date"], att_data[0]["end_date"])
            contract_doc= frappe.get_doc('Site Contract', data["contract"])
            si_doc.append('items', {"item_code": data["work_type"],
                                    "item_name": data["work_type"],
                                    "qty":  get_total_qty( data["total_bill_duty"], sunday_count, contract_doc, data["work_type"]),
                                    "uom": "Nos",
                                    "price_list_rate":rate,
                                    "rate": rate,
                                    "salary_structure":data["wage_rule"],
                                    "ss_revision_name":data["wage_rule_details"],
                                    "ss_revision_rate": rate,
                                    "contract": data["contract"],
                                    "contract_quantity": data["quantity"],
                                    "site": data["site"],
                                    "site_name": data["site_name"],
                                    "attendance": att_data[i]["name"],
                                    "item_from_date": att_data[i]["start_date"],
                                    "item_to_date": att_data[0]["end_date"]
                                    }
                        )
            address= get_customer_address(data["site"]) if get_customer_address(data["site"]) is not None else get_customer_address(customer.customer_code)# address display pending
        si_doc.company= att_data[0]["company"]
        if address and address.gst_state != None:
            if att_data[0]["company"] == 'Security & Personnel Services Pvt. Ltd.':
                si_doc.taxes_and_charges= "Out of State GST - SPS" if str(address.gst_state).strip().upper() != "MAHARASHTRA" else "In State GST - SPS"
                if si_doc.taxes_and_charges == "In State GST - SPS":
                    si_doc.append('taxes', {"charge_type": "On Net Total", "account_head": "SGST - SPS", "description": "SGST @ 9.0", "rate": 9.00})
                    si_doc.append('taxes', {"charge_type": "On Net Total", "account_head": "CGST - SPS", "description": "CGST @ 9.0", "rate": 9.00})
                elif si_doc.taxes_and_charges == "Out of State GST - SPS":
                    si_doc.append('taxes', {"charge_type": "On Net Total", "account_head": "IGST - SPS", "description": "IGST @ 18.0", "rate": 18.00})
                else : pass
                calculate_taxes_and_totals(si_doc)
            elif att_data[0]["company"] == 'Sukhi Facility Services Pvt. Ltd.':
                si_doc.taxes_and_charges= "Out of State GST - SFS" if str(address.gst_state).strip().upper() != "MAHARASHTRA" else "In State GST - SFS"
                if si_doc.taxes_and_charges == "In State GST - SFS":
                    si_doc.append('taxes', {"charge_type": "On Net Total", "account_head": "SGST - SFS", "description": "SGST @ 9.0", "rate": 9.00})
                    si_doc.append('taxes', {"charge_type": "On Net Total", "account_head": "CGST - SFS", "description": "CGST @ 9.0", "rate": 9.00})
                elif si_doc.taxes_and_charges == "Out of State GST - SFS":
                    si_doc.append('taxes', {"charge_type": "On Net Total", "account_head": "IGST - SFS", "description": "IGST @ 18.0", "rate": 18.00})
                else: pass
                calculate_taxes_and_totals(si_doc)
            elif att_data[0]["company"] == 'Metro Facility Services':
                si_doc.taxes_and_charges= "Out of State GST - MFS" if str(address.gst_state).strip().upper() != "MAHARASHTRA" else "In State GST - MFS"
                if si_doc.taxes_and_charges == "In State GST - MFS":
                    si_doc.append('taxes', {"charge_type": "On Net Total", "account_head": "SGST - MFS", "description": "SGST @ 9.0", "rate": 9.00})
                    si_doc.append('taxes', {"charge_type": "On Net Total", "account_head": "CGST - MFS", "description": "CGST @ 9.0", "rate": 9.00})
                elif si_doc.taxes_and_charges == "Out of State GST - MFS":
                    si_doc.append('taxes', {"charge_type": "On Net Total", "account_head": "IGST - MFS", "description": "IGST @ 18.0", "rate": 18.00})
                else: pass
                calculate_taxes_and_totals(si_doc)
            elif att_data[0]["company"] == 'Falcon Facility Services':
                si_doc.taxes_and_charges= "Out of State GST - FFS" if str(address.gst_state).strip().upper() != "MAHARASHTRA" else "In State GST - FFS"
                if si_doc.taxes_and_charges == "In State GST - FFS":
                    si_doc.append('taxes', {"charge_type": "On Net Total", "account_head": "SGST - FFS", "description": "SGST @ 9.0", "rate": 9.00})
                    si_doc.append('taxes', {"charge_type": "On Net Total", "account_head": "CGST - FFS", "description": "CGST @ 9.0", "rate": 9.00})
                elif si_doc.taxes_and_charges == "Out of State GST - FFS":
                    si_doc.append('taxes', {"charge_type": "On Net Total", "account_head": "IGST - FFS", "description": "IGST @ 18.0", "rate": 18.00})
                else: pass
                calculate_taxes_and_totals(si_doc)
            else: pass
        else: pass
        my_pointer= my_pointer + 1
        add_service_charges(si_doc)
        si_doc.save()
    return my_pointer

def customer_or_state_wise_invoicing(customer, billing_period, pointer):
    address= get_customer_address(customer.customer_code) # address display pending
    att_data= get_customer_attendances(billing_period, customer.name)
    posting_date= get_posting_date(str(att_data[0]["start_date"]))
    si_doc= frappe.new_doc("Sales Invoice")
    print(si_doc.as_dict())
    si_doc.billing_type= "Attendance"
    #si_doc.custom_bill_no= 1
    si_doc.custom_bill_no= 0
    #si_doc.bill_number= str(pointer)
    si_doc.billing_period= billing_period
    si_doc.customer= customer.name
    si_doc.posting_date= posting_date
    si_doc.si_from_date= att_data[0]["start_date"]
    si_doc.si_to_date= att_data[0]["end_date"]
    #si_doc.customer_name= customer.customer_code
    bill_data= frappe.db.sql("""select atd.work_type, sum(atd.bill_duty) as total_bill_duty, ctd.quantity, atd.wage_rule, att.include_relieving_charges, 
                                atd.wage_rule_details, att.contract, att.site, att.site_name, att.name, att.weekly_off_included 
                                from `tabPeople Attendance` att 
                                inner join `tabAttendance Details` atd on atd.parent= att.name 
                                inner join `tabContract Details` ctd on att.contract= ctd.parent and atd.work_type= ctd.work_type
                                where att.customer= '%s' and att.attendance_period= '%s' 
                                and att.status= 'To Bill' and att.name not in(select distinct sii.attendance from `tabSales Invoice` si inner join `tabSales Invoice Item` sii 
                                on si.name= sii.parent where si.docstatus= 0 
                                and si.billing_period= '%s' and si.customer= '%s' and sii.attendance is not null)
                                group by atd.work_type, att.name ;"""%(customer.name, billing_period, billing_period, customer.name), as_dict= True)

    sunday_count= get_sunday_count(att_data[0]["start_date"], att_data[0]["end_date"], billing_period)
    for data in bill_data:
        rate= get_price(data["wage_rule"], data["wage_rule_details"], att_data[0]["start_date"], att_data[0]["end_date"])
        contract_doc= frappe.get_doc('Site Contract', data["contract"])
        si_doc.append('items', {"item_code": data["work_type"],
                                    "item_name": data["work_type"],
                                    "qty": get_total_qty( data["total_bill_duty"], sunday_count, contract_doc, data["work_type"]),
                                    "uom": "Nos",
                                    "price_list_rate":rate,
                                    "rate": rate,
                                    "salary_structure":data["wage_rule"],
                                    "ss_revision_name":data["wage_rule_details"],
                                    "ss_revision_rate": rate,
                                    "contract": data["contract"],
                                    "contract_quantity": data["quantity"],
                                    "site": data["site"],
                                    "site_name": data["site_name"],
                                    "attendance": data["name"],
                                    "item_from_date": att_data[0]["start_date"],
                                    "item_to_date": att_data[0]["end_date"]
                                    }
                    )
    si_doc.company= att_data[0]["company"]
    if address and address.gst_state != None:
        if att_data[0]["company"] == 'Security & Personnel Services Pvt. Ltd.':
            si_doc.taxes_and_charges= "Out of State GST - SPS" if str(address.gst_state).strip().upper() != "MAHARASHTRA" else "In State GST - SPS"
            if si_doc.taxes_and_charges == "In State GST - SPS":
                si_doc.append('taxes', {"charge_type": "On Net Total", "account_head": "SGST - SPS", "description": "SGST @ 9.0", "rate": 9.00})
                si_doc.append('taxes', {"charge_type": "On Net Total", "account_head": "CGST - SPS", "description": "CGST @ 9.0", "rate": 9.00})
            elif si_doc.taxes_and_charges == "Out of State GST - SPS":
                si_doc.append('taxes', {"charge_type": "On Net Total", "account_head": "IGST - SPS", "description": "IGST @ 18.0", "rate": 18.00})
            else : pass
            calculate_taxes_and_totals(si_doc)
        elif att_data[0]["company"] == 'Sukhi Facility Services Pvt. Ltd.':
            si_doc.taxes_and_charges= "Out of State GST - SFS" if str(address.gst_state).strip().upper() != "MAHARASHTRA" else "In State GST - SFS"
            if si_doc.taxes_and_charges == "In State GST - SFS":
                si_doc.append('taxes', {"charge_type": "On Net Total", "account_head": "SGST - SFS", "description": "SGST @ 9.0", "rate": 9.00})
                si_doc.append('taxes', {"charge_type": "On Net Total", "account_head": "CGST - SFS", "description": "CGST @ 9.0", "rate": 9.00})
            elif si_doc.taxes_and_charges == "Out of State GST - SFS":
                si_doc.append('taxes', {"charge_type": "On Net Total", "account_head": "IGST - SFS", "description": "IGST @ 18.0", "rate": 18.00})
            else: pass
            calculate_taxes_and_totals(si_doc)
        elif att_data[0]["company"] == 'Metro Facility Services':
            si_doc.taxes_and_charges= "Out of State GST - MFS" if str(address.gst_state).strip().upper() != "MAHARASHTRA" else "In State GST - MFS"
            if si_doc.taxes_and_charges == "In State GST - MFS":
                si_doc.append('taxes', {"charge_type": "On Net Total", "account_head": "SGST - MFS", "description": "SGST @ 9.0", "rate": 9.00})
                si_doc.append('taxes', {"charge_type": "On Net Total", "account_head": "CGST - MFS", "description": "CGST @ 9.0", "rate": 9.00})
            elif si_doc.taxes_and_charges == "Out of State GST - MFS":
                si_doc.append('taxes', {"charge_type": "On Net Total", "account_head": "IGST - MFS", "description": "IGST @ 18.0", "rate": 18.00})
            else: pass
            calculate_taxes_and_totals(si_doc)
        elif att_data[0]["company"] == 'Falcon Facility Services':
            si_doc.taxes_and_charges= "Out of State GST - FFS" if str(address.gst_state).strip().upper() != "MAHARASHTRA" else "In State GST - FFS"
            if si_doc.taxes_and_charges == "In State GST - FFS":
                si_doc.append('taxes', {"charge_type": "On Net Total", "account_head": "SGST - FFS", "description": "SGST @ 9.0", "rate": 9.00})
                si_doc.append('taxes', {"charge_type": "On Net Total", "account_head": "CGST - FFS", "description": "CGST @ 9.0", "rate": 9.00})
            elif si_doc.taxes_and_charges == "Out of State GST - FFS":
                si_doc.append('taxes', {"charge_type": "On Net Total", "account_head": "IGST - FFS", "description": "IGST @ 18.0", "rate": 18.00})
            else: pass
            calculate_taxes_and_totals(si_doc)
        else:pass
    else: pass
    add_service_charges(si_doc) 
    si_doc.save()
    return pointer + 1



def standard_invoicing(customer, billing_period, pointer):
    from frappe.utils import date_diff
    period= frappe.get_doc("Salary Payroll Period", billing_period)
    billed_contract= get_draft_bill_contract_wise(customer.name, billing_period)
    posting_date= get_posting_date(str(period.start_date))
    address= get_customer_address(customer.name) # address display pending
    all_contract= frappe.get_list("Site Contract", filters= [
                                                            ['party_name', '=',  customer.name],
                                                            ['is_standard', '=', 1],
                                                            ['start_date', '<=', period.start_date],
                                                            ['end_date', '>=', period.end_date],
                                                            ['docstatus', '=', 1],
                                                            ['name', 'not in', billed_contract]
                                                        ],
                                            fields= ['name'])
    if len(all_contract) > 0:
        my_pointer= pointer
        for i in range(0, len(all_contract)):
            si_doc= frappe.new_doc("Sales Invoice")
            si_doc.billing_type= "Standard"
            #si_doc.custom_bill_no= 1
            si_doc.custom_bill_no= 0
            #si_doc.bill_number= str(my_pointer)
            si_doc.billing_period= billing_period
            si_doc.customer= customer.name
            si_doc.posting_date= posting_date
            si_doc.si_from_date= period.start_date
            si_doc.si_to_date= period.end_date
            #si_doc.customer_name= customer.customer_code
            address= None
            bill_data= frappe.db.sql("""select ctd.work_type, ctd.quantity, ctd.wage_rule, ctd.from_date, ctd.to_date, 
                                        ct.name as contract, ct.company as company, ct.bu_site as site, ct.bu_site_name as site_name 
                                        from `tabSite Contract` ct 
                                        inner join `tabContract Details` ctd on ctd.parent= ct.name
                                        where ct.name= '%s' and and ((ctd.from_date <= '%s' and ctd.to_date >= '%s') 
                                        or (ctd.from_date <= '%s' and ctd.to_date <= '%s')); """ %(all_contract[i]["name"]), as_dict= True)
            for data in bill_data:
                rate= get_price(data["wage_rule"], None, period.start_date, period.end_date)
                from_date=  data["from_date"] if str(data["from_date"]) >= str(period.start_date) else period.start_date
                to_date= data["to_date"] if str(data["to_date"]) <= str(period.end_date) else period.end_date
                total_days= date_diff(to_date, from_date) + 1 
                si_doc.append('items', {"item_code": data["work_type"],
                                        "item_name": data["work_type"],
                                        "qty": (int(data["quantity"]) *  int(total_days)),
                                        "uom": "Nos",
                                        "price_list_rate":rate,
                                        "rate": rate,
                                        "salary_structure":data["wage_rule"],
                                        "ss_revision_rate": rate,
                                        "contract": data["contract"],
                                        "contract_quantity": data["quantity"],
                                        "site": data["site"],
                                        "site_name": data["site_name"],
                                        "item_from_date": period.start_date,
                                        "item_to_date": period.end_date
                                    }
                        )
                #address= get_customer_address(data["site"]) # address display pending
                address= get_customer_address(data["site"]) if get_customer_address(data["site"]) is not None else get_customer_address(customer.customer_code)# address display pending
            add_service_charges(si_doc)
            si_doc.company= bill_data[0]["company"]
            if address and address.gst_state != None:
                if bill_data[0]["company"]  == 'Security & Personnel Services Pvt. Ltd.':
                    si_doc.taxes_and_charges= "Out of State GST - SPS" if str(address.gst_state).strip().upper() != "MAHARASHTRA" else "In State GST - SPS"
                    if si_doc.taxes_and_charges == "In State GST - SPS":
                        si_doc.append('taxes', {"charge_type": "On Net Total", "account_head": "SGST - SPS", "description": "SGST @ 9.0", "rate": 9.00})
                        si_doc.append('taxes', {"charge_type": "On Net Total", "account_head": "CGST - SPS", "description": "CGST @ 9.0", "rate": 9.00})
                    elif si_doc.taxes_and_charges == "Out of State GST - SPS":
                        si_doc.append('taxes', {"charge_type": "On Net Total", "account_head": "IGST - SPS", "description": "IGST @ 18.0", "rate": 18.00})
                    else : pass
                elif att_data[0]["company"] == 'Sukhi Facility Services Pvt. Ltd.':
                    si_doc.taxes_and_charges= "Out of State GST - SFS" if str(address.gst_state).strip().upper() != "MAHARASHTRA" else "In State GST - SFS"
                    if si_doc.taxes_and_charges == "In State GST - SFS":
                        si_doc.append('taxes', {"charge_type": "On Net Total", "account_head": "SGST - SFS", "description": "SGST @ 9.0", "rate": 9.00})
                        si_doc.append('taxes', {"charge_type": "On Net Total", "account_head": "CGST - SFS", "description": "CGST @ 9.0", "rate": 9.00})
                    elif si_doc.taxes_and_charges == "Out of State GST - SFS":
                        si_doc.append('taxes', {"charge_type": "On Net Total", "account_head": "IGST - SFS", "description": "IGST @ 18.0", "rate": 18.00})
                    else: pass
                    calculate_taxes_and_totals(si_doc)
                elif bill_data[0]["company"]  == 'Metro Facility Services':
                    si_doc.taxes_and_charges= "Out of State GST - MFS" if str(address.gst_state).strip().upper() != "MAHARASHTRA" else "In State GST - MFS"
                    if si_doc.taxes_and_charges == "In State GST - MFS":
                        si_doc.append('taxes', {"charge_type": "On Net Total", "account_head": "SGST - MFS", "description": "SGST @ 9.0", "rate": 9.00})
                        si_doc.append('taxes', {"charge_type": "On Net Total", "account_head": "CGST - MFS", "description": "CGST @ 9.0", "rate": 9.00})
                    elif si_doc.taxes_and_charges == "Out of State GST - MFS":
                        si_doc.append('taxes', {"charge_type": "On Net Total", "account_head": "IGST - MFS", "description": "IGST @ 18.0", "rate": 18.00})
                    else: pass
                elif bill_data[0]["company"] == 'Falcon Facility Services':
                    si_doc.taxes_and_charges= "Out of State GST - FFS" if str(address.gst_state).strip().upper() != "MAHARASHTRA" else "In State GST - FFS"
                    if si_doc.taxes_and_charges == "In State GST - FFS":
                        si_doc.append('taxes', {"charge_type": "On Net Total", "account_head": "SGST - FFS", "description": "SGST @ 9.0", "rate": 9.00})
                        si_doc.append('taxes', {"charge_type": "On Net Total", "account_head": "CGST - FFS", "description": "CGST @ 9.0", "rate": 9.00})
                    elif si_doc.taxes_and_charges == "Out of State GST - FFS":
                        si_doc.append('taxes', {"charge_type": "On Net Total", "account_head": "IGST - FFS", "description": "IGST @ 18.0", "rate": 18.00})
                    else: pass
                else: pass
            else: pass
            my_pointer += 1
            calculate_taxes_and_totals(si_doc)
            si_doc.save()
    return my_pointer

def po_wise_billing(customer, billing_period, pointer):
    from frappe.utils import date_diff
    
    period= frappe.get_doc("Salary Payroll Period", billing_period)
    posting_date= get_posting_date(str(period.start_date))
    my_pointer= pointer
    si_doc= frappe.new_doc("Sales Invoice")
    si_doc.billing_type= "Standard"
    si_doc.bill_number= str(my_pointer)
    si_doc.billing_period= billing_period
    si_doc.customer= customer.name
    si_doc.posting_date= posting_date
    si_doc.si_from_date= period.start_date
    si_doc.si_to_date= period.end_date
    bill_data= frappe.db.sql(""" select c.name as contract, c.party_name, c.company, c.bu_site as site, c.bu_site_name as site_name,
                                sum(cd.quantity) as quantity, cd.work_type, cd.wage_rule, c.start_date, c.end_date, cd.from_date, cd.to_date
                                from `tabSite Contract` c
                                inner join `tabContract Details` cd on c.name= cd.parent
                                where c.party_name= '%s' and c.name not in(select distinct sii.contract from `tabSales Invoice` si inner join `tabSales Invoice Item` sii 
                                on si.name= sii.parent where si.docstatus= 0 and si.billing_period= '%s' and si.customer= '%s' and 
                                sii.contract is not null)
                                and c.start_date <= '%s' and c.end_date >= '%s'
                                and cd.from_date <= '%s' and cd.to_date >= '%s' and c.docstatus=1
                                group by c.bu_site, cd.work_type;""" %(customer.name, billing_period, customer.name, period.start_date, period.end_date, period.start_date, period.end_date), as_dict= True)
    total_days= float(date_diff(period.end_date, period.start_date)) + 1.0
    for data in bill_data:
        wage_rule= frappe.get_doc("Wage Structure", data["wage_rule"])
        rate= 0.0
        for i in range(0, len(wage_rule.wage_rule_details)):
            if getdate(wage_rule.wage_rule_details[i].from_date) <= getdate(period.start_date) and getdate(wage_rule.wage_rule_details[i].to_date) >= getdate(period.end_date):
                if wage_rule.wage_rule_details[i].rate_per == "Month":
                    rate= round(wage_rule.wage_rule_details[i].rate / total_days, 2)
                else: rate= round(wage_rule.wage_rule_details[i].rate, 2)
        si_doc.append('items', {"item_code": data["work_type"],
                                "item_name": data["work_type"],
                                "qty": (int(data["quantity"]) *  int(period.total_days)),
                                "uom": "Nos",
                                "price_list_rate":rate,
                                "rate": rate,
                                "salary_structure":data["wage_rule"],
                                "ss_revision_rate": rate,
                                "contract": data["contract"],
                                "contract_quantity": data["quantity"],
                                "site": data["site"],
                                "site_name": data["site_name"],
                                "item_from_date": period.start_date,
                                "item_to_date": period.end_date
                                }
            )
    address= get_customer_address(customer.customer_code)# address display pending
    add_service_charges(si_doc)
    si_doc.company= bill_data[0]["company"]
    if address and address.gst_state != None:
        if bill_data[0]["company"] == 'Security & Personnel Services Pvt. Ltd.':
            si_doc.taxes_and_charges= "Out of State GST - SPS" if str(address.gst_state).strip().upper() != "MAHARASHTRA" else "In State GST - SPS"
            if si_doc.taxes_and_charges == "In State GST - SPS":
                si_doc.append('taxes', {"charge_type": "On Net Total", "account_head": "SGST - SPS", "description": "SGST @ 9.0", "rate": 9.00})
                si_doc.append('taxes', {"charge_type": "On Net Total", "account_head": "CGST - SPS", "description": "CGST @ 9.0", "rate": 9.00})
            elif si_doc.taxes_and_charges == "Out of State GST - SPS":
                si_doc.append('taxes', {"charge_type": "On Net Total", "account_head": "IGST - SPS", "description": "IGST @ 18.0", "rate": 18.00})
            else : pass
        elif att_data[0]["company"] == 'Sukhi Facility Services Pvt. Ltd.':
            si_doc.taxes_and_charges= "Out of State GST - SFS" if str(address.gst_state).strip().upper() != "MAHARASHTRA" else "In State GST - SFS"
            if si_doc.taxes_and_charges == "In State GST - SFS":
                si_doc.append('taxes', {"charge_type": "On Net Total", "account_head": "SGST - SFS", "description": "SGST @ 9.0", "rate": 9.00})
                si_doc.append('taxes', {"charge_type": "On Net Total", "account_head": "CGST - SFS", "description": "CGST @ 9.0", "rate": 9.00})
            elif si_doc.taxes_and_charges == "Out of State GST - SFS":
                si_doc.append('taxes', {"charge_type": "On Net Total", "account_head": "IGST - SFS", "description": "IGST @ 18.0", "rate": 18.00})
            else: pass
        elif bill_data[0]["company"] == 'Metro Facility Services':
            si_doc.taxes_and_charges= "Out of State GST - MFS" if str(address.gst_state).strip().upper() != "MAHARASHTRA" else "In State GST - MFS"
            if si_doc.taxes_and_charges == "In State GST - MFS":
                si_doc.append('taxes', {"charge_type": "On Net Total", "account_head": "SGST - MFS", "description": "SGST @ 9.0", "rate": 9.00})
                si_doc.append('taxes', {"charge_type": "On Net Total", "account_head": "CGST - MFS", "description": "CGST @ 9.0", "rate": 9.00})
            elif si_doc.taxes_and_charges == "Out of State GST - MFS":
                si_doc.append('taxes', {"charge_type": "On Net Total", "account_head": "IGST - MFS", "description": "IGST @ 18.0", "rate": 18.00})
            else: pass
        elif bill_data[0]["company"] == 'Falcon Facility Services':
            si_doc.taxes_and_charges= "Out of State GST - FFS" if str(address.gst_state).strip().upper() != "MAHARASHTRA" else "In State GST - FFS"
            if si_doc.taxes_and_charges == "In State GST - FFS":
                si_doc.append('taxes', {"charge_type": "On Net Total", "account_head": "SGST - FFS", "description": "SGST @ 9.0", "rate": 9.00})
                si_doc.append('taxes', {"charge_type": "On Net Total", "account_head": "CGST - FFS", "description": "CGST @ 9.0", "rate": 9.00})
            elif si_doc.taxes_and_charges == "Out of State GST - FFS":
                si_doc.append('taxes', {"charge_type": "On Net Total", "account_head": "IGST - FFS", "description": "IGST @ 18.0", "rate": 18.00})
            else: pass
        else:pass
    else: pass
    my_pointer += 1
    calculate_taxes_and_totals(si_doc)
    si_doc.save()
    return my_pointer



################# YTPL Code End ###########################

