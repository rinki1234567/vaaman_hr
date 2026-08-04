# Copyright (c) 2026, Vaaman and Contributors
# See license.txt
"""Head Office half-day policy + Attendance Request checkin relink + salary-slip match."""

from datetime import datetime
from types import SimpleNamespace

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt, getdate, add_days, today

from vaaman_hr.vaaman_hr.head_office_policy import compute_head_office_status
from vaaman_hr.overrides.attendance_utils import (
	cancel_leave_attendance,
	relink_checkins_from_cancelled_attendance,
)


def _log(log_type, time_str, day="2026-07-13"):
	return SimpleNamespace(log_type=log_type, time=datetime.strptime(f"{day} {time_str}", "%Y-%m-%d %H:%M:%S"))


class TestHeadOfficeHalfDayPolicy(FrappeTestCase):
	"""Pure policy: half_day_status = Status for Other Half."""

	def test_first_half_only_other_half_absent(self):
		"""Worked morning only (like HR-ATT-2026-1485351) → Half Day / Other Half Absent."""
		logs = [_log("IN", "10:05:58"), _log("OUT", "15:40:53")]
		wh, status, hds, late, early = compute_head_office_status(
			getdate("2026-07-13"), logs, leave_application=None, employee=None
		)
		self.assertEqual(status, "Half Day")
		self.assertEqual(hds, "Absent")
		self.assertEqual(late, 1)
		self.assertGreaterEqual(flt(wh), 4.5)
		self.assertLess(flt(wh), 7.75)

	def test_full_day_present(self):
		logs = [_log("IN", "10:00:00"), _log("OUT", "18:30:00")]
		wh, status, hds, late, early = compute_head_office_status(
			getdate("2026-07-13"), logs, leave_application=None, employee=None
		)
		self.assertEqual(status, "Present")
		self.assertEqual(hds, "")
		self.assertEqual(late, 0)
		self.assertEqual(early, 0)

	def test_second_half_with_leave_other_half_present(self):
		"""Half-day leave + afternoon punch → Half Day / Other Half Present."""
		logs = [_log("IN", "14:00:00"), _log("OUT", "18:30:00")]
		wh, status, hds, late, early = compute_head_office_status(
			getdate("2026-07-13"),
			logs,
			leave_application="HR-LAP-TEST",
			employee=None,
		)
		self.assertEqual(status, "Half Day")
		self.assertEqual(hds, "Present")

	def test_leave_linked_but_insufficient_punch_other_half_absent(self):
		"""Leave linked but punch does not meet second-half hours/timing → Other Half Absent."""
		# Stay till mandatory out so early-exit Absent guard does not fire; hours still < half-day min
		logs = [_log("IN", "16:00:00"), _log("OUT", "18:30:00")]
		wh, status, hds, late, early = compute_head_office_status(
			getdate("2026-07-13"),
			logs,
			leave_application="HR-LAP-TEST",
			employee=None,
		)
		self.assertEqual(status, "Half Day")
		self.assertEqual(hds, "Absent")
		self.assertLess(flt(wh), 4.5)

	def test_no_logs_returns_none(self):
		"""No checkin → policy does not decide (leave/AR exemption / HRMS path)."""
		wh, status, hds, late, early = compute_head_office_status(
			getdate("2026-07-13"), [], leave_application=None, employee=None
		)
		self.assertEqual(wh, 0)
		self.assertIsNone(status)
		self.assertIsNone(hds)


