# Copyright (c) 2026, Enterprise Systems Australia and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt


class ESPPNTreatment(Document):
	def validate(self):
		self.validate_dpp_fraction()
		self.validate_lines()

	def validate_dpp_fraction(self):
		if not cint(self.ppn_applicable):
			return

		if flt(self.dpp_denominator) <= 0:
			frappe.throw(_("DPP denominator must be greater than zero."))

		if flt(self.dpp_numerator) > flt(self.dpp_denominator):
			frappe.throw(
				_("DPP numerator cannot exceed the denominator. The other-value base is a "
				  "fraction of the selling price, never more than it.")
			)

	def validate_lines(self):
		if cint(self.ppn_applicable) and not (self.spt_sales_line or self.spt_purchase_line):
			frappe.msgprint(
				_("{0} charges PPN but is not mapped to an SPT line, so it will not appear on the return.").format(
					frappe.bold(self.name)
				),
				indicator="orange",
				title=_("Not reported"),
			)

	@property
	def effective_rate(self):
		if not cint(self.ppn_applicable) or not flt(self.dpp_denominator):
			return 0.0
		return flt(self.ppn_rate) * flt(self.dpp_numerator) / flt(self.dpp_denominator)
