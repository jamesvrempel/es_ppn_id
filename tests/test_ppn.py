from decimal import Decimal

from es_ppn_id.utils import ppn


def test_effective_rate_is_eleven_percent_on_the_standard_base():
	assert ppn.effective_rate(12, 11, 12) == Decimal("11")


def test_effective_rate_is_twelve_percent_for_luxury_goods():
	assert ppn.effective_rate(12, 12, 12) == Decimal("12")


def test_inclusive_split_reconciles_to_the_original_amount():
	base, tax = ppn.split_inclusive(1386000)
	assert base + tax == Decimal("1386000")
	assert tax == Decimal("137351")


def test_luxury_split_bears_the_full_rate():
	base, tax = ppn.split_inclusive(1386000, ppn.TREATMENT_PPN_LUXURY, 12, 12, 12)
	assert tax == Decimal("148500")
	assert base + tax == Decimal("1386000")


def test_non_taxable_treatments_carry_no_tax():
	base, tax = ppn.split_inclusive(1000000, ppn.TREATMENT_NON_OBJEK)
	assert tax == Decimal("0")
	assert base == Decimal("1000000")


def test_exclusive_split_adds_eleven_percent():
	base, tax = ppn.split_exclusive(1000000)
	assert base == Decimal("1000000")
	assert tax == Decimal("110000")


def test_faktur_base_is_eleven_twelfths():
	assert ppn.faktur_base(1200000) == Decimal("1100000")


def test_rupiah_rounds_to_whole_units():
	assert ppn.money("1000.4") == Decimal("1000")
	assert ppn.money("1000.5") == Decimal("1001")
