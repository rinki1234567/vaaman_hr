# import frappe
# from frappe import _
# from frappe.model.document import Document
# from frappe.utils import add_days, cint, date_diff, format_date, get_url_to_list, getdate
# from hrms.hr.doctype.compensatory_leave_request.compensatory_leave_request import CompensatoryLeaveRequest
# from vaaman_hr.vaaman_hr.over_time import get_existing_allocation_for_period
# from hrms.hr.utils import (
# 	create_additional_leave_ledger_entry,
# 	get_holiday_dates_for_employee,
# 	get_leave_period,
# 	validate_active_employee,
# 	validate_dates,
# 	validate_overlap,
# )

# class CompOff(CompensatoryLeaveRequest): 
#     def validate_attendance(self):        
#         attendance_records = frappe.get_all(
#             "Attendance",
#             filters={
#                 "attendance_date": ["between", (self.work_from_date, self.work_end_date)],
#                 "status": ("in", ["Present", "Work From Home", "Half Day","Weekly Off"]),
#                 "docstatus": 1,
#                 "employee": self.employee,
#             },
#             fields=["attendance_date", "status"],
#         )
        
#         half_days = [entry["attendance_date"] for entry in attendance_records if entry["status"] == "Half Day"]

#         if half_days and (not self.half_day or getdate(self.half_day_date) not in half_days):
#             frappe.throw(
#                 _(
#                     "You were only present for Half Day on {}. Cannot apply for a full day compensatory leave"
#                 ).format(", ".join([frappe.bold(format_date(half_day)) for half_day in half_days]))
#             )

#         if len(attendance_records) < date_diff(self.work_end_date, self.work_from_date) + 1:
#             frappe.throw(_("You are not present all day(s) between compensatory leave request days"))

#     def validate_holidays(self):
#         holiday_list = frappe.db.get_value('Employee', self.employee, 'holiday_list')
#         if not holiday_list:
#             frappe.throw(_("No holiday list found for the employee."))

#         holidays = get_holiday_dates_for_employee(self.employee, self.work_from_date, self.work_end_date)

#         # Fetch attendance records including "Weekly Off"
#         attendance_records = frappe.get_all(
#             "Attendance",
#             filters={
#                 "attendance_date": ["between", (self.work_from_date, self.work_end_date)],
#                 "status": ("in", ["Work From Home","Weekly Off"]),
#                 "docstatus": 1,
#                 "employee": self.employee,
#             },
#             fields=["attendance_date", "status", "custom_over_time"],
#         )

#         overtime_days = [entry["attendance_date"] for entry in attendance_records if (entry.get("custom_over_time") or 0) > 0 and entry["status"] =="Weekly Off"] 
#         wfh = [entry["attendance_date"] for entry in attendance_records if entry["status"] == "Work From Home"]

#         weekend = frappe.db.sql(
#             """
#             SELECT a.attendance_date, h.weekly_off 
#             FROM `tabAttendance` a
#             LEFT JOIN `tabHoliday` h ON a.attendance_date = h.holiday_date
#             WHERE a.employee = %(employee)s
#             AND a.attendance_date BETWEEN %(start_date)s AND %(end_date)s
#             AND a.docstatus = 1
#             AND h.parent = %(holiday_list)s
#             AND h.weekly_off = 1
#             """,
#             {
#                 "employee": self.employee,
#                 "start_date": self.work_from_date,
#                 "end_date": self.work_end_date,
#                 "holiday_list": holiday_list,
#             },
#             as_dict=True,
#         )
        
#         weekend_days = [entry["attendance_date"] for entry in weekend if entry["weekly_off"] == 1]

#         # Check if there are valid holidays or weekly off days with overtime
#         if holidays or overtime_days or weekend_days or wfh:
#             frappe.msgprint(
#                 _("Compensatory leave will be added for the following dates: {}")
#                 .format(", ".join([frappe.bold(format_date(day)) for day in set(overtime_days + holidays + wfh + weekend_days)]))
#             )
#         else:
#             msg = _("No valid holidays or weekly offs with overtime found between {0} to {1}.")
#             frappe.throw(msg.format(
#                 frappe.bold(format_date(self.work_from_date)),
#                 frappe.bold(format_date(self.work_end_date)),
#             ))

