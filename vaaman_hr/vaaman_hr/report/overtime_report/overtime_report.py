
import frappe
from frappe.utils import getdate, add_months, formatdate

def execute(filters=None):
    conditions = ["1=1"]  
    values = {}

    if filters.get("employee"):
        conditions.append("ot.employee = %(employee)s")
        values["employee"] = filters.get("employee")

    if filters.get("branch"):
        conditions.append("emp.branch = %(branch)s")
        values["branch"] = filters.get("branch")

    if filters.get("attendance_date"):
        conditions.append("""
            COALESCE(att.attendance_date, ot.attendance_date) = %(attendance_date)s
        """)
        values["attendance_date"] = filters.get("attendance_date")
        
    if filters.get("month"):
        month_map = {
            "January": "01", "February": "02", "March": "03", "April": "04",
            "May": "05", "June": "06", "July": "07", "August": "08",
            "September": "09", "October": "10", "November": "11", "December": "12"
        }
        
        values["month"] = month_map.get(filters.get("month"))
        conditions.append("""
            MONTH(COALESCE(att.attendance_date, ot.attendance_date)) = %(month)s
        """)

    condition_str = " AND ".join(conditions)
    

    data = frappe.db.sql(f"""
        SELECT
            att.name AS attendance_name,
            COALESCE(att.employee, ot.employee) AS employee,
            emp.employee_name,
            COALESCE(att.custom_branch, emp.branch) AS branch,
            COALESCE(att.attendance_date, ot.attendance_date) AS attendance_date,

            IFNULL(att.custom_over_time, 0) AS overtime_hour,

            IFNULL(SUM(ot.over_time), 0) AS total_uploaded_overtime

        FROM `tabOvertime Import Item` ot
        INNER JOIN `tabOverTime Import` oi
            ON oi.name = ot.parent
            AND oi.docstatus = 1

        LEFT JOIN `tabAttendance` att
            ON att.employee = ot.employee
            AND att.attendance_date = ot.attendance_date

        INNER JOIN `tabEmployee` emp
            ON emp.name = ot.employee
        
        WHERE {condition_str}

        GROUP BY
            COALESCE(att.employee, ot.employee),
            COALESCE(att.attendance_date, ot.attendance_date)

        ORDER BY attendance_date DESC
    """,values, as_dict=True)


    columns = [
        {"label": "Attendance", "fieldname": "attendance_name", "fieldtype": "Link", "options": "Attendance", "width": 180},
        {"label": "Employee", "fieldname": "employee", "fieldtype": "Link", "options": "Employee", "width": 150},
        {"label": "Employee Name", "fieldname": "employee_name", "fieldtype": "Data", "width": 200},
        {"label": "Branch", "fieldname": "branch", "fieldtype": "Link", "options": "Branch", "width": 150},
        {"label": "Date", "fieldname": "attendance_date", "fieldtype": "Date", "width": 120},
        {"label": "Overtime Hours", "fieldname": "overtime_hour", "fieldtype": "Float", "width": 120},
        {"label": "Total Uploaded Overtime", "fieldname": "total_uploaded_overtime", "fieldtype": "Float", "width": 150}
    ]

    return columns, data
