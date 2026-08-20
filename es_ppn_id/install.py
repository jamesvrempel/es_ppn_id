# Copyright (c) 2026, Enterprise Systems Australia and contributors
# For license information, please see license.txt

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from es_ppn_id.utils.ppn import (
	TREATMENT_DIBEBASKAN,
	TREATMENT_DIPUNGUT_PEMUNGUT,
	TREATMENT_EKSPOR,
	TREATMENT_NON_OBJEK,
	TREATMENT_PBJT,
	TREATMENT_PM_TIDAK_KREDIT,
	TREATMENT_PPN,
	TREATMENT_PPN_LUXURY,
	TREATMENT_PRIBADI,
	TREATMENT_TIDAK_DIPUNGUT,
)

# Fieldnames are prefixed es_id_ / es_ppn_ rather than es_ so this app can
# be installed alongside es_gst_au on a site that runs both an Australian
# and an Indonesian company without the two fighting over one field.
CUSTOM_FIELDS = {
	"Bank Transaction": [
		{
			"fieldname": "es_id_row_hash",
			"label": "Statement Row Hash",
			"fieldtype": "Data",
			"read_only": 1,
			"hidden": 1,
			"no_copy": 1,
			"unique": 1,
			"insert_after": "reference_number",
			"description": "Fingerprint that stops the same statement line importing twice.",
		},
		{
			"fieldname": "es_id_import_batch",
			"label": "Import Batch",
			"fieldtype": "Link",
			"options": "ES ID Bank Import Batch",
			"read_only": 1,
			"no_copy": 1,
			"insert_after": "es_id_row_hash",
		},
	],
	"Journal Entry": [
		{
			"fieldname": "es_id_bank_transaction",
			"label": "Source Bank Transaction",
			"fieldtype": "Link",
			"options": "Bank Transaction",
			"read_only": 1,
			"no_copy": 1,
			"insert_after": "cheque_date",
		},
		{
			"fieldname": "es_ppn_treatment",
			"label": "PPN Treatment",
			"fieldtype": "Link",
			"options": "ES PPN Treatment",
			"insert_after": "es_id_bank_transaction",
		},
	],
	"Payment Entry": [
		{
			"fieldname": "es_id_bank_transaction",
			"label": "Source Bank Transaction",
			"fieldtype": "Link",
			"options": "Bank Transaction",
			"read_only": 1,
			"no_copy": 1,
			"insert_after": "reference_date",
		},
	],
	"Purchase Invoice": [
		{
			"fieldname": "es_id_bank_transaction",
			"label": "Source Bank Transaction",
			"fieldtype": "Link",
			"options": "Bank Transaction",
			"read_only": 1,
			"no_copy": 1,
			"insert_after": "bill_date",
		},
		{
			"fieldname": "es_ppn_treatment",
			"label": "PPN Treatment",
			"fieldtype": "Link",
			"options": "ES PPN Treatment",
			"insert_after": "es_id_bank_transaction",
		},
		{
			"fieldname": "es_supplier_faktur_number",
			"label": "Supplier Faktur Pajak No",
			"fieldtype": "Data",
			"insert_after": "es_ppn_treatment",
			"description": "Input VAT cannot be credited without the supplier's faktur number.",
		},
	],
	"Sales Invoice": [
		{
			"fieldname": "es_id_bank_transaction",
			"label": "Source Bank Transaction",
			"fieldtype": "Link",
			"options": "Bank Transaction",
			"read_only": 1,
			"no_copy": 1,
			"insert_after": "po_date",
		},
		{
			"fieldname": "es_ppn_treatment",
			"label": "PPN Treatment",
			"fieldtype": "Link",
			"options": "ES PPN Treatment",
			"insert_after": "es_id_bank_transaction",
		},
		{
			"fieldname": "es_faktur_code",
			"label": "Faktur Transaction Code",
			"fieldtype": "Data",
			"insert_after": "es_ppn_treatment",
			"description": "Kode transaksi, e.g. 01 standard, 02 government collector, 07 not collected, 08 exempt.",
		},
		{
			"fieldname": "es_faktur_number",
			"label": "Faktur Pajak No",
			"fieldtype": "Data",
			"no_copy": 1,
			"insert_after": "es_faktur_code",
			"description": "Serial issued by DJP and recorded here once the faktur is approved.",
		},
	],
	"Customer": [
		{
			"fieldname": "es_npwp",
			"label": "NPWP",
			"fieldtype": "Data",
			"insert_after": "tax_id",
			"description": "15 or 16 digits. Required on a faktur for a registered buyer.",
		},
	],
	"Supplier": [
		{
			"fieldname": "es_npwp",
			"label": "NPWP",
			"fieldtype": "Data",
			"insert_after": "tax_id",
		},
	],
}

