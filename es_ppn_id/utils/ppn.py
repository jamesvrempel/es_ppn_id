# Copyright (c) 2026, Enterprise Systems Australia and contributors
# For license information, please see license.txt
"""
Indonesian PPN (VAT) calculation helpers.

The mechanism that makes Indonesia different from most VAT systems is the
"other value" tax base (DPP Nilai Lain). Since 1 January 2025 the
statutory rate is 12 per cent, but for most goods and services the tax
base is 11/12 of the selling price, so the tax actually charged is:

    PPN = 12% x (11/12 x price) = 11% of price

Luxury goods subject to PPnBM use the full price as the base and
therefore bear the full 12 per cent. Modelling this as (statutory rate,
DPP numerator, DPP denominator) rather than hard-coding "11%" means that
when the transitional rule ends and the base becomes the full price, only
the treatment record changes, not the code, and historical periods keep
reporting the rate that actually applied to them.

Amounts are rounded to whole rupiah. The rupiah has no circulating
subunit, e-Faktur reports whole rupiah, and deriving the base as
(total - ppn) rather than (total / 1.11) guarantees the two components
re-sum to the bank line to the rupiah.
"""

from decimal import ROUND_HALF_UP, Decimal

import frappe
from frappe import _

# Treatment codes. Stable identifiers used on Bank Rules and vouchers; the
# human-readable labels live in the ES PPN Treatment doctype so they can be
# relabelled without breaking stored data.
TREATMENT_PPN = "PPN"                                # Taxable, standard (12% on 11/12 base)
TREATMENT_PPN_LUXURY = "PPN Barang Mewah"            # Taxable, full 12% base
TREATMENT_DIPUNGUT_PEMUNGUT = "PPN Dipungut Pemungut"  # Collected by an appointed collector
TREATMENT_TIDAK_DIPUNGUT = "PPN Tidak Dipungut"      # Not collected (KEK, bonded zone, projects)
TREATMENT_DIBEBASKAN = "PPN Dibebaskan"              # Exempted supplies
TREATMENT_EKSPOR = "Ekspor"                          # Zero-rated export
TREATMENT_NON_OBJEK = "Non-Objek PPN"                # Outside PPN: wages, tax, transfers
TREATMENT_PBJT = "PBJT Daerah"                       # Regional service tax instead of PPN
TREATMENT_PM_TIDAK_KREDIT = "PM Tidak Dapat Dikreditkan"  # Input VAT that cannot be credited
TREATMENT_PRIBADI = "Pribadi"                        # Private / non-deductible

# Only these carry a PPN component that is charged or claimed.
TAXABLE_TREATMENTS = (
	TREATMENT_PPN,
	TREATMENT_PPN_LUXURY,
	TREATMENT_DIPUNGUT_PEMUNGUT,
	TREATMENT_PM_TIDAK_KREDIT,
)

DEFAULT_RATE = Decimal("12")
DEFAULT_DPP_NUMERATOR = Decimal("11")
DEFAULT_DPP_DENOMINATOR = Decimal("12")

# Rupiah has no circulating subunit; PPN is reported in whole rupiah.
RUPIAH_DP = 0


def _d(value):
	"""Coerce to Decimal without float artefacts."""
	if isinstance(value, Decimal):
		return value
	return Decimal(str(value or 0))


def money(value, dp=RUPIAH_DP):
	"""Round half-up to `dp` places. Whole rupiah by default."""
	exponent = Decimal(1).scaleb(-dp) if dp else Decimal("1")
	return _d(value).quantize(exponent, rounding=ROUND_HALF_UP)


def effective_rate(rate=None, dpp_numerator=None, dpp_denominator=None):
	"""
	The rate actually borne by the customer.

	>>> effective_rate(12, 11, 12)
	Decimal('11')
	>>> effective_rate(12, 12, 12)
	Decimal('12')
	"""
	rate = _d(rate if rate is not None else DEFAULT_RATE)
	num = _d(dpp_numerator if dpp_numerator is not None else DEFAULT_DPP_NUMERATOR)
	den = _d(dpp_denominator if dpp_denominator is not None else DEFAULT_DPP_DENOMINATOR)
	if den <= 0:
		return rate
	return rate * num / den


