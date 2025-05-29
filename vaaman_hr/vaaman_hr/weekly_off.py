import frappe
from frappe.utils import getdate

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
