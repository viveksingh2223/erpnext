// Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
// License: GNU General Public License v3. See license.txt

// print heading code
cur_frm.pformat.print_heading = 'Invoice';

{% include 'erpnext/selling/sales_common.js' %};

cur_frm.add_fetch('customer', 'tax_id', 'tax_id');

frappe.provide("erpnext.accounts");
erpnext.accounts.SalesInvoiceController = erpnext.selling.SellingController.extend({
	setup: function(doc) {
		//this.setup_posting_date_time_check();
		this._super(doc);
	},
	onload: function() {
		var me = this;
		this._super();

        /*************************** Custom YTPL*****************************/
        if(me.frm.doc.billing_type=="Rate Revision") {
            me.frm.set_df_property("billing_period", "reqd", 0);
        }else{
            me.frm.set_df_property("billing_period", "reqd", 1);
        }
        /*************************** Custom YTPL*****************************/

		if(!this.frm.doc.__islocal && !this.frm.doc.customer && this.frm.doc.debit_to) {
			// show debit_to in print format
			this.frm.set_df_property("debit_to", "print_hide", 0);
		}

		erpnext.queries.setup_queries(this.frm, "Warehouse", function() {
			return erpnext.queries.warehouse(me.frm.doc);
		});

		if(this.frm.doc.__islocal && this.frm.doc.is_pos) {
			//Load pos profile data on the invoice if the default value of Is POS is 1

			me.frm.script_manager.trigger("is_pos");
			me.frm.refresh_fields();
		}

	},
	/*************************** Custom YTPL*****************************/
	site_address: function() {
		erpnext.utils.get_address_display(me.frm, "site_address", "site_address_display", false);
	},
	/*************************** Custom YTPL*****************************/
	refresh: function(doc, dt, dn) {
		this._super();
		if(cur_frm.msgbox && cur_frm.msgbox.$wrapper.is(":visible")) {
			// hide new msgbox
			cur_frm.msgbox.hide();
		}

		this.frm.toggle_reqd("due_date", !this.frm.doc.is_return);

		this.show_general_ledger();

		if(doc.update_stock) this.show_stock_ledger();

		if (doc.docstatus == 1 && doc.outstanding_amount!=0
			&& !(cint(doc.is_return) && doc.return_against)) {
			cur_frm.add_custom_button(__('Payment'),
				this.make_payment_entry, __("Make"));
			cur_frm.page.set_inner_btn_group_as_primary(__("Make"));
		}

		if(doc.docstatus==1 && !doc.is_return) {

			var is_delivered_by_supplier = false;

			is_delivered_by_supplier = cur_frm.doc.items.some(function(item){
				return item.is_delivered_by_supplier ? true : false;
			})

			if(doc.outstanding_amount >= 0 || Math.abs(flt(doc.outstanding_amount)) < flt(doc.grand_total)) {
				cur_frm.add_custom_button(__('Return / Credit Note'),
					this.make_sales_return, __("Make"));
				cur_frm.page.set_inner_btn_group_as_primary(__("Make"));
			}

			if(cint(doc.update_stock)!=1) {
				// show Make Delivery Note button only if Sales Invoice is not created from Delivery Note
				var from_delivery_note = false;
				from_delivery_note = cur_frm.doc.items
					.some(function(item) {
						return item.delivery_note ? true : false;
					});

				if(!from_delivery_note && !is_delivered_by_supplier) {
					cur_frm.add_custom_button(__('Delivery'),
						cur_frm.cscript['Make Delivery Note'], __("Make"));
				}
			}

			if(doc.outstanding_amount>0 && !cint(doc.is_return)) {
				cur_frm.add_custom_button(__('Payment Request'),
					this.make_payment_request, __("Make"));
			}

			if(!doc.auto_repeat) {
				cur_frm.add_custom_button(__('Subscription'), function() {
					erpnext.utils.make_subscription(doc.doctype, doc.name)
				}, __("Make"))
			}
		}

		// Show buttons only when pos view is active
		if (cint(doc.docstatus==0) && cur_frm.page.current_view_name!=="pos" && !doc.is_return) {
			this.frm.cscript.sales_order_btn();
			this.frm.cscript.delivery_note_btn();
			this.frm.cscript.quotation_btn();
		}

		this.set_default_print_format();
		var me = this;
		if (doc.docstatus == 1 && !doc.inter_company_invoice_reference) {
			frappe.model.with_doc("Customer", me.frm.doc.customer, function() {
				var customer = frappe.model.get_doc("Customer", me.frm.doc.customer);
				var internal = customer.is_internal_customer;
				var disabled = customer.disabled;
				if (internal == 1 && disabled == 0) {
					me.frm.add_custom_button("Inter Company Invoice", function() {
						me.make_inter_company_invoice();
					}, __("Make"));
				}
			});
		}

        //################ YTPL CUSTOM CODE START#########################
        var filters =  {
            'bu_name': 'None',
            'bu_type': 'None'
        }
        if(cur_frm.doc.customer){
            frappe.model.with_doc("Customer", cur_frm.doc.customer, function() {
                var customer = frappe.model.get_doc("Customer", cur_frm.doc.customer);
                if(customer != undefined && customer.customer_code != undefined && customer != "" && customer.disabled == 0){
                    filters =  {
                        'business_unit': customer.customer_code,
                        'bu_type': 'Site'
                    }
                }
                cur_frm.set_query("site", function() {
                    return {filters : filters}
                });
            });
        }
        if(cur_frm.doc.docstatus == 1){
           if(!cur_frm.is_new()) {
                cur_frm.add_custom_button(__("Create XML"), function(){
                    frappe.call({
                        method:"erpnext.accounts.doctype.sales_invoice.sales_invoice.create_xml_file_for_tally",
                        args: {"sales_invoice_list" : [cur_frm.doc.name]},
                        callback:function(r){
                            if(r.message){
                                var a = document.createElement("a");
                                a.href = r.message[0];
                                a.setAttribute("download", r.message[1]);
                                a.click();
                            }
                        }
                    });
                }).toggleClass('btn-primary'); 
            }
        }
        if(me.frm.doc.billing_type=="Rate Revision") {
            me.frm.set_df_property("arrears_bill_from", "reqd", 1);
            me.frm.set_df_property("billing_period", "reqd", 0);
            me.frm.set_df_property("bonus_bill_from", "reqd", 0);
            me.frm.set_df_property("bonus_bill_till_date", "reqd", 0);
            me.frm.set_df_property("agency_charges_percentage", "reqd", 0);
        }
        else if(me.frm.doc.billing_type=="Bonus") {
            me.frm.set_df_property("bonus_bill_from", "reqd", 1);
            me.frm.set_df_property("bonus_bill_till_date", "reqd", 1);
            me.frm.set_df_property("billing_period", "reqd", 0);
            me.frm.set_df_property("agency_charges_percentage", "reqd", 1);
        }else{
            me.frm.set_df_property("bonus_bill_from", "reqd", 0);
            me.frm.set_df_property("bonus_bill_till_date", "reqd", 0);
            me.frm.set_df_property("agency_charges_percentage", "reqd", 0);
            me.frm.set_df_property("billing_period", "reqd", 1);
        }
        me.frm.refresh_field('arrears_bill_from')
        me.frm.refresh_field('billing_period')
        me.frm.refresh_field('bonus_bill_from')
        me.frm.refresh_field('bonus_bill_till_date')
        me.frm.refresh_field('agency_charges_percentage')
        //################ YTPL CUSTOM CODE END#########################
	},

	on_submit: function(doc, dt, dn) {
		var me = this;

		if (frappe.get_route()[0] != 'Form') {
			return
		}

		$.each(doc["items"], function(i, row) {
			if(row.delivery_note) frappe.model.clear_doc("Delivery Note", row.delivery_note)
		})
	},

	set_default_print_format: function() {
		// set default print format to POS type
		if(cur_frm.doc.is_pos) {
			if(cur_frm.pos_print_format) {
				cur_frm.meta._default_print_format = cur_frm.meta.default_print_format;
				cur_frm.meta.default_print_format = cur_frm.pos_print_format;
			}
		} else {
			if(cur_frm.meta._default_print_format) {
				cur_frm.meta.default_print_format = cur_frm.meta._default_print_format;
				cur_frm.meta._default_print_format = null;
			}
		}
	},
	/*************************** Custom YTPL*****************************/
	billing_type: function() {
        frappe.model.clear_table(me.frm.doc, "items");
        me.frm.set_value('billing_period', "");
        me.frm.set_value('si_from_date', "");
        me.frm.set_value('si_to_date', "");
        me.frm.set_value('customer', "");
        me.frm.set_value('site', "");
        me.frm.set_value('site_address_on_bill', 0);
        me.frm.set_value('standard_bill', "");
        me.frm.set_value('arrears_bill_from', "");

        if(me.frm.doc.billing_type=="Rate Revision") {
            me.frm.set_df_property("arrears_bill_from", "reqd", 1);
            me.frm.set_df_property("billing_period", "reqd", 0);
            me.frm.set_df_property("bonus_bill_from", "reqd", 0);
            me.frm.set_df_property("bonus_bill_till_date", "reqd", 0);
            me.frm.set_df_property("agency_charges_percentage", "reqd", 0);
        }
        else if(me.frm.doc.billing_type=="Bonus") {
            me.frm.set_df_property("bonus_bill_from", "reqd", 1);
            me.frm.set_df_property("bonus_bill_till_date", "reqd", 1);
            me.frm.set_df_property("billing_period", "reqd", 0);
            me.frm.set_df_property("agency_charges_percentage", "reqd", 1);
        }else{
            me.frm.set_df_property("bonus_bill_from", "reqd", 0);
            me.frm.set_df_property("bonus_bill_till_date", "reqd", 0);
            me.frm.set_df_property("agency_charges_percentage", "reqd", 0);    
            me.frm.set_df_property("billing_period", "reqd", 1);
        }
        me.frm.refresh_field('arrears_bill_from')
        me.frm.refresh_field('billing_period')
        me.frm.refresh_field('bonus_bill_from')
        me.frm.refresh_field('bonus_bill_till_date')
        me.frm.refresh_field('agency_charges_percentage')
	},
	billing_period: function() {
	    frappe.model.clear_table(me.frm.doc, "items");
        me.frm.set_value('customer', "");
        me.frm.set_value('standard_bill', "");
        me.frm.set_value('site', "");
        me.frm.set_value('site_address_on_bill', 0);
        if(cur_frm.doc.billing_period != undefined && cur_frm.doc.billing_period != '' && cur_frm.doc.si_from_date != ''){
            frappe.call({
                method: "erpnext.accounts.doctype.sales_invoice.sales_invoice.get_posting_date",
                args: {'start_date': cur_frm.doc.si_from_date},
                callback:function(r){
                    me.frm.set_value('posting_date', r.message);
                    me.frm.set_value('due_date', frappe.datetime.add_days(r.message, 15))
                }
            })
        }
	},
    agency_charges_percentage: function(){
        frappe.model.clear_table(me.frm.doc, "items");
        me.frm.refresh_field('items')
    },
	/*************************** Custom YTPL*****************************/
	sales_order_btn: function() {
		var me = this;
		this.$sales_order_btn = this.frm.add_custom_button(__('Sales Order'),
			function() {
				erpnext.utils.map_current_doc({
					method: "erpnext.selling.doctype.sales_order.sales_order.make_sales_invoice",
					source_doctype: "Sales Order",
					target: me.frm,
					setters: {
						customer: me.frm.doc.customer || undefined,
					},
					get_query_filters: {
						docstatus: 1,
						status: ["!=", "Closed"],
						per_billed: ["<", 99.99],
						company: me.frm.doc.company
					}
				})
			}, __("Get items from"));
	},
	quotation_btn: function() {
		var me = this;
		this.$quotation_btn = this.frm.add_custom_button(__('Quotation'),
			function() {
				erpnext.utils.map_current_doc({
					method: "erpnext.selling.doctype.quotation.quotation.make_sales_invoice",
					source_doctype: "Quotation",
					target: me.frm,
					setters: {
						customer: me.frm.doc.customer || undefined,
					},
					get_query_filters: {
						docstatus: 1,
						status: ["!=", "Lost"],
						company: me.frm.doc.company
					}
				})
			}, __("Get items from"));
	},
	/*************************** Custom YTPL*****************************/
    get_contract_btn:function(frm){
	    //if(cur_frm.doc.billing_period != undefined && cur_frm.doc.customer != undefined && cur_frm.doc.billing_period != "" && cur_frm.doc.customer != ""){
	    if(me.frm.doc.billing_period && me.frm.doc.customer){
	        frappe.model.with_doc("Salary Payroll Period", cur_frm.doc.billing_period, function() {
                var billing_period_doc = frappe.model.get_doc("Salary Payroll Period", cur_frm.doc.billing_period);
                var bill_type_flag=0;
                if((me.frm.doc.billing_type).toUpperCase() == 'STANDARD'){
				    bill_type_flag=1;
					map_doc({
						//method: "erpnext.crm.doctype.contract.contract.make_sales_invoice",
						source_doctype: "Site Contract",
						target: me.frm,
						me:me,
						date_field:"start_date",
						setters: {
							//customer: me.frm.doc.customer || undefined,
						},
						get_query_filters: {
							docstatus: 1,
							party_name: ["=", me.frm.doc.customer],
							start_date: ['<=', billing_period_doc.end_date],
							end_date: ['>=', billing_period_doc.start_date],
							is_standard: ['=', bill_type_flag], // filter stander contract based on bill type
							company: me.frm.doc.company
						}
					})
				}
            })
	    }else{
	        frappe.msgprint(__("Select Billing Period and Customer to Load Contracts"))
	    }
	},
    get_bonus_btn:function(frm){
        //if(cur_frm.doc.billing_period != undefined && cur_frm.doc.customer != undefined && cur_frm.doc.billing_period != "" && cur_frm.doc.customer != ""){
        if(me.frm.doc.customer != undefined && cur_frm.doc.bonus_bill_from != undefined && cur_frm.doc.bonus_bill_till_date != undefined){
                if((me.frm.doc.billing_type).toUpperCase() == 'BONUS'){
                    map_bonus_doc({
                        //method: "erpnext.crm.doctype.contract.contract.make_sales_invoice",
                        source_doctype: "Site Contract",
                        target: me.frm,
                        me:me,
                        date_field:"start_date",
                        setters: {
                            //customer: me.frm.doc.customer || undefined,
                        },
                        get_query_filters: {
                            docstatus: 1,
                            party_name: ["=", me.frm.doc.customer],
                            start_date: ['<=', me.frm.doc.bonus_bill_till_date],
                            end_date: ['>=', me.frm.doc.bonus_bill_from],
                        }
                    })
                }
        }else{
            frappe.msgprint(__("Select Customer, Bonus From Date, Bonus Till Date to Load Contracts"))
        }
    },
	get_attendance_btn: function(frm) {
        if(me.frm.doc.billing_type=="Attendance" && me.frm.doc.billing_period && me.frm.doc.customer){
	        frappe.model.with_doc("Salary Payroll Period", cur_frm.doc.billing_period, function() {
                var billing_period_doc = frappe.model.get_doc("Salary Payroll Period", cur_frm.doc.billing_period);
                var draft_bill_list= []
                frappe.call({
                    method: 'get_draft_bill',
                    doc: cur_frm.doc,
                    async: false,
                    callback: function(r){
                        draft_bill_list= r.message
                    }
                })
                map_att_doc({
                    source_doctype: "People Attendance",
                    target: me.frm,
                    me: me,
                    date_field:"start_date",
                    setters: {
                        //site: me.frm.doc.site || undefined,
                    },
                    get_query_filters: {
                        docstatus: 1,
                        attendance_period: ["=", me.frm.doc.billing_period],
                        customer: ["=", me.frm.doc.customer],
                        status: ["in", ["To Bill", 'Difference Attendance']],
                        company: me.frm.doc.company,
                        name: ['not in', draft_bill_list]
                    }
                })
            })
	    }else{
	        frappe.msgprint(__("Select Billing Type Attendance, Period and Customer to Load Bill"))
	    }
	},
    get_supplementary_btn: function() {
        var me = this;
        if(me.frm.doc.billing_period && me.frm.doc.customer && me.frm.doc.standard_bill){
            frappe.model.clear_table(me.frm.doc, "items");
			frappe.call({
				method: "get_data_to_make_supplementary_bill",
				doc: cur_frm.doc,
                async: false,
				callback: function(r) {
				if(r.message) {
				cur_frm.save()
				cur_frm.refresh()
				}
				}
			})
		}
    },
	get_rate_revision_btn: function(frm) {
        var me = this;
        if(me.frm.doc.arrears_bill_from && me.frm.doc.customer){
             map_arrears_doc({
                        //method: "erpnext.crm.doctype.contract.contract.make_sales_invoice",
                        source_doctype: "Site Contract",
                        target: me.frm,
                        me:me,
                        date_field:"start_date",
                        setters: {
                            //customer: me.frm.doc.customer || undefined,
                        },
                        get_query_filters: {
                            docstatus: 1,
                            status: 'Active',
                            party_name: ["=", me.frm.doc.customer],
                            start_date: ['<=', me.frm.doc.arrears_bill_from],
                        }
             })
            /*frappe.model.clear_table(me.frm.doc, "items");
            me.frm.refresh_field("items");
            frappe.call({
                method: "get_data_to_make_arrears_bill",
                doc: cur_frm.doc,
                async: false,
                callback: function(r) {
                    if(r.message) {
                        cur_frm.save()
                        cur_frm.refresh()
                    }
                }
            });*/
        }
    },
    /*************************** Custom YTPL*****************************/
	delivery_note_btn: function() {
		var me = this;
		this.$delivery_note_btn = this.frm.add_custom_button(__('Delivery Note'),
			function() {
				erpnext.utils.map_current_doc({
					method: "erpnext.stock.doctype.delivery_note.delivery_note.make_sales_invoice",
					source_doctype: "Delivery Note",
					target: me.frm,
					date_field: "posting_date",
					setters: {
						customer: me.frm.doc.customer || undefined
					},
					get_query: function() {
						var filters = {
							docstatus: 1,
							company: me.frm.doc.company
						};
						if(me.frm.doc.customer) filters["customer"] = me.frm.doc.customer;
						return {
							query: "erpnext.controllers.queries.get_delivery_notes_to_be_billed",
							filters: filters
						};
					}
				});
			}, __("Get items from"));
	},

	tc_name: function() {
		this.get_terms();
	},
	customer: function() {
		var me = this;
		if(this.frm.updating_party_details) return;
		erpnext.utils.get_party_details(this.frm,
			"erpnext.accounts.party.get_party_details", {
				posting_date: this.frm.doc.posting_date,
				party: this.frm.doc.customer,
				party_type: "Customer",
				account: this.frm.doc.debit_to,
				price_list: this.frm.doc.selling_price_list,
			}, function() {
				me.apply_pricing_rule();
			});
		if(this.frm.doc.customer) {
			frappe.call({
				"method": "erpnext.accounts.doctype.sales_invoice.sales_invoice.get_loyalty_programs",
				"args": {
					"customer": this.frm.doc.customer
				},
				callback: function(r) {
					if(r.message && r.message.length) {
						select_loyalty_program(me.frm, r.message);
					}
				}
			});
		}
		/*************************** Custom YTPL*****************************/
		if(this.frm.doc.billing_period && this.frm.doc.customer){
		    me.frm.set_value('attendance', "");
            me.frm.set_value('standard_bill', "");
            cur_frm.fields_dict['attendance'].get_query = function(doc) {
                return {
                    filters: {
                        "attendance_period": cur_frm.doc.billing_period,
                        "docstatus": 1,
                        "customer": cur_frm.doc.customer
                    }
                }
            }
            cur_frm.fields_dict['standard_bill'].get_query = function(doc) {
                return {
                    filters: {
                        "billing_period": cur_frm.doc.billing_period,
                        "docstatus": 1,
                        "customer": cur_frm.doc.customer
                    }
                }
            }
		}
        if(cur_frm.doc.billing_period != undefined && (cur_frm.doc.si_from_date != '' && cur_frm.doc.si_from_date != undefined)){
            if (cur_frm.doc.posting_date == undefined){
                frappe.call({
                    method: "erpnext.accounts.doctype.sales_invoice.sales_invoice.get_posting_date",
                    args: {'start_date': cur_frm.doc.si_from_date},
                    callback:function(r){
                        cur_frm.set_value('posting_date', r.message);
                        cur_frm.set_value('due_date', frappe.datetime.add_days(r.message, 15))
                    }
                })
            }
            else{
                cur_frm.set_value('posting_date', cur_frm.doc.posting_date);
                cur_frm.set_value('due_date', frappe.datetime.add_days(cur_frm.doc.posting_date, 15))
            }
        }
		/*************************** Custom YTPL*****************************/
	},

	make_inter_company_invoice: function() {
		frappe.model.open_mapped_doc({
			method: "erpnext.accounts.doctype.sales_invoice.sales_invoice.make_inter_company_purchase_invoice",
			frm: me.frm
		});
	},

	debit_to: function() {
		var me = this;
		if(this.frm.doc.debit_to) {
			me.frm.call({
				method: "frappe.client.get_value",
				args: {
					doctype: "Account",
					fieldname: "account_currency",
					filters: { name: me.frm.doc.debit_to },
				},
				callback: function(r, rt) {
					if(r.message) {
						me.frm.set_value("party_account_currency", r.message.account_currency);
						me.set_dynamic_labels();
					}
				}
			});
		}
	},

	allocated_amount: function() {
		this.calculate_total_advance();
		this.frm.refresh_fields();
	},

	write_off_outstanding_amount_automatically: function() {
		if(cint(this.frm.doc.write_off_outstanding_amount_automatically)) {
			frappe.model.round_floats_in(this.frm.doc, ["grand_total", "paid_amount"]);
			// this will make outstanding amount 0
			this.frm.set_value("write_off_amount",
				flt(this.frm.doc.grand_total - this.frm.doc.paid_amount - this.frm.doc.total_advance, precision("write_off_amount"))
			);
			this.frm.toggle_enable("write_off_amount", false);

		} else {
			this.frm.toggle_enable("write_off_amount", true);
		}

		this.calculate_outstanding_amount(false);
		this.frm.refresh_fields();
	},

	write_off_amount: function() {
		this.set_in_company_currency(this.frm.doc, ["write_off_amount"]);
		this.write_off_outstanding_amount_automatically();
	},

	items_add: function(doc, cdt, cdn) {
		var row = frappe.get_doc(cdt, cdn);
		this.frm.script_manager.copy_from_first_row("items", row, ["income_account", "cost_center"]);
	},

	set_dynamic_labels: function() {
		this._super();
		this.hide_fields(this.frm.doc);
	},

	items_on_form_rendered: function() {
		erpnext.setup_serial_no();
	},

	make_sales_return: function() {
		frappe.model.open_mapped_doc({
			method: "erpnext.accounts.doctype.sales_invoice.sales_invoice.make_sales_return",
			frm: cur_frm
		})
	},

	asset: function(frm, cdt, cdn) {
		var row = locals[cdt][cdn];
		if(row.asset) {
			frappe.call({
				method: erpnext.assets.doctype.asset.depreciation.get_disposal_account_and_cost_center,
				args: {
					"company": frm.doc.company
				},
				callback: function(r, rt) {
					frappe.model.set_value(cdt, cdn, "income_account", r.message[0]);
					frappe.model.set_value(cdt, cdn, "cost_center", r.message[1]);
				}
			})
		}
	},

	is_pos: function(frm){
		this.set_pos_data();
	},

	pos_profile: function() {
		this.frm.doc.taxes = []
		this.set_pos_data();
	},

	set_pos_data: function() {
		if(this.frm.doc.is_pos) {
			if(!this.frm.doc.company) {
				this.frm.set_value("is_pos", 0);
				frappe.msgprint(__("Please specify Company to proceed"));
			} else {
				var me = this;
				return this.frm.call({
					doc: me.frm.doc,
					method: "set_missing_values",
					callback: function(r) {
						if(!r.exc) {
							if(r.message && r.message.print_format) {
								me.frm.pos_print_format = r.message.print_format;
							}
							me.frm.script_manager.trigger("update_stock");
							frappe.model.set_default_values(me.frm.doc);
							me.set_dynamic_labels();
							me.calculate_taxes_and_totals();
						}
					}
				});
			}
		}
		else this.frm.trigger("refresh");
	},

	amount: function(){
		this.write_off_outstanding_amount_automatically()
	},

	change_amount: function(){
		if(this.frm.doc.paid_amount > this.frm.doc.grand_total){
			this.calculate_write_off_amount();
		}else {
			this.frm.set_value("change_amount", 0.0);
			this.frm.set_value("base_change_amount", 0.0);
		}

		this.frm.refresh_fields();
	},

	loyalty_amount: function(){
		this.calculate_outstanding_amount();
		this.frm.refresh_field("outstanding_amount");
		this.frm.refresh_field("paid_amount");
		this.frm.refresh_field("base_paid_amount");
	}
});