class TestSalarySlipHalfDayMatch(FrappeTestCase):
	"""Salary slip absent/payable must align with half_day_status semantics."""

	def test_other_half_absent_counts_half_absent_day(self):
		"""Payable days follow HRMS: paid leave + Other Half Present = 1.0."""
		daily_frac = 0.5
		leave_type_map = {
			"Leave Without Pay": {"is_lwp": 1, "is_ppl": 0, "fraction_of_daily_salary_per_leave": 1},
		}
		rows = [
			SimpleNamespace(status="Half Day", half_day_status="Absent", leave_type=None),
			SimpleNamespace(status="Half Day", half_day_status="Present", leave_type="Privilege Leave"),
			SimpleNamespace(status="Present", half_day_status="", leave_type=None),
			SimpleNamespace(status="Half Day", half_day_status="Present", leave_type="Leave Without Pay"),
			SimpleNamespace(status="Half Day", half_day_status="Absent", leave_type="Leave Without Pay"),
		]

		half_absent_count = sum(
			1 for r in rows if r.status == "Half Day" and r.half_day_status == "Absent"
		)
		absent_from_half = half_absent_count * daily_frac

		payable = 0
		for r in rows:
			if r.status == "Present":
				payable += 1
			elif r.status == "Half Day":
				is_lwp_or_ppl = r.leave_type in leave_type_map and (
					leave_type_map[r.leave_type].get("is_lwp")
					or leave_type_map[r.leave_type].get("is_ppl")
				)
				if r.half_day_status == "Absent":
					if not is_lwp_or_ppl:
						payable += daily_frac
				elif is_lwp_or_ppl:
					payable += 1 - daily_frac
				else:
					payable += 1

		self.assertEqual(absent_from_half, 1.0)  # two Other Half Absent rows
		# 0.5 (HD/A) + 1.0 (PL+Present) + 1.0 (Present) + 0.5 (LWP+Present) + 0 (LWP+Absent)
		self.assertEqual(payable, 3.0)


