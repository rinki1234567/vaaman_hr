from hrms.payroll.doctype.salary_slip.salary_slip import SalarySlip as ERPNextSalarySlip
import frappe
from frappe.utils import getdate, flt, cint
from frappe.query_builder.functions import Count
from packaging import version
from datetime import timedelta

# ==========================================
# VERSION DETECTION
# ==========================================
HRMS_VERSION = version.parse(frappe.get_attr("hrms.__version__"))

# ==========================================
# HOLIDAY FUNCTION COMPATIBILITY
# ==========================================
try:
    # v16
    from hrms.hr.utils import get_holiday_dates_for_employee

    def get_employee_holidays(employee, start_date, end_date):
        return get_holiday_dates_for_employee(employee, start_date, end_date)

except ImportError:
    # v15 fallback
    from erpnext.setup.doctype.employee.employee import (
        get_holiday_list_for_employee,
    )
    from hrms.utils.holiday_list import get_holiday_dates_between

    def get_employee_holidays(employee, start_date, end_date):
        holiday_list = get_holiday_list_for_employee(employee)

        return get_holiday_dates_between(
            holiday_list,
            start_date,
            end_date,
        )


# ==========================================
# HOLIDAY LIST NAME COMPATIBILITY
# ==========================================
def get_employee_holiday_list_name(employee, start_date, end_date):
    """
    v15: holiday list is stored directly on the Employee record.
    v16: holiday list is managed via Holiday List Assignment.
         One assignment is active at a time — a newer from_date supersedes
         the older one (no to_date field).
    """
    if HRMS_VERSION.major >= 16:
        return frappe.db.get_value(
            "Holiday List Assignment",
            {
                "applicable_for": "Employee",
                "assigned_to": employee,
                "from_date": ["<=", end_date],
                "docstatus": 1,
            },
            "holiday_list",
            order_by="from_date desc",
        )

    return frappe.db.get_value("Employee", employee, "holiday_list")