// for backward compatibility: combine new and previous states
$.extend(cur_frm.cscript, new erpnext.accounts.SalesInvoiceController({frm: cur_frm}));

/*************************** Custom YTPL*****************************/
var map_doc = function(opts) {
	if(opts.get_query_filters) {
		opts.get_query = function() {
			return {filters: opts.get_query_filters};
		}
	}
	var _map = function() {
        frappe.model.clear_table(cur_frm.doc, "items");
        cur_frm.refresh_field("items");
		if(cur_frm.doc.customer && cur_frm.doc.billing_period) {
			// Get linked Wage Rule to Rate
			var billing_period_doc = frappe.model.get_doc("Salary Payroll Period", cur_frm.doc.billing_period);
			frappe.call({
				method: "get_items_for_standard_billing",
				doc: cur_frm.doc,
                async: false,
				args:{	"contract_list": opts.source_name, 
						"period_from_date": billing_period_doc.start_date,
						"period_to_date": billing_period_doc.end_date
					},
				callback: function(r) {
					if(r.message) {
						cur_frm.save()
	                    cur_frm.refresh()
					}
				}
           	});
        }else{
            frappe.msgprint(__("Select Billing Period and Contract before load contracts"));
        }
    }
	if(opts.source_doctype) {

		var d = new frappe.ui.form.MultiSelectDialog({
			doctype: opts.source_doctype,
			target: opts.target,
			date_field: opts.date_field || undefined,
			setters: opts.setters,
			get_query: opts.get_query,
			action: function(selections, args) {
				let values = selections;
				if(values.length === 0){
					frappe.msgprint(__("Please select {0}", [opts.source_doctype]))
					return;
				}
				opts.source_name = values;
				opts.setters = args;
				d.dialog.hide();
				_map();
			},
		});
	} else if(opts.source_name) {
		opts.source_name = [opts.source_name];
		_map();
	}
}
var map_att_doc = function(opts) {
    /*************** Start Load Items From multiple Attendance *******************/
    var me = this;
	if(opts.get_query_filters) {
		opts.get_query = function() {
			return {filters: opts.get_query_filters};
		}
	}
	var _map = function() {
	    // If single site set address
	    if(opts.source_name.length==1){
            frappe.model.with_doc("People Attendance", opts.source_name[0], function() {
                var doc = frappe.model.get_doc("People Attendance", opts.source_name[0]);
                cur_frm.set_value('site', doc.site);
            });
	    }else{
	        cur_frm.set_value('site_address_on_bill', 0);
	        cur_frm.set_value('site', "");
	    }
	    /************ Load Items for All Attendance ***********/
        frappe.call({
            method: "get_details_to_create_items",
			doc: cur_frm.doc,
            args: {
                "att_list": opts.source_name,
                "billing_period": cur_frm.doc.billing_period
            },
            async: false,
            callback: function(r) {
                if(r.message) {
					cur_frm.save()
					cur_frm.refresh()
                }else{
                    frappe.msgprint(__("Select Billing Period and Contract before load contracts"));
                }
            }
        });
    }
	if(opts.source_doctype) {

		var d = new frappe.ui.form.MultiSelectDialog({
			doctype: opts.source_doctype,
			target: opts.target,
			date_field: opts.date_field || undefined,
			setters: opts.setters,
			get_query: opts.get_query,
			action: function(selections, args) {
				let values = selections;
				if(values.length === 0){
					frappe.msgprint(__("Please select {0}", [opts.source_doctype]))
					return;
				}
				opts.source_name = values;
				opts.setters = args;
				d.dialog.hide();
				_map();
			},
		});
	} else if(opts.source_name) {
		opts.source_name = [opts.source_name];
		_map();
	}
	/*************** End Load Items From multiple Attendance *******************/
}
/******************* BONUS CALCULATION******************************************/
var map_bonus_doc = function(opts) {
    if(opts.get_query_filters) {
        opts.get_query = function() {
            return {filters: opts.get_query_filters};
        }
    }
    var _map = function() {
        frappe.model.clear_table(cur_frm.doc, "items");
        cur_frm.refresh_field("items");
        if(cur_frm.doc.customer && cur_frm.doc.bonus_bill_from && cur_frm.doc.bonus_bill_till_date){
            // Get linked Wage Rule to Rate
            frappe.call({
                method: "get_items_for_bonus_billing",
                doc: cur_frm.doc,
                args: {'contract_list': opts.source_name},
                async: false,
                callback: function(r) {
                    if(r.message) {
                        cur_frm.save()
                        cur_frm.refresh()
                    }
                }
            });
        }else{
            frappe.msgprint(__("Select Bonus Bill From Date, Bonus Bill Till Date before load contracts"));
        }
    }
    if(opts.source_doctype) {

        var d = new frappe.ui.form.MultiSelectDialog({
            doctype: opts.source_doctype,
            target: opts.target,
            date_field: opts.date_field || undefined,
            setters: opts.setters,
            get_query: opts.get_query,
            action: function(selections, args) {
                let values = selections;
                if(values.length === 0){
                    frappe.msgprint(__("Please select {0}", [opts.source_doctype]))
                    return;
                }
                opts.source_name = values;
                opts.setters = args;
                d.dialog.hide();
                _map();
            },
        });
    } else if(opts.source_name) {
        opts.source_name = [opts.source_name];
        _map();
    }
}
/******************* BONUS CALCULATION END**************************************/


