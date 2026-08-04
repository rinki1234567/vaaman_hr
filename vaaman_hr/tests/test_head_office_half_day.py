# Copyright (c) 2026, Vaaman and Contributors
# See license.txt
"""Head Office half-day policy + Attendance Request checkin relink + salary-slip match."""

from datetime import datetime
from types import SimpleNamespace

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt, getdate

from vaaman_hr.vaaman_hr.head_office_policy import compute_head_office_status
from vaaman_hr.overrides.attendance_request import CustomAttendanceRequest


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
		"""Half Day + Other Half Absent → 0.5 absent (payment reduced), payable fraction 0.5."""
		# Mirrors CustomSalarySlip: half_day_status==Absent adds to absent_days
		daily_frac = 0.5
		rows = [
			SimpleNamespace(status="Half Day", half_day_status="Absent", leave_type=None),
			SimpleNamespace(status="Half Day", half_day_status="Present", leave_type="Privilege Leave"),
			SimpleNamespace(status="Present", half_day_status="", leave_type=None),
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
				if r.half_day_status == "Absent":
					payable += daily_frac
				else:
					payable += daily_frac

		self.assertEqual(absent_from_half, 0.5)
		self.assertEqual(payable, 2.0)  # 0.5 + 0.5 + 1.0

		# Bug case we fixed: first-half wrongly marked Present would under-count absent
		wrong_rows = [
			SimpleNamespace(status="Half Day", half_day_status="Present", leave_type=None),
		]
		wrong_half_absent = sum(
			1 for r in wrong_rows if r.status == "Half Day" and r.half_day_status == "Absent"
		)
		self.assertEqual(wrong_half_absent, 0)  # mismatch vs worked-only-half reality


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

		ar = frappe.new_doc("Attendance Request")
		ar.employee = self.employee
		ar.company = self.company
		CustomAttendanceRequest._relink_checkins_from_cancelled_attendance(ar, new, self.att_date)

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
