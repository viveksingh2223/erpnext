// Copyright (c) 2018, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on('Compensatory Leave Request', {
	refresh: function(frm) {
		// ######## CUSTOM YTPL CODE START ############
        cur_frm.fields_dict.employee.get_query = function(doc) {
            return {
                filters: { "employee_type" : 'MORGAN STAFF', "status" : "Active"}
            }
        }
        frm.refresh_field("employee");
    	// ######## CUSTOM YTPL CODE END############
		frm.set_query("leave_type", function() {
			return {
				filters: {
					"is_compensatory": true
				}
			};
		});
	},
	half_day: function(frm) {
		if(frm.doc.half_day == 1){
			frm.set_df_property('half_day_date', 'reqd', true);
		}
		else{
			frm.set_df_property('half_day_date', 'reqd', false);
		}
	}
});
