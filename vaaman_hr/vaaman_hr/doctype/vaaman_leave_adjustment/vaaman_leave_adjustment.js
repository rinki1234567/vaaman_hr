// Copyright (c) 2026, Pratul Tripathi and contributors
// For license information, please see license.txt

frappe.ui.form.on("Vaaman Leave Adjustment", {
	onload: function (frm) {
		if (!frm.doc.posting_date) {
			frm.set_value("posting_date", frappe.datetime.get_today());
		}
	},

	refresh: function (frm) {
		frm.set_query("employee", function () {
			return { query: "erpnext.controllers.queries.employee_query" };
		});

		frm.set_query("leave_type", function () {
			return { filters: { is_lwp: 0 } };
		});

		frm.set_query("leave_allocation", function () {
			return {
				filters: {
					employee: frm.doc.employee,
					leave_type: frm.doc.leave_type,
					docstatus: 1,
				},
			};
		});
	},

	employee: function (frm) {
		frm.trigger("fetch_allocation_and_balance");
	},

	leave_type: function (frm) {
		frm.set_value("leave_allocation", null);
		frm.trigger("fetch_allocation_and_balance");
	},

	posting_date: function (frm) {
		frm.trigger("fetch_allocation_and_balance");
	},

	leave_allocation: function (frm) {
		frm.trigger("fetch_allocation_and_balance");
	},

	fetch_allocation_and_balance: function (frm) {
		if (!(frm.doc.employee && frm.doc.leave_type && frm.doc.posting_date)) {
			return;
		}
		frappe.call({
			method: "vaaman_hr.vaaman_hr.doctype.vaaman_leave_adjustment.vaaman_leave_adjustment.get_allocation_and_balance",
			args: {
				employee: frm.doc.employee,
				leave_type: frm.doc.leave_type,
				posting_date: frm.doc.posting_date,
			},
			callback: function (r) {
				if (!r.message) return;
				if (!frm.doc.leave_allocation && r.message.allocation) {
					frm.set_value("leave_allocation", r.message.allocation);
				}
				frm.set_value("current_balance", r.message.balance);
				frm.trigger("recompute");
			},
		});
	},

	adjustment_mode: function (frm) {
		frm.trigger("recompute");
	},

	target_balance: function (frm) {
		frm.trigger("recompute");
	},

	leaves_to_adjust: function (frm) {
		frm.trigger("recompute");
	},

	recompute: function (frm) {
		const current = flt(frm.doc.current_balance);
		if (frm.doc.adjustment_mode === "Set Opening Balance") {
			frm.set_value("net_adjustment", flt(flt(frm.doc.target_balance) - current));
			frm.set_value("leaves_after_adjustment", flt(frm.doc.target_balance));
		} else {
			frm.set_value("net_adjustment", flt(frm.doc.leaves_to_adjust));
			frm.set_value("leaves_after_adjustment", flt(current + flt(frm.doc.leaves_to_adjust)));
		}
	},
});
