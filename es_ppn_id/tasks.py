# Copyright (c) 2026, Enterprise Systems Australia and contributors
"""Scheduled maintenance tasks."""

import frappe
from frappe.utils import add_days, getdate, nowdate

from es_ppn_id.utils.spt import get_period_dates


def retry_failed_postings(limit=200):
	"""
	Re-attempt rows that errored during posting.

	Most failures are transient: a lock, or a party created since the run.
	Rows that keep failing stay visible on their batch for review rather
	than being retried forever in silence.
	"""
	rows = frappe.get_all(
		"ES ID Bank Import Batch Row",
		filters={"status": "Error", "voucher": ["is", "not set"]},
		fields=["name", "parent", "bank_transaction"],
		limit=limit,
	)
	for row in rows:
		if not row.bank_transaction:
			continue
		try:
			from es_ppn_id.utils.vouchers import process_transaction

			voucher_type, voucher = process_transaction(row.bank_transaction, submit=True)
			if voucher:
				frappe.db.set_value(
					"ES ID Bank Import Batch Row", row.name,
					{
						"voucher_type": voucher_type,
						"voucher": voucher,
						"status": "Settled" if voucher_type == "Payment Entry" else "Posted",
						"error": None,
					},
					update_modified=False,
				)
				frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title=f"ES PPN ID: retry failed for {row.bank_transaction}")


def match_open_transactions(limit=200):
	"""
	Sweep unreconciled bank transactions for invoices that have since been raised.

	A transfer often lands before the invoice is issued, so a line that had
	no match yesterday can have an obvious one today. Only high-confidence
	matches settle; the rest are left for a human.
	"""
	from es_ppn_id.utils.matching import auto_match

	companies = frappe.get_all(
		"ES ID Bank Import Settings", filters={"auto_match_payments": 1}, pluck="company"
	)
	if not companies:
		return

	transactions = frappe.get_all(
		"Bank Transaction",
		filters={
			"company": ["in", companies],
			"docstatus": 1,
			"status": ["in", ["Pending", "Unreconciled"]],
			"date": [">=", add_days(getdate(nowdate()), -120)],
		},
		pluck="name",
		limit=limit,
	)

	for name in transactions:
		try:
			auto_match(name, submit=True)
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title=f"ES PPN ID: auto match failed for {name}")


def flag_due_ppn_periods(days_ahead=10):
	"""
	Notify Accounts Managers when a month has closed without a return.

	Indonesian deadlines are monthly and unforgiving, and the late filing
	penalty for an SPT Masa PPN is a fixed amount per return regardless of
	how small the month was, so a quiet month still needs filing.
	"""
	today = getdate(nowdate())
	horizon = getdate(add_days(today, days_ahead))

	for row in frappe.get_all("ES PPN Settings", filters={"pkp": 1}, fields=["company"]):
		for offset in (1, 2):
			year = today.year
			month = today.month - offset
			while month < 1:
				month += 12
				year -= 1

			from_date, to_date, ppn_due, _pph_due = get_period_dates(year, month)

			if to_date >= today or not (today <= ppn_due <= horizon):
				continue

			exists = frappe.db.exists(
				"ES PPN Return",
				{
					"company": row.company, "from_date": from_date,
					"to_date": to_date, "docstatus": ["<", 2],
				},
			)
			if exists:
				continue

			_notify(row.company, from_date, to_date, ppn_due)


def _notify(company, from_date, to_date, due):
	recipients = _accounts_managers()
	if not recipients:
		return

	subject = f"SPT Masa PPN due {due} for {company}"
	message = (
		f"The month {from_date} to {to_date} for {company} has closed and the SPT Masa PPN "
		f"is due on {due}. No return has been prepared yet. A nil month still has to be filed."
	)

	for user in recipients:
		if frappe.db.exists(
			"Notification Log",
			{"subject": subject, "for_user": user, "document_name": company},
		):
			continue
		frappe.get_doc({
			"doctype": "Notification Log",
			"subject": subject,
			"email_content": message,
			"for_user": user,
			"type": "Alert",
			"document_type": "Company",
			"document_name": company,
		}).insert(ignore_permissions=True)

	frappe.db.commit()


def _accounts_managers():
	return frappe.get_all(
		"Has Role",
		filters={"role": "Accounts Manager", "parenttype": "User"},
		pluck="parent",
	)
