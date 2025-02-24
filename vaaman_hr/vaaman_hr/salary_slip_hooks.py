import frappe

def calculate_overtime_hours(doc, method):
    if not doc.start_date or not doc.end_date:
        return
    
    # Fetch total overtime hours from Attendance
    total_overtime = frappe.db.sql("""
        SELECT SUM(custom_over_time) 
        FROM `tabAttendance`
        WHERE employee = %s 
        AND attendance_date BETWEEN %s AND %s
        AND docstatus = 1
    """, (doc.employee, doc.start_date, doc.end_date))[0][0] or 0
    
    # Update the total_overtime_hours field in Salary Slip
    doc.custom_total_overtime_hours = total_overtime
