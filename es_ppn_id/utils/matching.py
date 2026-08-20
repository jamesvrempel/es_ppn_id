# Copyright (c) 2026, Enterprise Systems Australia and contributors
# For license information, please see license.txt
"""
Payment matching and reconciliation.

Most statement lines for a services business are not expenses to be
coded, they are customers paying invoices. Coding those through the rule
engine would post income twice: once when the invoice was raised and
again when the money arrived. So every line is offered to the matcher
first, and only lines with no plausible invoice fall through to the rule
engine.

A match becomes a Payment Entry built by ERPNext's own
`get_payment_entry`, which is what keeps the receivable, the party
ledger and any linked Payment Request in step. The bank transaction is
then reconciled against it, which is the step that actually clears the
line in the Bank Reconciliation Tool.

Scoring, rather than a single rule, is deliberate. An Indonesian transfer
descriptor usually carries the payer's name and sometimes the invoice
number, but the amount is often short by the bank's transfer fee. No one
signal is reliable; three weak signals agreeing is.
"""

import re
from decimal import Decimal

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate

from es_ppn_id.utils.ppn import money

# Document names as ERPNext writes them: ACC-SINV-2026-00006,
# ACC-PRQ-2026-00001, SAL-ORD-2026-00002, and shortened client series.
DOC_TOKEN = re.compile(r"\b([A-Z]{2,6}(?:-[A-Z]{2,6})?-\d{4}-\d{3,6})\b", re.IGNORECASE)
# Bare trailing digits, which is what a customer usually types as a reference.
NUMERIC_TOKEN = re.compile(r"\b(\d{3,6})\b")

SCORE_REFERENCE = 60
SCORE_AMOUNT_EXACT = 25
SCORE_AMOUNT_CLOSE = 12
SCORE_PARTY_NAME = 18
SCORE_DATE_WINDOW = 5
SCORE_OVERPAYMENT_PENALTY = 20

DEFAULT_MIN_SCORE = 80
DEFAULT_WINDOW_DAYS = 60
DEFAULT_FORWARD_DAYS = 7


def _settings(company):
	name = frappe.db.get_value("ES ID Bank Import Settings", {"company": company})
	return frappe.get_cached_doc("ES ID Bank Import Settings", name) if name else None


def _squash(text):
	return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def _name_tokens(party_name):
	"""Significant words in a party name, for loose description matching."""
	stop = {"pt", "cv", "ud", "tbk", "persero", "the", "and", "dan", "mr", "mrs", "ms"}
	words = re.findall(r"[A-Za-z]{3,}", str(party_name or ""))
	return [w.lower() for w in words if w.lower() not in stop]


def _referenced_documents(description, reference, company):
	"""
	Resolve any document name written into the statement narrative.

	A customer quoting the payment request rather than the invoice is
	common, so a Payment Request or Sales Order is followed through to the
	invoice it belongs to.
	"""
	text = f"{description or ''} {reference or ''}"
	found = {}

	for token in set(m.group(1).upper() for m in DOC_TOKEN.finditer(text)):
		for doctype in ("Sales Invoice", "Purchase Invoice"):
			if frappe.db.exists(doctype, {"name": token, "company": company, "docstatus": 1}):
				found[(doctype, token)] = "reference"

		if frappe.db.exists("Payment Request", {"name": token, "docstatus": 1}):
			ref = frappe.db.get_value(
				"Payment Request", token, ["reference_doctype", "reference_name"], as_dict=True
			)
			if ref and ref.reference_doctype in ("Sales Invoice", "Purchase Invoice"):
				found[(ref.reference_doctype, ref.reference_name)] = "payment request"

		if frappe.db.exists("Sales Order", {"name": token, "company": company, "docstatus": 1}):
			for inv in frappe.get_all(
				"Sales Invoice Item",
				filters={"sales_order": token, "docstatus": 1},
				fields=["parent"],
			):
				found[("Sales Invoice", inv.parent)] = "sales order"

	return found


def _open_invoices(company, doctype, from_date, to_date, currency=None):
	filters = {
		"company": company,
		"docstatus": 1,
		"outstanding_amount": [">", 0],
		"posting_date": ["between", [from_date, to_date]],
	}
	if currency:
		filters["currency"] = currency

	party_field = "customer" if doctype == "Sales Invoice" else "supplier"
	name_field = "customer_name" if doctype == "Sales Invoice" else "supplier_name"

	return frappe.get_all(
		doctype,
		filters=filters,
		fields=[
			"name", "posting_date", "currency", "grand_total", "outstanding_amount",
			f"{party_field} as party", f"{name_field} as party_name",
		],
		limit=500,
	)