var map_arrears_doc= function(opts) {
    if(opts.get_query_filters) {
    opts.get_query = function() {
        return {filters: opts.get_query_filters};
        }
    }
    var _map = function() {
        frappe.model.clear_table(cur_frm.doc, "items");
        cur_frm.refresh_field("items");
        if(cur_frm.doc.customer && cur_frm.doc.arrears_bill_from) {
            frappe.call({
                method: "get_data_to_make_arrears_bill",
                doc: cur_frm.doc,
                args: {'contract_list': opts.source_name},
                async: false,
                callback: function(r) {
                    if(r.message) {
                        cur_frm.save()
                        cur_frm.refresh()
                    }
                }
            });
                
        }else{
            frappe.msgprint(__("Select Arrears Bill From Date And Customer before load contracts"));
        }
    }
    if(opts.source_doctype) {
        var d = new frappe.ui.form.MultiSelectDialog({
            doctype: opts.source_doctype,
            target: opts.target,
            //date_field: opts.date_field || undefined,
            setters: opts.setters,
            get_query: opts.get_query,
            action: function(selections, args) {
                let values = selections;
                if(values.length === 0){
                    frappe.msgprint(__("Please select {0}", [opts.source_doctype]))
                    return;
                }
                opts.source_name = values;
                opts.setters = args;
                d.dialog.hide();
                _map();
            },
        });
    } else if(opts.source_name) {
    opts.source_name = [opts.source_name];
        _map();
    }
}