# Shipped treatments. The SPT lines follow the 1111 return; the faktur
# codes follow the DJP transaction code list.
DEFAULT_TREATMENTS = [
	{
		"treatment_name": TREATMENT_PPN,
		"description": "Standard taxable delivery. 12% applied to an 11/12 base, so 11% of the price.",
		"ppn_applicable": 1, "creditable": 1,
		"ppn_rate": 12, "dpp_numerator": 11, "dpp_denominator": 12,
		"spt_sales_line": "I.A.2", "spt_purchase_line": "II.A", "faktur_code": "01",
	},
	{
		"treatment_name": TREATMENT_PPN_LUXURY,
		"description": "Luxury goods. The full selling price is the base, so the whole 12% applies.",
		"ppn_applicable": 1, "creditable": 1,
		"ppn_rate": 12, "dpp_numerator": 12, "dpp_denominator": 12,
		"spt_sales_line": "I.A.2", "spt_purchase_line": "II.A", "faktur_code": "01",
	},
	{
		"treatment_name": TREATMENT_DIPUNGUT_PEMUNGUT,
		"description": "Delivered to a government body or appointed collector, who remits the PPN.",
		"ppn_applicable": 1, "creditable": 1,
		"ppn_rate": 12, "dpp_numerator": 11, "dpp_denominator": 12,
		"spt_sales_line": "I.A.3", "spt_purchase_line": "II.A", "faktur_code": "02",
	},
	{
		"treatment_name": TREATMENT_TIDAK_DIPUNGUT,
		"description": "PPN not collected: bonded zones, KEK, and designated projects.",
		"ppn_applicable": 0, "creditable": 0,
		"spt_sales_line": "I.A.4", "faktur_code": "07",
	},
	{
		"treatment_name": TREATMENT_DIBEBASKAN,
		"description": "Exempted delivery. Input VAT attributable to it cannot be credited.",
		"ppn_applicable": 0, "creditable": 0,
		"spt_sales_line": "I.A.5", "faktur_code": "08",
	},
	{
		"treatment_name": TREATMENT_EKSPOR,
		"description": "Export of goods or eligible services, zero rated. Evidenced by the PEB, not a faktur.",
		"ppn_applicable": 0, "creditable": 0,
		"spt_sales_line": "I.A.1",
	},
	{
		"treatment_name": TREATMENT_NON_OBJEK,
		"description": "Outside PPN: wages, tax payments, internal transfers, financial services.",
		"ppn_applicable": 0, "creditable": 0,
		"spt_sales_line": "I.B",
	},
	{
		"treatment_name": TREATMENT_PBJT,
		"description": "Taxed by the regency as PBJT (food, drink, hotel, recreation) instead of PPN.",
		"ppn_applicable": 0, "creditable": 0,
		"spt_sales_line": "I.B", "regional_rate": 10,
	},
	{
		"treatment_name": TREATMENT_PM_TIDAK_KREDIT,
		"description": "Input VAT that cannot be credited, e.g. no valid faktur or a non-business purchase.",
		"ppn_applicable": 1, "creditable": 0,
		"ppn_rate": 12, "dpp_numerator": 11, "dpp_denominator": 12,
		"spt_purchase_line": "II.B",
	},
	{
		"treatment_name": TREATMENT_PRIBADI,
		"description": "Private or non-deductible. No input VAT credit.",
		"ppn_applicable": 0, "creditable": 0,
		"spt_purchase_line": "II.B",
	},
]

