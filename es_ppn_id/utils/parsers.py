# Copyright (c) 2026, Enterprise Systems Australia and contributors
# For license information, please see license.txt
"""
Indonesian bank statement parsers.

Every bank exports a different shape and none of them agree on much, so
the file is sniffed and normalised to one row structure:

    {date, description, debit, credit, balance, reference}

Two things bite here and neither exists in an Australian statement:

Decimal convention
    Indonesian exports write one million rupiah and fifty sen as
    "1.000.000,50" while some internet-banking exports of the same bank
    write "1,000,000.50". Stripping commas as thousands separators, which
    is safe in Australia, turns "1.386.000,00" into 1.386 here. The
    separator is therefore decided per value by looking at which symbol
    appears last, falling back to the one that appears only once in a
    three-digit grouping.

Direction markers
    BCA and several others export a single amount column plus a "DB"/"CR"
    flag rather than separate debit and credit columns, and BCA omits the
    year from the date entirely, so the statement year must be supplied
    by the caller.
"""

import csv
import hashlib
import io
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

import frappe
from frappe import _

from es_ppn_id.utils.ppn import money

# Indonesian month names, short and long, lowercased.
ID_MONTHS = {
	"jan": 1, "januari": 1,
	"feb": 2, "peb": 2, "februari": 2,
	"mar": 3, "maret": 3,
	"apr": 4, "april": 4,
	"mei": 5,
	"jun": 6, "juni": 6,
	"jul": 7, "juli": 7,
	"agu": 8, "ags": 8, "agt": 8, "agustus": 8,
	"sep": 9, "sept": 9, "september": 9,
	"okt": 10, "oct": 10, "oktober": 10,
	"nov": 11, "november": 11,
	"des": 12, "dec": 12, "desember": 12,
}

DATE_FORMATS = (
	"%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y",
	"%Y-%m-%d", "%Y/%m/%d",
	"%d/%m/%y", "%d-%m-%y",
	"%d %b %Y", "%d %B %Y",
)

DEBIT_MARKERS = {"db", "d", "dr", "debet", "debit", "keluar"}
CREDIT_MARKERS = {"cr", "c", "k", "kredit", "credit", "masuk"}


def parse_date(value, default_year=None):
	"""
	Parse a statement date.

	`default_year` covers BCA, which exports "01/08" with no year at all.
	Without it those rows would be silently dropped as unparseable.
	"""
	text = re.sub(r"\s+", " ", str(value or "")).strip()
	if not text:
		return None

	for fmt in DATE_FORMATS:
		try:
			return datetime.strptime(text, fmt).date()
		except ValueError:
			continue

	# "12 Agu 2026" and friends, where strptime has no Indonesian locale.
	match = re.match(r"^(\d{1,2})[\s/-]+([A-Za-z]+)[\s/-]+(\d{2,4})$", text)
	if match:
		day, month_name, year = match.groups()
		month = ID_MONTHS.get(month_name.lower())
		if month:
			year_int = int(year)
			if year_int < 100:
				year_int += 2000
			try:
				return datetime(year_int, month, int(day)).date()
			except ValueError:
				return None

	# "01/08" or "01-08": day and month only.
	match = re.match(r"^(\d{1,2})[/-](\d{1,2})$", text)
	if match and default_year:
		day, month = match.groups()
		try:
			return datetime(int(default_year), int(month), int(day)).date()
		except ValueError:
			return None

	return None


