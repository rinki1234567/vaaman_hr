"""
Shared Head Office attendance policy — used by half_day_leaves and late_entry_atten.
"""
import frappe
from frappe.utils import getdate, get_time, flt, time_diff_in_hours

HEAD_OFFICE_BRANCH = "Head Office"
GRACE_MINUTES = 15
ALLOWED_LATE_EARLY_PER_MONTH = 3

# Core office hours
CORE_IN_TIME = "10:00:00"
WEEKDAY_FULL_DAY_OUT = "18:30:00"  # must work till 6:30 PM
SATURDAY_FULL_DAY_OUT = "17:00:00"  # may leave by 5:00 PM

# Allowed late/early window — each counts as 1 of 3 occasions per month (late + early combined).
# Any punch after 10:00 is a late mark; beyond 10:15 is Absent (not one of the 3 slots).
LATE_ENTRY_AFTER = "10:15:00"  # latest allowed late punch-in (15 min after 10:00 AM)
WEEKDAY_EARLY_EXIT_BEFORE = "18:15:00"  # earliest allowed weekday early out (15 min before 6:30 PM)
SATURDAY_EARLY_EXIT_BEFORE = "16:45:00"  # earliest allowed Saturday early out (15 min before 5:00 PM)

POLICY = {
	"weekday": {
		"full_day_hours": 8.0,
		"min_half_day_hours": 4.5,
		"core_in_by": LATE_ENTRY_AFTER,
		"first_half_out_by": "14:30:00",
		"second_half_in_by": "14:00:00",
		"full_day_out_by": WEEKDAY_FULL_DAY_OUT,
	},
	"saturday": {
		"full_day_hours": 6.5,
		"min_half_day_hours": 4.0,
		"core_in_by": LATE_ENTRY_AFTER,
		"first_half_out_by": "14:00:00",
		"second_half_in_by": "13:00:00",
		"full_day_out_by": SATURDAY_FULL_DAY_OUT,
	},
}


def is_saturday(attendance_date):
	return getdate(attendance_date).weekday() == 5


def get_rules(attendance_date):
	return POLICY["saturday"] if is_saturday(attendance_date) else POLICY["weekday"]


def is_head_office_employee(employee):
	return frappe.db.get_value("Employee", employee, "branch") == HEAD_OFFICE_BRANCH


def head_office_branch_condition(alias="e"):
	"""SQL fragment: employee or attendance custom_branch is Head Office."""
	return f"({alias}.branch = '{HEAD_OFFICE_BRANCH}' OR att.custom_branch = '{HEAD_OFFICE_BRANCH}')"


def is_exempt_from_head_office_policy(attendance):
	"""Skip policy when marked via Attendance Request or Leave Application."""
	if isinstance(attendance, str):
		row = frappe.db.get_value(
			"Attendance",
			attendance,
			["attendance_request", "leave_application"],
			as_dict=True,
		)
		return bool(row and (row.attendance_request or row.leave_application))
	return bool(
		getattr(attendance, "attendance_request", None)
		or getattr(attendance, "leave_application", None)
	)


def get_expected_status_from_leave(leave_application, attendance_date, employee=None):
	"""Status from approved Leave Application (not punch policy)."""
	from frappe.utils import getdate

	la = frappe.get_doc("Leave Application", leave_application)
	att_date = getdate(attendance_date)

	if la.half_day and getdate(la.half_day_date) == att_date:
		half_day_status = "Absent"
		if employee:
			logs = get_checkin_logs(employee, att_date)
			if logs:
				wh, st, hds, _, _ = compute_head_office_status(att_date, logs)
				if st == "Half Day" and hds == "Present":
					half_day_status = "Present"
		return "Half Day", half_day_status

	if getdate(la.from_date) <= att_date <= getdate(la.to_date):
		return "On Leave", ""

	return None, None


def get_expected_status_from_attendance_request(attendance_request, attendance_date):
	"""Status from approved Attendance Request."""
	from hrms.hr.doctype.attendance_request.attendance_request import AttendanceRequest

	doc = frappe.get_doc("Attendance Request", attendance_request)
	status = AttendanceRequest.get_attendance_status(doc, attendance_date)
	half_day_status = "Absent" if status == "Half Day" else ""
	return status, half_day_status


def has_approved_half_day_leave(employee, attendance_date):
	"""Approved half-day leave covering this date (exclude from late/early enforcement)."""
	return frappe.db.exists(
		"Leave Application",
		{
			"employee": employee,
			"half_day": 1,
			"half_day_date": attendance_date,
			"status": "Approved",
			"docstatus": 1,
		},
	)


def get_checkin_logs(employee, attendance_date):
	return frappe.get_all(
		"Employee Checkin",
		filters={
			"employee": employee,
			"time": [
				"between",
				[
					str(attendance_date) + " 00:00:00",
					str(attendance_date) + " 23:59:59",
				],
			],
		},
		fields=["time", "log_type"],
		order_by="time asc",
	)


def is_beyond_allowed_late(in_time):
	"""Punch-in more than 15 minutes after core start → not covered by monthly allowance."""
	if not in_time:
		return False
	return get_time(in_time) > get_time(LATE_ENTRY_AFTER)


def is_beyond_allowed_early_exit(out_time, attendance_date):
	"""Left more than 15 minutes before mandatory end → not covered by monthly allowance."""
	if not out_time:
		return False

	out_t = get_time(out_time)
	rules = get_rules(attendance_date)
	mandatory_out = get_time(rules["full_day_out_by"])
	grace_early_out = get_time(
		SATURDAY_EARLY_EXIT_BEFORE if is_saturday(attendance_date) else WEEKDAY_EARLY_EXIT_BEFORE
	)
	return out_t < mandatory_out and out_t < grace_early_out