class TestAttendanceRequestCheckinRelink(FrappeTestCase):
	"""AR creating new attendance after cancelled one must inherit checkins + times."""

	def setUp(self):
		super().setUp()
		self.company = frappe.db.get_single_value("Global Defaults", "default_company") or frappe.db.get_value(
			"Company", {"name": ("like", "%")}, "name"
		)
		self.employee = self._get_or_skip_ho_employee()
		self.att_date = getdate("2099-01-06")  # far future weekday — avoid real payroll clash
		self._cleanup()

	def tearDown(self):
		self._cleanup()
		super().tearDown()

	def _get_or_skip_ho_employee(self):
		emp = frappe.db.get_value(
			"Employee",
			{"branch": "Head Office", "status": "Active", "default_shift": "Head Office"},
			"name",
		)
		if not emp:
			self.skipTest("No Head Office employee with default_shift Head Office")
		return emp

	def _cleanup(self):
		# Unlink checkins first
		for name in frappe.get_all(
			"Employee Checkin",
			filters={"employee": self.employee, "time": ["between", [f"{self.att_date} 00:00:00", f"{self.att_date} 23:59:59"]]},
			pluck="name",
		):
			frappe.db.set_value("Employee Checkin", name, "attendance", None, update_modified=False)
			frappe.delete_doc("Employee Checkin", name, force=1, ignore_permissions=True)

		for name in frappe.get_all(
			"Attendance",
			filters={"employee": self.employee, "attendance_date": self.att_date},
			pluck="name",
		):
			frappe.db.set_value("Attendance", name, "docstatus", 0, update_modified=False)
			frappe.delete_doc("Attendance", name, force=1, ignore_permissions=True)

		frappe.db.commit()

	def test_relink_checkins_from_cancelled_attendance(self):
		# Checkins first (unlinked), then cancelled attendance with punches
		ck_in = frappe.get_doc(
			{
				"doctype": "Employee Checkin",
				"employee": self.employee,
				"time": f"{self.att_date} 12:11:56",
				"log_type": "IN",
				"skip_auto_attendance": 1,
				"latitude": 19.0760,
				"longitude": 72.8777,
			}
		).insert(ignore_permissions=True)
		ck_out = frappe.get_doc(
			{
				"doctype": "Employee Checkin",
				"employee": self.employee,
				"time": f"{self.att_date} 17:25:11",
				"log_type": "OUT",
				"skip_auto_attendance": 1,
				"latitude": 19.0760,
				"longitude": 72.8777,
			}
		).insert(ignore_permissions=True)

		old = frappe.get_doc(
			{
				"doctype": "Attendance",
				"employee": self.employee,
				"attendance_date": self.att_date,
				"company": self.company,
				"status": "Half Day",
				"half_day_status": "Present",
				"shift": "Head Office",
				"in_time": f"{self.att_date} 12:11:56",
				"out_time": f"{self.att_date} 17:25:11",
				"working_hours": 5.22,
			}
		)
		old.flags.ignore_validate = True
		old.insert(ignore_permissions=True)
		old.submit()
		# Simulate leave cancel (db_set skips on_cancel unlink)
		frappe.db.set_value("Attendance", old.name, "docstatus", 2, update_modified=False)
		frappe.db.set_value("Employee Checkin", ck_in.name, "attendance", old.name, update_modified=False)
		frappe.db.set_value("Employee Checkin", ck_out.name, "attendance", old.name, update_modified=False)

		# New active attendance (as AR would create — request link skips HO after_insert override)
		frappe.flags.skip_head_office_attendance_validation = True
		try:
			new = frappe.get_doc(
				{
					"doctype": "Attendance",
					"employee": self.employee,
					"attendance_date": self.att_date,
					"company": self.company,
					"status": "Half Day",
					"half_day_status": "Absent",
					"shift": "Head Office",
				}
			)
			new.flags.ignore_validate = True
			new.insert(ignore_permissions=True)
			new.submit()
		finally:
			frappe.flags.skip_head_office_attendance_validation = False

		relink_checkins_from_cancelled_attendance(self.employee, new, self.att_date)

		self.assertEqual(frappe.db.get_value("Employee Checkin", ck_in.name, "attendance"), new.name)
		self.assertEqual(frappe.db.get_value("Employee Checkin", ck_out.name, "attendance"), new.name)

		updated = frappe.db.get_value(
			"Attendance",
			new.name,
			["in_time", "out_time", "working_hours", "half_day_status", "modify_half_day_status"],
			as_dict=True,
		)
		self.assertIsNotNone(updated.in_time)
		self.assertIsNotNone(updated.out_time)
		self.assertEqual(flt(updated.working_hours, 2), 5.22)
		self.assertEqual(updated.half_day_status, "Present")
		self.assertEqual(updated.modify_half_day_status, 0)

	def test_leave_cancel_then_recreate_relinks_checkins(self):
		"""Leave cancel → new Half Day attendance must inherit punches (point 2)."""
		# Checkins + submitted leave attendance
		ck_in = frappe.get_doc(
			{
				"doctype": "Employee Checkin",
				"employee": self.employee,
				"time": f"{self.att_date} 12:11:56",
				"log_type": "IN",
				"skip_auto_attendance": 1,
				"latitude": 19.0760,
				"longitude": 72.8777,
			}
		).insert(ignore_permissions=True)
		ck_out = frappe.get_doc(
			{
				"doctype": "Employee Checkin",
				"employee": self.employee,
				"time": f"{self.att_date} 17:25:11",
				"log_type": "OUT",
				"skip_auto_attendance": 1,
				"latitude": 19.0760,
				"longitude": 72.8777,
			}
		).insert(ignore_permissions=True)

		frappe.flags.skip_head_office_attendance_validation = True
		try:
			old = frappe.get_doc(
				{
					"doctype": "Attendance",
					"employee": self.employee,
					"attendance_date": self.att_date,
					"company": self.company,
					"status": "Half Day",
					"half_day_status": "Present",
					"leave_type": "Privilege Leave",
					"shift": "Head Office",
					"in_time": f"{self.att_date} 12:11:56",
					"out_time": f"{self.att_date} 17:25:11",
					"working_hours": 5.22,
				}
			)
			old.flags.ignore_validate = True
			old.flags.ignore_links = True
			old.insert(ignore_permissions=True)
			old.submit()
		finally:
			frappe.flags.skip_head_office_attendance_validation = False

		frappe.db.set_value("Employee Checkin", ck_in.name, "attendance", old.name, update_modified=False)
		frappe.db.set_value("Employee Checkin", ck_out.name, "attendance", old.name, update_modified=False)

		# Cancel via our helper (leave_application filter — use None to cancel by status range)
		cancel_leave_attendance(self.employee, self.att_date, self.att_date, leave_application=None)

		self.assertEqual(frappe.db.get_value("Attendance", old.name, "docstatus"), 2)

		# New leave attendance
		frappe.flags.skip_head_office_attendance_validation = True
		try:
			new = frappe.get_doc(
				{
					"doctype": "Attendance",
					"employee": self.employee,
					"attendance_date": self.att_date,
					"company": self.company,
					"status": "Half Day",
					"half_day_status": "Present",
					"modify_half_day_status": 1,
					"leave_type": "Privilege Leave",
					"shift": "Head Office",
				}
			)
			new.flags.ignore_validate = True
			new.insert(ignore_permissions=True)
			new.submit()
		finally:
			frappe.flags.skip_head_office_attendance_validation = False

		relink_checkins_from_cancelled_attendance(self.employee, new, self.att_date)

		self.assertEqual(frappe.db.get_value("Employee Checkin", ck_in.name, "attendance"), new.name)
		self.assertEqual(frappe.db.get_value("Employee Checkin", ck_out.name, "attendance"), new.name)
		updated = frappe.db.get_value(
			"Attendance", new.name, ["in_time", "out_time", "half_day_status", "modify_half_day_status"], as_dict=True
		)
		self.assertIsNotNone(updated.in_time)
		self.assertEqual(updated.half_day_status, "Present")
		self.assertEqual(updated.modify_half_day_status, 0)


