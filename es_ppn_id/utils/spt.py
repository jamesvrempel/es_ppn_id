# Copyright (c) 2026, Enterprise Systems Australia and contributors
# For license information, please see license.txt
"""
SPT Masa PPN computation.

Lines follow the 1111 return: Part I is deliveries (penyerahan), Part II
is input VAT (pajak masukan), Part III is the calculation. Every line
carries both a base (DPP) and a tax (PPN) figure, because the form asks
for both and a single amount cannot represent a delivery that is exempt
in one column and nil in the other.

The figures are built from taxable delivery events rather than from GL
account balances. One PPN account is hit by transactions with different
treatments -- exports, exempt supplies and standard supplies all touch
income but only one of them belongs at I.A.2 -- and a balance cannot tell
them apart.

Basis
    PPN in Indonesia is due on delivery, not on payment, so the accrual
    basis is the default and the correct one for a PKP. A cash basis is
    offered for the small number of businesses on a special arrangement,
    and apportions each invoice across the payments allocated to it pro
    rata, so a part-paid invoice reports only the paid proportion.

Deadlines (PMK 81/2024)
    Withholding tax is paid by the 15th of the following month, the
    unified PPh return is filed by the 20th, and the SPT Masa PPN is due
    by the end of the following month.
"""

from collections import defaultdict
from decimal import Decimal

import frappe
from frappe import _
from frappe.utils import cint, flt, get_last_day, getdate

from es_ppn_id.utils.ppn import money

BASIS_CASH = "Cash"
BASIS_ACCRUAL = "Accrual"

SALES = "Sales"
PURCHASES = "Purchases"

# Sales lines carry PPN only where it is actually charged and payable.
LINE_EKSPOR = "I.A.1"
LINE_DIPUNGUT_SENDIRI = "I.A.2"
LINE_DIPUNGUT_PEMUNGUT = "I.A.3"
LINE_TIDAK_DIPUNGUT = "I.A.4"
LINE_DIBEBASKAN = "I.A.5"
LINE_SALES_TOTAL = "I.A.6"
LINE_NON_OBJEK = "I.B"
LINE_ALL_SALES = "I.C"

LINE_PM_KREDIT = "II.A"
LINE_PM_NON_KREDIT = "II.B"
LINE_PM_TOTAL = "II.C"

LINE_PPN_KELUARAN = "III.A"
LINE_PPN_MASUKAN = "III.B"
LINE_KURANG_BAYAR = "III.C"
LINE_KOMPENSASI = "III.D"
LINE_PAYABLE = "III.E"

DERIVED_LINES = {
	LINE_SALES_TOTAL, LINE_ALL_SALES, LINE_PM_TOTAL,
	LINE_PPN_KELUARAN, LINE_PPN_MASUKAN, LINE_KURANG_BAYAR, LINE_PAYABLE,
}
EDITABLE_LINES = {LINE_KOMPENSASI}


def get_period_dates(year, month):
	"""
	Resolve a monthly period to (from_date, to_date, ppn_due, pph_due).

	PPN: filed and paid by the end of the following month.
	PPh: paid by the 15th and the unified return filed by the 20th.
	"""
	year = cint(year)
	month = cint(month)
	if not (1 <= month <= 12):
		frappe.throw(_("Month must be 1 to 12."))

	start = getdate(f"{year}-{month:02d}-01")
	end = get_last_day(start)

	next_month = 1 if month == 12 else month + 1
	next_year = year + 1 if month == 12 else year

	ppn_due = get_last_day(getdate(f"{next_year}-{next_month:02d}-01"))
	pph_due = getdate(f"{next_year}-{next_month:02d}-20")

	return start, end, ppn_due, pph_due


def _treatment_map():
	"""treatment -> line mapping and flags"""
	rows = frappe.get_all(
		"ES PPN Treatment",
		fields=[
			"name", "spt_sales_line", "spt_purchase_line",
			"ppn_applicable", "creditable",
		],
	)
	return {
		r.name: {
			"sales_line": r.spt_sales_line or "",
			"purchase_line": r.spt_purchase_line or "",
			"ppn_applicable": cint(r.ppn_applicable),
			"creditable": cint(r.creditable),
		}
		for r in rows
	}


