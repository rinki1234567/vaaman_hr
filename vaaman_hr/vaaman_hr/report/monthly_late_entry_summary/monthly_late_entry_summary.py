import frappe
from frappe.utils import get_first_day, get_last_day

def execute(filters=None):
    columns = [
        {"label": "Employee", "fieldname": "employee", "fieldtype": "Link", "options": "Employee", "width": 120},
        {"label": "Employee Name", "fieldname": "employee_name", "fieldtype": "Data", "width": 150},
        {"label": "Late Entries", "fieldname": "late_entries", "fieldtype": "Int", "width": 100},
        {"label": "Early Exits", "fieldname": "early_exits", "fieldtype": "Int", "width": 100},
        {"label": "Total Occasions (Late+Early)", "fieldname": "total_occasions", "fieldtype": "Int", "width": 160},
        {"label": "Excess (Above 3)", "fieldname": "converted", "fieldtype": "Int", "width": 120},
    ]

    from_date = filters.get("from_date")
    to_date = filters.get("to_date")

    data = []
    employees = frappe.get_all("Employee", filters={"status": "Active"}, fields=["name", "employee_name"])

    for emp in employees:
        logs = frappe.db.sql("""
            SELECT name, late_entry, early_exit
            FROM `tabAttendance`
            WHERE employee = %s
              AND attendance_date BETWEEN %s AND %s
              AND status = 'Present'
        """, (emp.name, from_date, to_date), as_dict=True)

        late = sum(1 for l in logs if l.late_entry)
        early = sum(1 for l in logs if l.early_exit)
        # Combined max 3: same day with both flags counts once
        total_occasions = sum(1 for l in logs if l.late_entry or l.early_exit)
        converted = max(0, total_occasions - 3)

        data.append({
            "employee": emp.name,
            "employee_name": emp.employee_name,
            "late_entries": late,
            "early_exits": early,
            "total_occasions": total_occasions,
            "converted": converted,
        })

    return columns, data
