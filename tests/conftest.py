"""
Test bootstrap.

The pure calculation and parsing helpers do not need a bench: they take
numbers and strings and give numbers back. A minimal `frappe` stub lets
them be tested in CI without a database, which is where the arithmetic
that matters most is worth pinning down.

Anything that touches the ORM belongs in a bench-side FrappeTestCase, not
here, and is deliberately not stubbed.
"""

import sys
import types

if "frappe" not in sys.modules:
	frappe = types.ModuleType("frappe")

	class _ValidationError(Exception):
		pass

	def _throw(message, *args, **kwargs):
		raise _ValidationError(message)

	frappe.ValidationError = _ValidationError
	frappe.throw = _throw
	frappe.bold = lambda value: str(value)
	frappe.log_error = lambda *args, **kwargs: None
	frappe._ = lambda text: text

	utils = types.ModuleType("frappe.utils")
	utils.cint = lambda value, default=0: int(float(value)) if str(value or "").strip() else default
	utils.flt = lambda value, precision=None: float(value or 0)
	frappe.utils = utils

	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils
