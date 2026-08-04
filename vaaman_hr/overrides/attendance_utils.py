"""Shared attendance helpers for Leave Application / Attendance Request overrides."""

import frappe
from frappe.utils import getdate


def relink_checkins_from_cancelled_attendance(employee, new_doc, date):
	"""Move punches onto a newly created Attendance.

	Handles both:
	- Leave cancel via db_set(docstatus=2) — checkins still point at cancelled Attendance
	- Proper Attendance.cancel() — checkins are unlinked (orphaned) for that date
	"""
	if not employee or not new_doc or not date:
		return

	date = getdate(date)
	day_start = f"{date} 00:00:00"
	day_end = f"{date} 23:59:59"

	cancelled = frappe.get_all(
		"Attendance",
		filters={
			"employee": employee,
			"attendance_date": date,
			"docstatus": 2,
			"name": ("!=", new_doc.name),
		},
		fields=["name", "in_time", "out_time", "working_hours"],
		order_by="modified desc",
		limit=1,
	)

	checkin_names = []
	in_time = out_time = working_hours = None

	if cancelled:
		old = cancelled[0]
		checkin_names = frappe.get_all(
			"Employee Checkin",
			filters={"attendance": old.name},
			pluck="name",
		)
		in_time = old.in_time
		out_time = old.out_time
		working_hours = old.working_hours

	# Orphaned checkins for the same day (after proper Attendance.cancel unlink)
	orphaned = frappe.get_all(
		"Employee Checkin",
		filters={
			"employee": employee,
			"time": ["between", [day_start, day_end]],
			"attendance": ["is", "not set"],
		},
		pluck="name",
		order_by="time asc",
	)
	for name in orphaned:
		if name not in checkin_names:
			checkin_names.append(name)

	if not checkin_names and not (in_time or out_time):
		return

	for checkin_name in checkin_names:
		frappe.db.set_value(
			"Employee Checkin", checkin_name, "attendance", new_doc.name, update_modified=False
		)

	values = {}
	if in_time and not new_doc.in_time:
		values["in_time"] = in_time
	if out_time and not new_doc.out_time:
		values["out_time"] = out_time
	if working_hours and not new_doc.working_hours:
		values["working_hours"] = working_hours

	# Punches for the other half → Present (leave half + worked half)
	if checkin_names or in_time or out_time:
		if new_doc.status == "Half Day":
			values["half_day_status"] = "Present"
			values["modify_half_day_status"] = 0

	if values:
		frappe.db.set_value("Attendance", new_doc.name, values, update_modified=False)
		new_doc.update(values)


def cancel_leave_attendance(employee, from_date, to_date, leave_application=None):
	"""Cancel On Leave / Half Day attendance for a leave — prefer proper cancel().

	Proper cancel runs Attendance.on_cancel → unlinks Employee Checkin.
	Falls back to docstatus=2 (keeps checkin link on cancelled doc) if cancel fails.
	"""
	filters = {
		"employee": employee,
		"attendance_date": ["between", [getdate(from_date), getdate(to_date)]],
		"docstatus": 1,
		"status": ["in", ["On Leave", "Half Day"]],
	}
	if leave_application:
		filters["leave_application"] = leave_application

	for name in frappe.get_all("Attendance", filters=filters, pluck="name"):
		doc = frappe.get_doc("Attendance", name)
		try:
			doc.flags.ignore_permissions = True
			frappe.flags.skip_head_office_attendance_validation = True
			doc.cancel()
		except Exception:
			frappe.log_error(
				title=f"Leave attendance cancel fallback: {name}",
				message=frappe.get_traceback(),
			)
			# Fallback: keep checkins linked on cancelled attendance for later relink
			frappe.db.set_value("Attendance", name, "docstatus", 2, update_modified=False)
		finally:
			frappe.flags.skip_head_office_attendance_validation = False
