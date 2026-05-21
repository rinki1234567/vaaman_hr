import frappe
from frappe.utils import flt, today

from vaaman_hr.vaaman_hr.head_office_policy import (
	HEAD_OFFICE_BRANCH,
	calc_working_hours,
	compute_head_office_status,
	get_checkin_logs,
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


@frappe.whitelist()
def recalculate_head_office_attendance(from_date=None, to_date=None, dry_run=False):
	"""Recalculate Head Office attendance status from check-in logs."""
	from_date = from_date or "2026-01-01"
	to_date = to_date or today()
	dry_run = frappe.parse_json(dry_run) if isinstance(dry_run, str) else dry_run

	attendance_names = frappe.db.sql(
		"""
		SELECT a.name
		FROM `tabAttendance` a
		INNER JOIN `tabEmployee` e ON e.name = a.employee
		WHERE e.branch = %(branch)s
			AND a.docstatus = 1
			AND a.attendance_date BETWEEN %(from_date)s AND %(to_date)s
			AND a.status NOT IN ('On Leave', 'Holiday', 'Weekly Off', 'Work From Home')
			AND (a.leave_application IS NULL OR a.leave_application = '')
		ORDER BY a.attendance_date
		""",
		{"branch": HEAD_OFFICE_BRANCH, "from_date": from_date, "to_date": to_date},
		pluck=True,
	)

	updated = 0
	skipped = 0
	changes = []

	for name in attendance_names:
		att = frappe.get_doc("Attendance", name)
		logs = get_checkin_logs(att.employee, att.attendance_date)
		if not logs:
			skipped += 1
			continue

		working_hours, status, half_day_status, late_entry, early_exit = compute_head_office_status(
			att.attendance_date, logs
		)
		if status is None:
			skipped += 1
			continue

		old = (
			att.status,
			att.half_day_status or "",
			flt(att.working_hours),
			att.late_entry or 0,
			att.early_exit or 0,
		)
		new = (status, half_day_status or "", flt(working_hours), late_entry, early_exit)
		if old != new:
			changes.append({"name": name, "date": str(att.attendance_date), "old": old, "new": new})
			if not dry_run:
				apply_attendance_status(
					att, working_hours, status, half_day_status, logs, late_entry, early_exit
				)
			updated += 1

	if not dry_run:
		frappe.db.commit()

	return {
		"from_date": from_date,
		"to_date": to_date,
		"scanned": len(attendance_names),
		"updated": updated,
		"skipped": skipped,
		"dry_run": dry_run,
		"sample_changes": changes[:25],
	}