def calc_working_hours(logs):
	working_hours = 0
	last_in = None
	for log in logs:
		if log.log_type == "IN":
			last_in = log.time
		elif log.log_type == "OUT" and last_in:
			working_hours += time_diff_in_hours(log.time, last_in)
			last_in = None
	return flt(working_hours)


def check_late_entry_early_exit(in_time, out_time, attendance_date):
	"""
	Return (late_entry, early_exit) as 0/1 for allowed monthly occasions only.
	- late_entry: in after 10:00 AM and up to 10:15 AM
	- early_exit: out within allowed early window (6:15–6:30 / 4:45–5:00)
	"""
	if not in_time or not out_time:
		return 0, 0

	in_t = get_time(in_time)
	out_t = get_time(out_time)
	rules = get_rules(attendance_date)

	core_in = get_time(CORE_IN_TIME)
	allowed_late_cutoff = get_time(LATE_ENTRY_AFTER)
	late_entry = 1 if core_in < in_t <= allowed_late_cutoff else 0

	mandatory_out = get_time(rules["full_day_out_by"])
	grace_early_out = get_time(
		SATURDAY_EARLY_EXIT_BEFORE if is_saturday(attendance_date) else WEEKDAY_EARLY_EXIT_BEFORE
	)
	early_exit = 1 if grace_early_out <= out_t < mandatory_out else 0

	return late_entry, early_exit


def get_immediate_violation_reason(in_time, out_time, attendance_date):
	"""Same-day Absent: late beyond 15 min or early beyond 15 min."""
	if not in_time or not out_time:
		return None

	if is_beyond_allowed_late(in_time):
		return f"Late Entry beyond allowed 15 minutes (After {LATE_ENTRY_AFTER[:5]} AM)"

	if is_beyond_allowed_early_exit(out_time, attendance_date):
		if is_saturday(attendance_date):
			return f"Early Leaving beyond allowed 15 minutes (Saturday before {SATURDAY_EARLY_EXIT_BEFORE[:5]} PM)"
		return f"Early Leaving beyond allowed 15 minutes (Before {WEEKDAY_EARLY_EXIT_BEFORE[:5]} PM)"

	return None


def get_violation_reason(in_time, out_time, attendance_date):
	"""Monthly 4th+ violation among allowed late/early occasions."""
	if not in_time or not out_time:
		return None

	late_entry, early_exit = check_late_entry_early_exit(in_time, out_time, attendance_date)

	if late_entry and early_exit:
		return f"Late Entry (After {CORE_IN_TIME[:5]} AM) and Early Leaving"
	if late_entry:
		return f"Late Entry (After {CORE_IN_TIME[:5]} AM)"
	if early_exit:
		if is_saturday(attendance_date):
			return f"Early Leaving (Saturday before {SATURDAY_EARLY_EXIT_BEFORE[:5]} PM)"
		return f"Early Leaving (Before {WEEKDAY_EARLY_EXIT_BEFORE[:5]} PM)"
	return None


def compute_head_office_status(attendance_date, logs, leave_application=None):
	"""Return (working_hours, status, half_day_status, late_entry, early_exit)."""
	if not logs:
		return 0, None, None, 0, 0

	working_hours = calc_working_hours(logs)
	in_time = get_time(logs[0].time)
	out_time = get_time(logs[-1].time)
	rules = get_rules(attendance_date)
	late_entry, early_exit = check_late_entry_early_exit(logs[0].time, logs[-1].time, attendance_date)

	final_status = "Absent"
	final_half_day_status = ""

	mandatory_out = get_time(rules["full_day_out_by"])
	grace_early_out = get_time(
		SATURDAY_EARLY_EXIT_BEFORE if is_saturday(attendance_date) else WEEKDAY_EARLY_EXIT_BEFORE
	)

	if is_beyond_allowed_late(logs[0].time) or is_beyond_allowed_early_exit(logs[-1].time, attendance_date):
		return working_hours, "Absent", "", 0, 0

	# Full day Present: enough hours + on-time out OR allowed early exit (uses 1 of 3/month)
	if working_hours >= rules["full_day_hours"] and (
		out_time >= mandatory_out or (early_exit and out_time >= grace_early_out)
	):
		final_status = "Present"
		final_half_day_status = ""
	elif working_hours >= rules["min_half_day_hours"]:
		is_timing_ok = False
		if in_time <= get_time(rules["core_in_by"]) and out_time >= get_time(rules["first_half_out_by"]):
			is_timing_ok = True
		elif in_time <= get_time(rules["second_half_in_by"]) and (
			out_time >= mandatory_out or (grace_early_out <= out_time < mandatory_out)
		):
			is_timing_ok = True

		if is_timing_ok:
			final_status = "Half Day"
			final_half_day_status = "Present"
		else:
			final_status = "Absent"
			final_half_day_status = ""
	else:
		if leave_application:
			final_status = "Half Day"
			final_half_day_status = "Absent"
		else:
			final_status = "Absent"
			final_half_day_status = ""

	# Late/early only applies to full-day Present without approved half-day leave
	if final_status != "Present" or leave_application:
		late_entry, early_exit = 0, 0

	return working_hours, final_status, final_half_day_status, late_entry, early_exit
