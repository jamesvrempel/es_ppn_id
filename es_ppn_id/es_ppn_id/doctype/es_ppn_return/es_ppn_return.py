# Copyright (c) 2026, Enterprise Systems Australia and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt, getdate

from es_ppn_id.utils.ppn import money
from es_ppn_id.utils.spt import (
	DERIVED_LINES,
	EDITABLE_LINES,
	LINE_ALL_SALES,
	LINE_DIBEBASKAN,
	LINE_DIPUNGUT_PEMUNGUT,
	LINE_DIPUNGUT_SENDIRI,
	LINE_EKSPOR,
	LINE_KOMPENSASI,
	LINE_KURANG_BAYAR,
	LINE_NON_OBJEK,
	LINE_PAYABLE,
	LINE_PM_KREDIT,
	LINE_PM_NON_KREDIT,
	LINE_PM_TOTAL,
	LINE_PPN_KELUARAN,
	LINE_PPN_MASUKAN,
	LINE_SALES_TOTAL,
	LINE_TIDAK_DIPUNGUT,
	compute,
	compute_pph,
	get_period_dates,
)

# (line, description, part)
LINE_DEFINITIONS = [
	(LINE_EKSPOR, "Ekspor", "Penyerahan"),
	(LINE_DIPUNGUT_SENDIRI, "Penyerahan yang PPN-nya harus dipungut sendiri", "Penyerahan"),
	(LINE_DIPUNGUT_PEMUNGUT, "Penyerahan yang PPN-nya dipungut oleh Pemungut PPN", "Penyerahan"),
	(LINE_TIDAK_DIPUNGUT, "Penyerahan yang PPN-nya tidak dipungut", "Penyerahan"),
	(LINE_DIBEBASKAN, "Penyerahan yang dibebaskan dari pengenaan PPN", "Penyerahan"),
	(LINE_SALES_TOTAL, "Jumlah penyerahan terutang PPN", "Penyerahan"),
	(LINE_NON_OBJEK, "Penyerahan barang dan jasa yang tidak terutang PPN", "Penyerahan"),
	(LINE_ALL_SALES, "Jumlah seluruh penyerahan", "Penyerahan"),
	(LINE_PM_KREDIT, "Pajak Masukan yang dapat dikreditkan", "Pajak Masukan"),
	(LINE_PM_NON_KREDIT, "Pajak Masukan yang tidak dapat dikreditkan", "Pajak Masukan"),
	(LINE_PM_TOTAL, "Jumlah Pajak Masukan", "Pajak Masukan"),
	(LINE_PPN_KELUARAN, "PPN Keluaran", "Perhitungan"),
	(LINE_PPN_MASUKAN, "PPN Masukan yang dapat dikreditkan", "Perhitungan"),
	(LINE_KURANG_BAYAR, "PPN kurang / (lebih) bayar sebelum kompensasi", "Perhitungan"),
	(LINE_KOMPENSASI, "Kompensasi kelebihan PPN bulan lalu", "Perhitungan"),
	(LINE_PAYABLE, "PPN kurang / (lebih) bayar", "Perhitungan"),
]


