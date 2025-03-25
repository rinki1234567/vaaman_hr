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
    def validate_attendance(self):        
        attendance_records = frappe.get_all(
            "Attendance",
            filters={
                "attendance_date": ["between", (self.work_from_date, self.work_end_date)],
                "status": ("in", ["Present", "Work From Home", "Half Day","Weekly Off"]),
                "docstatus": 1,
                "employee": self.employee,
            },
            fields=["attendance_date", "status"],
        )
        
        half_days = [entry.attendance_date for entry in attendance_records if entry.status == "Half Day"]

        if half_days and (not self.half_day or getdate(self.half_day_date) not in half_days):
            frappe.throw(
                _(
                    "You were only present for Half Day on {}. Cannot apply for a full day compensatory leave"
                ).format(", ".join([frappe.bold(format_date(half_day)) for half_day in half_days]))
            )

        if len(attendance_records) < date_diff(self.work_end_date, self.work_from_date) + 1:
            frappe.throw(_("You are not present all day(s) between compensatory leave request days"))

    def validate_holidays(self):
            holidays = get_holiday_dates_for_employee(self.employee, self.work_from_date, self.work_end_date)
            holiday_list = frappe.db.get_value('Employee', self.employee, 'holiday_list')
            # Fetch attendance records including "Weekly Off"
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

            # Filter for "Weekly Off" days with overtime
            overtime_days = [entry.attendance_date for entry in attendance_records if entry.custom_over_time > 0 and entry.status =="Weekly Off"] 
            wfh = [entry.attendance_date for entry in attendance_records if entry.status =="Work From Home"]
            weekend_days = [entry["attendance_date"] for entry in weekend if entry["weekly_off"] == 1]

            # Check if there are valid holidays or weekly off days with overtime
            if holidays  or overtime_days or weekend_days or wfh:
                frappe.msgprint("Compensatory leave will be added for the following dates: {}".format(", ".join([frappe.bold(format_date(day)) for day in overtime_days or holidays or wfh or weekend_days])))
            elif not holidays and not overtime_days:
                if date_diff(self.work_end_date, self.work_from_date):
                    msg = _("The days between {0} to {1} are not valid holidays or weekly offs with overtime.").format(
                        frappe.bold(format_date(self.work_from_date)),
                        frappe.bold(format_date(self.work_end_date)),
                    )
                else:
                    msg = _("{0} is not a holiday or a weekly off with overtime.").format(frappe.bold(format_date(self.work_from_date)))

                frappe.throw(msg)


    def on_submit(self):
        company = frappe.db.get_value("Employee", self.employee, "company")
        date_difference = date_diff(self.work_end_date, self.work_from_date) + 1
        if self.half_day:
            date_difference -= 0.5

        # Custom logic to calculate additional compensatory leave based on overtime
        overtime_leave_days = self.calculate_overtime_leave()

        
        if overtime_leave_days:
            total_leave_days=overtime_leave_days
        else:
            total_leave_days = date_difference

        comp_leave_valid_from = add_days(self.work_end_date, 1)
        leave_period = get_leave_period(comp_leave_valid_from, comp_leave_valid_from, company)
        if leave_period:
            leave_allocation = self.get_existing_allocation_for_period(leave_period)
            if leave_allocation:
                leave_allocation.new_leaves_allocated += total_leave_days
                leave_allocation.validate()
                leave_allocation.db_set("new_leaves_allocated", leave_allocation.total_leaves_allocated)
                leave_allocation.db_set("total_leaves_allocated", leave_allocation.total_leaves_allocated)

                # Generate additional ledger entry for the new compensatory leaves off
                create_additional_leave_ledger_entry(leave_allocation, total_leave_days, comp_leave_valid_from)

            else:
                leave_allocation = self.create_leave_allocation(leave_period, total_leave_days)
            self.db_set("leave_allocation", leave_allocation.name)
        else:
            comp_leave_valid_from = frappe.bold(format_date(comp_leave_valid_from))
            msg = _("This compensatory leave will be applicable from {0}.").format(comp_leave_valid_from)
            msg += " " + _(
                "Currently, there is no {0} leave period for this date to create/update leave allocation."
            ).format(frappe.bold(_("active")))
            msg += "<br><br>" + _("Please create a new {0} for the date {1} first.").format(
                f"""<a href='{get_url_to_list("Leave Period")}'>Leave Period</a>""",
                comp_leave_valid_from,
            )
            frappe.throw(msg, title=_("No Leave Period Found"))

    def on_cancel(self):
        if self.leave_allocation:
            date_difference = date_diff(self.work_end_date, self.work_from_date) + 1
            if self.half_day:
                date_difference -= 0.5
            
            # Custom logic to calculate additional compensatory leave based on overtime
            overtime_leave_days = self.calculate_overtime_leave()

            if overtime_leave_days:
                total_leave_days=overtime_leave_days
            else:
                total_leave_days = date_difference

            leave_allocation = frappe.get_doc("Leave Allocation", self.leave_allocation)
            if leave_allocation:
                leave_allocation.new_leaves_allocated -= total_leave_days
                if leave_allocation.new_leaves_allocated - total_leave_days <= 0:
                    leave_allocation.new_leaves_allocated = 0
                leave_allocation.validate()
                leave_allocation.db_set("new_leaves_allocated", leave_allocation.total_leaves_allocated)
                leave_allocation.db_set("total_leaves_allocated", leave_allocation.total_leaves_allocated)

                # Create reverse entry on cancellation
                create_additional_leave_ledger_entry(
                    leave_allocation, total_leave_days * -1, add_days(self.work_end_date, 1)
                )

    def calculate_overtime_leave(self):
        attendance_records = frappe.get_all(
            "Attendance",
            filters={
                "attendance_date": ["between", (self.work_from_date, self.work_end_date)],
                "status": "Weekly Off",
                "docstatus": 1,
                "employee": self.employee,
            },
            fields=["attendance_date", "custom_over_time"],
        )

        overtime_leave_days = 0
        for record in attendance_records:
            overtime_hours = record.custom_over_time
            overtime_leave_days += overtime_hours / 8  # Assuming 8 hours equals 1 day of leave

        return overtime_leave_days