def parse_amount(value):
	"""
	Parse an amount written in either Indonesian or Anglo convention.

	>>> parse_amount("1.386.000,00")
	Decimal('1386000')
	>>> parse_amount("1,386,000.00")
	Decimal('1386000')
	>>> parse_amount("1.386.000")
	Decimal('1386000')
	>>> parse_amount("(250.000)")
	Decimal('-250000')
	"""
	if value is None:
		return Decimal("0")
	if isinstance(value, (int, float, Decimal)):
		return money(value)

	text = str(value).strip()
	if not text:
		return Decimal("0")

	negative = (text.startswith("(") and text.endswith(")")) or text.endswith("-")
	text = text.strip("()").rstrip("-").strip()
	text = re.sub(r"(?i)\b(rp|idr)\b\.?", "", text)
	# Some exports append the direction to the amount cell: "15.000,00 DB".
	text = re.sub(r"(?i)\s*\b(db|cr|dr|k)\b\s*$", "", text)
	text = text.replace(" ", "").replace(" ", "")
	if not text or text in {"-", ".", ","}:
		return Decimal("0")

	last_dot = text.rfind(".")
	last_comma = text.rfind(",")

	if last_dot >= 0 and last_comma >= 0:
		# Both present: whichever comes last is the decimal separator.
		if last_comma > last_dot:
			text = text.replace(".", "").replace(",", ".")
		else:
			text = text.replace(",", "")
	elif last_comma >= 0:
		# Only commas. Three trailing digits in a group means thousands.
		tail = text[last_comma + 1:]
		if len(tail) == 3 and text.count(",") >= 1 and len(text) - len(tail) - 1 <= 3:
			# Ambiguous "1,000": treat as thousands, which is the common export.
			text = text.replace(",", "")
		elif len(tail) == 3 and text.count(",") > 1:
			text = text.replace(",", "")
		else:
			text = text.replace(",", ".")
	elif last_dot >= 0:
		tail = text[last_dot + 1:]
		if len(tail) == 3:
			# "1.386.000" or "1.000": rupiah thousands, never a decimal.
			text = text.replace(".", "")
		# else a genuine two-decimal value, leave it alone.

	try:
		amount = money(text, dp=2)
	except (InvalidOperation, ValueError):
		return Decimal("0")

	return -amount if negative else amount


def clean_description(text):
	"""Collapse whitespace and trim the padding banks pack descriptions with."""
	return re.sub(r"\s+", " ", str(text or "")).strip()


def row_hash(bank_account, date, description, debit, credit, balance=None):
	"""
	Stable fingerprint for a statement line, used to prevent double import.

	The running balance is included when the bank supplies one, because it
	is what distinguishes two genuinely separate transactions sharing a
	date, amount and description. Where there is no balance column the
	caller must add an occurrence index, or real duplicates collapse into
	one row and the statement stops tying to the bank.
	"""
	parts = [
		str(bank_account or ""),
		str(date or ""),
		clean_description(description).lower(),
		str(money(debit)),
		str(money(credit)),
		str(money(balance)) if balance is not None else "",
	]
	return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


