import frappe
from frappe.utils import flt

from vaaman_hr.vaaman_hr.head_office_policy import (
	calc_working_hours,
	compute_head_office_status,
	get_checkin_logs,
	is_exempt_from_head_office_policy,
	is_head_office_employee,
)


def apply_attendance_status(doc, working_hours, status, half_day_status, logs, late_entry=0, early_exit=0):
	in_dt = logs[0].time if logs else None
	out_dt = logs[-1].time if logs else None

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

	frappe.db.set_value("Attendance", doc.name, values, update_modified=False)
	doc.working_hours = flt(working_hours)
	doc.status = status
	doc.half_day_status = half_day_status or ""
	doc.late_entry = late_entry
	doc.early_exit = early_exit


def validate_half_day_attendance(doc, method=None):
	if not is_head_office_employee(doc.employee):
		return

	# Attendance Request (e.g. On Duty → Present) must not be overridden by punch logic
	if is_exempt_from_head_office_policy(doc):
		return

	if doc.leave_application:
		logs = get_checkin_logs(doc.employee, doc.attendance_date)
		values = {"late_entry": 0, "early_exit": 0}
		if logs:
			values["working_hours"] = calc_working_hours(logs)
			doc.working_hours = values["working_hours"]
		frappe.db.set_value("Attendance", doc.name, values, update_modified=False)
		doc.late_entry = 0
		doc.early_exit = 0
		return

	logs = get_checkin_logs(doc.employee, doc.attendance_date)
	if not logs:
		return

	working_hours, status, half_day_status, late_entry, early_exit = compute_head_office_status(
		doc.attendance_date, logs, doc.leave_application
	)
	if status is None:
		return

	apply_attendance_status(
		doc, working_hours, status, half_day_status, logs, late_entry, early_exit
	)
