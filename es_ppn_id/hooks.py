# Copyright (c) 2026, Enterprise Systems Australia and contributors
from . import __version__ as app_version  # noqa

app_name = "es_ppn_id"
app_title = "ES PPN ID"
app_publisher = "Enterprise Systems Australia"
app_description = (
	"Indonesian bank statement import, automated payment matching and reconciliation, "
	"PPN treatment and SPT Masa PPN reporting for ERPNext."
)
app_email = "support@enterprisesystems.com.au"
app_license = "MIT"
required_apps = ["erpnext"]

# Custom fields and shipped defaults are installed after install/migrate.
after_install = "es_ppn_id.install.after_install"
after_migrate = "es_ppn_id.install.after_migrate"

_LOCK = "es_ppn_id.es_ppn_id.doctype.es_ppn_return.es_ppn_return.validate_period_not_locked"

doc_events = {
	"Bank Transaction": {
		"after_insert": "es_ppn_id.events.bank_transaction.after_insert",
	},
	# Once an SPT Masa PPN is submitted, its month is closed to new postings.
	# Amending the return reopens it.
	"Journal Entry": {"before_submit": _LOCK, "before_cancel": _LOCK},
	"Purchase Invoice": {"before_submit": _LOCK, "before_cancel": _LOCK},
	"Sales Invoice": {"before_submit": _LOCK, "before_cancel": _LOCK},
	"Payment Entry": {"before_submit": _LOCK, "before_cancel": _LOCK},
}

scheduler_events = {
	"daily": [
		"es_ppn_id.tasks.retry_failed_postings",
		"es_ppn_id.tasks.match_open_transactions",
	],
	"cron": {
		# Indonesian monthly deadlines: PPh payment on the 15th, unified
		# PPh return on the 20th, SPT Masa PPN by the end of the month.
		"0 6 * * *": ["es_ppn_id.tasks.flag_due_ppn_periods"],
	},
}

override_whitelisted_methods = {}

fixtures = [
	{"dt": "ES PPN Treatment"},
]