class ESPPNReturn(Document):
	def validate(self):
		self.set_period()
		self.check_overlap()
		if not self.lines:
			self.calculate()
		else:
			self.recalculate_derived()
		self.set_summary()

	def before_submit(self):
		if not self.lines:
			frappe.throw(_("Calculate the return before submitting."))

	def on_submit(self):
		frappe.msgprint(
			_("Period {0} to {1} is now locked. Postings dated inside it will be blocked "
			  "until this return is cancelled or amended.").format(
				frappe.bold(self.from_date), frappe.bold(self.to_date)
			)
		)

	def on_cancel(self):
		if cint(self.filed):
			frappe.throw(
				_("This return is marked as filed with DJP. Unmark it before cancelling, so the "
				  "ledger and the filed return do not drift apart silently.")
			)

	# ------------------------------------------------------------------

	def set_period(self):
		settings = frappe.db.get_value(
			"ES PPN Settings", {"company": self.company},
			["npwp", "accounting_basis", "pkp"], as_dict=True,
		)
		if not settings:
			frappe.throw(_("No ES PPN Settings found for {0}.").format(frappe.bold(self.company)))

		if not cint(settings.pkp):
			frappe.msgprint(
				_("{0} is not marked as a registered PKP. Check ES PPN Settings before filing.").format(
					frappe.bold(self.company)
				),
				indicator="orange", title=_("Not registered"),
			)

		self.npwp = settings.npwp
		self.accounting_basis = self.accounting_basis or settings.accounting_basis

		from_date, to_date, ppn_due, pph_due = get_period_dates(self.period_year, self.period_month)
		self.from_date = from_date
		self.to_date = to_date
		self.ppn_due_date = ppn_due
		self.pph_due_date = pph_due
		# PMK 81/2024 harmonised the payment deadline to the 15th; the unified
		# PPh return then follows on the 20th.
		self.pph_payment_due = getdate(pph_due).replace(day=15)

	def check_overlap(self):
		existing = frappe.db.sql(
			"""
			SELECT name FROM `tabES PPN Return`
			WHERE company = %(company)s AND docstatus < 2 AND name != %(name)s
			  AND from_date <= %(to_date)s AND to_date >= %(from_date)s
			""",
			{
				"company": self.company, "name": self.name or "new",
				"from_date": self.from_date, "to_date": self.to_date,
			},
		)
		if existing:
			frappe.throw(
				_("Return {0} already covers this month.").format(frappe.bold(existing[0][0]))
			)

	@frappe.whitelist()
	def calculate(self):
		"""Recompute every non-editable line from the ledger."""
		result = compute(
			self.company, getdate(self.from_date), getdate(self.to_date), self.accounting_basis
		)
		computed = result["lines"]

		ppn_settings = frappe.db.get_value(
			"ES PPN Settings", {"company": self.company},
			["pph_21_account", "pph_23_account", "pph_4_2_account", "pph_25_account"],
			as_dict=True,
		) or {}
		pph = compute_pph(
			self.company, getdate(self.from_date), getdate(self.to_date), ppn_settings
		)

		# Anything the preparer typed into an editable line is kept.
		retained = {
			row.line: flt(row.ppn_amount) for row in (self.lines or []) if row.line in EDITABLE_LINES
		}
		if self.carried_forward:
			retained[LINE_KOMPENSASI] = flt(self.carried_forward)

		self.set("lines", [])
		for line, description, part in LINE_DEFINITIONS:
			values = computed.get(line) or {}
			dpp = flt(values.get("dpp", 0))
			ppn = flt(values.get("ppn", 0))

			if line in retained:
				ppn = retained[line]

			self.append("lines", {
				"line": line,
				"line_description": description,
				"section": part,
				"dpp_amount": dpp,
				"ppn_amount": ppn,
				"is_derived": 1 if line in DERIVED_LINES else 0,
				"is_editable": 1 if line in EDITABLE_LINES else 0,
			})

		self.pph_21 = flt(pph.get("PPh 21", 0)) or flt(self.pph_21)
		self.pph_23 = flt(pph.get("PPh 23", 0)) or flt(self.pph_23)
		self.pph_4_2 = flt(pph.get("PPh 4(2)", 0)) or flt(self.pph_4_2)
		self.pph_25 = flt(pph.get("PPh 25", 0)) or flt(self.pph_25)

		self.recalculate_derived()
		self.set_summary()
		return {"lines": len(self.lines), "events": len(result["events"])}

	def recalculate_derived(self):
		"""Recompute derived lines so manual adjustments flow through."""
		dpp = {row.line: flt(row.dpp_amount) for row in self.lines}
		ppn = {row.line: flt(row.ppn_amount) for row in self.lines}

		sales = (
			LINE_EKSPOR, LINE_DIPUNGUT_SENDIRI, LINE_DIPUNGUT_PEMUNGUT,
			LINE_TIDAK_DIPUNGUT, LINE_DIBEBASKAN,
		)
		dpp[LINE_SALES_TOTAL] = sum(dpp.get(line, 0) for line in sales)
		ppn[LINE_SALES_TOTAL] = sum(ppn.get(line, 0) for line in sales)
		dpp[LINE_ALL_SALES] = dpp[LINE_SALES_TOTAL] + dpp.get(LINE_NON_OBJEK, 0)
		ppn[LINE_ALL_SALES] = ppn[LINE_SALES_TOTAL]

		dpp[LINE_PM_TOTAL] = dpp.get(LINE_PM_KREDIT, 0) + dpp.get(LINE_PM_NON_KREDIT, 0)
		ppn[LINE_PM_TOTAL] = ppn.get(LINE_PM_KREDIT, 0) + ppn.get(LINE_PM_NON_KREDIT, 0)

		# PPN collected by an appointed collector is remitted by that
		# collector, so it is deliberately excluded here. Including it
		# would pay the same tax twice.
		keluaran = ppn.get(LINE_DIPUNGUT_SENDIRI, 0)
		masukan = ppn.get(LINE_PM_KREDIT, 0)

		ppn[LINE_PPN_KELUARAN] = keluaran
		ppn[LINE_PPN_MASUKAN] = masukan
		ppn[LINE_KURANG_BAYAR] = keluaran - masukan
		ppn[LINE_PAYABLE] = keluaran - masukan - ppn.get(LINE_KOMPENSASI, 0)

		for row in self.lines:
			if row.line in DERIVED_LINES:
				row.dpp_amount = flt(dpp.get(row.line, 0))
				row.ppn_amount = flt(ppn.get(row.line, 0))

	def set_summary(self):
		ppn = {row.line: flt(row.ppn_amount) for row in self.lines}

		self.ppn_keluaran = ppn.get(LINE_PPN_KELUARAN, 0)
		self.ppn_masukan = ppn.get(LINE_PPN_MASUKAN, 0)
		self.carried_forward = ppn.get(LINE_KOMPENSASI, 0)
		self.net_amount = flt(money(ppn.get(LINE_PAYABLE, 0)))
		self.net_label = (
			_("Kurang bayar - payable to DJP") if self.net_amount > 0
			else _("Lebih bayar - overpaid, carry forward or claim")
			if self.net_amount < 0
			else _("Nil")
		)


