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
		fields=["name", "user_id", "company", "branch", "holiday_list"]
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

	# Build holiday map from Holiday List: { holiday_list_name -> { day -> "Holiday"/"Weekly Off" } }
	default_holiday_lists = {}  # cache per company
	holiday_list_day_map = {}   # { list_name -> { day_number -> status } }

	def get_holiday_status_from_list(holiday_list_name):
		if holiday_list_name in holiday_list_day_map:
			return holiday_list_day_map[holiday_list_name]
		rows = frappe.db.get_all(
			"Holiday",
			filters={
				"parent": holiday_list_name,
				"holiday_date": ["between", [str(from_date), str(to_date)]]
			},
			fields=["holiday_date", "weekly_off"]
		)
		day_map = {}
		for r in rows:
			d_num = getdate(r.holiday_date).day
			day_map[d_num] = "Weekly Off" if r.weekly_off else "Holiday"
		holiday_list_day_map[holiday_list_name] = day_map
		return day_map

	def get_d1_status_from_holiday_list(emp_name, day_num):
		"""Returns 'Holiday', 'Weekly Off', or None by checking employee's holiday list."""
		emp = employee_map[emp_name]
		hl = emp.holiday_list
		if not hl:
			company = emp.company
			if company not in default_holiday_lists:
				default_holiday_lists[company] = frappe.get_cached_value(
					"Company", company, "default_holiday_list"
				)
			hl = default_holiday_lists[company]
		if not hl:
			return None
		return get_holiday_status_from_list(hl).get(day_num)

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
			# First check attendance record, then fall back to holiday list
			rec_d1 = emp_att.get(d1)
			if rec_d1 and rec_d1.status in ["Holiday", "Weekly Off"]:
				original_status = rec_d1.status
			elif not rec_d1:
				hl_status = get_d1_status_from_holiday_list(emp_name, d1)
				if not hl_status:
					continue
				original_status = hl_status
				rec_d1 = None  # no attendance record exists — will create directly
			else:
				continue

			# Check 3 — D-2 is Absent
			rec_d2 = emp_att.get(d2)
			if not rec_d2 or rec_d2.status != "Absent":
				continue

			# All 3 conditions passed
			holiday_date    = datetime.date(year, month, d1)
			day_before_date = datetime.date(year, month, d2)
			day_after_date  = datetime.date(year, month, d)

			try:
				if rec_d1 and rec_d1.get("name"):
					# Attendance record exists — directly override status in DB (no cancel/create)
					frappe.db.set_value("Attendance", rec_d1.name, "status", "Absent")
					frappe.db.commit()
					att_name = rec_d1.name
				else:
					# No attendance record — Holiday/WO comes from Holiday List, create fresh Absent
					att = frappe.new_doc("Attendance")
					att.employee        = emp_name
					att.attendance_date = holiday_date
					att.status          = "Absent"
					att.company         = employee.company
					att.custom_branch   = employee.branch
					att.flags.ignore_permissions = True
					att.insert()
					att.submit()
					frappe.db.commit()
					att_name = att.name

				# Log to Attendance Policy Log
				frappe.get_doc({
					"doctype": "Attendance Policy Log",
					"employee": emp_name,
					"attendance": att_name,
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
				emp_att[d1] = frappe._dict({
					"name": att_name,
					"status": "Absent",
					"company": (rec_d1.company if rec_d1 else None) or employee.company,
					"custom_branch": (rec_d1.custom_branch if rec_d1 else None) or employee.branch
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
		fields=["name", "user_id", "company", "branch", "holiday_list"]
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

	# Build holiday-list day map for fallback
	default_holiday_lists = {}
	holiday_list_day_map = {}

	def get_hl_day_map(hl_name):
		if hl_name in holiday_list_day_map:
			return holiday_list_day_map[hl_name]
		rows = frappe.db.get_all(
			"Holiday",
			filters={"parent": hl_name, "holiday_date": ["between", [str(from_date), str(to_date)]]},
			fields=["holiday_date", "weekly_off"]
		)
		day_map = {getdate(r.holiday_date).day: ("Weekly Off" if r.weekly_off else "Holiday") for r in rows}
		holiday_list_day_map[hl_name] = day_map
		return day_map

	def get_d1_hl_status(emp_name, day_num):
		emp = employee_map[emp_name]
		hl = emp.holiday_list
		if not hl:
			company = emp.company
			if company not in default_holiday_lists:
				default_holiday_lists[company] = frappe.get_cached_value("Company", company, "default_holiday_list")
			hl = default_holiday_lists[company]
		if not hl:
			return None
		return get_hl_day_map(hl).get(day_num)

	matched = 0
	for emp_name in employee_names:
		emp_att = att_map.get(emp_name, {})

		for d in range(3, today_day + 1):
			d1 = d - 1
			d2 = d - 2

			rec_d  = emp_att.get(d)
			rec_d1 = emp_att.get(d1)
			rec_d2 = emp_att.get(d2)

			if not rec_d or rec_d.status != "Absent":
				continue
			if not rec_d2 or rec_d2.status != "Absent":
				continue

			if rec_d1 and rec_d1.status in ["Holiday", "Weekly Off"]:
				d1_status = rec_d1.status
				source = "Attendance record"
			elif not rec_d1:
				d1_status = get_d1_hl_status(emp_name, d1)
				if not d1_status:
					continue
				source = "Holiday List"
			else:
				continue

			print(
				f"Employee {emp_name} | "
				f"Day {d2} Absent → Day {d1} {d1_status} ({source}) → Day {d} Absent"
				f"  >>> WILL BE MARKED ABSENT <<<"
			)
			matched += 1

	print(f"\n=== Total sandwiches found: {matched} ===")