/*************************** Custom YTPL*****************************/

// Hide Fields
// ------------
cur_frm.cscript.hide_fields = function(doc) {
	var parent_fields = ['project', 'due_date', 'is_opening', 'source', 'total_advance', 'get_advances',
		'advances', 'advances', 'from_date', 'to_date'];

	if(cint(doc.is_pos) == 1) {
		hide_field(parent_fields);
	} else {
		for (var i in parent_fields) {
			var docfield = frappe.meta.docfield_map[doc.doctype][parent_fields[i]];
			if(!docfield.hidden) unhide_field(parent_fields[i]);
		}
	}

	// India related fields
	if (frappe.boot.sysdefaults.country == 'India') unhide_field(['c_form_applicable', 'c_form_no']);
	else hide_field(['c_form_applicable', 'c_form_no']);

	this.frm.toggle_enable("write_off_amount", !!!cint(doc.write_off_outstanding_amount_automatically));

	cur_frm.refresh_fields();
}

cur_frm.cscript.update_stock = function(doc, dt, dn) {
	cur_frm.cscript.hide_fields(doc, dt, dn);
	this.frm.fields_dict.items.grid.toggle_reqd("item_code", doc.update_stock? true: false)
}

cur_frm.cscript['Make Delivery Note'] = function() {
	frappe.model.open_mapped_doc({
		method: "erpnext.accounts.doctype.sales_invoice.sales_invoice.make_delivery_note",
		frm: cur_frm
	})
}