def find_candidates(txn, settings=None):
	"""
	Rank the invoices a bank line might be paying.

	Returns a list of dicts sorted by score, highest first.
	"""
	if isinstance(txn, str):
		txn = frappe.get_doc("Bank Transaction", txn)

	settings = settings or _settings(txn.company)
	window = cint(getattr(settings, "match_date_window_days", 0)) or DEFAULT_WINDOW_DAYS
	tolerance = flt(getattr(settings, "match_amount_tolerance", 0))

	is_credit = flt(txn.deposit) > 0
	amount = money(txn.deposit if is_credit else txn.withdrawal)
	if not amount:
		return []

	doctype = "Sales Invoice" if is_credit else "Purchase Invoice"
	txn_date = getdate(txn.date)
	from_date = add_days(txn_date, -window)
	to_date = add_days(txn_date, DEFAULT_FORWARD_DAYS)

	referenced = _referenced_documents(txn.description, txn.reference_number, txn.company)
	candidates = {}

	# Referenced documents are considered even when they fall outside the
	# date window: a quoted invoice number is stronger evidence than a date.
	for (ref_doctype, ref_name), why in referenced.items():
		if ref_doctype != doctype:
			continue
		row = frappe.db.get_value(
			ref_doctype, ref_name,
			["name", "posting_date", "currency", "grand_total", "outstanding_amount"],
			as_dict=True,
		)
		if not row or flt(row.outstanding_amount) <= 0:
			continue
		party_field = "customer" if doctype == "Sales Invoice" else "supplier"
		name_field = "customer_name" if doctype == "Sales Invoice" else "supplier_name"
		row.party = frappe.db.get_value(ref_doctype, ref_name, party_field)
		row.party_name = frappe.db.get_value(ref_doctype, ref_name, name_field)
		candidates[row.name] = {"row": row, "reasons": [f"quoted {why} on the statement"], "score": SCORE_REFERENCE}

	for row in _open_invoices(txn.company, doctype, from_date, to_date, txn.currency):
		candidates.setdefault(row.name, {"row": row, "reasons": [], "score": 0})

	description_squashed = _squash(f"{txn.description} {txn.reference_number or ''}")
	results = []

	for name, entry in candidates.items():
		row = entry["row"]
		score = entry["score"]
		reasons = list(entry["reasons"])

		outstanding = money(row.outstanding_amount)
		difference = abs(amount - outstanding)

		if difference == 0:
			score += SCORE_AMOUNT_EXACT
			reasons.append("amount matches the outstanding balance exactly")
		elif tolerance and difference <= Decimal(str(tolerance)):
			score += SCORE_AMOUNT_CLOSE
			reasons.append(f"amount is within tolerance ({difference} difference)")
		elif amount > outstanding:
			score -= SCORE_OVERPAYMENT_PENALTY
			reasons.append("amount is more than the outstanding balance")

		tokens = _name_tokens(row.party_name or row.party)
		if tokens and any(_squash(token) in description_squashed for token in tokens):
			score += SCORE_PARTY_NAME
			reasons.append("payer name appears in the statement narrative")

		if from_date <= getdate(row.posting_date) <= to_date:
			score += SCORE_DATE_WINDOW

		if score <= 0:
			continue

		results.append({
			"voucher_type": doctype,
			"voucher": name,
			"party_type": "Customer" if doctype == "Sales Invoice" else "Supplier",
			"party": row.party,
			"party_name": row.party_name,
			"posting_date": str(row.posting_date),
			"currency": row.currency,
			"grand_total": flt(row.grand_total),
			"outstanding": flt(outstanding),
			"amount": flt(amount),
			"difference": flt(amount - outstanding),
			"score": int(score),
			"reasons": reasons,
		})

	results.sort(key=lambda r: (-r["score"], abs(r["difference"])))
	return results


def _bank_gl_account(bank_account):
	account = frappe.db.get_value("Bank Account", bank_account, "account")
	if not account:
		frappe.throw(
			_("Bank Account {0} has no linked GL account.").format(frappe.bold(bank_account))
		)
	return account


