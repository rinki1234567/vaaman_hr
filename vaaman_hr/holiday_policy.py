import frappe
from frappe.utils import getdate, add_days, today


def debug_holiday_sandwich():
    """Read-only debug function — no records created, just prints what it finds.
    Usage: bench --site [site] execute vaaman_hr.holiday_policy.debug_holiday_sandwich
    """
    holiday_date = add_days(getdate(today()), -1)
    day_before = add_days(holiday_date, -1)
    day_after = add_days(holiday_date, 1)

    print(f"\n=== Holiday Sandwich Debug ===")
    print(f"Holiday date : {holiday_date}")
    print(f"Day before   : {day_before}")
    print(f"Day after    : {day_after}\n")

    employees = frappe.db.sql("""
        SELECT name, holiday_list, company, branch
        FROM `tabEmployee`
        WHERE status = 'Active'
        AND holiday_list IS NOT NULL
        AND holiday_list != ''
    """, as_dict=True)

    print(f"Total active employees with holiday list: {len(employees)}")

    # Show upcoming/recent holidays across all holiday lists for reference
    recent_holidays = frappe.db.sql("""
        SELECT h.holiday_date, h.description, h.parent
        FROM `tabHoliday` h
        WHERE h.weekly_off = 0
        AND h.holiday_date BETWEEN %s AND %s
        ORDER BY h.holiday_date
        LIMIT 10
    """, (add_days(holiday_date, -30), add_days(holiday_date, 30)), as_dict=True)

    print(f"\nHolidays within 30 days of {holiday_date}:")
    if recent_holidays:
        for h in recent_holidays:
            print(f"  {h.holiday_date} — {h.description} ({h.parent})")
    else:
        print("  None found")
    print()

    matched = 0
    for employee in employees:
        is_holiday = frappe.db.exists("Holiday", {
            "parent": employee.holiday_list,
            "holiday_date": holiday_date
        })
        if not is_holiday:
            continue

        day_before_absent = frappe.db.exists("Attendance", {
            "employee": employee.name,
            "attendance_date": day_before,
            "status": "Absent",
            "docstatus": 1
        })

        day_after_absent = frappe.db.exists("Attendance", {
            "employee": employee.name,
            "attendance_date": day_after,
            "status": "Absent",
            "docstatus": 1
        })

        already_exists = frappe.db.exists("Attendance", {
            "employee": employee.name,
            "attendance_date": holiday_date,
            "docstatus": ["!=", 2]
        })

        print(f"Employee     : {employee.name}")
        print(f"  Holiday list  : {employee.holiday_list}")
        print(f"  Is holiday    : {bool(is_holiday)}")
        print(f"  Before absent : {bool(day_before_absent)}")
        print(f"  After absent  : {bool(day_after_absent)}")
        print(f"  Already exists: {bool(already_exists)}")

        if is_holiday and day_before_absent and day_after_absent and not already_exists:
            print(f"  >>> WILL BE MARKED ABSENT <<<")
            matched += 1
        print()

    print(f"=== Total employees that will be processed: {matched} ===")


def process_holiday_sandwich_policy():
    # Runs daily at 11 PM — checks if yesterday was a holiday.
    # If employee was Absent the day before AND day after that holiday → mark holiday as Absent.
    holiday_date = add_days(getdate(today()), -1)

    employees = frappe.db.sql("""
        SELECT name, holiday_list, user_id, company, branch
        FROM `tabEmployee`
        WHERE status = 'Active'
        AND holiday_list IS NOT NULL
        AND holiday_list != ''
    """, as_dict=True)

    for employee in employees:
        is_holiday = frappe.db.exists("Holiday", {
            "parent": employee.holiday_list,
            "holiday_date": holiday_date
        })

        if not is_holiday:
            continue

        day_before = add_days(holiday_date, -1)
        day_after = add_days(holiday_date, 1)

        day_before_absent = frappe.db.exists("Attendance", {
            "employee": employee.name,
            "attendance_date": day_before,
            "status": "Absent",
            "docstatus": 1
        })

        day_after_absent = frappe.db.exists("Attendance", {
            "employee": employee.name,
            "attendance_date": day_after,
            "status": "Absent",
            "docstatus": 1
        })

        if not (day_before_absent and day_after_absent):
            continue

        already_exists = frappe.db.exists("Attendance", {
            "employee": employee.name,
            "attendance_date": holiday_date,
            "docstatus": ["!=", 2]
        })

        if already_exists:
            continue

        try:
            att = frappe.new_doc("Attendance")
            att.employee = employee.name
            att.attendance_date = holiday_date
            att.status = "Absent"
            att.company = employee.company
            att.custom_branch = employee.branch
            att.insert(ignore_permissions=True)
            att.submit()

            frappe.get_doc({
                "doctype": "Attendance Policy Log",
                "employee": employee.name,
                "attendance": att.name,
                "attendance_date": str(holiday_date),
                "action_taken": "Marked as Absent (Holiday Sandwich)",
                "remarks": (
                    f"Holiday on {holiday_date} marked as Absent — "
                    f"employee was absent on {day_before} (day before) "
                    f"and {day_after} (day after) the holiday."
                )
            }).insert(ignore_permissions=True)

            if employee.user_id:
                frappe.sendmail(
                    recipients=[employee.user_id],
                    subject="Attendance Policy — Holiday Marked as Absent",
                    message=(
                        f"Your holiday on {holiday_date} has been marked as Absent "
                        f"because you were absent on {day_before} (day before the holiday) "
                        f"and {day_after} (day after the holiday), "
                        f"as per the company attendance policy."
                    )
                )

            frappe.db.commit()

        except Exception as e:
            frappe.log_error(
                f"Failed to mark holiday as absent for {employee.name} on {holiday_date}: {str(e)}",
                "Holiday Sandwich Policy"
            )
