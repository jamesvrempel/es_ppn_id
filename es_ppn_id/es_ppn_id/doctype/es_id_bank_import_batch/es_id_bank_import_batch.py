# Copyright (c) 2026, Enterprise Systems Australia and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt

from es_ppn_id.utils.matching import find_candidates
from es_ppn_id.utils.parsers import row_hash, sniff_and_parse
from es_ppn_id.utils.ppn import money
from es_ppn_id.utils.rules import ACTION_IGNORE, ACTION_MATCH_PAYMENT, classify, get_rules
from es_ppn_id.utils.vouchers import get_ppn_settings, get_settings, process_transaction

# Posting runs in chunks so a long statement neither blocks a web worker
# nor loses everything to one bad row.
POST_CHUNK_SIZE = 50


class ESIDBankImportBatch(Document):
	def validate(self):
		self.validate_bank_account()
		self.recalculate_totals()

	def validate_bank_account(self):
		if not self.bank_account:
			return
		account_company = frappe.db.get_value("Bank Account", self.bank_account, "company")
		if account_company and account_company != self.company:
			frappe.throw(
				_("Bank Account {0} belongs to {1}, not {2}.").format(
					frappe.bold(self.bank_account),
					frappe.bold(account_company),
					frappe.bold(self.company),
				)
			)

	def on_submit(self):
		if not self.rows:
			frappe.throw(_("Parse the statement before submitting."))
		self.create_bank_transactions()

	def on_cancel(self):
		self.ignore_linked_doctypes = ("GL Entry",)
		posted = [r for r in self.rows if r.voucher]
		if posted:
			frappe.throw(
				_("This batch has {0} posted vouchers. Cancel those first.").format(len(posted))
			)

	# ------------------------------------------------------------------
	# Parsing
	# ------------------------------------------------------------------

	@frappe.whitelist()
	def parse_statement(self):
		"""Read the attached file into rows, flagging duplicates as we go."""
		if not self.import_file:
			frappe.throw(_("Attach a statement file first."))

		content = get_file_content(self.import_file)
		forced = None if self.bank_format in (None, "Auto Detect") else self.bank_format
		fmt, parsed = sniff_and_parse(
			content, self.import_file, forced, default_year=cint(self.statement_year) or None
		)

		self.set("rows", [])
		seen_in_file = {}
		rules = get_rules(self.company)

		for line in parsed:
			# Two genuinely distinct transactions can share a date, amount
			# and description. Where the bank gives no running balance they
			# are disambiguated by occurrence so they do not collapse into
			# one row and quietly lose a payment.
			base = row_hash(
				self.bank_account, line["date"], line["description"],
				line["debit"], line["credit"], line.get("balance"),
			)
			occurrence = seen_in_file.get(base, 0)
			seen_in_file[base] = occurrence + 1
			fingerprint = base if occurrence == 0 else f"{base}:{occurrence}"

			decision = classify(
				self.company, line["description"], line["debit"], line["credit"], rules=rules
			)

			existing = frappe.db.get_value(
				"Bank Transaction", {"es_id_row_hash": fingerprint, "docstatus": ["<", 2]}, "name"
			)

			row = {
				"date": line["date"],
				"description": line["description"][:500],
				"debit": flt(line["debit"]),
				"credit": flt(line["credit"]),
				"balance": flt(line["balance"]) if line.get("balance") is not None else None,
				"row_hash": fingerprint,
				"matched_rule": decision.rule if decision.matched else None,
				"account": decision.account if decision.matched else None,
				"ppn_treatment": decision.ppn_treatment if decision.matched else None,
			}

			if existing:
				row["status"] = "Duplicate"
				row["bank_transaction"] = existing
			elif decision.matched and decision.action == ACTION_IGNORE:
				row["status"] = "Ignored"
			else:
				row["status"] = "Pending" if decision.matched else "Unmatched"
				if decision.matched and decision.action == ACTION_MATCH_PAYMENT:
					row["status"] = "Pending"

			self.append("rows", row)

		if parsed:
			self.statement_from = min(line["date"] for line in parsed)
			self.statement_to = max(line["date"] for line in parsed)

		self.bank_format = fmt
		self.status = "Parsed"
		self.recalculate_totals()
		self.save()

		return {
			"format": fmt,
			"total": len(parsed),
			"duplicates": self.duplicate_rows,
			"unmatched": self.unmatched_rows,
		}

	@frappe.whitelist()
	def find_payment_matches(self):
		"""
		Look for an invoice behind every unposted credit and debit.

		Run after submitting, when Bank Transactions exist. This only
		records the best candidate and its score; nothing is posted, so it
		is safe to run repeatedly while reviewing.
		"""
		if self.docstatus != 1:
			frappe.throw(_("Submit the batch before matching payments."))

		settings = get_settings(self.company)
		found = 0

		for row in self.rows:
			if not row.bank_transaction or row.voucher or row.status in ("Duplicate", "Ignored"):
				continue

			candidates = find_candidates(row.bank_transaction, settings)
			if not candidates:
				continue

			best = candidates[0]
			row.db_set("match_score", best["score"], update_modified=False)
			row.db_set("matched_voucher_type", best["voucher_type"], update_modified=False)
			row.db_set("matched_voucher", best["voucher"], update_modified=False)
			row.db_set("matched_party", best.get("party_name") or best.get("party"), update_modified=False)
			if row.status in ("Pending", "Unmatched"):
				row.db_set("status", "Matched", update_modified=False)
			found += 1

		self.reload()
		self.recalculate_totals()
		self.db_update()
		frappe.msgprint(
			_("Found a candidate invoice for {0} of {1} open lines.").format(
				frappe.bold(found), frappe.bold(len(self.rows))
			)
		)
		return {"matched": found}

	def recalculate_totals(self):
		self.total_rows = len(self.rows)
		self.duplicate_rows = sum(1 for r in self.rows if r.status == "Duplicate")
		self.unmatched_rows = sum(1 for r in self.rows if r.status == "Unmatched")
		self.matched_rows = sum(1 for r in self.rows if r.status == "Matched")
		self.settled_rows = sum(1 for r in self.rows if r.status == "Settled")
		self.imported_rows = sum(
			1 for r in self.rows if r.bank_transaction and r.status != "Duplicate"
		)
		self.posted_rows = sum(1 for r in self.rows if r.voucher)
		self.error_rows = sum(1 for r in self.rows if r.status == "Error")
		self.total_debits = money(sum(flt(r.debit) for r in self.rows), dp=2)
		self.total_credits = money(sum(flt(r.credit) for r in self.rows), dp=2)

	# ------------------------------------------------------------------
	# Bank Transactions
	# ------------------------------------------------------------------

	def create_bank_transactions(self):
		"""Create a Bank Transaction per non-duplicate row."""
		created = 0
		currency = frappe.db.get_value("Company", self.company, "default_currency")

		for row in self.rows:
			if row.status == "Duplicate" or row.bank_transaction:
				continue
			try:
				txn = frappe.new_doc("Bank Transaction")
				txn.update({
					"date": row.date,
					"bank_account": self.bank_account,
					"company": self.company,
					"deposit": flt(row.credit),
					"withdrawal": flt(row.debit),
					"description": row.description,
					"reference_number": self.name,
					"currency": currency,
					"es_id_row_hash": row.row_hash,
					"es_id_import_batch": self.name,
				})
				txn.insert(ignore_permissions=True)
				txn.submit()
				row.db_set("bank_transaction", txn.name, update_modified=False)
				created += 1
			except Exception:
				row.db_set("status", "Error", update_modified=False)
				row.db_set(
					"error", frappe.get_traceback(with_context=False)[:500], update_modified=False
				)
				frappe.log_error(title=f"ES PPN ID: bank transaction failed ({self.name})")

		self.reload()
		self.recalculate_totals()
		self.status = "Imported"
		self.db_update()
		frappe.msgprint(
			_("Created {0} bank transactions. {1} duplicates skipped.").format(
				frappe.bold(created), frappe.bold(self.duplicate_rows)
			)
		)

	# ------------------------------------------------------------------
	# Posting
	# ------------------------------------------------------------------

	@frappe.whitelist()
	def post_vouchers(self, enqueue=True):
		"""
		Settle or code every open row, then reconcile it.

		Idempotent: a row that already carries a voucher is skipped, so a
		re-run after a partial failure resumes rather than duplicating.
		"""
		if self.docstatus != 1:
			frappe.throw(_("Submit the batch before posting vouchers."))

		pending = [
			r for r in self.rows
			if r.bank_transaction and not r.voucher and r.status not in ("Ignored", "Duplicate")
		]
		if not pending:
			frappe.msgprint(_("Nothing left to post."))
			return {"queued": 0}

		if enqueue:
			frappe.enqueue(
				"es_ppn_id.es_ppn_id.doctype.es_id_bank_import_batch.es_id_bank_import_batch._post_batch",
				queue="long", timeout=7200, batch=self.name, job_name=f"es-ppn-post-{self.name}",
			)
			frappe.msgprint(
				_("Queued {0} rows. Progress will update on this document.").format(
					frappe.bold(len(pending))
				)
			)
			return {"queued": len(pending)}

		return _post_batch(self.name)