# A deliberately small starter set: patterns that mean the same thing at
# every Indonesian bank. Merchant-level rules belong to the client and are
# learned from the unmatched queue, not shipped as guesses.
STARTER_RULES = [
	{
		"rule_name": "Incoming Transfer",
		"priority": 10,
		"match_type": "Regex",
		"pattern": r"(?i)(trsf\s*e-?banking\s*cr|bi-?fast\s*cr|transfer\s*masuk|kredit\s*transfer)",
		"direction": "Credit",
		"action": "Match Payment",
	},
	{
		"rule_name": "QRIS Settlement",
		"priority": 15,
		"match_type": "Regex",
		"pattern": r"(?i)\b(qris|qr\s*payment|settlement\s*qr)\b",
		"direction": "Credit",
		"action": "Match Payment",
	},
	{
		"rule_name": "Bank Administration Fee",
		"priority": 20,
		"match_type": "Regex",
		"pattern": r"(?i)\b(biaya\s*adm|biaya\s*admin|adm\s*bulanan|biaya\s*transfer|admin\s*fee)\b",
		"direction": "Debit",
		"ppn_treatment": TREATMENT_NON_OBJEK,
		"action": "Journal Entry",
	},
	{
		"rule_name": "Interest Received",
		"priority": 25,
		"match_type": "Regex",
		"pattern": r"(?i)\b(bunga|jasa\s*giro)\b",
		"direction": "Credit",
		"ppn_treatment": TREATMENT_NON_OBJEK,
		"action": "Journal Entry",
	},
	{
		"rule_name": "Withholding on Interest",
		"priority": 26,
		"match_type": "Regex",
		"pattern": r"(?i)\b(pajak\s*bunga|pph\s*bunga)\b",
		"direction": "Debit",
		"ppn_treatment": TREATMENT_NON_OBJEK,
		"action": "Journal Entry",
	},
	{
		"rule_name": "Tax Payment to DJP",
		"priority": 30,
		"match_type": "Regex",
		"pattern": r"(?i)\b(djp|pajak|ntpn|mpn\s*g3|e-?billing)\b",
		"direction": "Debit",
		"ppn_treatment": TREATMENT_NON_OBJEK,
		"action": "Journal Entry",
	},
	{
		"rule_name": "Payroll",
		"priority": 40,
		"match_type": "Regex",
		"pattern": r"(?i)\b(gaji|payroll|thr|upah)\b",
		"ppn_treatment": TREATMENT_NON_OBJEK,
		"action": "Journal Entry",
	},
	{
		"rule_name": "BPJS Contributions",
		"priority": 45,
		"match_type": "Regex",
		"pattern": r"(?i)\bbpjs\b",
		"direction": "Debit",
		"ppn_treatment": TREATMENT_NON_OBJEK,
		"action": "Journal Entry",
	},
	{
		"rule_name": "Internal Transfer",
		"priority": 50,
		"match_type": "Regex",
		"pattern": r"(?i)(transfer\s*antar\s*rekening|pindah\s*buku|internal\s*transfer)",
		"action": "Ignore",
	},
]


def after_install():
	create_custom_fields(CUSTOM_FIELDS, ignore_validate=True)
	seed_treatments()
	seed_starter_rules()
	frappe.db.commit()


def after_migrate():
	create_custom_fields(CUSTOM_FIELDS, ignore_validate=True)
	seed_treatments()
	frappe.db.commit()


def seed_treatments():
	for treatment in DEFAULT_TREATMENTS:
		if frappe.db.exists("ES PPN Treatment", treatment["treatment_name"]):
			continue
		doc = frappe.new_doc("ES PPN Treatment")
		doc.update(treatment)
		doc.insert(ignore_permissions=True)


def seed_starter_rules():
	"""
	Seed rules with no account attached, and disabled.

	The correct account differs with every chart of accounts, and a rule
	pointing at the wrong one is worse than a rule that asks to be
	completed. Nothing posts until an accountant has reviewed each rule
	and enabled it.
	"""
	for rule in STARTER_RULES:
		if frappe.db.exists("ES ID Bank Rule", rule["rule_name"]):
			continue
		doc = frappe.new_doc("ES ID Bank Rule")
		doc.update(rule)
		doc.enabled = 0
		doc.insert(ignore_permissions=True)
