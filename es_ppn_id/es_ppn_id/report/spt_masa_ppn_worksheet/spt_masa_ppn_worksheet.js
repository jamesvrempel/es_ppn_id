// Copyright (c) 2026, Enterprise Systems Australia and contributors
frappe.query_reports["SPT Masa PPN Worksheet"] = {
	filters: [
		{
			fieldname: "company", label: __("Company"), fieldtype: "Link",
			options: "Company", reqd: 1, default: frappe.defaults.get_user_default("Company"),
		},
		{
			fieldname: "from_date", label: __("From Date"), fieldtype: "Date", reqd: 1,
			default: frappe.datetime.month_start(frappe.datetime.add_months(frappe.datetime.get_today(), -1)),
		},
		{
			fieldname: "to_date", label: __("To Date"), fieldtype: "Date", reqd: 1,
			default: frappe.datetime.month_end(frappe.datetime.add_months(frappe.datetime.get_today(), -1)),
		},
		{
			fieldname: "basis", label: __("Basis"), fieldtype: "Select",
			options: "Accrual\nCash", default: "Accrual",
		},
		{
			fieldname: "line", label: __("SPT Line"), fieldtype: "Select",
			options: ["", "I.A.1", "I.A.2", "I.A.3", "I.A.4", "I.A.5", "I.B", "II.A", "II.B"].join("\n"),
		},
	],
};
