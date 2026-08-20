# Copyright (c) 2026, Enterprise Systems Australia and contributors
# For license information, please see license.txt
"""
Voucher construction and reconciliation.

Three kinds of statement line, three different documents:

Customer payments
    Handled by `matching`: a Payment Entry against the invoice, so the
    receivable clears and any linked Payment Request updates.

Card and direct expenses
    A single Journal Entry moving money out of the bank into an expense
    account, splitting out PPN where the treatment calls for it. One
    document per line instead of an invoice, a payment and a match.

Genuine trade creditors and cash sales
    Purchase or Sales Invoices, selected per rule.

Debits and credits are read from the Bank Transaction rather than
recomputed, and PPN is derived with the DPP Nilai Lain mechanism in
`ppn.py`, so the components always re-sum to the bank line.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt

from es_ppn_id.utils.ppn import (
	TREATMENT_PPN,
	get_company_rate,
	money,
	split_inclusive,
)
from es_ppn_id.utils.rules import (
	ACTION_IGNORE,
	ACTION_JOURNAL,
	ACTION_MATCH_PAYMENT,
	ACTION_PURCHASE_INVOICE,
	ACTION_SALES_INVOICE,
)


def get_settings(company):
	name = frappe.db.get_value("ES ID Bank Import Settings", {"company": company})
	if not name:
		frappe.throw(
			_("No ES ID Bank Import Settings found for {0}. Create one before importing.").format(
				frappe.bold(company)
			)
		)
	return frappe.get_cached_doc("ES ID Bank Import Settings", name)


def get_ppn_settings(company):
	name = frappe.db.get_value("ES PPN Settings", {"company": company})
	if not name:
		frappe.throw(
			_("No ES PPN Settings found for {0}. Create one before importing.").format(
				frappe.bold(company)
			)
		)
	return frappe.get_cached_doc("ES PPN Settings", name)


def resolve_cost_center(company, preferred=None):
	"""
	Return a postable cost centre.

	ERPNext seeds a group cost centre named after the company which cannot
	be posted to, so falling back to a child avoids a failure that would
	otherwise only surface at submit time.
	"""
	if preferred and not cint(frappe.db.get_value("Cost Center", preferred, "is_group")):
		return preferred

	configured = frappe.db.get_value(
		"ES ID Bank Import Settings", {"company": company}, "cost_center"
	)
	if configured and not cint(frappe.db.get_value("Cost Center", configured, "is_group")):
		return configured

	fallback = frappe.db.get_value(
		"Cost Center", {"company": company, "is_group": 0}, "name", order_by="lft asc"
	)
	if not fallback:
		frappe.throw(_("No postable cost centre exists for {0}.").format(frappe.bold(company)))
	return fallback


def build_journal_entry(txn, decision, settings, ppn_settings):
	"""
	Build (but do not insert) a Journal Entry for a bank transaction.

	Payment of Rp 1,110,000 at the standard treatment (12% on an 11/12
	base, so 11% effective):

	    Dr Expense        1,000,000
	    Dr PPN Masukan      110,000
	    Cr Bank           1,110,000
	"""
	company = txn.company
	bank_gl = frappe.db.get_value("Bank Account", txn.bank_account, "account")
	if not bank_gl:
		frappe.throw(
			_("Bank Account {0} has no linked GL account.").format(frappe.bold(txn.bank_account))
		)

	cost_center = resolve_cost_center(company, decision.cost_center)
	is_debit = flt(txn.withdrawal) > 0
	gross = money(txn.withdrawal if is_debit else txn.deposit)

	treatment = decision.ppn_treatment or TREATMENT_PPN
	rate, num, den = get_company_rate(company, treatment)
	base, ppn = split_inclusive(gross, treatment, rate, num, den)

	ppn_account = (
		ppn_settings.ppn_masukan_account if is_debit else ppn_settings.ppn_keluaran_account
	)
	if ppn and not ppn_account:
		frappe.throw(
			_("ES PPN Settings for {0} has no {1} account configured.").format(
				frappe.bold(company),
				_("PPN Masukan") if is_debit else _("PPN Keluaran"),
			)
		)

	accounts = []

	if is_debit:
		accounts.append({
			"account": decision.account,
			"debit_in_account_currency": flt(base),
			"cost_center": cost_center,
			"user_remark": txn.description,
		})
		if ppn:
			accounts.append({
				"account": ppn_account,
				"debit_in_account_currency": flt(ppn),
				"cost_center": cost_center,
			})
		accounts.append({
			"account": bank_gl,
			"credit_in_account_currency": flt(gross),
			"cost_center": cost_center,
		})
	else:
		accounts.append({
			"account": bank_gl,
			"debit_in_account_currency": flt(gross),
			"cost_center": cost_center,
		})
		accounts.append({
			"account": decision.account,
			"credit_in_account_currency": flt(base),
			"cost_center": cost_center,
			"user_remark": txn.description,
		})
		if ppn:
			accounts.append({
				"account": ppn_account,
				"credit_in_account_currency": flt(ppn),
				"cost_center": cost_center,
			})

	# Attach the party to the expense or income leg when the rule names
	# one, so the ledger still shows who was paid without an invoice.
	if decision.party_type and decision.party:
		leg = accounts[0] if is_debit else accounts[1]
		leg["party_type"] = decision.party_type
		leg["party"] = decision.party

	je = frappe.new_doc("Journal Entry")
	je.update({
		"voucher_type": "Bank Entry",
		"company": company,
		"posting_date": txn.date,
		"user_remark": txn.description,
		"cheque_no": txn.reference_number or txn.name,
		"cheque_date": txn.date,
		"es_id_bank_transaction": txn.name,
		"es_ppn_treatment": treatment,
	})
	for row in accounts:
		je.append("accounts", row)

	return je


def build_purchase_invoice(txn, decision, settings, ppn_settings):
	"""Build a Purchase Invoice for a genuine trade creditor."""
	if not decision.party:
		frappe.throw(
			_("Rule for {0} selects Purchase Invoice but names no supplier.").format(txn.name)
		)

	cost_center = resolve_cost_center(txn.company, decision.cost_center)
	gross = money(txn.withdrawal)

	pi = frappe.new_doc("Purchase Invoice")
	pi.update({
		"company": txn.company,
		"supplier": decision.party,
		"posting_date": txn.date,
		"set_posting_time": 1,
		"bill_no": (txn.description or "")[:140],
		"bill_date": txn.date,
		"currency": txn.currency or frappe.db.get_value("Company", txn.company, "default_currency"),
		"conversion_rate": 1,
		"cost_center": cost_center,
		"update_stock": 0,
		"es_id_bank_transaction": txn.name,
		"es_ppn_treatment": decision.ppn_treatment,
	})
	pi.append("items", {
		"item_code": settings.default_expense_item,
		"description": (txn.description or "")[:140],
		"qty": 1,
		"uom": "Nos",
		"stock_uom": "Nos",
		"conversion_factor": 1,
		"rate": flt(gross),
		"expense_account": decision.account,
		"cost_center": cost_center,
	})
	if ppn_settings.purchase_tax_template and _is_taxable(decision.ppn_treatment):
		pi.taxes_and_charges = ppn_settings.purchase_tax_template
		apply_tax_template(pi, ppn_settings.purchase_tax_template, "Purchase Taxes and Charges")
	return pi


def build_sales_invoice(txn, decision, settings, ppn_settings):
	"""Build a Sales Invoice for a genuine sale banked directly."""
	if not decision.party:
		frappe.throw(
			_("Rule for {0} selects Sales Invoice but names no customer.").format(txn.name)
		)

	cost_center = resolve_cost_center(txn.company, decision.cost_center)
	gross = money(txn.deposit)

	si = frappe.new_doc("Sales Invoice")
	si.update({
		"company": txn.company,
		"customer": decision.party,
		"posting_date": txn.date,
		"set_posting_time": 1,
		"currency": txn.currency or frappe.db.get_value("Company", txn.company, "default_currency"),
		"conversion_rate": 1,
		"cost_center": cost_center,
		"update_stock": 0,
		"po_no": (txn.description or "")[:140],
		"es_id_bank_transaction": txn.name,
		"es_ppn_treatment": decision.ppn_treatment,
	})
	si.append("items", {
		"item_code": settings.default_income_item,
		"description": (txn.description or "")[:140],
		"qty": 1,
		"uom": "Nos",
		"stock_uom": "Nos",
		"conversion_factor": 1,
		"rate": flt(gross),
		"income_account": decision.account,
		"cost_center": cost_center,
	})
	if ppn_settings.sales_tax_template and _is_taxable(decision.ppn_treatment):
		si.taxes_and_charges = ppn_settings.sales_tax_template
		apply_tax_template(si, ppn_settings.sales_tax_template, "Sales Taxes and Charges")
	return si


def _is_taxable(treatment):
	if not treatment:
		return False
	return bool(cint(frappe.db.get_value("ES PPN Treatment", treatment, "ppn_applicable")))


def apply_tax_template(doc, template, child_doctype):
	"""
	Copy tax rows from a template onto a document.

	Setting `taxes_and_charges` server-side does not fire the client fetch
	that normally populates the table, so the rows are copied explicitly.
	"""
	rows = frappe.get_all(
		child_doctype,
		filters={"parent": template},
		fields=[
			"charge_type", "account_head", "rate", "description",
			"included_in_print_rate", "category", "add_deduct_tax", "cost_center",
		],
		order_by="idx asc",
	)
	doc.set("taxes", [])
	for row in rows:
		doc.append("taxes", {k: v for k, v in row.items() if v is not None})


def reconcile(txn, voucher_doctype, voucher_name, amount):
	"""
	Link a submitted voucher to its bank transaction and clear the line.

	Creating the voucher alone leaves the bank transaction unreconciled,
	which is the difference between a posted ledger and a reconciled one.
	"""
	txn = frappe.get_doc("Bank Transaction", txn.name if hasattr(txn, "name") else txn)

	already = [
		p for p in txn.payment_entries
		if p.payment_document == voucher_doctype and p.payment_entry == voucher_name
	]
	if already:
		return txn

	txn.append("payment_entries", {
		"payment_document": voucher_doctype,
		"payment_entry": voucher_name,
		"allocated_amount": flt(amount),
	})
	txn.save(ignore_permissions=True)
	txn.update_allocations()
	return txn


def process_transaction(txn, settings=None, ppn_settings=None, submit=True):
	"""
	Code and post a single Bank Transaction end to end.

	Payment matching is attempted first: a line that settles an existing
	invoice must not also be posted as income or expense. Only lines with
	no plausible invoice reach the rule engine.

	Returns (voucher_doctype, voucher_name), or (None, None) when the line
	was ignored, unmatched, or left for review.
	"""
	from es_ppn_id.utils.matching import auto_match, find_candidates
	from es_ppn_id.utils.rules import classify

	if isinstance(txn, str):
		txn = frappe.get_doc("Bank Transaction", txn)

	settings = settings or get_settings(txn.company)
	ppn_settings = ppn_settings or get_ppn_settings(txn.company)

	decision = classify(txn.company, txn.description, txn.withdrawal, txn.deposit)

	if decision.matched and decision.action == ACTION_IGNORE:
		return None, None

	if decision.matched and decision.action == ACTION_MATCH_PAYMENT:
		# The rule says this line is a settlement, so post nothing else.
		candidates = find_candidates(txn, settings)
		minimum = cint(getattr(settings, "match_min_score", 0)) or 80
		if candidates and candidates[0]["score"] >= minimum:
			from es_ppn_id.utils.matching import settle

			return settle(txn, candidates[0], settings, submit=submit)
		return None, None

	voucher_type, voucher, _candidates = auto_match(txn, settings, submit=submit)
	if voucher:
		return voucher_type, voucher

	if not decision.matched:
		return None, None

	if not decision.account and decision.action == ACTION_JOURNAL:
		frappe.throw(_("Rule {0} matched but names no account.").format(frappe.bold(decision.rule)))

	builders = {
		ACTION_JOURNAL: build_journal_entry,
		ACTION_PURCHASE_INVOICE: build_purchase_invoice,
		ACTION_SALES_INVOICE: build_sales_invoice,
	}
	builder = builders.get(decision.action)
	if not builder:
		frappe.throw(_("Unsupported rule action {0}").format(decision.action))

	doc = builder(txn, decision, settings, ppn_settings)
	doc.insert(ignore_permissions=True)

	if submit:
		doc.submit()
		reconcile(txn, doc.doctype, doc.name, flt(txn.withdrawal) or flt(txn.deposit))

	return doc.doctype, doc.name