def split_inclusive(total, treatment=TREATMENT_PPN, rate=None, dpp_numerator=None, dpp_denominator=None):
	"""
	Split a PPN-inclusive amount into (dpp, ppn).

	`dpp` here is the invoice base as booked to income or expense; the
	narrower "DPP Nilai Lain" used on the faktur is exposed separately by
	`faktur_base`. dpp + ppn == total is guaranteed.

	>>> split_inclusive(1386000)
	(Decimal('1248649'), Decimal('137351'))
	>>> split_inclusive(1000000, TREATMENT_NON_OBJEK)
	(Decimal('1000000'), Decimal('0'))
	"""
	total = money(total)

	if treatment not in TAXABLE_TREATMENTS:
		return total, money(0)

	rate_eff = effective_rate(rate, dpp_numerator, dpp_denominator)
	if rate_eff <= 0:
		return total, money(0)

	ppn = money(total * rate_eff / (Decimal("100") + rate_eff))
	return total - ppn, ppn


def split_exclusive(base, treatment=TREATMENT_PPN, rate=None, dpp_numerator=None, dpp_denominator=None):
	"""Split a PPN-exclusive amount into (dpp, ppn)."""
	base = money(base)

	if treatment not in TAXABLE_TREATMENTS:
		return base, money(0)

	rate_eff = effective_rate(rate, dpp_numerator, dpp_denominator)
	if rate_eff <= 0:
		return base, money(0)

	return base, money(base * rate_eff / Decimal("100"))


def faktur_base(base, dpp_numerator=None, dpp_denominator=None):
	"""
	The DPP Nilai Lain shown on an e-Faktur line.

	e-Faktur asks for the 11/12 base and the full 12 per cent applied to
	it, not the selling price and 11 per cent. The numbers reconcile
	either way but only one of them matches the form.
	"""
	num = _d(dpp_numerator if dpp_numerator is not None else DEFAULT_DPP_NUMERATOR)
	den = _d(dpp_denominator if dpp_denominator is not None else DEFAULT_DPP_DENOMINATOR)
	if den <= 0:
		return money(base)
	return money(_d(base) * num / den)


def get_treatment_rates(treatment):
	"""
	Resolve (rate, dpp_numerator, dpp_denominator) for a treatment.

	Read from the treatment record so a rate change is a data change.
	"""
	row = frappe.db.get_value(
		"ES PPN Treatment",
		treatment,
		["ppn_rate", "dpp_numerator", "dpp_denominator", "ppn_applicable"],
		as_dict=True,
	)
	if not row or not row.get("ppn_applicable"):
		return Decimal("0"), DEFAULT_DPP_NUMERATOR, DEFAULT_DPP_DENOMINATOR

	return (
		_d(row.get("ppn_rate")) or DEFAULT_RATE,
		_d(row.get("dpp_numerator")) or DEFAULT_DPP_NUMERATOR,
		_d(row.get("dpp_denominator")) or DEFAULT_DPP_DENOMINATOR,
	)


def get_company_rate(company, treatment=TREATMENT_PPN):
	"""
	Resolve the rate for a company and treatment.

	The treatment record wins; ES PPN Settings only supplies a fallback so
	that a company on a transitional or special rate can be handled
	without editing shared treatment records.
	"""
	if treatment not in TAXABLE_TREATMENTS:
		return Decimal("0"), DEFAULT_DPP_NUMERATOR, DEFAULT_DPP_DENOMINATOR

	rate, num, den = get_treatment_rates(treatment)
	if rate:
		return rate, num, den

	fallback = frappe.db.get_value("ES PPN Settings", {"company": company}, "ppn_rate")
	return (_d(fallback) or DEFAULT_RATE), DEFAULT_DPP_NUMERATOR, DEFAULT_DPP_DENOMINATOR


def validate_treatment(treatment):
	if not treatment:
		frappe.throw(_("A PPN treatment is required."))
	if not frappe.db.exists("ES PPN Treatment", treatment):
		frappe.throw(
			_("Unknown PPN treatment {0}. Create it under ES PPN Treatment first.").format(
				frappe.bold(treatment)
			)
		)
	return treatment
