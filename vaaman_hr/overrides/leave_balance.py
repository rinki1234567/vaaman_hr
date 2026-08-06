"""Overrides for HRMS leave balance APIs — Comp Off uses FIFO unused-only expiry."""

import frappe
from frappe.utils import cint, flt, getdate

from hrms.hr.doctype.leave_application import leave_application as hrms_leave_application
from hrms.hr.doctype.leave_application.leave_application import (
	get_leave_allocation_records,
	get_leave_approver,
	get_leaves_for_period,
	get_leaves_pending_approval_for_period,
	validate_leave_access,
)

from vaaman_hr.overrides.comp_off_balance import (
	get_comp_off_balance_on,
	get_comp_off_leave_details_bucket,
	is_compensatory_off,
)

# Keep a reference to the original HRMS implementations
_original_get_leave_balance_on = hrms_leave_application.get_leave_balance_on
_original_get_leave_details = hrms_leave_application.get_leave_details


@frappe.whitelist()
def get_leave_balance_on(
	employee: str,
	leave_type: str,
	date,
	to_date=None,
	consider_all_leaves_in_the_allocation_period: bool = False,
	for_consumption: bool = False,
):
	validate_leave_access(employee)

	if is_compensatory_off(leave_type):
		return get_comp_off_balance_on(
			employee,
			date,
			to_date=to_date,
			for_consumption=cint(for_consumption),
		)

	return _original_get_leave_balance_on(
		employee,
		leave_type,
		date,
		to_date=to_date,
		consider_all_leaves_in_the_allocation_period=consider_all_leaves_in_the_allocation_period,
		for_consumption=for_consumption,
	)


@frappe.whitelist()
def get_leave_details(employee: str, date, for_salary_slip: bool = False) -> dict:
	validate_leave_access(employee)

	allocation_records = get_leave_allocation_records(employee, date)
	leave_allocation = {}
	precision = cint(frappe.db.get_single_value("System Settings", "float_precision")) or 2
	date = getdate(date)

	for leave_type in allocation_records:
		if is_compensatory_off(leave_type):
			leave_allocation[leave_type] = get_comp_off_leave_details_bucket(employee, date)
			continue

		allocation = allocation_records.get(leave_type, frappe._dict())
		to_date = date if for_salary_slip else allocation.to_date
		remaining_leaves = get_leave_balance_on(
			employee,
			leave_type,
			date,
			to_date=to_date,
			consider_all_leaves_in_the_allocation_period=False if for_salary_slip else True,
		)

		leaves_taken = get_leaves_for_period(employee, leave_type, allocation.from_date, to_date) * -1
		leaves_pending = get_leaves_pending_approval_for_period(
			employee, leave_type, allocation.from_date, to_date
		)
		expired_leaves = allocation.total_leaves_allocated - (remaining_leaves + leaves_taken)

		leave_allocation[leave_type] = {
			"total_leaves": flt(allocation.total_leaves_allocated, precision),
			"expired_leaves": flt(expired_leaves, precision) if expired_leaves > 0 else 0,
			"leaves_taken": flt(leaves_taken, precision),
			"leaves_pending_approval": flt(leaves_pending, precision),
			"remaining_leaves": flt(remaining_leaves, precision),
		}

	# Comp Off may have balance even when HRMS allocation_records is empty for the date
	if "Compensatory Off" not in leave_allocation and frappe.db.exists(
		"Leave Ledger Entry",
		{"employee": employee, "leave_type": "Compensatory Off", "docstatus": 1},
	):
		bucket = get_comp_off_leave_details_bucket(employee, date)
		if bucket.get("remaining_leaves") or bucket.get("total_leaves") or bucket.get("leaves_taken"):
			leave_allocation["Compensatory Off"] = bucket

	lwp = frappe.get_list("Leave Type", filters={"is_lwp": 1}, pluck="name")

	return {
		"leave_allocation": leave_allocation,
		"leave_approver": get_leave_approver(employee),
		"lwps": lwp,
	}


def apply_comp_off_balance_patch():
	"""Monkeypatch HRMS leave balance helpers so internal callers also use FIFO Comp Off."""
	if getattr(hrms_leave_application, "_vaaman_comp_off_balance_patched", False):
		return

	# Preserve true originals even if this module is reloaded after a prior patch
	if not getattr(hrms_leave_application, "_vaaman_original_get_leave_balance_on", None):
		hrms_leave_application._vaaman_original_get_leave_balance_on = (
			hrms_leave_application.get_leave_balance_on
		)
		hrms_leave_application._vaaman_original_get_leave_details = (
			hrms_leave_application.get_leave_details
		)

	global _original_get_leave_balance_on, _original_get_leave_details
	_original_get_leave_balance_on = hrms_leave_application._vaaman_original_get_leave_balance_on
	_original_get_leave_details = hrms_leave_application._vaaman_original_get_leave_details

	hrms_leave_application.get_leave_balance_on = get_leave_balance_on
	hrms_leave_application.get_leave_details = get_leave_details
	hrms_leave_application._vaaman_comp_off_balance_patched = True
