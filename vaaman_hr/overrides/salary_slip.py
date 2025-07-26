# custom_hr/overrides/salary_slip.py

from hrms.payroll.doctype.salary_slip.salary_slip import SalarySlip as ERPNextSalarySlip
import frappe
from frappe.utils import getdate
from datetime import timedelta
from hrms.hr.utils import get_holiday_dates_for_employee

class CustomSalarySlip(ERPNextSalarySlip):
    def get_working_days_details(self, joining_date=None, relieving_date=None, lwp=None, for_preview=0):
        joining_date = getdate(joining_date) if joining_date else None
        relieving_date = getdate(relieving_date) if relieving_date else None
        start_date = getdate(self.start_date)
        end_date = getdate(self.end_date)

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
