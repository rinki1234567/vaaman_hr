import frappe
from frappe import _
from frappe.utils import add_days, date_diff

from hrms.hr.doctype.attendance_request.attendance_request import AttendanceRequest


class CustomAttendanceRequest(AttendanceRequest):

	def validate(self):
		if self.reason == "Weekly Off" and self.half_day:
			frappe.throw(_("Weekly Off and Half Day cannot be selected at the same time."))
		super().validate()
		
	def before_submit(self):
		if self.custom_attendance_request_status not in ("Approved"):
			frappe.throw(_("Please select Approved before submitting."))

	def validate_no_attendance_to_create(self):
		if self.reason in ("Weekly Off",) or self.custom_mark_absent:
			return
		super().validate_no_attendance_to_create()

	def get_attendance_status(self, attendance_date):
		if self.reason == "Weekly Off":
			return "Weekly Off"

		if self.custom_mark_absent:
			return "Absent"

		return super().get_attendance_status(attendance_date)

	def should_mark_attendance(self, attendance_date):
		if self.reason == "Weekly Off":
			return True

		if self.custom_mark_absent:
			return True

		return super().should_mark_attendance(attendance_date)

	def create_or_update_attendance(self, date: str):
		doc = self.get_attendance_doc(date)
		if doc:
			# existing record — let the base class handle the update
			super().create_or_update_attendance(date)
		else:
			# new record — base class doesn't set custom_branch, so we do it here
			branch = frappe.db.get_value("Employee", self.employee, "branch")
			status = self.get_attendance_status(date)
			new_doc = frappe.new_doc("Attendance")
			new_doc.employee = self.employee
			new_doc.attendance_date = date
			new_doc.shift = self.shift
			new_doc.company = self.company
			new_doc.attendance_request = self.name
			new_doc.status = status
			new_doc.half_day_status = "Absent" if status == "Half Day" else None
			new_doc.custom_branch = branch
			new_doc.insert(ignore_permissions=True)
			new_doc.submit()

	@frappe.whitelist()
	def get_attendance_warnings(self):
		if self.reason == "Weekly Off":
			return self._get_weekly_off_warnings()

		if self.custom_mark_absent:
			return self._get_mark_absent_warnings()

		return super().get_attendance_warnings()

	def _get_weekly_off_warnings(self):
		attendance_warnings = []
		request_days = date_diff(self.to_date, self.from_date) + 1

		for day in range(request_days):
			attendance_date = add_days(self.from_date, day)
			existing = self.get_attendance_doc(attendance_date)

			if existing:
				if existing.status == "Weekly Off":
					attendance_warnings.append(
						{"date": attendance_date, "reason": "Already marked as Weekly Off", "action": "Skip"}
					)
				else:
					attendance_warnings.append(
						{
							"date": attendance_date,
							"reason": f"Existing {existing.status} attendance will be changed to Weekly Off",
							"record": existing.name,
							"action": "Overwrite",
						}
					)

		return attendance_warnings

	def _get_mark_absent_warnings(self):
		attendance_warnings = []
		request_days = date_diff(self.to_date, self.from_date) + 1

		for day in range(request_days):
			attendance_date = add_days(self.from_date, day)
			existing = self.get_attendance_doc(attendance_date)

			if existing:
				if existing.status == "Absent":
					attendance_warnings.append(
						{"date": attendance_date, "reason": "Already marked as Absent", "action": "Skip"}
					)
				else:
					attendance_warnings.append(
						{
							"date": attendance_date,
							"reason": f"Existing {existing.status} attendance will be changed to Absent",
							"record": existing.name,
							"action": "Overwrite",
						}
					)

		return attendance_warnings
