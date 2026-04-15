
import frappe
from frappe.utils import getdate, get_time
from collections import defaultdict

def process_attendance_policy():
    print("--- Script Started ---")
    # Fetch attendance records for late entry
    
    attendance_records = frappe.db.sql("""
        SELECT att.name, att.employee, att.attendance_date, att.in_time, att.out_time, att.attendance_request
        FROM `tabAttendance` att
        WHERE att.status = 'Present'
        AND att.docstatus = 1
        AND att.attendance_date >= '2025-01-01'
        AND att.custom_branch = 'Head Office'
        AND NOT EXISTS (
                SELECT la.name FROM `tabLeave Application` la 
                WHERE la.employee = att.employee 
                AND la.attendance_date = att.attendance_date 
                AND la.status = 'Approved' 
                AND la.half_day = 1
            )
        AND (
                TIME(att.in_time) > '10:15:00' 
                OR (DAYOFWEEK(att.attendance_date) BETWEEN 2 AND 6 AND TIME(att.out_time) < '18:15:00')
                OR (DAYOFWEEK(att.attendance_date) = 7 AND TIME(att.out_time) < '17:00:00')
            )
        ORDER BY att.employee, att.attendance_date
    """, as_dict=True)

    print(f"Total records found: {len(attendance_records)}")

    # Group by employee and then by month
    employee_monthly_late = defaultdict(lambda: defaultdict(list))

    for record in attendance_records:
        print(f"Processing: {record['employee']} on {record['attendance_date']}")
        date_obj = getdate(record["attendance_date"])
        month_key = f"{date_obj.year}-{date_obj.month:02d}"
        employee_monthly_late[record["employee"]][month_key].append(record)

    # Process each employee's monthly records
    for employee, month_data in employee_monthly_late.items():
        for month, records in month_data.items():
            if len(records) <= 3:
                continue 

            for record in records[3:]:
                if record["attendance_request"]:
                    continue 

                try:
                    in_time_val = get_time(record["in_time"])
                    out_time_val = get_time(record["out_time"])
                    is_sat = getdate(record["attendance_date"]).strftime('%a') == 'Sat'
                    
                    if in_time_val > get_time("10:15:00"):
                        reason = "Late Entry (After 10:15 AM)"
                    elif is_sat and out_time_val < get_time("17:00:00"):
                        reason = "Early Leaving (Saturday before 05:00 PM)"
                    else:
                        reason = "Early Leaving (Before 06:15 PM)"
                        
                    original = frappe.get_doc("Attendance", record["name"])

                    if original.docstatus == 1:
                        original.cancel()

                    amended_att = frappe.copy_doc(original)
                    amended_att.docstatus = 1
                    amended_att.status = "Absent"
                    amended_att.amended_from = original.name
                    amended_att.save(ignore_permissions=True)

                    amended_att.add_comment("Comment", f"Marked as Absent due to 4th or subsequent late entry ({reason}) in the same month.")
                    
                    # Log entry in custom DocType
                    frappe.get_doc({
                        "doctype": "Attendance Policy Log",
                        "employee": original.employee,
                        "attendance": amended_att.name,
                        "attendance_date": amended_att.attendance_date,
                        "action_taken": "Converted to Absent",
                        "remarks": f"Exceeded 3 occurrences (Late/Early). Current violation: {reason}"
                    }).insert(ignore_permissions=True)

                    user_id = frappe.db.get_value("Employee", employee, "user_id")
                    if user_id:
                        frappe.sendmail(
                            recipients=[user_id],
                            subject="Attendance Policy Violation",
                            message=f"You have been marked as Absent on {amended_att.attendance_date} "
                                    f"for exceeding 3 allowed occasions of Late Entry or Early Leaving in {month}."
                        )

                    frappe.db.commit()

                except Exception as e:
                    frappe.log_error(
                        f"Failed to amend attendance for {employee} on {record['attendance_date']}: {str(e)}",
                        "Attendance Policy"
                    )