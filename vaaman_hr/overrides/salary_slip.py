# custom_hr/overrides/salary_slip.py

from hrms.payroll.doctype.salary_slip.salary_slip import SalarySlip as ERPNextSalarySlip
import frappe
from frappe.utils import getdate, flt, cint
from datetime import timedelta
from hrms.hr.utils import get_holiday_dates_for_employee


class CustomSalarySlip(ERPNextSalarySlip):

    def get_working_days_details(self, lwp=None, for_preview=0):
        """
        Custom logic applies ONLY when:
        Salary Structure Assignment.weekly_off_on_attendance = 1

        Otherwise, ERPNext default behavior is used.
        """

        # ---------- BASIC SAFETY ----------
        if not self.employee:
            return super().get_working_days_details(lwp, for_preview)

        start_date = getdate(self.start_date)
        end_date = getdate(self.end_date)

        joining_date = getdate(self.joining_date) if self.joining_date else None
        relieving_date = getdate(self.relieving_date) if self.relieving_date else None

        # ---------- FETCH ACTIVE SALARY STRUCTURE ASSIGNMENT ----------
        ssa = frappe.db.get_value(
            "Salary Structure Assignment",
            {
                "employee": self.employee,
                "from_date": ["<=", end_date],
                "docstatus": 1,
            },
            ["weekly_off_on_attendance", "override_weekly_off_with_absent"],
            order_by="from_date desc",
        )

        # ---------- EXIT EARLY IF CUSTOM LOGIC SHOULD NOT APPLY ----------
        if not ssa:
            return super().get_working_days_details(lwp, for_preview)

        weekly_off_on_attendance, override_absent_on_holiday = ssa

        if not cint(weekly_off_on_attendance):
            return super().get_working_days_details(lwp, for_preview)

        override_absent_on_holiday = cint(override_absent_on_holiday or 0)

        # ---------- PREVIEW MODE ----------
        if for_preview:
            total_days = (end_date - start_date).days + 1
            self.total_working_days = total_days
            self.payment_days = total_days
            self.absent_days = 0
            self.leave_without_pay = 0
            return

        # ---------- CUSTOM ATTENDANCE-BASED CALCULATION ----------
        holidays = get_holiday_dates_for_employee(self.employee, start_date, end_date)

        total_working_days = 0
        absent_days = 0

        total_days = (end_date - start_date).days + 1

        for i in range(total_days):
            current_date = start_date + timedelta(days=i)

            # Respect joining / relieving
            if joining_date and current_date < joining_date:
                continue
            if relieving_date and current_date > relieving_date:
                continue

            is_holiday = current_date in holidays

            att = frappe.get_value(
                "Attendance",
                {
                    "employee": self.employee,
                    "attendance_date": current_date,
                    "docstatus": 1,
                },
                "status",
            )

            # ---- UNMARKED ATTENDANCE ----
            # Leave unmarked handling to ERPNext
            if not att:
                continue

            # ---- ABSENT ----
            if att == "Absent":
                if override_absent_on_holiday or not is_holiday:
                    absent_days += 1
                continue

            # ---- PRESENT / WORKING ----
            if not is_holiday:
                total_working_days += 1

        # ---------- SET FINAL VALUES ----------
        self.total_working_days = total_working_days
        self.absent_days = absent_days

        # Leave Without Pay (kept explicit)
        self.leave_without_pay = flt(lwp or 0)

        # ---------- PAYMENT DAYS (DO NOT CALL get_payment_days) ----------
        self.payment_days = flt(self.total_working_days) - flt(self.absent_days)
        self.payment_days = max(0, self.payment_days)
        self.absent_days = max(0, self.absent_days)
