# Copyright (c) 2026, Enterprise Systems Australia and contributors
# For license information, please see license.txt
"""
Bank Rule matching engine.

Rules are evaluated in priority order (ascending) and the first rule
whose conditions all hold supplies the coding decision. Company-specific
rules outrank global rules at equal priority, so a shared default set can
ship and then be overridden per client without editing the defaults.

Indonesian bank descriptors are the reason for `_squash`. A BCA line
reads "TRSF E-BANKING CR 1508/FTSCY/WS95051 GILANG PRIMA" and the same
merchant next month may break at a different width, so matching ignores
spacing and punctuation entirely.
"""

import re

import frappe
from frappe import _
from frappe.utils import flt

from es_ppn_id.utils.ppn import TREATMENT_PPN, validate_treatment

MATCH_CONTAINS = "Contains"
MATCH_NOT_CONTAINS = "Does Not Contain"
MATCH_STARTS_WITH = "Starts With"
MATCH_EXACT = "Exact"
MATCH_REGEX = "Regex"

DIRECTION_ANY = "Any"
DIRECTION_DEBIT = "Debit"
DIRECTION_CREDIT = "Credit"

ACTION_MATCH_PAYMENT = "Match Payment"
ACTION_JOURNAL = "Journal Entry"
ACTION_PURCHASE_INVOICE = "Purchase Invoice"
ACTION_SALES_INVOICE = "Sales Invoice"
ACTION_IGNORE = "Ignore"


class Decision:
	"""The outcome of matching a bank transaction against the rule set."""

	__slots__ = (
		"matched", "rule", "action", "party_type", "party", "account",
		"ppn_treatment", "cost_center", "reason",
	)

	def __init__(self, **kwargs):
		for slot in self.__slots__:
			setattr(self, slot, kwargs.get(slot))
		if self.matched is None:
			self.matched = False

	def as_dict(self):
		return {slot: getattr(self, slot) for slot in self.__slots__}

	def __repr__(self):
		if not self.matched:
			return "<Decision unmatched>"
		return f"<Decision {self.action} party={self.party} account={self.account} ppn={self.ppn_treatment}>"


def _normalise(text):
	return re.sub(r"\s+", " ", str(text or "")).strip()


def _squash(text):
	"""Lowercase and strip everything but alphanumerics."""
	return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


@frappe.request_cache
def get_rules(company):
	"""Load enabled rules for a company, most specific first."""
	return frappe.get_all(
		"ES ID Bank Rule",
		filters={"enabled": 1},
		or_filters=[["company", "=", company], ["company", "is", "not set"]],
		fields=[
			"name", "rule_name", "priority", "company", "match_type", "pattern",
			"direction", "min_amount", "max_amount", "action", "party_type",
			"party", "account", "ppn_treatment", "cost_center", "stop_processing",
		],
		order_by="priority asc, company desc, name asc",
	)


def _pattern_matches(rule, description, squashed):
	pattern = rule.get("pattern") or ""
	if not pattern:
		return False

	match_type = rule.get("match_type") or MATCH_CONTAINS

	if match_type == MATCH_REGEX:
		try:
			return bool(re.search(pattern, description, re.IGNORECASE))
		except re.error:
			frappe.log_error(
				title="ES PPN ID: invalid regex in Bank Rule",
				message=f"Rule {rule.get('name')} pattern {pattern!r} is not valid regex.",
			)
			return False

	needle = _squash(pattern)
	if not needle:
		return False

	if match_type == MATCH_CONTAINS:
		return needle in squashed
	if match_type == MATCH_NOT_CONTAINS:
		return needle not in squashed
	if match_type == MATCH_STARTS_WITH:
		return squashed.startswith(needle)
	if match_type == MATCH_EXACT:
		return squashed == needle

	return False


def _conditions_hold(rule, description, squashed, debit, credit):
	if not _pattern_matches(rule, description, squashed):
		return False

	direction = rule.get("direction") or DIRECTION_ANY
	if direction == DIRECTION_DEBIT and not debit:
		return False
	if direction == DIRECTION_CREDIT and not credit:
		return False

	amount = flt(debit or credit)
	min_amount = flt(rule.get("min_amount"))
	max_amount = flt(rule.get("max_amount"))
	if min_amount and amount < min_amount:
		return False
	if max_amount and amount > max_amount:
		return False

	return True


def classify(company, description, debit=0, credit=0, rules=None):
	"""
	Match a single bank line against the rule set.

	An unmatched line yields Decision(matched=False) and belongs in the
	review queue. Guessing an account is worse than leaving it unposted.
	"""
	description = _normalise(description)
	squashed = _squash(description)
	rules = rules if rules is not None else get_rules(company)

	for rule in rules:
		if not _conditions_hold(rule, description, squashed, debit, credit):
			continue

		action = rule.get("action") or ACTION_JOURNAL

		if action in (ACTION_IGNORE, ACTION_MATCH_PAYMENT):
			return Decision(
				matched=True, rule=rule.get("name"), action=action,
				reason=_("Matched rule {0}").format(rule.get("rule_name")),
			)

		return Decision(
			matched=True,
			rule=rule.get("name"),
			action=action,
			party_type=rule.get("party_type"),
			party=rule.get("party"),
			account=rule.get("account"),
			ppn_treatment=validate_treatment(rule.get("ppn_treatment") or TREATMENT_PPN),
			cost_center=rule.get("cost_center"),
			reason=_("Matched rule {0}").format(rule.get("rule_name")),
		)

	return Decision(matched=False, reason=_("No rule matched"))


def classify_many(company, lines):
	"""Classify a batch, loading the rule set once."""
	rules = get_rules(company)
	return [
		classify(company, line.get("description"), line.get("debit"), line.get("credit"), rules=rules)
		for line in lines
	]


@frappe.whitelist()
def preview(company, description, debit=0, credit=0):
	"""Test a description against the live rule set from the UI."""
	frappe.has_permission("ES ID Bank Rule", throw=True)
	return classify(company, description, flt(debit), flt(credit)).as_dict()


@frappe.whitelist()
def suggest_rule_from_transaction(bank_transaction):
	"""
	Propose a rule from an unmatched Bank Transaction.

	Coding a line once should teach the engine for next month, so the
	review queue offers this rather than making the user retype a pattern.
	"""
	frappe.has_permission("ES ID Bank Rule", ptype="create", throw=True)
	txn = frappe.get_doc("Bank Transaction", bank_transaction)

	description = _normalise(txn.description)
	# Indonesian descriptors lead with the channel ("TRSF E-BANKING CR",
	# "BI-FAST", "QR PAYMENT") and end with the counterparty, which is the
	# part worth matching on, so channel noise is dropped first.
	cleaned = re.sub(
		r"(?i)\b(trsf|transfer|e-banking|ebanking|bi-fast|bifast|switching|qr|qris|db|cr|sw|kr)\b",
		" ",
		description,
	)
	words = re.findall(r"[A-Za-z]{3,}", cleaned)
	suggested = " ".join(words[:3]) if words else description[:40]

	return {
		"pattern": suggested,
		"match_type": MATCH_CONTAINS,
		"direction": DIRECTION_DEBIT if flt(txn.withdrawal) else DIRECTION_CREDIT,
		"ppn_treatment": TREATMENT_PPN,
		"description": description,
	}
