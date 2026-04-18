import datetime

import frappe
from frappe.utils import getdate, today


def process_holiday_sandwich_policy():
	"""
	Runs daily at 11 PM via scheduler (0 23 * * *).

	Scans every day from day 3 to today in the current month for every active employee.
	Sandwich condition:
		D-2 = Absent
		D-1 = Holiday or Weekly Off   ← gets cancelled and replaced with Absent
		D   = Absent
	"""
	# Run as Administrator so cancel + insert have full permissions
	frappe.set_user("Administrator")

	today_date = getdate(today())
	year = today_date.year
	month = today_date.month
	today_day = today_date.day

	# Need at least 3 days in the month to find a sandwich
	if today_day < 3:
		return

	from_date = datetime.date(year, month, 1)
	to_date = today_date

	# Step 1: Get all active employees
	employees = frappe.db.get_all(
		"Employee",
		filters={"status": "Active"},
		fields=["name", "user_id", "company", "branch"]
	)

	if not employees:
		return

	employee_names = [e.name for e in employees]
	employee_map = {e.name: e for e in employees}

	# Step 2: Fetch ALL submitted attendance records for the month
	#         for ALL employees in one single query
	attendance_records = frappe.db.get_all(
		"Attendance",
		filters={
			"employee": ["in", employee_names],
			"attendance_date": ["between", [str(from_date), str(to_date)]],
			"docstatus": 1
		},
		fields=["name", "employee", "attendance_date", "status", "company", "custom_branch"]
	)

	# Build map: { employee -> { day_number -> record } }
	att_map = {}
	for rec in attendance_records:
		day = getdate(rec.attendance_date).day
		att_map.setdefault(rec.employee, {})[day] = rec

	# Step 3: For each employee scan day 3 → today
	for emp_name, employee in employee_map.items():
		emp_att = att_map.get(emp_name, {})

		for d in range(3, today_day + 1):
			d1 = d - 1  # the holiday / weekly off day
			d2 = d - 2  # must be absent

			# Check 1 — D is Absent
			rec_d = emp_att.get(d)
			if not rec_d or rec_d.status != "Absent":
				continue

			# Check 2 — D-1 is Holiday or Weekly Off
			rec_d1 = emp_att.get(d1)
			if not rec_d1 or rec_d1.status not in ["Holiday", "Weekly Off"]:
				continue

			# Check 3 — D-2 is Absent
			rec_d2 = emp_att.get(d2)
			if not rec_d2 or rec_d2.status != "Absent":
				continue

			# All 3 conditions passed
			original_status = rec_d1.status
			holiday_date    = datetime.date(year, month, d1)
			day_before_date = datetime.date(year, month, d2)
			day_after_date  = datetime.date(year, month, d)

			try:
				# Cancel the existing Holiday / Weekly Off attendance record
				existing_doc = frappe.get_doc("Attendance", rec_d1.name)
				existing_doc.flags.ignore_permissions = True
				existing_doc.cancel()
				frappe.db.commit()

				# Create a new Absent attendance for D-1
				att = frappe.new_doc("Attendance")
				att.employee        = emp_name
				att.attendance_date = holiday_date
				att.status          = "Absent"
				att.company         = rec_d1.company or employee.company
				att.custom_branch   = rec_d1.custom_branch or employee.branch
				att.flags.ignore_permissions = True
				att.insert()
				att.submit()
				frappe.db.commit()

				# Log to Attendance Policy Log
				frappe.get_doc({
					"doctype": "Attendance Policy Log",
					"employee": emp_name,
					"attendance": att.name,
					"attendance_date": str(holiday_date),
					"action_taken": f"Marked as Absent ({original_status} Sandwich)",
					"remarks": (
						f"{original_status} on {holiday_date} marked as Absent — "
						f"employee was absent on {day_before_date} (day before) "
						f"and {day_after_date} (day after)."
					)
				}).insert(ignore_permissions=True)
				frappe.db.commit()

				# Notify employee
				if employee.user_id:
					frappe.sendmail(
						recipients=[employee.user_id],
						subject="Attendance Policy — Holiday/Weekly Off Marked as Absent",
						message=(
							f"Your {original_status} on {holiday_date} has been marked as Absent "
							f"because you were absent on {day_before_date} (day before) "
							f"and {day_after_date} (day after), "
							f"as per the company attendance policy."
						)
					)

				# Update att_map so cascading sandwiches in the same run are also detected
				# e.g. Absent → Holiday → Weekly Off → Absent across 4 days
				emp_att[d1] = frappe._dict({
					"name": att.name,
					"status": "Absent",
					"company": att.company,
					"custom_branch": att.custom_branch
				})

			except Exception as e:
				frappe.log_error(
					frappe.get_traceback(),
					f"Holiday Sandwich Policy — {emp_name} on {holiday_date}"
				)


def debug_holiday_sandwich():
	"""
	Read-only debug — prints what the sandwich policy will do for the current month so far.
	Usage: bench --site [site] execute vaaman_hr.holiday_policy.debug_holiday_sandwich
	"""
	today_date = getdate(today())
	year = today_date.year
	month = today_date.month
	today_day = today_date.day

	print(f"\n=== Holiday Sandwich Debug — {year}-{month:02d} (up to day {today_day}) ===\n")

	if today_day < 3:
		print("Not enough days in month to check. Exiting.")
		return

	from_date = datetime.date(year, month, 1)
	to_date = today_date

	employees = frappe.db.get_all(
		"Employee",
		filters={"status": "Active"},
		fields=["name", "user_id", "company", "branch"]
	)

	print(f"Total active employees: {len(employees)}\n")

	employee_names = [e.name for e in employees]
	employee_map = {e.name: e for e in employees}

	attendance_records = frappe.db.get_all(
		"Attendance",
		filters={
			"employee": ["in", employee_names],
			"attendance_date": ["between", [str(from_date), str(to_date)]],
			"docstatus": 1
		},
		fields=["name", "employee", "attendance_date", "status"]
	)

	att_map = {}
	for rec in attendance_records:
		day = getdate(rec.attendance_date).day
		att_map.setdefault(rec.employee, {})[day] = rec

	matched = 0
	for emp_name in employee_names:
		emp_att = att_map.get(emp_name, {})

		for d in range(3, today_day + 1):
			d1 = d - 1
			d2 = d - 2

			rec_d  = emp_att.get(d)
			rec_d1 = emp_att.get(d1)
			rec_d2 = emp_att.get(d2)

			if (
				rec_d  and rec_d.status  == "Absent"
				and rec_d1 and rec_d1.status in ["Holiday", "Weekly Off"]
				and rec_d2 and rec_d2.status == "Absent"
			):
				print(
					f"Employee {emp_name} | "
					f"Day {d2} Absent → Day {d1} {rec_d1.status} → Day {d} Absent"
					f"  >>> WILL BE MARKED ABSENT <<<"
				)
				matched += 1

	print(f"\n=== Total sandwiches found: {matched} ===")
