import frappe
from frappe.utils import getdate, add_months, formatdate

def execute(filters=None):
    conditions = ["custom_over_time > 0"]  # Ensure only overtime > 0 is shown
    values = {}

    if filters.get("employee"):
        conditions.append("employee = %(employee)s")
        values["employee"] = filters.get("employee")

    if filters.get("branch"):
        conditions.append("custom_branch = %(branch)s")
        values["branch"] = filters.get("branch")

    if filters.get("attendance_date"):
        conditions.append("attendance_date = %(attendance_date)s")
        values["attendance_date"] = filters.get("attendance_date")

    if filters.get("month"):
        month_map = {
            "January": "01", "February": "02", "March": "03", "April": "04",
            "May": "05", "June": "06", "July": "07", "August": "08",
            "September": "09", "October": "10", "November": "11", "December": "12"
        }
        month = month_map.get(filters.get("month"))
        conditions.append("MONTH(attendance_date) = %(month)s")
        values["month"] = month

    condition_str = " AND ".join(conditions)

    data = frappe.db.sql(f"""
        SELECT 
            name AS attendance_name,
            employee,
            employee_name,
            custom_branch as branch,
            attendance_date,
            custom_over_time AS overtime_hour
        FROM `tabAttendance`
        WHERE {condition_str}
        ORDER BY attendance_date DESC
    """, values, as_dict=True)

    columns = [
        {"label": "Attendance", "fieldname": "attendance_name", "fieldtype": "Link", "options": "Attendance", "width": 180},
        {"label": "Employee", "fieldname": "employee", "fieldtype": "Link", "options": "Employee", "width": 150},
        {"label": "Employee Name", "fieldname": "employee_name", "fieldtype": "Data", "width": 200},
        {"label": "Branch", "fieldname": "branch", "fieldtype": "Link", "options": "Branch", "width": 150},
        {"label": "Date", "fieldname": "attendance_date", "fieldtype": "Date", "width": 120},
        {"label": "Overtime Hours", "fieldname": "overtime_hour", "fieldtype": "Float", "width": 120}
    ]

    return columns, data
