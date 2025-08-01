import frappe
from frappe.utils import nowdate
from datetime import datetime
from collections import defaultdict

def process_attendance_policy():
    # Get all attendance records from June 1st onward with in_time between 10:01 and 11:00
    attendance_records = frappe.db.sql("""
        SELECT name, employee, attendance_date, in_time, attendance_request
        FROM `tabAttendance`
        WHERE status = 'Present'
        AND docstatus = 1
        AND TIME(in_time) BETWEEN '10:01:00' AND '11:00:00'
        AND attendance_date >= '2025-06-01'
        """, as_dict=True
    )

    # Track employee infractions
    from collections import defaultdict
    employee_late_days = defaultdict(list)
    for record in attendance_records:
        employee_late_days[record["employee"]].append(record)

    for employee, late_days in employee_late_days.items():
        if len(late_days) > 3:
            for i, record in enumerate(late_days[3:], start=4):  # 4th onwards
                # Skip if linked to an Attendance Request
                if record["attendance_request"]:
                    continue

                try:
                    original = frappe.get_doc("Attendance", record["name"])

                    if original.docstatus == 1:
                        original.cancel()

                    amended_att = frappe.copy_doc(original)
                    amended_att.docstatus = 1
                    amended_att.status = "Absent"
                    amended_att.amended_from = original.name
                    amended_att.save(ignore_permissions=True)

                    amended_att.add_comment("Comment", "Marked as Absent due to late entry between 10:01 and 11:00 (4th or later this month).")

                    frappe.get_doc({
                        "doctype": "Attendance Policy Log",
                        "employee": original.employee,
                        "attendance": amended_att.name,
                        "attendance_date": amended_att.attendance_date,
                        "action_taken": "Converted to Absent",
                        "remarks": "Exceeded 3 late entries (based on in_time)"
                    }).insert(ignore_permissions=True)

                    user_id = frappe.db.get_value("Employee", employee, "user_id")
                    if user_id:
                        frappe.sendmail(
                            recipients=[user_id],
                            subject="Attendance Policy Violation",
                            message=f"You have been marked as Absent on {amended_att.attendance_date} "
                                    f"for exceeding 3 late entries (between 10:01 and 11:00) this month."
                        )

                    frappe.db.commit()

                except Exception as e:
                    frappe.log_error(f"Failed to amend attendance for {employee} on {record['attendance_date']}: {str(e)}", "Attendance Policy")
