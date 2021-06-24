// Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
// License: GNU General Public License v3. See license.txt

frappe.views.calendar["Leave Application"] = {
	field_map: {
		"start": "from_date",
		"end": "to_date",
		"id": "name",
		"title": "title",
		"docstatus": 1
	},
	options: {
		header: {
			left: 'prev,next today',
			center: 'title',
			right: 'month'
		}
	},
    get_css_class: function(data) {
        var me = this;
        if(data.doctype==="Holiday") {
            return "red";
        } else if(data.doctype==="Leave Application") {
            return "orange"
        }
    },
	get_events_method: "erpnext.hr.doctype.leave_application.leave_application.get_events"
}
