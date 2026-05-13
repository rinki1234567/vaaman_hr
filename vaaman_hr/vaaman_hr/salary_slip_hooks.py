import frappe

def calculate_overtime_hours(doc, method):
    if not doc.start_date or not doc.end_date:
        return

    # Fetch total overtime hours from Overtime Import Item
    total_overtime = frappe.db.sql("""
        SELECT SUM(oti.over_time)
        FROM `tabOvertime Import Item` oti
        INNER JOIN `tabOverTime Import` oti_parent
            ON oti.parent = oti_parent.name
        WHERE oti.employee = %s
        AND oti.attendance_date BETWEEN %s AND %s
        AND oti_parent.docstatus = 1
    """, (doc.employee, doc.start_date, doc.end_date))[0][0] or 0

    # Update Salary Slip field
    doc.total_overtime_hours = total_overtime