class TestHeadOfficeLiveAttendanceSalaryMatch(FrappeTestCase):
	"""Audit real HO July half-days: policy expectation vs stored half_day_status."""

	def test_ho_first_half_records_match_absent_other_half(self):
		"""Submitted HO Half Days without leave, first-half punches → must be Other Half Absent."""
		mismatches = []
		rows = frappe.db.sql(
			"""
			SELECT att.name, att.employee, att.attendance_date, att.half_day_status,
				att.in_time, att.out_time, att.working_hours, att.leave_application, att.attendance_request
			FROM `tabAttendance` att
			INNER JOIN `tabEmployee` e ON e.name = att.employee
			WHERE att.docstatus = 1
				AND att.status = 'Half Day'
				AND (e.branch = 'Head Office' OR att.custom_branch = 'Head Office')
				AND att.attendance_date BETWEEN '2026-07-01' AND '2026-07-31'
				AND (att.leave_application IS NULL OR att.leave_application = '')
				AND (att.attendance_request IS NULL OR att.attendance_request = '')
				AND att.in_time IS NOT NULL
				AND att.out_time IS NOT NULL
			""",
			as_dict=True,
		)

		for row in rows:
			logs = [
				SimpleNamespace(log_type="IN", time=row.in_time),
				SimpleNamespace(log_type="OUT", time=row.out_time),
			]
			_, status, hds, _, _ = compute_head_office_status(
				row.attendance_date, logs, leave_application=None, employee=row.employee
			)
			if status == "Half Day" and hds == "Absent" and row.half_day_status != "Absent":
				mismatches.append(
					{
						"name": row.name,
						"employee": row.employee,
						"date": str(row.attendance_date),
						"stored": row.half_day_status,
						"expected": hds,
					}
				)

		self.assertEqual(
			mismatches,
			[],
			msg=f"HO first-half Half Day records with wrong Other Half (salary absent mismatch): {mismatches}",
		)

	def test_fixed_records_state(self):
		"""Records we fixed in this incident stay correct."""
		a = frappe.db.get_value(
			"Attendance",
			"HR-ATT-2026-1485351",
			["half_day_status", "status"],
			as_dict=True,
		)
		if a:
			self.assertEqual(a.status, "Half Day")
			self.assertEqual(a.half_day_status, "Absent")

		b = frappe.db.get_value(
			"Attendance",
			"HR-ATT-2026-1450708",
			["half_day_status", "in_time", "out_time"],
			as_dict=True,
		)
		if b:
			self.assertEqual(b.half_day_status, "Absent")
			self.assertIsNone(b.in_time)

		c = frappe.db.get_value(
			"Attendance",
			"HR-ATT-2026-1469132",
			["in_time", "out_time", "half_day_status"],
			as_dict=True,
		)
		if c:
			self.assertIsNotNone(c.in_time)
			self.assertIsNotNone(c.out_time)
			self.assertEqual(c.half_day_status, "Present")
			linked = frappe.db.count("Employee Checkin", {"attendance": "HR-ATT-2026-1469132"})
			self.assertGreaterEqual(linked, 2)


