# Copyright (c) 2026, Enterprise Systems Australia and contributors
"""
Bank Transaction hooks.

Transactions created by an import batch are handled by the batch, which
tracks progress and surfaces failures on the document. Transactions
arriving any other way -- a manual entry, another importer, a live bank
feed -- are matched and coded here instead, provided the company has
opted into automatic posting.
"""

import frappe
from frappe.utils import cint


def after_insert(doc, method=None):
	if doc.get("es_id_import_batch"):
		# The batch owns posting for its own rows.
		return

	settings_name = frappe.db.get_value("ES ID Bank Import Settings", {"company": doc.company})
	if not settings_name:
		return

	settings = frappe.get_cached_doc("ES ID Bank Import Settings", settings_name)
	if not (cint(settings.auto_submit) or cint(settings.auto_match_payments)):
		return

	frappe.enqueue(
		"es_ppn_id.utils.vouchers.process_transaction",
		queue="short",
		txn=doc.name,
		submit=True,
		job_name=f"es-ppn-code-{doc.name}",
	)
