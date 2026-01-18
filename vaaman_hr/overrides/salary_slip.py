# custom_hr/overrides/salary_slip.py

from hrms.payroll.doctype.salary_slip.salary_slip import SalarySlip as ERPNextSalarySlip
import frappe
from frappe.utils import getdate, flt, cint
from datetime import timedelta
from hrms.hr.utils import get_holiday_dates_for_employee

class CustomSalarySlip(ERPNextSalarySlip):
    def get_working_days_details(self, lwp=None, for_preview=0):
        # Use properties from base class (these fetch from Employee record)
        joining_date = getdate(self.joining_date) if self.joining_date else None
        relieving_date = getdate(self.relieving_date) if self.relieving_date else None
        start_date = getdate(self.start_date)
        end_date = getdate(self.end_date)

        # Handle preview mode
        if for_preview:
            working_days = (end_date - start_date).days + 1
            self.total_working_days = working_days
            self.payment_days = working_days
            self.absent_days = 0
            self.leave_without_pay = 0
            return

        holidays = get_holiday_dates_for_employee(self.employee, start_date, end_date)
        total_days = (end_date - start_date).days + 1
        working_days = 0
        absent_days = 0

        # Fetch active salary structure assignment during this period
        structure_assignment = frappe.db.get_value(
            "Salary Structure Assignment",
            {
                "employee": self.employee,
                "from_date": ["<=", end_date],
                "docstatus": 1
            },
            ["name", "override_weekly_off_with_absent"],
            order_by="from_date desc"
        )


        override_absent_on_holiday = 0
        if structure_assignment:
            _, override_absent_on_holiday = structure_assignment
            override_absent_on_holiday = int(override_absent_on_holiday or 0)

        for i in range(total_days):
            current_date = start_date + timedelta(days=i)

            if (not joining_date or current_date >= joining_date) and (not relieving_date or current_date <= relieving_date):
                att = frappe.get_value("Attendance", {
                    "employee": self.employee,
                    "attendance_date": current_date,
                    "docstatus": 1
                }, "status")

                # If override is enabled: always count 'Absent' regardless of holiday
                if override_absent_on_holiday and att == "Absent":
                    absent_days += 1
                # Otherwise, skip holidays
                elif not override_absent_on_holiday:
                    if att == "Absent" and current_date not in holidays:
                        absent_days += 1
                    elif att != "Absent" and current_date not in holidays:
                        working_days += 1
                # If it's a holiday and not absent, skip it
                elif att != "Absent" and current_date not in holidays:
                    working_days += 1

        self.total_working_days = working_days
        self.absent_days = absent_days

        # Get payroll settings for payment_days calculation
        payroll_settings = frappe.get_cached_value(
            "Payroll Settings",
            None,
            (
                "payroll_based_on",
                "include_holidays_in_total_working_days",
                "consider_marked_attendance_on_holidays",
                "daily_wages_fraction_for_half_day",
                "consider_unmarked_attendance_as",
            ),
            as_dict=1,
        )

        # Calculate LWP if not provided
        if lwp is None:
            # For Attendance-based payroll, LWP is typically 0 when using custom calculation
            # You may need to adjust this based on your business logic
            lwp = 0
        
        self.leave_without_pay = lwp

        # Calculate payment_days using the base class method
        include_holidays = cint(payroll_settings.get("include_holidays_in_total_working_days", 0)) if payroll_settings else 0
        payment_days = self.get_payment_days(include_holidays)

        # Calculate final payment_days - following base class pattern
        if flt(payment_days) > flt(lwp):
            self.payment_days = flt(payment_days) - flt(lwp)

            if payroll_settings and payroll_settings.get("payroll_based_on") == "Attendance":
                # Subtract absent_days calculated from our custom attendance loop
                # This matches base class line 509: self.payment_days -= flt(absent)
                self.payment_days -= flt(self.absent_days)

                # Handle unmarked attendance and half absent days
                # This matches base class lines 511-525
                consider_unmarked_attendance_as = payroll_settings.get("consider_unmarked_attendance_as") or "Present"
                daily_wages_fraction_for_half_day = flt(payroll_settings.get("daily_wages_fraction_for_half_day")) or 0.5
                consider_marked_attendance_on_holidays = (
                    payroll_settings.get("include_holidays_in_total_working_days")
                    and payroll_settings.get("consider_marked_attendance_on_holidays")
                )

                if consider_unmarked_attendance_as == "Absent":
                    unmarked_days = self.get_unmarked_days(include_holidays, holidays)
                    self.absent_days += unmarked_days  # will be treated as absent
                    self.payment_days -= unmarked_days

                half_absent_days = self.get_half_absent_days(
                    consider_marked_attendance_on_holidays,
                    holidays,
                )
                self.absent_days += half_absent_days * daily_wages_fraction_for_half_day
                self.payment_days -= half_absent_days * daily_wages_fraction_for_half_day
        else:
            self.payment_days = 0
