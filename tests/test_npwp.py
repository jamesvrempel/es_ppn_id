from es_ppn_id.utils import npwp


def test_legacy_fifteen_digit_number_is_accepted():
	assert npwp.is_valid("01.234.567.8-901.000")


def test_coretax_sixteen_digit_number_is_accepted():
	assert npwp.is_valid("0012345678901000")


def test_wrong_length_is_rejected():
	assert not npwp.is_valid("12345")


def test_legacy_numbers_normalise_to_sixteen_digits():
	assert npwp.normalise("01.234.567.8-901.000") == "0012345678901000"


def test_formatting_round_trips():
	assert npwp.format_legacy("0012345678901000") == "01.234.567.8-901.000"


def test_unrecognisable_input_normalises_to_none():
	assert npwp.normalise("not a number") is None