cur_frm.fields_dict.cash_bank_account.get_query = function(doc) {
	return {
		filters: [
			["Account", "account_type", "in", ["Cash", "Bank"]],
			["Account", "root_type", "=", "Asset"],
			["Account", "is_group", "=",0],
			["Account", "company", "=", doc.company]
		]
	}
}

cur_frm.fields_dict.write_off_account.get_query = function(doc) {
	return{
		filters:{
			'report_type': 'Profit and Loss',
			'is_group': 0,
			'company': doc.company
		}
	}
}

// Write off cost center
//-----------------------
cur_frm.fields_dict.write_off_cost_center.get_query = function(doc) {
	return{
		filters:{
			'is_group': 0,
			'company': doc.company
		}
	}
}

//project name
//--------------------------
cur_frm.fields_dict['project'].get_query = function(doc, cdt, cdn) {
	return{
		query: "erpnext.controllers.queries.get_project_name",
		filters: {'customer': doc.customer}
	}
}

// Income Account in Details Table
// --------------------------------
cur_frm.set_query("income_account", "items", function(doc) {
	return{
		query: "erpnext.controllers.queries.get_income_account",
		filters: {'company': doc.company}
	}
});


// Cost Center in Details Table
// -----------------------------
cur_frm.fields_dict["items"].grid.get_field("cost_center").get_query = function(doc) {
	return {
		filters: {
			'company': doc.company,
			"is_group": 0
		}
	}
}

