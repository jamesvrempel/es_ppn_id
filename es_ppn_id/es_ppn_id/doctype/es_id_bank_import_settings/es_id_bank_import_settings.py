# Copyright (c) 2026, Enterprise Systems Australia and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt


class ESIDBankImportSettings(Document):
	def validate(self):
		self.validate_cost_center()
		self.validate_tolerance()

	def validate_cost_center(self):
		if self.cost_center and cint(frappe.db.get_value("Cost Center", self.cost_center, "is_group")):
			frappe.throw(
				_("{0} is a group cost centre and cannot be posted to. Choose a child.").format(
					frappe.bold(self.cost_center)
				)
			)

	def validate_tolerance(self):
		if flt(self.match_amount_tolerance) and not self.bank_charges_account:
			frappe.throw(
				_("An amount tolerance needs a Bank Charges Account, otherwise the shortfall "
				  "has nowhere to post and the invoice would be left part paid.")
			)
