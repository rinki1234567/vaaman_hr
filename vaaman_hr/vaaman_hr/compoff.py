import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, cint, date_diff, format_date, get_url_to_list, getdate
from hrms.hr.doctype.compensatory_leave_request.compensatory_leave_request import CompensatoryLeaveRequest
from vaaman_hr.vaaman_hr.over_time import get_existing_allocation_for_period

from hrms.hr.utils import (
	create_additional_leave_ledger_entry,
	get_holiday_dates_for_employee,
	get_leave_period,
	validate_active_employee,
	validate_dates,
	validate_overlap,
)

class CompOff(CompensatoryLeaveRequest): 
    def validate_holidays(self):
        try:
            frappe.logger().info(f"Starting validate_holidays for Employee: {self.employee}")
            holidays = get_holiday_dates_for_employee(self.employee, self.work_from_date, self.work_end_date)
            holiday_list = frappe.db.get_value('Employee', self.employee, 'holiday_list')
            frappe.logger().info(f"Holiday List: {holiday_list}")

            attendance_records = frappe.get_all(
                "Attendance",
                filters={
                    "attendance_date": ["between", (self.work_from_date, self.work_end_date)],
                    "status": ("in", ["Work From Home","Weekly Off"]),
                    "docstatus": 1,
                    "employee": self.employee,
                },
                fields=["attendance_date", "status", "custom_over_time"],
            )
            frappe.logger().info(f"Attendance Records: {attendance_records}")

            weekend = frappe.db.sql(
                """
                SELECT 
                    a.attendance_date, a.status, h.holiday_date, h.weekly_off 
                FROM 
                    `tabAttendance` a
                LEFT JOIN 
                    `tabHoliday` h ON a.attendance_date = h.holiday_date
                WHERE 
                    a.employee = %(employee)s
                    AND a.attendance_date BETWEEN %(start_date)s AND %(end_date)s
                    AND a.docstatus = 1
                    AND h.parent = %(holiday_list)s
                    AND h.weekly_off = 1
                """,
                {
                    "employee": self.employee,
                    "start_date": self.work_from_date,
                    "end_date": self.work_end_date,
                    "holiday_list": holiday_list,
                },
                as_dict=True,
            )
            frappe.logger().info(f"Weekend Records: {weekend}")

            overtime_days = [entry.attendance_date for entry in attendance_records if entry.get("custom_over_time", 0) > 0 and entry.get("status") == "Weekly Off"]
            wfh = [entry.attendance_date for entry in attendance_records if entry.get("status") == "Work From Home"]
            weekend_days = [entry["attendance_date"] for entry in weekend if entry.get("weekly_off") == 1]

            all_dates = list(set(overtime_days + holidays + wfh + weekend_days))
            all_dates.sort()

            if all_dates:
                if len(all_dates) == 1:
                    frappe.msgprint("Compensatory leave will be added for the date: {}".format(
                        frappe.bold(format_date(all_dates[0]))
                    ))
                else:
                    frappe.msgprint("Compensatory leave will be added from {} to {}".format(
                        frappe.bold(format_date(min(all_dates))),
                        frappe.bold(format_date(max(all_dates)))
                    ))
            else:
                msg = _(f"The days between {format_date(self.work_from_date)} to {format_date(self.work_end_date)} are not valid holidays or weekly offs with overtime.")
                frappe.throw(msg)
        
        except Exception as e:
            frappe.log_error(f"Error in validate_holidays: {str(e)}", "Validate Holidays Error")
            frappe.throw(_("An unexpected error occurred. Please check the error log for more details."))