cur_frm.cscript.income_account = function(doc, cdt, cdn) {
	erpnext.utils.copy_value_in_all_row(doc, cdt, cdn, "items", "income_account");
}

cur_frm.cscript.expense_account = function(doc, cdt, cdn) {
	erpnext.utils.copy_value_in_all_row(doc, cdt, cdn, "items", "expense_account");
}

cur_frm.cscript.cost_center = function(doc, cdt, cdn) {
	erpnext.utils.copy_value_in_all_row(doc, cdt, cdn, "items", "cost_center");
}

cur_frm.set_query("debit_to", function(doc) {
	// filter on Account
	if (doc.customer) {
		return {
			filters: {
				'account_type': 'Receivable',
				'is_group': 0,
				'company': doc.company
			}
		}
	} else {
		return {
			filters: {
				'report_type': 'Balance Sheet',
				'is_group': 0,
				'company': doc.company
			}
		}
	}
});

cur_frm.set_query("asset", "items", function(doc, cdt, cdn) {
	var d = locals[cdt][cdn];
	return {
		filters: [
			["Asset", "item_code", "=", d.item_code],
			["Asset", "docstatus", "=", 1],
			["Asset", "status", "in", ["Submitted", "Partially Depreciated", "Fully Depreciated"]],
			["Asset", "company", "=", doc.company]
		]
	}
});

