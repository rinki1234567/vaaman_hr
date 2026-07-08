from datetime import timedelta

import frappe
from frappe import _
from frappe.query_builder.functions import Count
from frappe.utils import cint, flt, getdate
from packaging import version

from hrms.payroll.doctype.salary_slip.salary_slip import SalarySlip as ERPNextSalarySlip

# ==========================================
# VERSION DETECTION
# ==========================================
HRMS_VERSION = version.parse(frappe.get_attr("hrms.__version__"))

# ==========================================
# HOLIDAY FUNCTION COMPATIBILITY
# ==========================================
try:
    from hrms.hr.utils import get_holiday_dates_for_employee

    def get_employee_holidays(employee, start_date, end_date):
        return get_holiday_dates_for_employee(employee, start_date, end_date)

except ImportError:
    from erpnext.setup.doctype.employee.employee import get_holiday_list_for_employee
    from hrms.utils.holiday_list import get_holiday_dates_between

    def get_employee_holidays(employee, start_date, end_date):
        holiday_list = get_holiday_list_for_employee(employee)
        return get_holiday_dates_between(holiday_list, start_date, end_date)


def get_employee_holiday_list_name(employee, start_date, end_date):
    """v15: holiday list on Employee. v16: Holiday List Assignment."""
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


class CustomSalarySlip(ERPNextSalarySlip):

    def call_super_working_days(self, lwp=None, for_preview=0, lwp_days_corrected=None):
        if HRMS_VERSION.major >= 16:
            return super().get_working_days_details(lwp, for_preview, lwp_days_corrected)

        return super().get_working_days_details(lwp, for_preview)

    def _get_payroll_settings(self):
        return frappe.get_cached_value(
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

    def _is_worker_structure(self, salary_structure):
        return salary_structure and "worker" in salary_structure.lower()

    def _is_current_month_joining(self, start_date=None, end_date=None, joining_date=None, ssa_flag=0):
        """True when DOJ falls in this payroll month (auto). SSA flag is fallback only if DOJ is missing."""
        start_date = getdate(start_date or self.start_date)
        end_date = getdate(end_date or self.end_date)
        if joining_date:
            joining_date = getdate(joining_date)
        elif self.joining_date:
            joining_date = getdate(self.joining_date)
        else:
            joining_date = None

        if joining_date and start_date <= joining_date <= end_date:
            return True
        return bool(cint(ssa_flag)) if not joining_date else False

    def get_data_for_eval(self):
        data, default_data = super().get_data_for_eval()
        is_join_month = self._is_current_month_joining(
            ssa_flag=data.get("custom_current_month_joining")
        )
        data.custom_current_month_joining = 1 if is_join_month else 0
        default_data.custom_current_month_joining = data.custom_current_month_joining
        if self.joining_date:
            joining_date = getdate(self.joining_date)
            data.joining_date = joining_date
            default_data.joining_date = joining_date
        return data, default_data

    def _count_payable_attendance_days(
        self,
        attendance_by_date,
        period_start,
        period_end,
        holidays,
        consider_unmarked_as,
        daily_wages_fraction_for_half_day,
        is_staff=False,
        national_holiday_dates=None,
    ):
        """Paid days = Present + paid leaves + half-days; excludes absent, LWP, weekly off.

        Unmarked national holidays on the employee calendar count as paid for all employees.
        If leave, present, or absent is marked on that date, attendance rules apply instead
        (workers who did not work on a national holiday are marked Absent).
        """
        national_holiday_dates = national_holiday_dates or set()
        leave_type_map = self.get_leave_type_map()
        payable_days = 0
        current_date = period_start

        while current_date <= period_end:
            is_holiday = current_date in holidays
            row = attendance_by_date.get(current_date)

            if row:
                status = row.status
                if status == "Present":
                    payable_days += 1
                elif status == "Work From Home":
                    payable_days += 1
                elif status == "Weekly Off":
                    pass
                elif status == "On Leave":
                    leave_type = row.leave_type
                    if leave_type in leave_type_map and (
                        leave_type_map[leave_type].get("is_lwp") or leave_type_map[leave_type].get("is_ppl")
                    ):
                        pass
                    elif leave_type in PAID_LEAVE_TYPES or not leave_type:
                        payable_days += 1
                elif status == "Half Day":
                    if row.half_day_status == "Absent":
                        # Present for half the day; other half is absent.
                        payable_days += daily_wages_fraction_for_half_day
                    elif row.leave_type in leave_type_map and (
                        leave_type_map[row.leave_type].get("is_lwp")
                        or leave_type_map[row.leave_type].get("is_ppl")
                    ):
                        payable_days += 1 - daily_wages_fraction_for_half_day
                    else:
                        payable_days += daily_wages_fraction_for_half_day
            elif current_date in national_holiday_dates:
                payable_days += 1
            elif consider_unmarked_as == "Present" and not is_holiday:
                if is_staff and current_date.weekday() == 6:
                    pass
                else:
                    payable_days += 1

            current_date += timedelta(days=1)

        return payable_days

    def _calculate_actual_lwp(self, payroll_settings, holidays, period_start, period_end):
        """Use standard HRMS LWP/PPL calculation from Leave Type master."""
        if not payroll_settings.payroll_based_on:
            frappe.throw(_("Please set Payroll based on in Payroll settings"))

        daily_wages_fraction_for_half_day = (
            flt(payroll_settings.daily_wages_fraction_for_half_day) or 0.5
        )
        consider_marked_attendance_on_holidays = (
            payroll_settings.include_holidays_in_total_working_days
            and payroll_settings.consider_marked_attendance_on_holidays
        )
        holidays_list = list(holidays)

        if payroll_settings.payroll_based_on == "Attendance":
            return self.calculate_lwp_ppl_and_absent_days_based_on_attendance(
                holidays_list,
                daily_wages_fraction_for_half_day,
                consider_marked_attendance_on_holidays,
            )[0]

        period_days = (period_end - period_start).days + 1
        working_days_list = [period_start + timedelta(days=day) for day in range(period_days)]

        return self.calculate_lwp_or_ppl_based_on_leave_application(
            holidays_list,
            working_days_list,
            daily_wages_fraction_for_half_day,
        )

    def get_working_days_details(self, lwp=None, for_preview=0, lwp_days_corrected=None):
        """
        Custom logic applies ONLY when:
        Salary Structure Assignment.weekly_off_on_attendance = 1

        Otherwise ERPNext default behavior is used.
        """
        if not self.employee:
            return self.call_super_working_days(lwp, for_preview, lwp_days_corrected)

        start_date = getdate(self.start_date)
        end_date = getdate(self.end_date)
        joining_date = getdate(self.joining_date) if self.joining_date else None
        relieving_date = getdate(self.relieving_date) if self.relieving_date else None

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
                "mark_absent_on_public_holiday",
                "custom_current_month_joining",
                "salary_structure",
            ],
            order_by="from_date desc",
        )

        if not ssa:
            return self.call_super_working_days(lwp, for_preview, lwp_days_corrected)

        (
            weekly_off_on_attendance,
            override_absent_on_holiday,
            mark_absent_on_public_holiday,
            current_month_joining,
            salary_structure,
        ) = ssa

        if not cint(weekly_off_on_attendance):
            return self.call_super_working_days(lwp, for_preview, lwp_days_corrected)

        override_absent_on_holiday = cint(override_absent_on_holiday or 0)
        mark_absent_on_public_holiday = cint(mark_absent_on_public_holiday or 0)

        if for_preview:
            total_days = (end_date - start_date).days + 1
            self.total_working_days = total_days
            self.payment_days = total_days
            self.absent_days = 0
            self.leave_without_pay = 0
            return

        payroll_settings = self._get_payroll_settings()
        daily_wages_fraction_for_half_day = (
            flt(payroll_settings.daily_wages_fraction_for_half_day) or 0.5
        )
        consider_unmarked_as = payroll_settings.consider_unmarked_attendance_as

        period_start = max(start_date, joining_date) if joining_date else start_date
        period_end = min(end_date, relieving_date) if relieving_date else end_date

        if period_end < period_start:
            total_working_days = 0
        elif self._is_current_month_joining(start_date, end_date, joining_date, current_month_joining):
            total_working_days = (end_date - start_date).days + 1
        else:
            total_working_days = (period_end - period_start).days + 1

        holidays = set(get_employee_holidays(self.employee, start_date, end_date))

        holiday_list_name = get_employee_holiday_list_name(self.employee, period_start, period_end)
        pph_holiday_dates = set()

        if holiday_list_name:
            raw = frappe.db.get_all(
                "Holiday",
                filters={
                    "parent": holiday_list_name,
                    "holiday_date": ["between", [period_start, period_end]],
                },
                fields=["holiday_date", "weekly_off"],
            )
            pph_holiday_dates = {getdate(row.holiday_date) for row in raw if not row.weekly_off}
            holidays |= pph_holiday_dates | {
                getdate(row.holiday_date) for row in raw if row.weekly_off
            }

        attendance_rows = frappe.get_all(
            "Attendance",
            filters={
                "employee": self.employee,
                "attendance_date": ["between", [period_start, period_end]],
                "docstatus": 1,
            },
            fields=["attendance_date", "status", "leave_type", "half_day_status"],
        )
        attendance_by_date = {getdate(row.attendance_date): row for row in attendance_rows}

        # When current-month joiners use the full payroll month as working days,
        # unmarked days before DOJ must still reduce payment days.
        is_join_month = self._is_current_month_joining(
            start_date, end_date, joining_date, current_month_joining
        )
        attendance_count_start = start_date if is_join_month else period_start

        absent_days = 0
        unmarked_days = 0
        current_date = attendance_count_start
        while current_date <= period_end:
            is_holiday = current_date in holidays
            row = attendance_by_date.get(current_date)
            status = row.status if row else None

            if status == "Absent":
                if (
                    mark_absent_on_public_holiday
                    and current_date in pph_holiday_dates
                ):
                    absent_days += 1
                elif override_absent_on_holiday or not is_holiday:
                    absent_days += 1
            elif status is None:
                if not is_holiday:
                    unmarked_days += 1
                    if consider_unmarked_as == "Absent":
                        absent_days += 1

            current_date += timedelta(days=1)

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
            half_day_query = half_day_query.where(Attendance.attendance_date.notin(list(holidays)))

        half_absent_days = half_day_query.run()[0][0]
        absent_days += half_absent_days * daily_wages_fraction_for_half_day

        self.custom_pph = sum(
            1
            for date, row in attendance_by_date.items()
            if date in pph_holiday_dates and row.status in ("Present", "Half Day")
        )
        self.custom_public_holiday = len(pph_holiday_dates)

        paid_leaves = 0
        for row in attendance_by_date.values():
            if row.leave_type in PAID_LEAVE_TYPES:
                if row.status == "On Leave":
                    paid_leaves += 1
                elif row.status == "Half Day":
                    paid_leaves += daily_wages_fraction_for_half_day

        self.custom_paid_leaves = paid_leaves

        actual_lwp = self._calculate_actual_lwp(payroll_settings, holidays, period_start, period_end)

        if not lwp:
            lwp = actual_lwp
        elif flt(lwp) != flt(actual_lwp):
            frappe.msgprint(
                _("Leave Without Pay does not match with approved {} records").format(
                    payroll_settings.payroll_based_on
                )
            )
        
        # Calculate employee weekly off days based on Employee Weekly Off Master
        
        weekly_off_records = frappe.get_all(
            "Employee Weekly Off Master",
            filters={
                "employee": self.employee,
                "company": self.company,
                "branch": self.branch,
                "from_date": ["<=", end_date],
                
            },
            fields=["weekly_off_day", "to_date"],
            order_by="from_date desc",
        )
        
        weekly_off_day = None
        for row in weekly_off_records:
            if not row.to_date or getdate(row.to_date) >= start_date:
                weekly_off_day = row.weekly_off_day
                break
        if weekly_off_day:
            week_map ={
                "Monday": 0,
                "Tuesday": 1,
                "Wednesday": 2,
                "Thursday": 3,
                "Friday": 4,
                "Saturday": 5,
                "Sunday": 6,	
            }

            weekly_off_index = week_map.get(weekly_off_day)
            weekly_off_count = 0
            current_date = start_date

            while current_date <= end_date:
                if current_date.weekday() == weekly_off_index:
                    weekly_off_count += 1
                current_date += timedelta(days=1)
            
            self.custom_employee_weekly_off_days = weekly_off_count
        else:
            self.custom_employee_weekly_off_days = 0
         
    
          
        
        self.total_working_days = total_working_days
        self.absent_days = max(0, absent_days)
        self.unmarked_days = max(0, unmarked_days)
        self.leave_without_pay = flt(lwp)

        is_worker = self._is_worker_structure(salary_structure)

        payable_days = self._count_payable_attendance_days(
            attendance_by_date,
            period_start,
            period_end,
            holidays,
            consider_unmarked_as,
            daily_wages_fraction_for_half_day,
            is_staff=not is_worker,
            national_holiday_dates=pph_holiday_dates,
        )

        if is_worker:
            self.payment_days = max(
                0,
                flt(self.total_working_days) - flt(self.absent_days) - flt(self.leave_without_pay),
            )
        else:
            self.payment_days = max(0, flt(payable_days))
            
        if HRMS_VERSION.major >= 16 and lwp_days_corrected and lwp_days_corrected > 0:
            try:
                from hrms.payroll.doctype.salary_slip.salary_slip import verify_lwp_days_corrected

                if verify_lwp_days_corrected(
                    self.employee,
                    self.start_date,
                    self.end_date,
                    lwp_days_corrected,
                ):
                    self.payment_days += lwp_days_corrected
            except ImportError:
                pass
        