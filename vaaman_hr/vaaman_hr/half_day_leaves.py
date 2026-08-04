import frappe
from frappe.utils import add_days, flt, getdate, today

from vaaman_hr.vaaman_hr.head_office_policy import (
	compute_head_office_status,
	get_checkin_logs,
	get_policy_punch_times,
	is_exempt_from_head_office_policy,
	is_head_office_employee,
)

# Re-apply only for recent days so historical attendance is not rewritten
# when late checkins / sync catch up for old dates.
REAPPLY_LOOKBACK_DAYS = 7


def apply_attendance_status(doc, working_hours, status, half_day_status, logs, late_entry=0, early_exit=0):
	in_dt, out_dt = get_policy_punch_times(logs) if logs else (None, None)

	values = {
		"working_hours": flt(working_hours),
		"status": status,
		"half_day_status": half_day_status or "",
		"late_entry": late_entry,
		"early_exit": early_exit,
	}
	if in_dt:
		values["in_time"] = in_dt
	if out_dt:
		values["out_time"] = out_dt

	# Skip write if nothing changed (avoids noisy updates / recursive hooks)
	changed = False
	for field, value in values.items():
		current = doc.get(field)
		if field in ("working_hours",):
			if flt(current) != flt(value):
				changed = True
				break
		elif str(current or "") != str(value or ""):
			changed = True
			break
	if not changed:
		return

	frappe.db.set_value("Attendance", doc.name, values, update_modified=False)
	doc.working_hours = flt(working_hours)
	doc.status = status
	doc.half_day_status = half_day_status or ""
	doc.late_entry = late_entry
	doc.early_exit = early_exit


def _within_reapply_window(attendance_date):
	"""True for today and the last REAPPLY_LOOKBACK_DAYS — not older history."""
	return getdate(attendance_date) >= getdate(add_days(today(), -REAPPLY_LOOKBACK_DAYS))


def apply_head_office_policy_to_attendance(doc, method=None, *, enforce_lookback=False):
	"""Core HO policy apply. Used on Attendance insert/update and after checkin.

	enforce_lookback=True → only recent dates (checkin / on_update re-apply).
	enforce_lookback=False → after_insert of a new Attendance for any date (create-time only).
	"""
	if not doc or not doc.employee:
		return

	if not is_head_office_employee(doc.employee):
		return

	if frappe.flags.get("skip_head_office_attendance_validation"):
		return

	if frappe.flags.get("in_head_office_attendance_policy"):
		return

	if enforce_lookback and not _within_reapply_window(doc.attendance_date):
		return

	# Attendance Request or Leave Application — do not override
	if is_exempt_from_head_office_policy(doc):
		return

	if getattr(doc, "docstatus", None) == 2:
		return

	logs = get_checkin_logs(doc.employee, doc.attendance_date)
	if not logs:
		return

	working_hours, status, half_day_status, late_entry, early_exit = compute_head_office_status(
		doc.attendance_date, logs, doc.leave_application, employee=doc.employee
	)
	if status is None:
		return

	frappe.flags.in_head_office_attendance_policy = True
	try:
		apply_attendance_status(
			doc, working_hours, status, half_day_status, logs, late_entry, early_exit
		)
	finally:
		frappe.flags.in_head_office_attendance_policy = False


def validate_half_day_attendance(doc, method=None):
	"""Attendance after_insert — apply policy at create time (any date)."""
	apply_head_office_policy_to_attendance(doc, method, enforce_lookback=False)


def reapply_half_day_attendance_on_update(doc, method=None):
	"""Attendance on_update — re-apply only for recent dates (no old history rewrite)."""
	if doc.docstatus != 1:
		return
	apply_head_office_policy_to_attendance(doc, method, enforce_lookback=True)


def reapply_half_day_attendance_on_checkin(doc, method=None):
	"""Employee Checkin after_insert/on_update — refresh linked day's HO attendance.

	Shift auto-attendance often updates Attendance via db.set_value (no doc events).
	Checkin hooks catch punches that arrive after attendance was already created.
	Does not modify attendance older than REAPPLY_LOOKBACK_DAYS.
	"""
	if not doc.employee or not doc.time:
		return

	if frappe.flags.get("skip_head_office_attendance_validation"):
		return

	if not is_head_office_employee(doc.employee):
		return

	attendance_date = getdate(doc.time)
	if not _within_reapply_window(attendance_date):
		return

	att_name = frappe.db.exists(
		"Attendance",
		{
			"employee": doc.employee,
			"attendance_date": attendance_date,
			"docstatus": 1,
		},
	)
	if not att_name:
		return

	att = frappe.get_doc("Attendance", att_name)
	apply_head_office_policy_to_attendance(att, method, enforce_lookback=True)
