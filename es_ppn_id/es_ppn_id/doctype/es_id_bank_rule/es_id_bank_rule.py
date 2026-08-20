# Copyright (c) 2026, Enterprise Systems Australia and contributors
# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt

ACTIONS_NEEDING_ACCOUNT = {"Journal Entry"}
ACTIONS_NEEDING_PARTY = {"Purchase Invoice", "Sales Invoice"}


class ESIDBankRule(Document):
	def validate(self):
		self.validate_pattern()
		self.validate_amounts()
		self.validate_action()

	def validate_pattern(self):
		if self.match_type != "Regex":
			return
		try:
			re.compile(self.pattern or "")
		except re.error as exc:
			frappe.throw(_("Pattern is not valid regex: {0}").format(exc))

	def validate_amounts(self):
		if flt(self.min_amount) and flt(self.max_amount) and flt(self.min_amount) > flt(self.max_amount):
			frappe.throw(_("Min Amount cannot be greater than Max Amount."))

	def validate_action(self):
		# A disabled rule is allowed to be incomplete: the shipped starter
		# rules arrive without accounts on purpose, because the right
		# account differs with every chart of accounts. The requirement
		# bites when someone turns the rule on.
		if not cint(self.enabled):
			self._clear_unused_coding()
			return

		if self.action in ACTIONS_NEEDING_ACCOUNT and not self.account:
			frappe.throw(
				_("A {0} rule needs an account. A rule that posts to the wrong account is "
				  "worse than one that asks to be completed.").format(frappe.bold(self.action))
			)

		if self.action in ACTIONS_NEEDING_PARTY and not self.party:
			frappe.throw(
				_("A {0} rule needs a party.").format(frappe.bold(self.action))
			)

		self._clear_unused_coding()

	def _clear_unused_coding(self):
		"""
		Drop coding fields that the chosen action cannot use.

		Left in place they would be silently reused if the action changed
		back, which is how a payment ends up posted to last year's expense
		account.
		"""
		if self.action in ("Match Payment", "Ignore"):
			self.account = None
			self.party_type = None
			self.party = None
			self.ppn_treatment = None