class CustomSalarySlip(ERPNextSalarySlip):

    # ==========================================
    # SAFE SUPER CALL FOR v15/v16
    # ==========================================
    def call_super_working_days(
        self,
        lwp=None,
        for_preview=0,
        lwp_days_corrected=None,
    ):

        if HRMS_VERSION.major >= 16:
            return super().get_working_days_details(
                lwp,
                for_preview,
                lwp_days_corrected,
            )

        return super().get_working_days_details(
            lwp,
            for_preview,
        )

    # ==========================================
    # MAIN OVERRIDE
    # ==========================================
    def get_working_days_details(
        self,
        lwp=None,
        for_preview=0,
        lwp_days_corrected=None,
    ):
        """
        Custom logic applies ONLY when:
        Salary Structure Assignment.weekly_off_on_attendance = 1

        Otherwise ERPNext default behavior is used.
        """

        # ---------- BASIC SAFETY ----------
        if not self.employee:
            return self.call_super_working_days(
                lwp,
                for_preview,
                lwp_days_corrected,
            )

        start_date = getdate(self.start_date)
        end_date = getdate(self.end_date)

        joining_date = (
            getdate(self.joining_date)
            if self.joining_date
            else None
        )

        relieving_date = (
            getdate(self.relieving_date)
            if self.relieving_date
            else None
        )

        # ---------- FETCH SSA ----------
        ssa = frappe.db.get_value(
            "Salary Structure Assignment",
            {
                "employee": self.employee,
                "from_date": ["<=", end_date],
                "docstatus": 1,
            },
            [
                "weekly_off_on_attendance",
                "override_weekly_off_with_absent",
            ],
            order_by="from_date desc",
        )

        # ---------- FALLBACK TO ERPNext ----------
        if not ssa:
            return self.call_super_working_days(
                lwp,
                for_preview,
                lwp_days_corrected,
            )

        weekly_off_on_attendance, override_absent_on_holiday = ssa

        if not cint(weekly_off_on_attendance):
            return self.call_super_working_days(
                lwp,
                for_preview,
                lwp_days_corrected,
            )

        override_absent_on_holiday = cint(
            override_absent_on_holiday or 0
        )

        # ---------- PREVIEW MODE ----------
        if for_preview:
            total_days = (end_date - start_date).days + 1

            self.total_working_days = total_days
            self.payment_days = total_days
            self.absent_days = 0
            self.leave_without_pay = 0

            return

        # ==========================================
        # CUSTOM LOGIC
        # ==========================================

        holidays = get_employee_holidays(
            self.employee,
            start_date,
            end_date,
        )

        period_start = (
            max(start_date, joining_date)
            if joining_date
            else start_date
        )

        period_end = (
            min(end_date, relieving_date)
            if relieving_date
            else end_date
        )

        if period_end < period_start:
            total_working_days = 0
        else:
            total_working_days = (
                period_end - period_start
            ).days + 1

        # ---------- BULK ATTENDANCE QUERY ----------
        attendance_rows = frappe.get_all(
            "Attendance",
            filters={
                "employee": self.employee,
                "attendance_date": [
                    "between",
                    [period_start, period_end],
                ],
                "docstatus": 1,
            },
            fields=[
                "attendance_date",
                "status",
                "leave_type",
                "half_day_status",
            ],
        )

        attendance_by_date = {
            getdate(row.attendance_date): row
            for row in attendance_rows
        }
        
        
        # ---------- PAYROLL SETTINGS ----------
        payroll_settings = frappe.db.get_singles_dict("Payroll Settings")
        consider_unmarked_as = payroll_settings.get("consider_unmarked_attendance_as")
        daily_wages_fraction_for_half_day = flt(payroll_settings.get("daily_wages_fraction_for_half_day")) or 0.5

        # ---------- HOLIDAY DATES (PPH & ABSENT LOGIC) ----------
        holiday_list_name = get_employee_holiday_list_name(
            self.employee, period_start, period_end
        )

        all_holiday_dates = set()
        pph_holiday_dates = set()  # public holidays only (weekly_off = 0)

        if holiday_list_name:
            raw = frappe.db.get_all(
                "Holiday",
                filters={
                    "parent": holiday_list_name,
                    "holiday_date": ["between", [period_start, period_end]],
                },
                fields=["holiday_date", "weekly_off"],
            )
            all_holiday_dates = {getdate(r.holiday_date) for r in raw}
            pph_holiday_dates = {getdate(r.holiday_date) for r in raw if not r.weekly_off}

        holidays = set(holidays) | all_holiday_dates

        # ---------- ABSENT COUNT ----------
        absent_days = 0

        current_date = period_start
        while current_date <= period_end:
            is_holiday = current_date in holidays
            row = attendance_by_date.get(current_date)
            status = row.status if row else None

            if status == "Absent":
                absent_days += 1
            elif status is None and consider_unmarked_as == "Absent" and not is_holiday:
                absent_days += 1

            current_date += timedelta(days=1)

        # ---------- HALF DAY ABSENT (HRMS DEFAULT LOGIC) ----------
        Attendance = frappe.qb.DocType("Attendance")
        half_day_query = (
            frappe.qb.from_(Attendance)
            .select(Count("*"))
            .where(
                (Attendance.employee == self.employee)
                & (Attendance.attendance_date.between(period_start, period_end))
                & (Attendance.docstatus == 1)
                & (Attendance.status == "Half Day")
                & (Attendance.half_day_status == "Absent")
            )
        )

        if not override_absent_on_holiday and holidays:
            half_day_query = half_day_query.where(
                Attendance.attendance_date.notin(holidays)
            )

        half_absent_days = half_day_query.run()[0][0]
        absent_days += half_absent_days * daily_wages_fraction_for_half_day

        # ---------- PAID LEAVES ----------
        PAID_LEAVE_TYPES = {
            "Compensatory Off",
            "Maternity Leave",
            "Special Leave",
            "Privilege Leave",
            "Sick Leave",
            "Casual Leave",
            "Sick Leave - Zinc",
            "Festival Leave",
        }

        paid_leaves = 0
        for row in attendance_by_date.values():
            if row.leave_type in PAID_LEAVE_TYPES:
                if row.status == "On Leave":
                    paid_leaves += 1
                elif row.status == "Half Day":
                    paid_leaves += 0.5

        self.custom_paid_leaves = paid_leaves

        pph = 0
        for date, row in attendance_by_date.items():
            if date in pph_holiday_dates and row.status in ["Present", "Half Day"]:
                pph += 1

        self.custom_pph = pph

        # ---------- FINAL VALUES ----------
        self.total_working_days = total_working_days

        self.absent_days = max(
            0,
            absent_days,
        )

        self.leave_without_pay = flt(lwp or 0)

        self.payment_days = max(
            0,
            flt(self.total_working_days)
            - flt(self.absent_days)
            - flt(self.leave_without_pay),
        )

        # ==========================================
        # v16 PAYROLL CORRECTION SUPPORT
        # ==========================================
        if (
            HRMS_VERSION.major >= 16
            and lwp_days_corrected
            and lwp_days_corrected > 0
        ):

            from hrms.payroll.doctype.salary_slip.salary_slip import (
                verify_lwp_days_corrected,
            )

            if verify_lwp_days_corrected(
                self.employee,
                self.start_date,
                self.end_date,
                lwp_days_corrected,
            ):
                self.payment_days += lwp_days_corrected
