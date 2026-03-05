import frappe
from frappe import _
from frappe.utils import add_days, date_diff

from erpnext.setup.doctype.employee.employee import is_holiday
from hrms.hr.doctype.attendance_request.attendance_request import AttendanceRequest


class CustomAttendanceRequest(AttendanceRequest):

	def validate(self):
		if self.reason == "Weekly Off" and self.half_day:
			frappe.throw(_("Weekly Off and Half Day cannot be selected at the same time."))
		super().validate()

	def validate_no_attendance_to_create(self):
		if self.reason in ("Weekly Off", "Change State of Holiday"):
			return
		super().validate_no_attendance_to_create()

	def get_attendance_status(self, attendance_date):
		if self.reason == "Weekly Off":
			return "Weekly Off"

		if self.reason == "Change State of Holiday":
			return "Absent"

		return super().get_attendance_status(attendance_date)

	def should_mark_attendance(self, attendance_date):
		if self.reason == "Weekly Off":
			return True

		if self.reason == "Change State of Holiday":
			return is_holiday(self.employee, attendance_date)

		return super().should_mark_attendance(attendance_date)

	@frappe.whitelist()
	def get_attendance_warnings(self):
		if self.reason == "Weekly Off":
			return self._get_weekly_off_warnings()

		if self.reason == "Change State of Holiday":
			return self._get_holiday_status_warnings()

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

	def _get_holiday_status_warnings(self):
		attendance_warnings = []
		request_days = date_diff(self.to_date, self.from_date) + 1

		for day in range(request_days):
			attendance_date = add_days(self.from_date, day)

			if not is_holiday(self.employee, attendance_date):
				attendance_warnings.append(
					{"date": attendance_date, "reason": "Not a Holiday", "action": "Skip"}
				)
				continue

			existing = self.get_attendance_doc(attendance_date)

			if existing:
				if existing.status == "Absent":
					attendance_warnings.append(
						{
							"date": attendance_date,
							"reason": "Already marked as Absent",
							"action": "Skip",
						}
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
			# else: new record will be created, no warning needed

		return attendance_warnings
