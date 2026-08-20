# Copyright (c) 2026, Enterprise Systems Australia and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

from es_ppn_id.utils.npwp import validate as validate_npwp

# Entity types that lost access to the 0.5% final turnover regime when
# PP 20/2026 took effect on 22 April 2026. They fall back to corporate
# income tax at 22%, with the Article 31E reduction where turnover is
# under Rp 50 billion.
FINAL_REGIME_INELIGIBLE = {"CV", "Firma", "PT", "Yayasan", "BUT"}


class ESPPNSettings(Document):
	def validate(self):
		self.npwp = validate_npwp(self.npwp, label=_("NPWP")) or self.npwp
		self.warn_on_final_regime()
		self.warn_on_missing_accounts()

	def warn_on_final_regime(self):
		if self.entity_type in FINAL_REGIME_INELIGIBLE:
			frappe.msgprint(
				_("{0} entities cannot use the 0.5% final turnover regime since PP 20/2026. "
				  "Income tax is 22% on net profit, with the Article 31E reduction below "
				  "Rp 50 billion turnover.").format(frappe.bold(self.entity_type)),
				indicator="blue",
				title=_("Income tax basis"),
			)

	def warn_on_missing_accounts(self):
		if not cint(self.pkp):
			return
		missing = [
			label
			for label, value in (
				(_("PPN Keluaran"), self.ppn_keluaran_account),
				(_("PPN Masukan"), self.ppn_masukan_account),
			)
			if not value
		]
		if missing:
			frappe.msgprint(
				_("No account set for {0}. Returns will report zero until these are configured.").format(
					", ".join(missing)
				),
				indicator="orange",
				title=_("Incomplete setup"),
			)
