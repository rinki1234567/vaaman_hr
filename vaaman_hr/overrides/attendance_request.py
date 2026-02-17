import frappe
from frappe import _
from frappe.utils import add_days, date_diff

from hrms.hr.doctype.attendance_request.attendance_request import AttendanceRequest


class CustomAttendanceRequest(AttendanceRequest):

    def validate(self):
        if self.reason == "Weekly Off" and self.half_day:
            frappe.throw(_("Weekly Off and Half Day cannot be selected at the same time."))
        super().validate()

    def validate_no_attendance_to_create(self):
        if self.reason == "Weekly Off":
            return
        super().validate_no_attendance_to_create()

    def get_attendance_status(self, attendance_date):
        if self.reason == "Weekly Off":
            return "Weekly Off"
        return super().get_attendance_status(attendance_date)

    def should_mark_attendance(self, attendance_date):
        if self.reason == "Weekly Off":
            return True
        return super().should_mark_attendance(attendance_date)

    @frappe.whitelist()
    def get_attendance_warnings(self):
        if self.reason != "Weekly Off":
            return super().get_attendance_warnings()

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