def build_payment_entry(txn, candidate, settings=None):
	"""
	Build (but do not insert) a Payment Entry settling one invoice.

	ERPNext's own `get_payment_entry` is used rather than assembling the
	document by hand, because it is what sets the receivable account,
	party balances and any linked Payment Request correctly.
	"""
	from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

	settings = settings or _settings(txn.company)
	bank_gl = _bank_gl_account(txn.bank_account)

	pe = get_payment_entry(candidate["voucher_type"], candidate["voucher"], bank_account=bank_gl)
	pe.posting_date = txn.date
	pe.reference_no = txn.reference_number or txn.description[:140] or txn.name
	pe.reference_date = txn.date

	paid = flt(candidate["amount"])
	outstanding = flt(candidate["outstanding"])
	allocated = min(paid, outstanding)

	if pe.payment_type == "Receive":
		pe.received_amount = paid
		pe.paid_amount = paid
	else:
		pe.paid_amount = paid
		pe.received_amount = paid

	for ref in pe.references:
		ref.allocated_amount = allocated

	shortfall = flt(outstanding - paid, 2)
	tolerance = flt(getattr(settings, "match_amount_tolerance", 0))
	charges_account = getattr(settings, "bank_charges_account", None)

	# A transfer that arrives short by the bank's fee is still full
	# settlement of the invoice; the difference is a bank charge, not a
	# customer debt. Only do this inside the configured tolerance.
	if 0 < shortfall <= tolerance and charges_account:
		for ref in pe.references:
			ref.allocated_amount = outstanding
		pe.append("deductions", {
			"account": charges_account,
			"cost_center": frappe.db.get_value("Company", txn.company, "cost_center"),
			"amount": shortfall,
			"description": _("Bank transfer charge"),
		})

	pe.es_id_bank_transaction = txn.name
	pe.set_amounts()
	return pe


def settle(txn, candidate, settings=None, submit=True):
	"""
	Create, submit and reconcile a Payment Entry for one candidate.

	Returns (voucher_doctype, voucher_name).
	"""
	from es_ppn_id.utils.vouchers import reconcile

	if isinstance(txn, str):
		txn = frappe.get_doc("Bank Transaction", txn)

	pe = build_payment_entry(txn, candidate, settings)
	pe.insert(ignore_permissions=True)

	if not submit:
		return pe.doctype, pe.name

	pe.submit()
	reconcile(txn, pe.doctype, pe.name, flt(candidate["amount"]))
	return pe.doctype, pe.name


def auto_match(txn, settings=None, submit=True):
	"""
	Match and settle a bank line if the evidence is strong enough.

	Returns (voucher_doctype, voucher_name, candidates). A confident match
	settles; anything less is returned for a human to choose from, because
	paying down the wrong customer's invoice is materially worse than
	leaving a line unreconciled for a day.
	"""
	if isinstance(txn, str):
		txn = frappe.get_doc("Bank Transaction", txn)

	settings = settings or _settings(txn.company)
	if not settings or not cint(getattr(settings, "auto_match_payments", 0)):
		return None, None, []

	candidates = find_candidates(txn, settings)
	if not candidates:
		return None, None, []

	minimum = cint(getattr(settings, "match_min_score", 0)) or DEFAULT_MIN_SCORE
	best = candidates[0]
	runner_up = candidates[1] if len(candidates) > 1 else None

	if best["score"] < minimum:
		return None, None, candidates

	# Two invoices that fit equally well is not a match, it is a coin toss.
	if runner_up and runner_up["score"] == best["score"]:
		return None, None, candidates

	voucher_type, voucher = settle(txn, best, settings, submit=submit)
	return voucher_type, voucher, candidates


@frappe.whitelist()
def get_candidates(bank_transaction):
	"""Candidate list for the review UI."""
	frappe.has_permission("Bank Transaction", throw=True)
	return find_candidates(bank_transaction)


@frappe.whitelist()
def settle_transaction(bank_transaction, voucher_type, voucher):
	"""Settle a bank line against an invoice chosen by a human."""
	frappe.has_permission("Payment Entry", ptype="create", throw=True)

	txn = frappe.get_doc("Bank Transaction", bank_transaction)
	candidates = find_candidates(txn)
	chosen = next(
		(c for c in candidates if c["voucher_type"] == voucher_type and c["voucher"] == voucher),
		None,
	)

	if not chosen:
		# The invoice was not in the ranked list (outside the window, say),
		# so build the candidate directly rather than refusing a deliberate
		# instruction from an operator who can see more than the matcher.
		row = frappe.db.get_value(
			voucher_type, voucher,
			["name", "posting_date", "currency", "grand_total", "outstanding_amount"],
			as_dict=True,
		)
		if not row:
			frappe.throw(_("{0} {1} not found.").format(voucher_type, voucher))
		party_field = "customer" if voucher_type == "Sales Invoice" else "supplier"
		chosen = {
			"voucher_type": voucher_type,
			"voucher": voucher,
			"party_type": "Customer" if voucher_type == "Sales Invoice" else "Supplier",
			"party": frappe.db.get_value(voucher_type, voucher, party_field),
			"outstanding": flt(row.outstanding_amount),
			"amount": flt(txn.deposit) or flt(txn.withdrawal),
			"score": 0,
			"reasons": ["chosen manually"],
		}

	voucher_type, voucher_name = settle(txn, chosen)
	return {"voucher_type": voucher_type, "voucher": voucher_name}
