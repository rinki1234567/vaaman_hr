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
        if self.custom_attendance_request_status not in ("Approved", "Rejected"):
            frappe.throw(_("Attendance Request Status must be Approved or Rejected before submitting."))
        user_roles = frappe.get_roles(frappe.session.user)
        is_authorized_manager = "System Manager" in user_roles or "HO HR Manager" in user_roles
        if not is_authorized_manager:
            employee_approver = frappe.db.get_value("Employee", self.employee, "leave_approver")
            if employee_approver != frappe.session.user:
                frappe.throw(
                    _("Only the designated leave approver or an authorized manager can submit this request."), 
                    frappe.PermissionError
                )

    def on_submit(self):
        if self.custom_attendance_request_status == "Approved":
            super().on_submit()
        else:
            frappe.msgprint("Attendance Request is Rejected. The request will now be cancelled.")
            self.cancel()

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

    def get_attendance_doc(self, attendance_date):
        # When the request has no shift, match any existing (non-cancelled) attendance
        # for the same employee + date regardless of its shift, so the base class updates
        # it (e.g. Absent -> Half Day) instead of creating a duplicate that the custom
        # Attendance duplicate guard would reject.
        if not self.shift:
            existing = frappe.db.exists(
                "Attendance",
                {
                    "employee": self.employee,
                    "attendance_date": attendance_date,
                    "docstatus": ("!=", 2),
                },
            )
            return frappe.get_doc("Attendance", existing) if existing else None

        return super().get_attendance_doc(attendance_date)

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
            # Leave cancel often db_sets docstatus=2 (skips on_cancel), so checkins
            # stay on the cancelled attendance — move them to the replacement record.
            self._relink_checkins_from_cancelled_attendance(new_doc, date)

    def _relink_checkins_from_cancelled_attendance(self, new_doc, date):
        cancelled = frappe.get_all(
            "Attendance",
            filters={
                "employee": self.employee,
                "attendance_date": date,
                "docstatus": 2,
                "name": ("!=", new_doc.name),
            },
            fields=["name", "in_time", "out_time", "working_hours", "half_day_status"],
            order_by="modified desc",
            limit=1,
        )
        if not cancelled:
            return

        old = cancelled[0]
        checkins = frappe.get_all(
            "Employee Checkin",
            filters={"attendance": old.name},
            pluck="name",
        )
        if not checkins and not (old.in_time or old.out_time):
            return

        for checkin_name in checkins:
            frappe.db.set_value(
                "Employee Checkin", checkin_name, "attendance", new_doc.name, update_modified=False
            )

        values = {}
        if old.in_time:
            values["in_time"] = old.in_time
        if old.out_time:
            values["out_time"] = old.out_time
        if old.working_hours:
            values["working_hours"] = old.working_hours
        # If punches exist for the other half, keep Present (leave + worked other half)
        if checkins or old.in_time or old.out_time:
            if new_doc.status == "Half Day":
                values["half_day_status"] = "Present"
                values["modify_half_day_status"] = 0

        if values:
            frappe.db.set_value("Attendance", new_doc.name, values, update_modified=False)
            new_doc.update(values)

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
