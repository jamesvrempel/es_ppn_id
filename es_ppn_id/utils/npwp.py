# Copyright (c) 2026, Enterprise Systems Australia and contributors
# For license information, please see license.txt
"""
NPWP (Indonesian tax number) handling.

Two formats are in circulation. The legacy NPWP is 15 digits, usually
written 00.000.000.0-000.000. Since the Coretax migration the number is
16 digits: a corporate NPWP is the old 15 digits with a leading zero, and
an individual's tax number is their 16-digit NIK. Both must be accepted,
because supplier records will hold a mixture for years.

No checksum is published for the NPWP, so validation here is structural
only: correct length after stripping punctuation. Claiming to verify more
than that would be false assurance on a field that ends up on a faktur.
"""

import re

import frappe
from frappe import _

LEGACY_LENGTH = 15
CORETAX_LENGTH = 16


def digits(value):
	return re.sub(r"\D", "", str(value or ""))


def is_valid(value):
	return len(digits(value)) in (LEGACY_LENGTH, CORETAX_LENGTH)


def normalise(value):
	"""
	Return the 16-digit form, padding a legacy 15-digit corporate number.

	Returns None for anything that is not a recognisable NPWP so callers
	can decide whether a missing number is acceptable.
	"""
	raw = digits(value)
	if len(raw) == CORETAX_LENGTH:
		return raw
	if len(raw) == LEGACY_LENGTH:
		return "0" + raw
	return None


def format_legacy(value):
	"""Format as 00.000.000.0-000.000 for printing on documents."""
	raw = digits(value)
	if len(raw) == CORETAX_LENGTH and raw.startswith("0"):
		raw = raw[1:]
	if len(raw) != LEGACY_LENGTH:
		return str(value or "")
	return f"{raw[0:2]}.{raw[2:5]}.{raw[5:8]}.{raw[8]}-{raw[9:12]}.{raw[12:15]}"


def validate(value, label=None, throw=True):
	"""Validate an NPWP, optionally throwing with a field-specific message."""
	if not value:
		return None
	if is_valid(value):
		return normalise(value)
	if throw:
		frappe.throw(
			_("{0} does not look like an NPWP. Expected 15 digits (legacy) or 16 digits (Coretax).").format(
				frappe.bold(label or _("NPWP"))
			)
		)
	return None