class StatementParser:
	"""Base parser. Subclasses declare how to recognise and read a format."""

	name = "Generic"
	DATE_COLS = ("tanggal", "tgl", "date", "tanggal transaksi", "posting date", "tanggal posting")
	DESC_COLS = ("keterangan", "uraian", "deskripsi", "description", "berita", "remark", "narasi")
	DEBIT_COLS = ("debet", "debit", "keluar", "uang keluar", "debit amount")
	CREDIT_COLS = ("kredit", "credit", "masuk", "uang masuk", "credit amount")
	AMOUNT_COLS = ("jumlah", "nominal", "amount", "mutasi", "nilai")
	FLAG_COLS = ("db/cr", "dbcr", "d/k", "dk", "jenis", "tipe", "type", "mutasi db/cr")
	BALANCE_COLS = ("saldo", "balance", "saldo akhir")
	REF_COLS = ("referensi", "reference", "no referensi", "no. referensi", "id transaksi", "cabang")

	@classmethod
	def matches(cls, headers, sample_rows):
		return False

	@classmethod
	def _find(cls, headers, candidates):
		normalised = {(h or "").strip().lower(): i for i, h in enumerate(headers) if h}
		for candidate in candidates:
			if candidate in normalised:
				return normalised[candidate]
		for key, index in normalised.items():
			for candidate in candidates:
				if candidate in key:
					return index
		return None

	@classmethod
	def parse(cls, headers, rows, default_year=None):
		i_date = cls._find(headers, cls.DATE_COLS)
		i_desc = cls._find(headers, cls.DESC_COLS)
		i_debit = cls._find(headers, cls.DEBIT_COLS)
		i_credit = cls._find(headers, cls.CREDIT_COLS)
		i_amount = cls._find(headers, cls.AMOUNT_COLS)
		i_flag = cls._find(headers, cls.FLAG_COLS)
		i_balance = cls._find(headers, cls.BALANCE_COLS)
		i_ref = cls._find(headers, cls.REF_COLS)

		if i_date is None or i_desc is None:
			frappe.throw(
				_("Could not identify the date and description columns. Expected one of {0} and one of {1}.").format(
					", ".join(cls.DATE_COLS), ", ".join(cls.DESC_COLS)
				)
			)

		if i_debit is None and i_credit is None and i_amount is None:
			frappe.throw(_("Could not identify an amount column in this file."))

		out = []
		for raw in rows:
			if not any((cell or "").strip() for cell in raw):
				continue

			def cell(index):
				return raw[index] if index is not None and index < len(raw) else ""

			date = parse_date(cell(i_date), default_year)
			if not date:
				# Almost always a header repeat, subtotal or footer line.
				continue

			debit = credit = Decimal("0")

			if i_debit is not None or i_credit is not None:
				debit = abs(parse_amount(cell(i_debit)))
				credit = abs(parse_amount(cell(i_credit)))

			if not debit and not credit and i_amount is not None:
				amount = parse_amount(cell(i_amount))
				flag = (cell(i_flag) or "").strip().lower()
				if flag in DEBIT_MARKERS:
					debit = abs(amount)
				elif flag in CREDIT_MARKERS:
					credit = abs(amount)
				elif amount < 0:
					debit = -amount
				else:
					credit = amount

			if not debit and not credit:
				continue

			out.append({
				"date": date,
				"description": clean_description(cell(i_desc)),
				"debit": debit,
				"credit": credit,
				"balance": parse_amount(cell(i_balance)) if i_balance is not None else None,
				"reference": clean_description(cell(i_ref)) if i_ref is not None else "",
			})

		return out


class BCAParser(StatementParser):
	"""
	BCA internet banking: Tanggal, Keterangan, Cabang, Jumlah, Saldo, with
	the direction carried as "DB" or "CR" appended to the amount column,
	and the year omitted from the date.
	"""

	name = "BCA"

	@classmethod
	def matches(cls, headers, sample_rows):
		joined = " ".join((h or "").lower() for h in headers)
		if "bca" in joined:
			return True
		return "cabang" in joined and "keterangan" in joined

	@classmethod
	def parse(cls, headers, rows, default_year=None):
		i_date = cls._find(headers, cls.DATE_COLS)
		i_desc = cls._find(headers, cls.DESC_COLS)
		i_amount = cls._find(headers, ("jumlah", "mutasi", "nominal"))
		i_flag = cls._find(headers, cls.FLAG_COLS)
		i_balance = cls._find(headers, cls.BALANCE_COLS)
		i_branch = cls._find(headers, ("cabang",))

		out = []
		for raw in rows:
			def cell(index):
				return raw[index] if index is not None and index < len(raw) else ""

			date = parse_date(cell(i_date), default_year)
			if not date:
				continue

			amount_text = str(cell(i_amount) or "")
			flag = (cell(i_flag) or "").strip().lower()
			if not flag:
				# BCA often writes "1.386.000,00 DB" in the amount column.
				trailing = re.search(r"(?i)\b(db|cr)\b\s*$", amount_text.strip())
				flag = trailing.group(1).lower() if trailing else ""
			amount = abs(parse_amount(re.sub(r"(?i)\b(db|cr)\b", "", amount_text)))
			if not amount:
				continue

			debit = amount if flag in DEBIT_MARKERS else Decimal("0")
			credit = amount if flag in CREDIT_MARKERS else Decimal("0")
			if not debit and not credit:
				# No marker at all: a signed amount is the only signal left.
				signed = parse_amount(amount_text)
				debit, credit = (abs(signed), Decimal("0")) if signed < 0 else (Decimal("0"), amount)

			out.append({
				"date": date,
				"description": clean_description(cell(i_desc)),
				"debit": debit,
				"credit": credit,
				"balance": parse_amount(cell(i_balance)) if i_balance is not None else None,
				"reference": clean_description(cell(i_branch)) if i_branch is not None else "",
			})

		return out


