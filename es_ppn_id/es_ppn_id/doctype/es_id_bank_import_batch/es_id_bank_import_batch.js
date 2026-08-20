// Copyright (c) 2026, Enterprise Systems Australia and contributors
frappe.ui.form.on("ES ID Bank Import Batch", {
	refresh(frm) {
		if (frm.doc.docstatus === 0 && frm.doc.import_file) {
			frm.add_custom_button(__("Parse Statement"), () => {
				frm.call({ doc: frm.doc, method: "parse_statement", freeze: true,
					freeze_message: __("Reading the statement...") })
					.then((r) => {
						if (!r.message) return;
						frappe.show_alert({
							message: __("Read {0} rows as {1}. {2} duplicates, {3} unmatched.", [
								r.message.total, r.message.format,
								r.message.duplicates, r.message.unmatched,
							]),
							indicator: "green",
						});
						frm.refresh();
					});
			});
		}

		if (frm.doc.docstatus === 1) {
			frm.add_custom_button(__("Find Payment Matches"), () => {
				frm.call({ doc: frm.doc, method: "find_payment_matches", freeze: true,
					freeze_message: __("Looking for invoices behind these lines...") })
					.then(() => frm.refresh());
			}, __("Actions"));

			frm.add_custom_button(__("Post Vouchers"), () => {
				frappe.confirm(
					__("Settle matched payments and post coded lines to the ledger?"),
					() => frm.call({ doc: frm.doc, method: "post_vouchers", args: { enqueue: true } })
						.then(() => frm.refresh()),
				);
			}, __("Actions"));

			frm.page.set_inner_btn_group_as_primary(__("Actions"));
		}

		if (frm.doc.unmatched_rows) {
			frm.dashboard.add_comment(
				__("{0} lines matched no rule and no invoice. Write a rule for the recurring ones and the queue shrinks each month.",
					[frm.doc.unmatched_rows]),
				"orange", true,
			);
		}
	},

	bank_format(frm) {
		if (frm.doc.bank_format === "BCA" && !frm.doc.statement_year) {
			frm.set_value("statement_year", new Date().getFullYear());
			frappe.show_alert({
				message: __("BCA statements omit the year, so Statement Year has been set for you. Change it if this is an older statement."),
				indicator: "blue",
			});
		}
	},
});