frappe.ui.form.on('Sales Invoice', {
	setup: function(frm){
		
		frm.custom_make_buttons = {
			'Delivery Note': 'Delivery',
			'Sales Invoice': 'Sales Return',
			'Payment Request': 'Payment Request',
			'Payment Entry': 'Payment'
		},
		frm.fields_dict["timesheets"].grid.get_field("time_sheet").get_query = function(doc, cdt, cdn){
			return{
				query: "erpnext.projects.doctype.timesheet.timesheet.get_timesheet",
				filters: {'project': doc.project}
			}
		}

		// expense account
		frm.fields_dict['items'].grid.get_field('expense_account').get_query = function(doc) {
			if (erpnext.is_perpetual_inventory_enabled(doc.company)) {
				return {
					filters: {
						'report_type': 'Profit and Loss',
						'company': doc.company,
						"is_group": 0
					}
				}
			}
		}

		frm.fields_dict['items'].grid.get_field('deferred_revenue_account').get_query = function(doc) {
			return {
				filters: {
					'root_type': 'Liability',
					'company': doc.company,
					"is_group": 0
				}
			}
		}

		frm.set_query('company_address', function(doc) {
			if(!doc.company) {
				frappe.throw(_('Please set Company'));
			}

			return {
				query: 'frappe.contacts.doctype.address.address.address_query',
				filters: {
					link_doctype: 'Company',
					link_name: doc.company
				}
			};
		});

		frm.set_query('pos_profile', function(doc) {
			if(!doc.company) {
				frappe.throw(_('Please set Company'));
			}

			return {
				query: 'erpnext.accounts.doctype.pos_profile.pos_profile.pos_profile_query',
				filters: {
					company: doc.company
				}
			};
		});

		// set get_query for loyalty redemption account
		frm.fields_dict["loyalty_redemption_account"].get_query = function() {
			return {
				filters:{
					"company": frm.doc.company
				}
			}
		};

		// set get_query for loyalty redemption cost center
		frm.fields_dict["loyalty_redemption_cost_center"].get_query = function() {
			return {
				filters:{
					"company": frm.doc.company
				}
			}
		};
	},
	//When multiple companies are set up. in case company name is changed set default company address
	company:function(frm){
		if (frm.doc.company)
		{
			frappe.call({
				method:"frappe.contacts.doctype.address.address.get_default_address",
				args:{ doctype:'Company',name:frm.doc.company},
				callback: function(r){
					if (r.message){
						frm.set_value("company_address",r.message)
					}
					else {
						frm.set_value("company_address","")
					}
				}
			})
		}
	},
	project: function(frm){
		frm.call({
			method: "add_timesheet_data",
			doc: frm.doc,
			callback: function(r, rt) {
				refresh_field(['timesheets'])
			}
		})
	},
    /*************************** Custom YTPL*****************************/
    site: function(frm) {
        if(frm.doc.site) {
            frm.call({
                method: "set_site_address",
                doc: frm.doc,
                callback: function(r) {
                    if(r.message){
                    }else{
                        cur_frm.set_value('site_address', "");
                        cur_frm.set_value('site_address_display', "");
                        cur_frm.set_value('site_billing_address_gstin', "");
                    }
                }
            })
		}
	},
    /*************************** Custom YTPL*****************************/
	onload: function(frm) {
		frm.redemption_conversion_factor = null;
	},

	redeem_loyalty_points: function(frm) {
		frm.events.get_loyalty_details(frm);
	},

	loyalty_points: function(frm) {
		if (frm.redemption_conversion_factor) {
			frm.events.set_loyalty_points(frm);
		} else {
			frappe.call({
				method: "erpnext.accounts.doctype.loyalty_program.loyalty_program.get_redeemption_factor",
				args: {
					"loyalty_program": frm.doc.loyalty_program
				},
				callback: function(r) {
					if (r) {
						frm.redemption_conversion_factor = r.message;
						frm.events.set_loyalty_points(frm);
					}
				}
			});
		}
	},

	get_loyalty_details: function(frm) {
		if (frm.doc.customer && frm.doc.redeem_loyalty_points) {
			frappe.call({
				method: "erpnext.accounts.doctype.loyalty_program.loyalty_program.get_loyalty_program_details",
				args: {
					"customer": frm.doc.customer,
					"loyalty_program": frm.doc.loyalty_program,
					"expiry_date": frm.doc.posting_date,
					"company": frm.doc.company
				},
				callback: function(r) {
					if (r) {
						frm.set_value("loyalty_redemption_account", r.message.expense_account);
						frm.set_value("loyalty_redemption_cost_center", r.message.cost_center);
						frm.redemption_conversion_factor = r.message.conversion_factor;
					}
				}
			});
		}
	},

	set_loyalty_points: function(frm) {
		if (frm.redemption_conversion_factor) {
			let loyalty_amount = flt(frm.redemption_conversion_factor*flt(frm.doc.loyalty_points), precision("loyalty_amount"));
			var remaining_amount = flt(frm.doc.grand_total) - flt(frm.doc.total_advance) - flt(frm.doc.write_off_amount);
			if (frm.doc.grand_total && (remaining_amount < loyalty_amount)) {
				let redeemable_points = parseInt(remaining_amount/frm.redemption_conversion_factor);
				frappe.throw(__("You can only redeem max {0} points in this order.",[redeemable_points]));
			}
			frm.set_value("loyalty_amount", loyalty_amount);
		}
	}

})

