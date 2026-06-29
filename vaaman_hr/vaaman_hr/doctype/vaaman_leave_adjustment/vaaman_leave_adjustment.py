# Copyright (c) 2026, Pratul Tripathi and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt, formatdate, get_link_to_form, getdate

from hrms.hr.doctype.leave_application.leave_application import get_leave_balance_on


class VaamanLeaveAdjustment(Document):
	def validate(self):
		self.set_employee_details()
		self.validate_allocation()
		self.compute_adjustment()
		self.validate_non_zero_adjustment()

	def set_employee_details(self):
		if self.employee:
			details = frappe.db.get_value(
				"Employee", self.employee, ["employee_name", "company"], as_dict=True
			)
			if details:
				self.employee_name = details.employee_name
				if not self.company:
					self.company = details.company

	def validate_allocation(self):
		allocation = frappe.db.get_value(
			"Leave Allocation",
			self.leave_allocation,
			["name", "employee", "leave_type", "from_date", "to_date", "docstatus", "total_leaves_allocated"],
			as_dict=True,
		)
		if not allocation:
			frappe.throw(_("Please select a valid Leave Allocation"))

		if allocation.docstatus != 1:
			frappe.throw(_("Leave Allocation {0} is not submitted").format(self.leave_allocation))

		if allocation.employee != self.employee:
			frappe.throw(
				_("Leave Allocation {0} does not belong to employee {1}").format(
					self.leave_allocation, self.employee
				)
			)

		if allocation.leave_type != self.leave_type:
			frappe.throw(
				_("Leave Allocation {0} is not for Leave Type {1}").format(
					self.leave_allocation, self.leave_type
				)
			)

		if not (getdate(allocation.from_date) <= getdate(self.posting_date) <= getdate(allocation.to_date)):
			frappe.throw(
				_("Posting Date {0} must be within the allocation period {1} to {2}").format(
					frappe.bold(formatdate(self.posting_date)),
					frappe.bold(formatdate(allocation.from_date)),
					frappe.bold(formatdate(allocation.to_date)),
				)
			)

		self._allocation = allocation

	def compute_adjustment(self):
		precision = cint(frappe.db.get_single_value("System Settings", "float_precision")) or 3

		self.current_balance = flt(
			get_leave_balance_on(self.employee, self.leave_type, getdate(self.posting_date)),
			precision,
		)

		if self.adjustment_mode == "Set Opening Balance":
			self.net_adjustment = flt(flt(self.target_balance) - flt(self.current_balance), precision)
			self.leaves_after_adjustment = flt(self.target_balance, precision)
		else:
			self.net_adjustment = flt(self.leaves_to_adjust, precision)
			self.leaves_after_adjustment = flt(
				flt(self.current_balance) + flt(self.leaves_to_adjust), precision
			)

	def validate_non_zero_adjustment(self):
		if not self.net_adjustment:
			frappe.throw(
				_("Net adjustment is zero - current balance already matches the target. Nothing to change.")
			)

	def on_submit(self):
		allocation = frappe.get_doc("Leave Allocation", self.leave_allocation)
		self.create_adjustment_ledger_entry(allocation)
		self.sync_allocation_total(allocation, flt(self.net_adjustment))
		self.add_audit_comment(allocation)

	def on_cancel(self):
		self.delete_adjustment_ledger_entry()
		allocation_total = frappe.db.get_value(
			"Leave Allocation", self.leave_allocation, "total_leaves_allocated"
		)
		if allocation_total is not None:
			frappe.db.set_value(
				"Leave Allocation",
				self.leave_allocation,
				"total_leaves_allocated",
				flt(allocation_total) - flt(self.net_adjustment),
				update_modified=False,
			)
		self.add_audit_comment(None, reversed_entry=True)

	def create_adjustment_ledger_entry(self, allocation):
		"""Create a Leave Ledger Entry linked to the existing allocation.

		Using transaction_type="Leave Allocation" (instead of a new type) so that the
		entry is counted by every v15 balance function and report without patching hrms core.
		"""
		is_lwp = cint(frappe.db.get_value("Leave Type", self.leave_type, "is_lwp"))

		ledger = frappe.get_doc(
			{
				"doctype": "Leave Ledger Entry",
				"employee": self.employee,
				"employee_name": self.employee_name,
				"leave_type": self.leave_type,
				"transaction_type": "Leave Allocation",
				"transaction_name": allocation.name,
				"leaves": flt(self.net_adjustment),
				"from_date": self.posting_date,
				"to_date": allocation.to_date,
				"is_carry_forward": 0,
				"is_expired": 0,
				"is_lwp": is_lwp,
			}
		)
		ledger.flags.ignore_permissions = 1
		ledger.submit()
		self.db_set("ledger_entry", ledger.name)

	def delete_adjustment_ledger_entry(self):
		"""Remove the ledger entry created by this adjustment.

		Non-expired Leave Ledger Entries cannot be cancelled via the standard flow
		(it throws "Only expired allocation can be cancelled"), so we delete the row
		directly - the same low-level approach hrms itself uses in delete_ledger_entry.
		"""
		if self.ledger_entry and frappe.db.exists("Leave Ledger Entry", self.ledger_entry):
			frappe.db.delete("Leave Ledger Entry", {"name": self.ledger_entry})

	def sync_allocation_total(self, allocation, delta):
		"""Keep the allocation's stored total in sync (display only; balance is ledger-derived)."""
		frappe.db.set_value(
			"Leave Allocation",
			allocation.name,
			"total_leaves_allocated",
			flt(allocation.total_leaves_allocated) + flt(delta),
			update_modified=False,
		)

	def add_audit_comment(self, allocation, reversed_entry=False):
		alloc_name = self.leave_allocation
		if not alloc_name:
			return

		if reversed_entry:
			text = _("Leave adjustment {0} cancelled by {1}. Reversed {2} leaves.").format(
				get_link_to_form(self.doctype, self.name),
				frappe.session.user,
				frappe.bold(self.net_adjustment),
			)
		else:
			text = _("{0} leaves adjusted via {1} by {2}. Balance: {3} &rarr; {4}").format(
				frappe.bold(self.net_adjustment),
				get_link_to_form(self.doctype, self.name),
				frappe.session.user,
				frappe.bold(self.current_balance),
				frappe.bold(self.leaves_after_adjustment),
			)

		frappe.get_doc("Leave Allocation", alloc_name).add_comment(comment_type="Info", text=text)


@frappe.whitelist()
def get_allocation_and_balance(employee: str, leave_type: str, posting_date: str) -> dict:
	"""Return the active allocation covering posting_date and the current leave balance on that date."""
	allocation = frappe.db.get_value(
		"Leave Allocation",
		{
			"employee": employee,
			"leave_type": leave_type,
			"from_date": ["<=", posting_date],
			"to_date": [">=", posting_date],
			"docstatus": 1,
		},
		"name",
	)

	balance = get_leave_balance_on(employee, leave_type, getdate(posting_date))

	return {"allocation": allocation, "balance": flt(balance)}