class TestHeadOfficePolicyReapplyWindow(FrappeTestCase):
	"""Point 3: re-apply on checkin/update, but never rewrite old attendance."""

	def test_lookback_window(self):
		from vaaman_hr.vaaman_hr.half_day_leaves import (
			REAPPLY_LOOKBACK_DAYS,
			_within_reapply_window,
		)
		from frappe.utils import add_days, today

		self.assertTrue(_within_reapply_window(today()))
		self.assertTrue(_within_reapply_window(add_days(today(), -REAPPLY_LOOKBACK_DAYS)))
		self.assertFalse(_within_reapply_window(add_days(today(), -(REAPPLY_LOOKBACK_DAYS + 1))))

	def test_policy_only_for_head_office_branch_employees(self):
		"""Non–Head Office employees must never get HO status overwrite."""
		from unittest.mock import patch
		from vaaman_hr.vaaman_hr import half_day_leaves as hdl
		from vaaman_hr.vaaman_hr.head_office_policy import (
			HEAD_OFFICE_BRANCH,
			is_head_office_employee,
		)

		self.assertEqual(HEAD_OFFICE_BRANCH, "Head Office")

		non_ho = frappe.db.get_value(
			"Employee",
			{"status": "Active", "branch": ["!=", "Head Office"]},
			"name",
		)
		ho = frappe.db.get_value(
			"Employee",
			{"status": "Active", "branch": "Head Office"},
			"name",
		)
		if non_ho:
			self.assertFalse(is_head_office_employee(non_ho))
		if ho:
			self.assertTrue(is_head_office_employee(ho))

		fake_att = frappe._dict(
			name="ATT-NON-HO",
			employee=non_ho or "EMP-NON-HO",
			attendance_date=today(),
			docstatus=1,
			leave_application=None,
			attendance_request=None,
		)
		with patch.object(hdl, "is_head_office_employee", return_value=False), patch.object(
			hdl, "get_checkin_logs"
		) as logs, patch.object(hdl, "apply_attendance_status") as apply:
			hdl.apply_head_office_policy_to_attendance(fake_att, enforce_lookback=False)
			logs.assert_not_called()
			apply.assert_not_called()

	def test_reapply_skips_old_attendance_dates(self):
		"""Checkin-driven re-apply must not touch attendance older than lookback."""
		from unittest.mock import patch
		from vaaman_hr.vaaman_hr import half_day_leaves as hdl

		old_date = add_days(today(), -(hdl.REAPPLY_LOOKBACK_DAYS + 5))
		fake_att = frappe._dict(
			name="ATT-OLD",
			employee="EMP-HO",
			attendance_date=old_date,
			docstatus=1,
			leave_application=None,
			attendance_request=None,
		)

		with patch.object(hdl, "is_head_office_employee", return_value=True), patch.object(
			hdl, "get_checkin_logs", return_value=[object()]
		), patch.object(
			hdl, "compute_head_office_status", return_value=(5.0, "Half Day", "Absent", 0, 0)
		) as compute, patch.object(hdl, "apply_attendance_status") as apply:
			hdl.apply_head_office_policy_to_attendance(fake_att, enforce_lookback=True)
			compute.assert_not_called()
			apply.assert_not_called()

			# Create-time insert path still allowed for any date
			hdl.apply_head_office_policy_to_attendance(fake_att, enforce_lookback=False)
			compute.assert_called_once()