def _post_batch(batch):
	"""Worker entry point. Kept at module level so it is enqueueable."""
	doc = frappe.get_doc("ES ID Bank Import Batch", batch)
	settings = get_settings(doc.company)
	ppn_settings = get_ppn_settings(doc.company)

	posted = settled = failed = 0

	for index, row in enumerate(doc.rows):
		if not row.bank_transaction or row.voucher or row.status in ("Ignored", "Duplicate"):
			continue
		try:
			voucher_type, voucher = process_transaction(
				row.bank_transaction, settings, ppn_settings, submit=True
			)
			if voucher:
				row.db_set("voucher_type", voucher_type, update_modified=False)
				row.db_set("voucher", voucher, update_modified=False)
				if voucher_type == "Payment Entry":
					row.db_set("status", "Settled", update_modified=False)
					settled += 1
				else:
					row.db_set("status", "Posted", update_modified=False)
					posted += 1
			else:
				row.db_set("status", "Unmatched", update_modified=False)
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			row.db_set("status", "Error", update_modified=False)
			row.db_set("error", frappe.get_traceback(with_context=False)[:500], update_modified=False)
			frappe.db.commit()
			failed += 1
			frappe.log_error(title=f"ES PPN ID: voucher failed for {row.bank_transaction}")

		if index % POST_CHUNK_SIZE == 0:
			frappe.publish_progress(
				percent=(index / max(len(doc.rows), 1)) * 100,
				title=_("Posting vouchers"), doctype=doc.doctype, docname=doc.name,
			)

	doc.reload()
	doc.recalculate_totals()
	doc.status = "Posted" if not failed and not doc.unmatched_rows else "Partially Posted"
	doc.db_update()
	frappe.db.commit()

	return {"posted": posted, "settled": settled, "failed": failed}


def get_file_content(file_url):
	"""Read an attached File's bytes regardless of private or public storage."""
	file_doc = frappe.get_doc("File", {"file_url": file_url})
	return file_doc.get_content()


@frappe.whitelist()
def get_unmatched_summary(company, limit=50):
	"""
	Group unmatched descriptions so one new rule can clear many rows.

	This is what makes the rule set converge: the largest cluster is
	always the most valuable rule to write next.
	"""
	frappe.has_permission("ES ID Bank Import Batch", throw=True)
	return frappe.db.sql(
		"""
		SELECT r.description, COUNT(*) AS occurrences,
		       SUM(r.debit) AS total_debit, SUM(r.credit) AS total_credit
		FROM `tabES ID Bank Import Batch Row` r
		INNER JOIN `tabES ID Bank Import Batch` b ON b.name = r.parent
		WHERE b.company = %(company)s AND r.status = 'Unmatched'
		GROUP BY r.description
		ORDER BY occurrences DESC, total_debit DESC
		LIMIT %(limit)s
		""",
		{"company": company, "limit": cint(limit)},
		as_dict=True,
	)