class MandiriParser(StatementParser):
	name = "Mandiri"
	DESC_COLS = ("keterangan", "uraian transaksi", "description")

	@classmethod
	def matches(cls, headers, sample_rows):
		joined = " ".join((h or "").lower() for h in headers)
		if "mandiri" in joined:
			return True
		return "debet" in joined and "kredit" in joined and "saldo" in joined


class BNIParser(StatementParser):
	name = "BNI"
	DESC_COLS = ("uraian", "keterangan", "description")

	@classmethod
	def matches(cls, headers, sample_rows):
		joined = " ".join((h or "").lower() for h in headers)
		return "bni" in joined or "uraian" in joined


class BRIParser(StatementParser):
	name = "BRI"
	DATE_COLS = ("tanggal transaksi", "tanggal", "tgl transaksi", "tgl")

	@classmethod
	def matches(cls, headers, sample_rows):
		joined = " ".join((h or "").lower() for h in headers)
		return "bri" in joined or "tanggal transaksi" in joined


class PermataParser(StatementParser):
	name = "Permata"

	@classmethod
	def matches(cls, headers, sample_rows):
		joined = " ".join((h or "").lower() for h in headers)
		return "permata" in joined or "cimb" in joined


PARSERS = (BCAParser, MandiriParser, BRIParser, BNIParser, PermataParser, StatementParser)

FORMAT_CHOICES = ("Auto Detect", "BCA", "Mandiri", "BNI", "BRI", "Permata", "Generic")


def sniff_and_parse(content, filename="", forced_format=None, default_year=None):
	"""
	Detect the bank format and return (parser_name, rows).

	`content` may be bytes or str.
	"""
	if isinstance(content, bytes):
		for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
			try:
				content = content.decode(encoding)
				break
			except UnicodeDecodeError:
				continue
		else:
			frappe.throw(_("Could not decode the uploaded file as text."))

	text = content.lstrip("\r\n")

	try:
		dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
	except csv.Error:
		dialect = csv.excel

	reader = csv.reader(io.StringIO(text), dialect)
	all_rows = [r for r in reader]
	if not all_rows:
		frappe.throw(_("The uploaded file is empty."))

	# Indonesian exports frequently carry two or three banner lines above
	# the header (account number, period, print date), so the header is the
	# first row that names a date column rather than simply the first row.
	header_index = None
	for index, row in enumerate(all_rows[:10]):
		joined = " ".join((c or "").strip().lower() for c in row)
		if any(token in joined for token in ("tanggal", "tgl", "date")):
			header_index = index
			break

	if header_index is None:
		headers, body = [], all_rows
	else:
		headers, body = all_rows[header_index], all_rows[header_index + 1:]

	if forced_format and forced_format not in ("Auto Detect", "Generic"):
		parser = next((p for p in PARSERS if p.name == forced_format), StatementParser)
	elif forced_format == "Generic":
		parser = StatementParser
	else:
		parser = next((p for p in PARSERS if p.matches(headers, body[:5])), StatementParser)

	rows = parser.parse(headers, body, default_year)
	if not rows:
		frappe.throw(
			_("No transactions could be read from this file using the {0} format. "
			  "If the statement omits the year, set Statement Year on the batch.").format(parser.name)
		)

	return parser.name, rows
