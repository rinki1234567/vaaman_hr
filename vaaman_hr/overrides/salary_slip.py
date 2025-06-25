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

        holidays = get_holiday_dates_for_employee(self.employee,start_date, end_date)
        total_days = (end_date - start_date).days + 1
        working_days = 0
        absent_days = 0

        for i in range(total_days):
            current_date = start_date + timedelta(days=i)
            if (not joining_date or current_date >= joining_date) and (not relieving_date or current_date <= relieving_date):
                att = frappe.get_value("Attendance", {
                    "employee": self.employee,
                    "attendance_date": current_date,
                    "docstatus": 1
                }, "status")

                if att == "Absent":
                    absent_days += 1
                elif current_date not in holidays:
                    working_days += 1

        self.total_working_days = working_days
        self.absent_days = absent_days
