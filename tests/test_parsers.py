from datetime import date
from decimal import Decimal

from es_ppn_id.utils import parsers


def test_indonesian_decimal_convention():
	assert parsers.parse_amount("1.386.000,00") == Decimal("1386000")


def test_anglo_decimal_convention():
	assert parsers.parse_amount("1,386,000.00") == Decimal("1386000")


def test_thousands_without_decimals():
	assert parsers.parse_amount("1.386.000") == Decimal("1386000")


def test_currency_prefix_is_ignored():
	assert parsers.parse_amount("Rp 1.386.000,00") == Decimal("1386000")


def test_direction_suffix_is_ignored():
	assert parsers.parse_amount("15.000,00 DB") == Decimal("15000")


def test_brackets_mean_negative():
	assert parsers.parse_amount("(250.000)") == Decimal("-250000")


def test_genuine_decimals_survive():
	assert parsers.parse_amount("1.234.567,89") == Decimal("1234567.89")


def test_blank_is_zero():
	assert parsers.parse_amount("") == Decimal("0")


def test_dates_in_several_shapes():
	assert parsers.parse_date("12/08/2026") == date(2026, 8, 12)
	assert parsers.parse_date("2026-08-12") == date(2026, 8, 12)
	assert parsers.parse_date("12 Agu 2026") == date(2026, 8, 12)
	assert parsers.parse_date("12-Des-26") == date(2026, 12, 12)


def test_bca_dates_need_a_statement_year():
	assert parsers.parse_date("01/08") is None
	assert parsers.parse_date("01/08", 2026) == date(2026, 8, 1)


def test_unparseable_dates_return_none():
	assert parsers.parse_date("saldo akhir") is None


def test_row_hash_separates_otherwise_identical_lines():
	first = parsers.row_hash("BCA", date(2026, 8, 1), "QRIS", 0, 50000, balance=1000000)
	second = parsers.row_hash("BCA", date(2026, 8, 1), "QRIS", 0, 50000, balance=1050000)
	assert first != second
