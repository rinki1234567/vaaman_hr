import frappe
from frappe.utils import getdate
from collections import defaultdict

def process_attendance_policy():
    # Fetch attendance records for late entry
    attendance_records = frappe.db.sql("""
        SELECT name, employee, attendance_date, in_time, attendance_request
        FROM `tabAttendance`
        WHERE status = 'Present'
        AND docstatus = 1
        AND TIME(in_time) BETWEEN '10:01:00' AND '11:00:00'
        AND attendance_date >= '2025-06-01'
        AND custom_branch = 'Head Office'
        ORDER BY employee, attendance_date
    """, as_dict=True)

    # Group by employee and then by month
    employee_monthly_late = defaultdict(lambda: defaultdict(list))

    for record in attendance_records:
        date_obj = getdate(record["attendance_date"])
        month_key = f"{date_obj.year}-{date_obj.month:02d}"
        employee_monthly_late[record["employee"]][month_key].append(record)

    # Process each employee's monthly records
    for employee, month_data in employee_monthly_late.items():
        for month, records in month_data.items():
            if len(records) <= 3:
                continue  # within allowed late entries

            # Only mark the 4th, 5th, etc. late entries as Absent
            for record in records[3:]:
                if record["attendance_request"]:
                    continue  # skip if linked to attendance request

                try:
                    original = frappe.get_doc("Attendance", record["name"])

                    if original.docstatus == 1:
                        original.cancel()

                    amended_att = frappe.copy_doc(original)
                    amended_att.docstatus = 1
                    amended_att.status = "Absent"
                    amended_att.amended_from = original.name
                    amended_att.save(ignore_permissions=True)

                    amended_att.add_comment("Comment", "Marked as Absent due to 4th or subsequent late entry (10:01–11:00) in the same month.")

                    frappe.get_doc({
                        "doctype": "Attendance Policy Log",
                        "employee": original.employee,
                        "attendance": amended_att.name,
                        "attendance_date": amended_att.attendance_date,
                        "action_taken": "Converted to Absent",
                        "remarks": "Exceeded 3 late entries (based on in_time) for this month"
                    }).insert(ignore_permissions=True)

                    user_id = frappe.db.get_value("Employee", employee, "user_id")
                    if user_id:
                        frappe.sendmail(
                            recipients=[user_id],
                            subject="Attendance Policy Violation",
                            message=f"You have been marked as Absent on {amended_att.attendance_date} "
                                    f"for exceeding 3 late entries (between 10:01 and 11:00) in {month}."
                        )

                    frappe.db.commit()

                except Exception as e:
                    frappe.log_error(
                        f"Failed to amend attendance for {employee} on {record['attendance_date']}: {str(e)}",
                        "Attendance Policy"
                    )