frappe.ui.form.on('Sales Invoice Timesheet', {
	time_sheet: function(frm, cdt, cdn){
		var d = locals[cdt][cdn];
		if(d.time_sheet) {
			frappe.call({
				method: "erpnext.projects.doctype.timesheet.timesheet.get_timesheet_data",
				args: {
					'name': d.time_sheet,
					'project': frm.doc.project || null
				},
				callback: function(r, rt) {
					if(r.message){
						data = r.message;
						frappe.model.set_value(cdt, cdn, "billing_hours", data.billing_hours);
						frappe.model.set_value(cdt, cdn, "billing_amount", data.billing_amount);
						frappe.model.set_value(cdt, cdn, "timesheet_detail", data.timesheet_detail);
						calculate_total_billing_amount(frm)
					}
				}
			})
		}
	}
})

var calculate_total_billing_amount =  function(frm) {
	var doc = frm.doc;

	doc.total_billing_amount = 0.0
	if(doc.timesheets) {
		$.each(doc.timesheets, function(index, data){
			doc.total_billing_amount += data.billing_amount
		})
	}

	refresh_field('total_billing_amount')
}

var select_loyalty_program = function(frm, loyalty_programs) {
	var dialog = new frappe.ui.Dialog({
		title: __("Select Loyalty Program"),
		fields: [
			{
				"label": __("Loyalty Program"),
				"fieldname": "loyalty_program",
				"fieldtype": "Select",
				"options": loyalty_programs,
				"default": loyalty_programs[0]
			}
		]
	});

	dialog.set_primary_action(__("Set"), function() {
		dialog.hide();
		return frappe.call({
			method: "frappe.client.set_value",
			args: {
				doctype: "Customer",
				name: frm.doc.customer,
				fieldname: "loyalty_program",
				value: dialog.get_value("loyalty_program"),
			},
			callback: function(r) { }
		});
	});

	dialog.show();
}

