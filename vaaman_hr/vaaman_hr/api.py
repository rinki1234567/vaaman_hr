import frappe
from frappe import _
from frappe.utils import (
    add_days,
    cint,
    cstr,
    format_date,
    get_datetime,
    get_link_to_form,
    getdate,
    nowdate,
)
from hrms.hr.doctype.shift_assignment.shift_assignment import has_overlapping_timings
from hrms.hr.utils import (
    get_holiday_dates_for_employee,
    get_holidays_for_employee,
    validate_active_employee,
)

from frappe.model.document import Document
from hrms.hr.doctype.attendance.attendance import Attendance as OriginalAttendance
from erpnext.controllers.status_updater import validate_status

class DuplicateAttendanceError(frappe.ValidationError):
    pass

class OverlappingShiftAttendanceError(frappe.ValidationError):
    pass

class vaaman_hr(OriginalAttendance):
    def validate(self):
       from erpnext.controllers.status_updater import validate_status
       validate_status(self.status, ["Present", "Absent", "On Leave", "Half Day", "Work From Home","Weekly Off","Holiday"])
       validate_active_employee(self.employee)
       self.validate_attendance_date()
       self.validate_duplicate_record()
       self.validate_overlapping_shift_attendance()
       self.validate_employee_status()
       self.check_leave_record()
       

    def validate_attendance_date(self):
        date_of_joining = frappe.db.get_value("Employee", self.employee, "date_of_joining")

        # Leaves can be marked for future dates
        if (
            self.status != "On Leave"
            and not self.leave_application
            and getdate(self.attendance_date) > getdate(nowdate())
        ):
            frappe.throw(
                _("Attendance cannot be marked for future dates: {0}").format(
                    frappe.bold(format_date(self.attendance_date)),
                )
            )
        elif date_of_joining and getdate(self.attendance_date) < getdate(date_of_joining):
            frappe.throw(
                _("Attendance date {0} cannot be less than employee {1}'s joining date: {2}").format(
                    frappe.bold(format_date(self.attendance_date)),
                    frappe.bold(self.employee),
                    frappe.bold(format_date(date_of_joining)),
                )
            )

    def validate_duplicate_record(self):
        duplicate = self.get_duplicate_attendance_record()

        if duplicate:
            frappe.throw(
                _("Attendance for employee {0} is already marked for the date {1}: {2}").format(
                    frappe.bold(self.employee),
                    frappe.bold(format_date(self.attendance_date)),
                    get_link_to_form("Attendance", duplicate),
                ),
                title=_("Duplicate Attendance"),
                exc=DuplicateAttendanceError,
            )

    def get_duplicate_attendance_record(self) -> str | None:
        Attendance = frappe.qb.DocType("Attendance")
        query = (
            frappe.qb.from_(Attendance)
            .select(Attendance.name)
            .where(
                (Attendance.employee == self.employee)
                & (Attendance.docstatus < 2)
                & (Attendance.attendance_date == self.attendance_date)
                & (Attendance.name != self.name)
            )
        )

        if self.shift:
            query = query.where(
                ((Attendance.shift.isnull()) | (Attendance.shift == ""))
                | (
                    ((Attendance.shift.isnotnull()) | (Attendance.shift != ""))
                    & (Attendance.shift == self.shift)
                )
            )

        duplicate = query.run(pluck=True)

        return duplicate[0] if duplicate else None

    def validate_overlapping_shift_attendance(self):
        attendance = self.get_overlapping_shift_attendance()

        if attendance:
            frappe.throw(
                _("Attendance for employee {0} is already marked for an overlapping shift {1}: {2}").format(
                    frappe.bold(self.employee),
                    frappe.bold(attendance.shift),
                    get_link_to_form("Attendance", attendance.name),
                ),
                title=_("Overlapping Shift Attendance"),
                exc=OverlappingShiftAttendanceError,
            )

    def get_overlapping_shift_attendance(self) -> dict:
        if not self.shift:
            return {}

        Attendance = frappe.qb.DocType("Attendance")
        same_date_attendance = (
            frappe.qb.from_(Attendance)
            .select(Attendance.name, Attendance.shift)
            .where(
                (Attendance.employee == self.employee)
                & (Attendance.docstatus < 2)
                & (Attendance.attendance_date == self.attendance_date)
                & (Attendance.shift != self.shift)
                & (Attendance.name != self.name)
            )
        ).run(as_dict=True)

        if same_date_attendance and has_overlapping_timings(self.shift, same_date_attendance[0].shift):
            return same_date_attendance[0]
        return {}

    def validate_employee_status(self):
        if frappe.db.get_value("Employee", self.employee, "status") == "Inactive":
            frappe.throw(_("Cannot mark attendance for an Inactive employee {0}").format(self.employee))

    def check_leave_record(self):
        if self.attendance_request:
            return
        LeaveApplication = frappe.qb.DocType("Leave Application")
        leave_record = (
            frappe.qb.from_(LeaveApplication)
            .select(
                LeaveApplication.leave_type,
                LeaveApplication.half_day,
                LeaveApplication.half_day_date,
                LeaveApplication.name,
            )
            .where(
                (LeaveApplication.employee == self.employee)
                & (self.attendance_date >= LeaveApplication.from_date)
                & (self.attendance_date <= LeaveApplication.to_date)
                & (LeaveApplication.status == "Approved")
                & (LeaveApplication.docstatus == 1)
            )
        ).run(as_dict=True)

        if leave_record:
            for d in leave_record:
                self.leave_type = d.leave_type
                self.leave_application = d.name
                if d.half_day_date == getdate(self.attendance_date):
                    self.status = "Half Day"
                    frappe.msgprint(
                        _("Employee {0} on Half day on {1}").format(
                            self.employee, format_date(self.attendance_date)
                        )
                    )
                else:
                    self.status = "On Leave"
                    frappe.msgprint(
                        _("Employee {0} is on Leave on {1}").format(
                            self.employee, format_date(self.attendance_date)
                        )
                    )

        if self.status in ("On Leave", "Half Day"):
            if not leave_record:
                frappe.msgprint(
                    _("No leave record found for employee {0} on {1}").format(
                        self.employee, format_date(self.attendance_date)
                    ),
                    alert=1,
                )
        elif self.leave_type:
            self.leave_type = None
            self.leave_application = None

    def validate_employee(self):
        emp = frappe.db.sql(
            "select name from `tabEmployee` where name = %s and status = 'Active'", self.employee
        )
        if not emp:
            frappe.throw(_("Employee {0} is not active or does not exist").format(self.employee))

    def on_trash(self):
        # Delete linked Attendance Policy Logs
        frappe.db.delete("Attendance Policy Log", {"attendance": self.name})
        frappe.db.commit()
       

    def on_cancel(self):
        # Delete linked Attendance Policy Logs
        frappe.db.delete("Attendance Policy Log", {"attendance": self.name})
        frappe.db.commit()
        super().on_cancel()


# Register the custom class as the new implementation for the 'Attendance' doctype
Attendance = vaaman_hr
