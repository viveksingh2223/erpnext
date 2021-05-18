// Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
// License: GNU General Public License v3. See license.txt

// render
frappe.listview_settings['Sales Invoice'] = {
	add_fields: ["customer", "customer_name", "base_grand_total", "outstanding_amount", "due_date", "company",
		"currency", "is_return"],
	get_indicator: function(doc) {
		if(cint(doc.is_return)==1) {
			return [__("Return"), "darkgrey", "is_return,=,Yes"];
		} else if(flt(doc.outstanding_amount)==0) {
			return [__("Paid"), "green", "outstanding_amount,=,0"]
		} else if(flt(doc.outstanding_amount) < 0) {
			return [__("Credit Note Issued"), "darkgrey", "outstanding_amount,<,0"]
		}else if (flt(doc.outstanding_amount) > 0 && doc.due_date >= frappe.datetime.get_today()) {
			return [__("Unpaid"), "orange", "outstanding_amount,>,0|due_date,>,Today"]
		} else if (flt(doc.outstanding_amount) > 0 && doc.due_date < frappe.datetime.get_today()) {
			return [__("Overdue"), "red", "outstanding_amount,>,0|due_date,<=,Today"]
		}
	},
	right_column: "grand_total",
    	//####################### CUSTOM SPS ERP CODE START ########################################
	onload: function(listview){
        listview.page.add_menu_item(__("Create Invoice"), function(frm){
                        var dialog = new frappe.ui.Dialog({
                                title: __("Auto Invoicing Process"),
                                fields: [
                                            {
                                                fieldtype: "Link",
                                                fieldname: "billing_period",
                                                label: __("Please select a billing Period"),
                                                options: "Salary Payroll Period",
                                                reqd: 1,
                                            },
                                            {"fieldtype": "Section Break","fieldname": "sectionbreak12"},
                                            {"fieldtype": "Button", "label": __("Create Invoices"), "fieldname": "create_invoices"}

                                        ]
                        });
                        dialog.show();
                        dialog.fields_dict.create_invoices.input.onclick= function(){
                            dialog.hide();
                            var values = dialog.get_values();
                            console.log(values.billing_period)
			    msgprint("Please Wait!! Auto Invoicing In Progress")
                            frappe.call({
                                    method: "erpnext.accounts.doctype.sales_invoice.sales_invoice.auto_invoice_creation",
                                    args:{
                                            "billing_period": values.billing_period,
                                        },
                                    callback: function(r){
                                        frappe.msgprint(r)
                                        //cur_list.refresh()
                                    }
                            });
                        }

                });
        listview.page.add_action_item(__("Create XML File"), function(){        
            var selected_rows= listview.get_checked_items()
            var names= [];
            for(var i=0; i < listview.get_checked_items().length; i++){
                names.push(selected_rows[i].name);
            }
            frappe.call({
                method: "erpnext.accounts.doctype.sales_invoice.sales_invoice.create_xml_file_for_tally",
                args: {
                    "sales_invoice_list": names,
                },
                callback:function(r){
                                if(r.message){
                                    var a = document.createElement("a");
                                    a.href = r.message[0];
                                    a.setAttribute("download", r.message[1]);
                                    a.click();
                                }
                }
            });
        });

    }
    //####################### CUSTOM SPS ERP CODe END ########################################
};