def get_locked_period(company, posting_date):
	"""Return a submitted return covering this date, if any."""
	if not company or not posting_date:
		return None
	return frappe.db.get_value(
		"ES PPN Return",
		{
			"company": company, "docstatus": 1,
			"from_date": ["<=", posting_date], "to_date": [">=", posting_date],
		},
		"name",
	)


def validate_period_not_locked(doc, method=None):
	"""
	Block postings into a filed month.

	Attached to Journal Entry, Purchase Invoice, Sales Invoice and Payment
	Entry. Once a month has been reported to DJP, changing the numbers
	underneath it silently would put the ledger out of step with the
	return that was filed, and the faktur numbers with it.
	"""
	if doc.doctype == "ES PPN Return":
		return
	if not getattr(doc, "company", None) or not getattr(doc, "posting_date", None):
		return

	locked = get_locked_period(doc.company, doc.posting_date)
	if not locked:
		return

	if frappe.flags.get("es_ppn_allow_locked_period"):
		return

	frappe.throw(
		_("{0} is dated {1}, which falls inside SPT Masa PPN {2}. Cancel or amend that "
		  "return before posting into the month.").format(
			frappe.bold(doc.doctype), frappe.bold(doc.posting_date), frappe.bold(locked)
		),
		title=_("Period locked"),
	)


@frappe.whitelist()
def create_for_period(company, year, month, basis=None):
	"""Convenience entry point used by the workspace shortcut."""
	frappe.has_permission("ES PPN Return", ptype="create", throw=True)
	doc = frappe.new_doc("ES PPN Return")
	doc.company = company
	doc.period_year = cint(year)
	doc.period_month = str(cint(month))
	if basis:
		doc.accounting_basis = basis
	doc.insert()
	doc.calculate()
	doc.save()
	return doc.name
