# Copyright (c) 2026, Enterprise Systems Australia and contributors
"""
SPT Masa PPN Worksheet.

Every delivery event behind a line of the return, so a figure can be
traced back to the invoice or journal that produced it. This is what you
need when DJP asks how a number on the 1111 was arrived at, and it is
also the fastest way to find a sale coded to the wrong treatment.
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate

from es_ppn_id.utils.spt import BASIS_ACCRUAL, SALES, collect_events


def execute(filters=None):
	filters = frappe._dict(filters or {})

	if not filters.company:
		frappe.throw(_("Select a company."))
	if not (filters.from_date and filters.to_date):
		frappe.throw(_("Select a date range."))

	basis = filters.basis or BASIS_ACCRUAL
	events = collect_events(
		filters.company, getdate(filters.from_date), getdate(filters.to_date), basis
	)

	treatments = {
		t.name: t
		for t in frappe.get_all(
			"ES PPN Treatment",
			fields=["name", "spt_sales_line", "spt_purchase_line", "ppn_applicable", "creditable"],
		)
	}

	rows = []
	for event in sorted(events, key=lambda e: (e["date"], e.get("voucher") or "")):
		meta = treatments.get(event["treatment"])
		is_sale = event["direction"] == SALES
		line = ""
		if meta:
			line = (meta.spt_sales_line if is_sale else meta.spt_purchase_line) or ""

		if filters.line and line != filters.line:
			continue

		rows.append({
			"date": event["date"],
			"line": line,
			"direction": event["direction"],
			"treatment": event["treatment"],
			"voucher_type": event["voucher_type"],
			"voucher": event["voucher"],
			"against": event.get("against"),
			"party": event.get("party"),
			"dpp": flt(event["dpp"]),
			"ppn": flt(event["ppn"]),
			"gross": flt(event["gross"]),
			"remark": event.get("remark"),
		})

	return get_columns(), rows


def get_columns():
	return [
		{"label": _("Date"), "fieldname": "date", "fieldtype": "Date", "width": 95},
		{"label": _("Line"), "fieldname": "line", "fieldtype": "Data", "width": 70},
		{"label": _("Direction"), "fieldname": "direction", "fieldtype": "Data", "width": 90},
		{"label": _("Treatment"), "fieldname": "treatment", "fieldtype": "Link",
		 "options": "ES PPN Treatment", "width": 170},
		{"label": _("Type"), "fieldname": "voucher_type", "fieldtype": "Data", "width": 120},
		{"label": _("Voucher"), "fieldname": "voucher", "fieldtype": "Dynamic Link",
		 "options": "voucher_type", "width": 170},
		{"label": _("Against"), "fieldname": "against", "fieldtype": "Data", "width": 150},
		{"label": _("Party"), "fieldname": "party", "fieldtype": "Data", "width": 160},
		{"label": _("DPP"), "fieldname": "dpp", "fieldtype": "Currency", "width": 130},
		{"label": _("PPN"), "fieldname": "ppn", "fieldtype": "Currency", "width": 120},
		{"label": _("Gross"), "fieldname": "gross", "fieldtype": "Currency", "width": 130},
		{"label": _("Remark"), "fieldname": "remark", "fieldtype": "Data", "width": 240},
	]