def _invoice_events(company, from_date, to_date, basis, doctype, party_field):
	"""Taxable delivery events from invoices."""
	direction = SALES if doctype == "Sales Invoice" else PURCHASES
	events = []

	fields = [
		"name", "posting_date", f"{party_field} as party", "net_total",
		"total_taxes_and_charges", "grand_total", "es_ppn_treatment",
	]

	if basis == BASIS_ACCRUAL:
		for inv in frappe.get_all(
			doctype,
			filters={
				"company": company, "docstatus": 1,
				"posting_date": ["between", [from_date, to_date]],
			},
			fields=fields,
		):
			events.append({
				"date": inv.posting_date,
				"voucher_type": doctype,
				"voucher": inv.name,
				"party": inv.party,
				"treatment": inv.es_ppn_treatment,
				"direction": direction,
				"gross": money(inv.grand_total),
				"dpp": money(inv.net_total),
				"ppn": money(inv.total_taxes_and_charges),
			})
		return events

	# Cash basis: follow the payments allocated in the period.
	allocations = frappe.db.sql(
		"""
		SELECT per.reference_name AS invoice, pe.posting_date, pe.name AS payment,
		       per.allocated_amount
		FROM `tabPayment Entry Reference` per
		INNER JOIN `tabPayment Entry` pe ON pe.name = per.parent
		WHERE pe.docstatus = 1 AND pe.company = %(company)s
		  AND per.reference_doctype = %(doctype)s
		  AND pe.posting_date BETWEEN %(from_date)s AND %(to_date)s
		""",
		{"company": company, "doctype": doctype, "from_date": from_date, "to_date": to_date},
		as_dict=True,
	)
	if not allocations:
		return events

	invoices = {
		i.name: i
		for i in frappe.get_all(
			doctype, filters={"name": ["in", list({a.invoice for a in allocations})]}, fields=fields
		)
	}

	for alloc in allocations:
		inv = invoices.get(alloc.invoice)
		if not inv or not flt(inv.grand_total):
			continue
		share = Decimal(str(flt(alloc.allocated_amount))) / Decimal(str(flt(inv.grand_total)))
		events.append({
			"date": alloc.posting_date,
			"voucher_type": "Payment Entry",
			"voucher": alloc.payment,
			"against": inv.name,
			"party": inv.party,
			"treatment": inv.es_ppn_treatment,
			"direction": direction,
			"gross": money(flt(alloc.allocated_amount)),
			"dpp": money(Decimal(str(flt(inv.net_total))) * share),
			"ppn": money(Decimal(str(flt(inv.total_taxes_and_charges))) * share),
		})

	return events


def _journal_events(company, from_date, to_date, ppn_settings):
	"""
	Taxable events from Journal Entries posted off bank statements.

	The base and PPN are read from the entry's own lines rather than
	recomputed, so a manual correction to a Journal Entry is respected
	rather than silently overwritten at return time.
	"""
	journals = frappe.get_all(
		"Journal Entry",
		filters={
			"company": company, "docstatus": 1,
			"posting_date": ["between", [from_date, to_date]],
			"es_ppn_treatment": ["is", "set"],
		},
		fields=["name", "posting_date", "es_ppn_treatment", "user_remark"],
	)
	if not journals:
		return []

	lines = frappe.get_all(
		"Journal Entry Account",
		filters={"parent": ["in", [j.name for j in journals]]},
		fields=["parent", "account", "debit", "credit", "party_type", "party"],
	)

	ppn_accounts = {
		ppn_settings.get("ppn_masukan_account"),
		ppn_settings.get("ppn_keluaran_account"),
	}
	ppn_accounts.discard(None)

	bank_accounts = set(
		frappe.get_all(
			"Account",
			filters={"company": company, "account_type": "Bank", "is_group": 0},
			pluck="name",
		)
	)

	by_parent = defaultdict(list)
	for line in lines:
		by_parent[line.parent].append(line)

	events = []
	for je in journals:
		ppn = Decimal("0")
		dpp = Decimal("0")
		party = None
		direction = None

		for row in by_parent.get(je.name, []):
			if row.account in ppn_accounts:
				ppn += Decimal(str(flt(row.debit) - flt(row.credit)))
				continue
			if row.account in bank_accounts:
				# A credit to the bank is money out, therefore a purchase.
				direction = PURCHASES if flt(row.credit) else SALES
				continue
			dpp += Decimal(str(flt(row.debit) - flt(row.credit)))
			party = party or row.party

		if direction is None:
			continue

		ppn = abs(ppn)
		dpp = abs(dpp)
		events.append({
			"date": je.posting_date,
			"voucher_type": "Journal Entry",
			"voucher": je.name,
			"party": party,
			"treatment": je.es_ppn_treatment,
			"direction": direction,
			"gross": money(dpp + ppn),
			"dpp": money(dpp),
			"ppn": money(ppn),
			"remark": je.user_remark,
		})

	return events


def collect_events(company, from_date, to_date, basis=BASIS_ACCRUAL):
	"""Gather every taxable delivery event in the period."""
	ppn_settings = (
		frappe.db.get_value(
			"ES PPN Settings", {"company": company},
			["ppn_masukan_account", "ppn_keluaran_account"], as_dict=True,
		)
		or {}
	)

	events = []
	events += _journal_events(company, from_date, to_date, ppn_settings)
	events += _invoice_events(company, from_date, to_date, basis, "Sales Invoice", "customer")
	events += _invoice_events(company, from_date, to_date, basis, "Purchase Invoice", "supplier")
	return events


