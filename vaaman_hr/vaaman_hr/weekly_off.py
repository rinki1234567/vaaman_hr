import frappe
from frappe.utils import getdate, flt
from datetime import timedelta

def log_error(context, doc, error):
    frappe.log_error(f"""
[Context] {context}
[Doc] {doc.doctype} - {doc.name}
[Error] {str(error)}
""", f"{doc.doctype} Hook Error")

def set_total_weekly_off(doc, method):
    employee = doc.employee
    from_date = getdate(doc.start_date)
    to_date = getdate(doc.end_date)

    # Get active Salary Structure Assignment
    assignment = frappe.get_all(
        "Salary Structure Assignment",
        filters={
            "employee": employee,
            "from_date": ["<=", to_date],
            "docstatus": 1
        },
        order_by="from_date desc",
        limit=1,
        fields=["name", "weekly_off_on_attendance"]
    )

    use_attendance = False
    if assignment:
        use_attendance = assignment[0].weekly_off_on_attendance == 1

    total_weekly_off = 0

    if use_attendance:
        # Use only Attendance with "Weekly Off" status
        attendance_offs = frappe.get_all(
            "Attendance",
            filters={
                "employee": employee,
                "status": "Weekly Off",
                "attendance_date": ["between", [from_date, to_date]]
            }
        )
        total_weekly_off = len(attendance_offs)
    else:
        # Use only Holiday List assigned to employee
        holiday_list = frappe.get_value("Employee", employee, "holiday_list")
        if holiday_list:
            holidays = frappe.get_all(
                "Holiday",
                filters={
                    "holiday_date": ["between", [from_date, to_date]],
                    "parent": holiday_list
                }
            )
            total_weekly_off = len(holidays)

    doc.total_weekly_off = total_weekly_off
# ---------- Helper: Calculate Standard Weekly Offs ----------
def get_standard_weekoffs(start_date, end_date):
    try:
        start = getdate(start_date)
        end = getdate(end_date)
        count = 0
        while start <= end:
            if start.weekday() == 6:  # Sunday
                count += 1
            start += timedelta(days=1)
        return count
    except Exception as e:
        frappe.log_error(f"Date error:\nStart: {start_date}\nEnd: {end_date}\n{e}", "get_standard_weekoffs")
        return 0

# ---------- Main Hook: Set Standard Weekly Off & Salary Weekly Off ----------
def before_save(doc, method):
    try:
        if not (doc.start_date and doc.end_date):
            return

        from_date = getdate(doc.start_date)
        to_date = getdate(doc.end_date)
        employee = doc.employee

        # ----- Recalculate total_weekly_off dynamically -----
        total_weekly_off = 0
        assignment = frappe.get_all(
            "Salary Structure Assignment",
            filters={
                "employee": employee,
                "from_date": ["<=", to_date],
                "docstatus": 1
            },
            order_by="from_date desc",
            limit=1,
            fields=["name", "weekly_off_on_attendance"]
        )

        use_attendance = False
        if assignment:
            use_attendance = assignment[0].weekly_off_on_attendance == 1

        if use_attendance:
            attendance_offs = frappe.get_all(
                "Attendance",
                filters={
                    "employee": employee,
                    "status": "Weekly Off",
                    "attendance_date": ["between", [from_date, to_date]]
                }
            )
            total_weekly_off = len(attendance_offs)
        else:
            holiday_list = frappe.get_value("Employee", employee, "holiday_list")
            if holiday_list:
                holidays = frappe.get_all(
                    "Holiday",
                    filters={
                        "holiday_date": ["between", [from_date, to_date]],
                        "parent": holiday_list
                    }
                )
                total_weekly_off = len(holidays)

        doc.total_weekly_off = total_weekly_off

        # ----- Calculate and set standard_weekly_off (Sundays) -----
        standard_weekoff = get_standard_weekoffs(from_date, to_date)
        doc.standard_weekly_off = standard_weekoff

        # ----- Set salary-eligible weekly offs -----
        if total_weekly_off >= 5:
            doc.sal_weekoff = 5
        elif total_weekly_off < standard_weekoff:
            doc.sal_weekoff = standard_weekoff
        else:
            doc.sal_weekoff = total_weekly_off

    except Exception as e:
        log_error("before_save", doc, e)
