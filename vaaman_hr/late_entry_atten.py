import frappe
from frappe.utils import nowdate
from datetime import datetime
from collections import defaultdict

def process_attendance_policy():
    current_month = datetime.today().strftime("%Y-%m")

    # Get all attendance records marked as Late or Early in the current month
    attendance_records = frappe.db.sql("""
        SELECT name, employee, attendance_date
        FROM `tabAttendance`
        WHERE status = 'Present'
        AND docstatus = 1
        AND late_entry = 1
        AND attendance_date LIKE %s
        """, (f"{current_month}%",), as_dict=True
    )


    # Track employee infractions
    employee_late_days = defaultdict(list)
    for record in attendance_records:
        employee_late_days[record["employee"]].append(record)

    for employee, late_days in employee_late_days.items():
        if len(late_days) > 3:
            # Get the 4th or later occurrences
            for i, record in enumerate(late_days[3:], start=4):  # 4th onwards
                try:
                    original = frappe.get_doc("Attendance", record["name"])

                    # Cancel the original record
                    if original.docstatus == 1:
                        original.cancel()

                    # Amend the cancelled record
                    amended_att = frappe.copy_doc(original)
                    amended_att.docstatus = 1
                    amended_att.status = "On Leave"
                    amended_att.leave_type = "Casual Leave"
                    # amended_att.add_comment("Comment", "Marked as Casual Leave due to late entry (4th or later occurrence this month).")
                    amended_att.amended_from = original.name
                    amended_att.save(ignore_permissions=True)

                    # Log the action
                    frappe.get_doc({
                        "doctype": "Attendance Policy Log",
                        "employee": original.employee,
                        "attendance": amended_att.name,
                        "attendance_date": amended_att.attendance_date,
                        "action_taken": "Converted to Casual Leave",
                        "remarks": "Exceeded 3 late/early marks"
                    }).insert(ignore_permissions=True)

                    # Send notification
                    user_id = frappe.db.get_value("Employee", employee, "user_id")
                    if user_id:
                        frappe.sendmail(
                            recipients=[user_id],
                            subject="Attendance Policy Violation",
                            message=f"You have been marked as Casual Leave on {amended_att.attendance_date} "
                                    f"for exceeding 3 late/early entries this month."
                        )

                    frappe.db.commit()

                except Exception as e:
                    frappe.log_error(f"Failed to amend attendance for {employee} on {record['attendance_date']}: {str(e)}", "Attendance Policy")