def compute(company, from_date, to_date, basis=BASIS_ACCRUAL):
	"""
	Compute SPT Masa PPN line values for a period.

	Returns {"lines": {label: {"dpp": Decimal, "ppn": Decimal}}, "events": [...]}.
	"""
	events = collect_events(company, from_date, to_date, basis)
	treatments = _treatment_map()

	lines = defaultdict(lambda: {"dpp": Decimal("0"), "ppn": Decimal("0")})

	for event in events:
		meta = treatments.get(event["treatment"]) or {}
		dpp = money(event["dpp"])
		ppn = money(event["ppn"])

		if event["direction"] == SALES:
			label = meta.get("sales_line") or LINE_NON_OBJEK
			lines[label]["dpp"] += dpp
			if meta.get("ppn_applicable"):
				lines[label]["ppn"] += ppn
		else:
			label = meta.get("purchase_line")
			if not label:
				# A purchase with no input-VAT line is simply not reported
				# in Part II; it still hits the ledger.
				continue
			lines[label]["dpp"] += dpp
			if meta.get("ppn_applicable"):
				lines[label]["ppn"] += ppn

	_derive(lines)

	return {
		"lines": {k: {"dpp": money(v["dpp"]), "ppn": money(v["ppn"])} for k, v in lines.items()},
		"events": events,
		"basis": basis,
		"from_date": from_date,
		"to_date": to_date,
	}


def _derive(lines):
	"""Fill the derived totals and the Part III calculation."""
	sales_lines = (
		LINE_EKSPOR, LINE_DIPUNGUT_SENDIRI, LINE_DIPUNGUT_PEMUNGUT,
		LINE_TIDAK_DIPUNGUT, LINE_DIBEBASKAN,
	)

	lines[LINE_SALES_TOTAL] = {
		"dpp": sum((lines[label]["dpp"] for label in sales_lines), Decimal("0")),
		"ppn": sum((lines[label]["ppn"] for label in sales_lines), Decimal("0")),
	}
	lines[LINE_ALL_SALES] = {
		"dpp": lines[LINE_SALES_TOTAL]["dpp"] + lines[LINE_NON_OBJEK]["dpp"],
		"ppn": lines[LINE_SALES_TOTAL]["ppn"],
	}
	lines[LINE_PM_TOTAL] = {
		"dpp": lines[LINE_PM_KREDIT]["dpp"] + lines[LINE_PM_NON_KREDIT]["dpp"],
		"ppn": lines[LINE_PM_KREDIT]["ppn"] + lines[LINE_PM_NON_KREDIT]["ppn"],
	}

	# Output VAT that the company itself must remit. Deliveries collected
	# by an appointed collector are excluded: the collector remits that
	# PPN, so including it here would pay it twice.
	keluaran = lines[LINE_DIPUNGUT_SENDIRI]["ppn"]
	masukan = lines[LINE_PM_KREDIT]["ppn"]

	lines[LINE_PPN_KELUARAN] = {"dpp": Decimal("0"), "ppn": keluaran}
	lines[LINE_PPN_MASUKAN] = {"dpp": Decimal("0"), "ppn": masukan}
	lines[LINE_KURANG_BAYAR] = {"dpp": Decimal("0"), "ppn": keluaran - masukan}

	carried = lines[LINE_KOMPENSASI]["ppn"]
	lines[LINE_PAYABLE] = {"dpp": Decimal("0"), "ppn": keluaran - masukan - carried}


def compute_pph(company, from_date, to_date, ppn_settings):
	"""
	Withholding totals from the GL, where accounts are configured.

	Returns zeros rather than a guess when an account is not set, because
	a wrong withholding figure is a misstatement, not an approximation.
	"""
	result = {}
	account_fields = {
		"PPh 21": "pph_21_account",
		"PPh 23": "pph_23_account",
		"PPh 4(2)": "pph_4_2_account",
		"PPh 25": "pph_25_account",
	}

	for label, field in account_fields.items():
		account = ppn_settings.get(field)
		result[label] = Decimal("0")
		if not account:
			continue

		rows = frappe.db.sql(
			"""
			SELECT SUM(credit - debit) AS amount
			FROM `tabGL Entry`
			WHERE company = %(company)s AND account = %(account)s
			  AND is_cancelled = 0 AND posting_date BETWEEN %(from_date)s AND %(to_date)s
			""",
			{"company": company, "account": account, "from_date": from_date, "to_date": to_date},
			as_dict=True,
		)
		if rows and rows[0].amount:
			result[label] = money(rows[0].amount)

	return result


@frappe.whitelist()
def preview(company, from_date, to_date, basis=BASIS_ACCRUAL):
	"""Compute without creating a return, for the worksheet report."""
	frappe.has_permission("ES PPN Return", throw=True)
	result = compute(company, getdate(from_date), getdate(to_date), basis)
	return {
		"lines": {
			label: {"dpp": flt(value["dpp"]), "ppn": flt(value["ppn"])}
			for label, value in result["lines"].items()
		}
	}