#     def calculate_overtime_leave(self):
#         attendance_records = frappe.get_all(
#             "Attendance",
#             filters={
#                 "attendance_date": ["between", (self.work_from_date, self.work_end_date)],
#                 "status": "Weekly Off",
#                 "docstatus": 1,
#                 "employee": self.employee,
#             },
#             fields=["attendance_date", "custom_over_time"],
#         )

#         overtime_leave_days = 0
#         for record in attendance_records:
#             overtime_hours = record.get("custom_over_time") or 0
#             overtime_leave_days += overtime_hours / 8

#         return overtime_leave_days





import frappe
from frappe import _
from frappe.utils import date_diff, format_date, getdate
from hrms.hr.doctype.compensatory_leave_request.compensatory_leave_request import CompensatoryLeaveRequest
from hrms.hr.utils import get_holiday_dates_for_employee

class CompOff(CompensatoryLeaveRequest): 
    def validate(self):
        self.check_duplicate_request()
        self.validate_attendance()
        self.validate_holidays()

    def check_duplicate_request(self):
        existing = frappe.db.exists("Compensatory Leave Request", {
            "employee": self.employee,
            "work_from_date": self.work_from_date,
            "work_end_date": self.work_end_date,
            "docstatus": 1,
            "name": ["!=", self.name],
        })
        if existing:
            frappe.throw(
                _("A Compensatory Leave Request for {1} already exists for this employee.").format(
                    frappe.bold(format_date(self.work_from_date)),
                    frappe.bold(format_date(self.work_end_date)),
                )
            )

    def validate_attendance(self):        
        attendance_records = frappe.get_all(
            "Attendance",
            filters={
                "attendance_date": ["between", (self.work_from_date, self.work_end_date)],
                "status": ("in", ["Present", "Work From Home", "Half Day", "Weekly Off"]),
                "docstatus": 1,
                "employee": self.employee,
            },
            fields=["attendance_date", "status"],
        )
        
        expected_days = date_diff(self.work_end_date, self.work_from_date) + 1
        
        if len(attendance_records) < expected_days:
            frappe.throw(_("You are not present all day(s) between compensatory leave request days"))

        half_days = [entry["attendance_date"] for entry in attendance_records if entry["status"] == "Half Day"]
        if half_days and (not self.half_day or getdate(self.half_day_date) not in half_days):
            frappe.throw(
                _(
                    "You were only present for Half Day on {}. Cannot apply for a full day compensatory leave"
                ).format(", ".join([frappe.bold(format_date(half_day)) for half_day in half_days]))
            )

    def validate_holidays(self):
        holiday_list = frappe.db.get_value('Employee', self.employee, 'holiday_list')
        holidays = get_holiday_dates_for_employee(self.employee, self.work_from_date, self.work_end_date)

        attendance_records = frappe.get_all(
            "Attendance",
            filters={
                "attendance_date": ["between", (self.work_from_date, self.work_end_date)],
                "status": ("in", ["Work From Home", "Weekly Off"]),
                "docstatus": 1,
                "employee": self.employee,
            },
            fields=["attendance_date", "status", "custom_over_time"],
        )

        overtime_days = [entry["attendance_date"] for entry in attendance_records if (entry.get("custom_over_time") or 0) > 0 and entry["status"] =="Weekly Off"] 
        wfh = [entry["attendance_date"] for entry in attendance_records if entry["status"] == "Work From Home"]

        if holidays or overtime_days or wfh:
            frappe.msgprint(
                _("Compensatory leave will be added for the following dates: {}")
                .format(", ".join([frappe.bold(format_date(day)) for day in set(overtime_days + holidays + wfh)])),
                alert=True
            )
        else:
            frappe.throw(_("No valid holidays or weekly offs with overtime found between {0} to {1}.").format(
                frappe.bold(format_date(self.work_from_date)),
                frappe.bold(format_date(self.work_end_date)),
            ))