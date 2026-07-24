// Copyright (c) 2026, Pratul Tripathi and contributors
// For license information, please see license.txt

frappe.ui.form.on("Leave Policy Change", {
	onload(frm) {
		if (!frm.doc.change_date) {
			frm.set_value("change_date", frappe.datetime.get_today());
		}
	},

	refresh(frm) {
		frm.set_query("employee", () => ({
			query: "erpnext.controllers.queries.employee_query",
		}));

		if (frm.doc.docstatus === 1 && frm.doc.new_leave_policy_assignment) {
			frm.add_custom_button(__("New Leave Policy Assignment"), () => {
				frappe.set_route("Form", "Leave Policy Assignment", frm.doc.new_leave_policy_assignment);
			}, __("View"));
		}
		if (frm.doc.docstatus === 1 && frm.doc.old_leave_policy_assignment) {
			frm.add_custom_button(__("Old Leave Policy Assignment"), () => {
				frappe.set_route("Form", "Leave Policy Assignment", frm.doc.old_leave_policy_assignment);
			}, __("View"));
		}
	},

	employee(frm) {
		frm.trigger("load_preview");
	},

	change_date(frm) {
		frm.trigger("load_preview");
	},

	new_employment_type(frm) {
		frm.trigger("load_preview");
	},

	load_preview(frm) {
		if (!(frm.doc.employee && frm.doc.change_date && frm.doc.new_employment_type)) {
			return;
		}

		frappe.call({
			method: "vaaman_hr.vaaman_hr.doctype.leave_policy_change.leave_policy_change.get_change_preview",
			args: {
				employee: frm.doc.employee,
				change_date: frm.doc.change_date,
				new_employment_type: frm.doc.new_employment_type,
			},
			callback(r) {
				if (!r.message) return;
				const d = r.message;

				frm.set_value("employee_name", d.employee_name);
				frm.set_value("company", d.company);
				frm.set_value("branch", d.branch);
				frm.set_value("old_employment_type", d.old_employment_type);
				frm.set_value("staff_worker", d.staff_worker);
				frm.set_value("old_leave_policy_assignment", d.old_leave_policy_assignment);
				frm.set_value("old_leave_policy", d.old_leave_policy);
				frm.set_value("old_effective_from", d.old_effective_from);
				frm.set_value("old_effective_to", d.old_effective_to);
				frm.set_value("old_end_date", d.old_end_date);
				frm.set_value("new_effective_from", d.new_effective_from);
				frm.set_value("new_effective_to", d.new_effective_to);

				if (d.new_leave_policy) {
					frm.set_value("new_leave_policy", d.new_leave_policy);
					if (d.old_leave_policy && d.new_leave_policy === d.old_leave_policy) {
						frappe.msgprint({
							title: __("Same policy mapped"),
							message: __(
								"Master Leave Policy Assignment returns the same policy as the current one. Override <b>New Leave Policy</b> if the employee should move to a different policy (e.g. Probation)."
							),
							indicator: "orange",
						});
					} else if (
						frm.doc.new_employment_type &&
						d.new_leave_policy_title &&
						!(d.new_leave_policy_title || "")
							.toLowerCase()
							.includes((frm.doc.new_employment_type || "").toLowerCase())
					) {
						frappe.show_alert({
							message: __(
								"Master mapped policy is {0}. Confirm this is correct for {1}, or override New Leave Policy.",
								[d.new_leave_policy_title, frm.doc.new_employment_type]
							),
							indicator: "orange",
						});
					}
				} else {
					frm.set_value("new_leave_policy", null);
					frappe.show_alert({
						message: __(
							"No Master Leave Policy Assignment found for this Branch / Employment Type / Staff-Worker. Select New Leave Policy manually."
						),
						indicator: "orange",
					});
				}
			},
		});
	},
});
