import frappe
from frappe.model.meta import get_field_precision
from frappe.utils import add_months, flt, get_first_day, getdate

from hrms.hr.doctype.leave_policy_assignment.leave_policy_assignment import (
	LeavePolicyAssignment,
)

PRORATED_LEAVE_TYPES = frozenset({"Casual Leave", "Sick Leave"})


def get_eligibility_start_date(date_of_joining):
	"""First eligible month per Vaaman policy (aligned with leave application server script)."""
	doj = getdate(date_of_joining)
	if doj.day <= 15:
		return get_first_day(doj)
	return get_first_day(add_months(doj, 1))


def get_months_in_period(from_date, to_date):
	from_date = getdate(from_date)
	to_date = getdate(to_date)
	return (to_date.year - from_date.year) * 12 + (to_date.month - from_date.month) + 1


def calculate_monthly_prorated_leaves(annual_allocation, from_date, to_date):
	"""annual_allocation / 12 * months in assignment period."""
	annual_allocation = flt(annual_allocation)
	if not annual_allocation:
		return 0

	months = get_months_in_period(from_date, to_date)
	if months >= 12:
		return annual_allocation

	return annual_allocation / 12.0 * months


class CustomLeavePolicyAssignment(LeavePolicyAssignment):
	def set_dates(self):
		super().set_dates()

		if self.assignment_based_on != "Joining Date" or not self.employee:
			return

		date_of_joining = frappe.db.get_value("Employee", self.employee, "date_of_joining")
		if not date_of_joining:
			return

		self.effective_from = get_eligibility_start_date(date_of_joining)

	def get_new_leaves(self, annual_allocation, leave_details, date_of_joining):
		if leave_details.name in PRORATED_LEAVE_TYPES:
			precision = get_field_precision(
				frappe.get_meta("Leave Allocation").get_field("new_leaves_allocated")
			)
			new_leaves_allocated = calculate_monthly_prorated_leaves(
				annual_allocation,
				self.effective_from,
				self.effective_to,
			)
			new_leaves_allocated = min(new_leaves_allocated, flt(annual_allocation))
			return flt(new_leaves_allocated, precision)

		return super().get_new_leaves(annual_allocation, leave_details, date_of_joining)
