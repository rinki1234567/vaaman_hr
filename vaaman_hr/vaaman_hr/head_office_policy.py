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
LATE_ENTRY_FROM = "10:01:00"  # late mark starts from 10:01 AM (10:00:00–10:00:59 is on-time)
WEEKDAY_FULL_DAY_OUT = "18:30:00"  # must work till 6:30 PM
SATURDAY_FULL_DAY_OUT = "17:00:00"  # may leave by 5:00 PM

# Allowed late/early window — each counts as 1 of 3 occasions per month (late + early combined).
# Late mark from 10:01 AM; beyond 10:15 AM is Absent (not one of the 3 slots).
LATE_ENTRY_AFTER = "10:15:00"  # latest allowed late punch-in (15 min after 10:00 AM)
WEEKDAY_EARLY_EXIT_BEFORE = "18:15:00"  # earliest allowed weekday early out (15 min before 6:30 PM)
WEEKDAY_EARLY_EXIT_UNTIL = "18:29:00"  # last allowed early out minute (6:30:00–6:30:59 is on-time)
SATURDAY_EARLY_EXIT_BEFORE = "16:45:00"  # earliest allowed Saturday early out (15 min before 5:00 PM)
SATURDAY_EARLY_EXIT_UNTIL = "16:59:00"  # last allowed Saturday early out minute (5:00:00–5:00:59 is on-time)

POLICY = {
	"weekday": {
		"full_day_hours": 8.0,  # net work hours (10:00–6:30 span; lunch not auto-deducted from punches)
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
				wh, st, hds, _, _ = compute_head_office_status(
					att_date, logs, leave_application=leave_application, employee=employee
				)
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


def get_early_exit_bounds(attendance_date):
	"""Return (earliest allowed early out, last early-out minute, mandatory out)."""
	if is_saturday(attendance_date):
		return (
			get_time(SATURDAY_EARLY_EXIT_BEFORE),
			get_time(SATURDAY_EARLY_EXIT_UNTIL),
			get_time(SATURDAY_FULL_DAY_OUT),
		)
	return (
		get_time(WEEKDAY_EARLY_EXIT_BEFORE),
		get_time(WEEKDAY_EARLY_EXIT_UNTIL),
		get_time(WEEKDAY_FULL_DAY_OUT),
	)


def is_on_time_or_allowed_early_out(out_time, attendance_date):
	"""Out is OK for full-day timing: on-time, allowed early, or pre-close cushion minute."""
	if not out_time:
		return False

	out_t = get_time(out_time)
	grace_from, grace_until, mandatory_out = get_early_exit_bounds(attendance_date)
	if out_t >= mandatory_out:
		return True
	if grace_from <= out_t <= grace_until:
		return True
	if grace_until < out_t < mandatory_out:
		return True
	return False


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
	grace_from, _, mandatory_out = get_early_exit_bounds(attendance_date)
	return out_t < mandatory_out and out_t < grace_from


def calc_working_hours(logs, attendance_date=None):
	"""Sum IN→OUT segments. Lunch is not auto-deducted (early-exit allowance uses gross span)."""
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
	- late_entry: in from 10:01 AM up to 10:15 AM (10:00:00–10:00:59 is on-time)
	- early_exit: out from 6:15 PM up to 6:29 PM (6:30:00–6:30:59 is on-time)
	"""
	if not in_time or not out_time:
		return 0, 0

	in_t = get_time(in_time)
	out_t = get_time(out_time)

	late_from = get_time(LATE_ENTRY_FROM)
	allowed_late_cutoff = get_time(LATE_ENTRY_AFTER)
	late_entry = 1 if late_from <= in_t <= allowed_late_cutoff else 0

	grace_from, grace_until, _ = get_early_exit_bounds(attendance_date)
	early_exit = 1 if grace_from <= out_t <= grace_until else 0

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
		return f"Late Entry (After {LATE_ENTRY_FROM[:5]} AM) and Early Leaving"
	if late_entry:
		return f"Late Entry (After {LATE_ENTRY_FROM[:5]} AM)"
	if early_exit:
		if is_saturday(attendance_date):
			return f"Early Leaving (Saturday before {SATURDAY_EARLY_EXIT_BEFORE[:5]} PM)"
		return f"Early Leaving (Before {WEEKDAY_EARLY_EXIT_BEFORE[:5]} PM)"
	return None


def meets_first_half_timing(attendance_date, in_time, out_time):
	"""First half: on-time in by core cutoff and out by first-half end."""
	rules = get_rules(attendance_date)
	return get_time(in_time) <= get_time(rules["core_in_by"]) and get_time(out_time) >= get_time(
		rules["first_half_out_by"]
	)


def meets_second_half_timing(attendance_date, in_time, out_time):
	"""Second half: in by second-half cutoff and out by mandatory end (or allowed early)."""
	rules = get_rules(attendance_date)
	return get_time(in_time) <= get_time(rules["second_half_in_by"]) and is_on_time_or_allowed_early_out(
		out_time, attendance_date
	)


def meets_half_day_timing(attendance_date, in_time, out_time):
	return meets_first_half_timing(attendance_date, in_time, out_time) or meets_second_half_timing(
		attendance_date, in_time, out_time
	)


def get_violation_penalty_status(attendance_date, logs):
	"""4th late/early and late beyond 15 min — company policy is always Absent."""
	return "Absent", ""


def _allows_second_half_half_day(leave_application, employee, attendance_date):
	if leave_application:
		return True
	if employee and has_approved_half_day_leave(employee, attendance_date):
		return True
	return False


def compute_head_office_status(attendance_date, logs, leave_application=None, employee=None):
	"""Return (working_hours, status, half_day_status, late_entry, early_exit)."""
	if not logs:
		return 0, None, None, 0, 0

	working_hours = calc_working_hours(logs, attendance_date)
	in_dt = logs[0].time
	out_dt = logs[-1].time
	in_time = get_time(in_dt)
	out_time = get_time(out_dt)
	rules = get_rules(attendance_date)
	late_entry, early_exit = check_late_entry_early_exit(in_dt, out_dt, attendance_date)

	final_status = "Absent"
	final_half_day_status = ""
	has_half_day_leave = _allows_second_half_half_day(leave_application, employee, attendance_date)

	if is_beyond_allowed_early_exit(out_dt, attendance_date):
		return working_hours, "Absent", "", 0, 0

	if is_beyond_allowed_late(in_dt) and not has_half_day_leave:
		return working_hours, "Absent", "", 0, 0

	# Full day Present: enough hours + on-time / allowed early out
	if (
		working_hours >= rules["full_day_hours"]
		and is_on_time_or_allowed_early_out(out_dt, attendance_date)
	):
		final_status = "Present"
		final_half_day_status = ""
	elif working_hours >= rules["min_half_day_hours"] and meets_first_half_timing(
		attendance_date, in_dt, out_dt
	):
		final_status = "Half Day"
		final_half_day_status = "Present"
	elif (
		working_hours >= rules["min_half_day_hours"]
		and meets_second_half_timing(attendance_date, in_dt, out_dt)
		and has_half_day_leave
	):
		# Second half requires advance half-day leave application
		final_status = "Half Day"
		final_half_day_status = "Present"
	elif leave_application:
		final_status = "Half Day"
		final_half_day_status = "Absent"
	else:
		final_status = "Absent"
		final_half_day_status = ""

	# Monthly late/early flags: keep on Present and Half Day (allowed window only)
	if leave_application or final_status == "Absent":
		late_entry, early_exit = 0, 0

	return working_hours, final_status, final_half_day_status, late_entry, early_exit
