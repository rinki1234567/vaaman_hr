import frappe
from frappe.utils import getdate
from collections import defaultdict

from vaaman_hr.vaaman_hr.head_office_policy import (
	ALLOWED_LATE_EARLY_PER_MONTH,
	check_late_entry_early_exit,
	get_violation_reason,
	has_approved_half_day_leave,
	head_office_branch_condition,
)


def sync_late_entry_flags(from_date="2026-04-01"):
	"""Set late_entry / early_exit on Head Office Present attendance from in/out times."""
	records = frappe.db.sql(
		f"""
		SELECT att.name, att.employee, att.attendance_date, att.in_time, att.out_time,
			att.late_entry, att.early_exit
		FROM `tabAttendance` att
		INNER JOIN `tabEmployee` e ON e.name = att.employee
		WHERE att.docstatus = 1
			AND att.attendance_date >= %(from_date)s
			AND {head_office_branch_condition("e")}
			AND att.status = 'Present'
			AND (att.attendance_request IS NULL OR att.attendance_request = '')
			AND att.in_time IS NOT NULL
			AND att.out_time IS NOT NULL
		""",
		{"from_date": from_date},
		as_dict=True,
	)

	updated = 0
	for record in records:
		if has_approved_half_day_leave(record.employee, record.attendance_date):
			late_entry, early_exit = 0, 0
		else:
			late_entry, early_exit = check_late_entry_early_exit(
				record.in_time, record.out_time, record.attendance_date
			)

		if (record.late_entry or 0) != late_entry or (record.early_exit or 0) != early_exit:
			frappe.db.set_value(
				"Attendance",
				record.name,
				{"late_entry": late_entry, "early_exit": early_exit},
				update_modified=False,
			)
			updated += 1

	if updated:
		frappe.db.commit()

	return updated


def process_attendance_policy():
	"""Daily job: sync flags, then mark 4th+ late/early in a month as Absent."""
	from_date = "2026-04-01"
	sync_late_entry_flags(from_date)

	attendance_records = frappe.db.sql(
		f"""
		SELECT att.name, att.employee, att.attendance_date, att.in_time, att.out_time,
			att.attendance_request
		FROM `tabAttendance` att
		INNER JOIN `tabEmployee` e ON e.name = att.employee
		WHERE att.status = 'Present'
			AND att.docstatus = 1
			AND att.attendance_date >= %(from_date)s
			AND {head_office_branch_condition("e")}
			AND (att.late_entry = 1 OR att.early_exit = 1)
			AND (att.attendance_request IS NULL OR att.attendance_request = '')
		ORDER BY att.employee, att.attendance_date
		""",
		{"from_date": from_date},
		as_dict=True,
	)

	# Exclude approved half-day leave dates
	attendance_records = [
		r
		for r in attendance_records
		if not has_approved_half_day_leave(r.employee, r.attendance_date)
	]

	employee_monthly_late = defaultdict(lambda: defaultdict(list))
	for record in attendance_records:
		date_obj = getdate(record.attendance_date)
		month_key = f"{date_obj.year}-{date_obj.month:02d}"
		employee_monthly_late[record.employee][month_key].append(record)

	for employee, month_data in employee_monthly_late.items():
		for month, records in month_data.items():
			if len(records) <= ALLOWED_LATE_EARLY_PER_MONTH:
				continue

			for record in records[ALLOWED_LATE_EARLY_PER_MONTH:]:
				if record.attendance_request:
					continue

				try:
					reason = get_violation_reason(
						record.in_time, record.out_time, record.attendance_date
					)
					if not reason:
						continue

					original = frappe.get_doc("Attendance", record.name)
					if original.docstatus == 1:
						original.cancel()

					amended_att = frappe.copy_doc(original)
					amended_att.docstatus = 1
					amended_att.status = "Absent"
					amended_att.late_entry = 0
					amended_att.early_exit = 0
					amended_att.amended_from = original.name
					amended_att.save(ignore_permissions=True)

					amended_att.add_comment(
						"Comment",
						f"Marked as Absent due to {ALLOWED_LATE_EARLY_PER_MONTH + 1}th or subsequent "
						f"late/early violation ({reason}) in {month}.",
					)

					frappe.get_doc(
						{
							"doctype": "Attendance Policy Log",
							"employee": original.employee,
							"attendance": amended_att.name,
							"attendance_date": amended_att.attendance_date,
							"action_taken": "Converted to Absent",
							"remarks": (
								f"Exceeded {ALLOWED_LATE_EARLY_PER_MONTH} allowed late/early occasions. "
								f"Violation: {reason}"
							),
						}
					).insert(ignore_permissions=True)

					user_id = frappe.db.get_value("Employee", employee, "user_id")
					if user_id:
						frappe.sendmail(
							recipients=[user_id],
							subject="Attendance Policy Violation",
							message=(
								f"You have been marked as Absent on {amended_att.attendance_date} "
								f"for exceeding {ALLOWED_LATE_EARLY_PER_MONTH} allowed occasions of "
								f"late entry or early leaving in {month}."
							),
						)

					frappe.db.commit()

				except Exception as e:
					frappe.log_error(
						f"Failed to amend attendance for {employee} on {record.attendance_date}: {e!s}",
						"Attendance Policy",
					)
