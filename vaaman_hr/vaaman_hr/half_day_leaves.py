
# import frappe
# from frappe.utils import getdate, get_time, flt, time_diff_in_hours

# def validate_half_day_attendance(doc, method=None):

#     if doc.status == "Half Day":

#         #  Fetch Checkin Logs
#         logs = frappe.get_all(
#             "Employee Checkin",
#             filters={
#                 "employee": doc.employee,
#                 "time": [
#                     "between",
#                     [
#                         str(doc.attendance_date) + " 00:00:00",
#                         str(doc.attendance_date) + " 23:59:59",
#                     ],
#                 ],
#             },
#             fields=["time", "log_type"],
#             order_by="time asc",
#         )

#         working_hours = 0
#         in_time = None
#         out_time = None

#         #  Session-wise calculation
#         last_in = None
        
#         for log in logs:
#             if log.log_type == "IN":
#                 last_in = log.time

#             elif log.log_type == "OUT" and last_in:
#                 working_hours += time_diff_in_hours(log.time, last_in)
#                 last_in = None

#         if logs:
#             in_time = logs[0].time
#             out_time = logs[-1].time

            

#         # Set values
#         doc.in_time = in_time
#         doc.out_time = out_time
#         doc.working_hours = flt(working_hours)

#         # Date & Day
#         attendance_date = getdate(doc.attendance_date)
#         is_saturday = attendance_date.weekday() == 5

#         act_in = get_time(in_time) if in_time else None
#         act_out = get_time(out_time) if out_time else None

#         # First / Second Half Detection
#         is_first_half = act_in and act_in <= get_time("12:00:00")

#         # Minimum Hours
#         min_hours = 4.0 if is_saturday else 4.5

#         status_ok = False
#         error_msg = ""
    
        

#         # No Punch
#         if working_hours == 0:
#             error_msg = "No Punches found."

#         # Less Hours
#         elif working_hours < min_hours:
#             error_msg = f"Working Hours ({round(working_hours, 2)}) less than required {min_hours}"

#         else:
           
#             #  FIRST HALF LOGIC
#             if is_first_half:

#                 if not is_saturday:
                   
#                     status_ok = True

#                 else:
                   
#                     if act_out and act_out >= get_time("14:00:00"):
#                         status_ok = True
#                     else:
#                         error_msg = "Saturday first half logout must be after 2:00 PM"

           
#             # SECOND HALF LOGIC
          
#             else:

#                 if not is_saturday:
#                     #  Mon–Fri → logout ≥ 6:30 PM
#                     if act_out and act_out >= get_time("18:30:00"):
#                         status_ok = True
#                     else:
#                         error_msg = "Must logout after 6:30 PM"

#                 else:
#                     # Saturday → logout ≥ 5 PM
#                     if act_out and act_out >= get_time("17:00:00"):
#                         status_ok = True
#                     else:
#                         error_msg = "Saturday logout must be after 5:00 PM"

#         #  Final Status
#         if status_ok:
#             doc.half_day_status = "Present"
#         else:
#             doc.half_day_status = "Absent"
#             if error_msg:
#                 frappe.msgprint(error_msg)

#         #  Save safely (NULL error avoid)
#         doc.db_set({
#             "in_time": doc.in_time,
#             "out_time": doc.out_time,
#             "working_hours": doc.working_hours or 0,
#             "half_day_status": doc.half_day_status
#         })



import frappe
from frappe.utils import getdate, get_time, flt, time_diff_in_hours

def validate_half_day_attendance(doc, method=None):

    logs = frappe.get_all("Employee Checkin", filters={
        "employee": doc.employee,
        "time": ["between", [str(doc.attendance_date) + " 00:00:00", str(doc.attendance_date) + " 23:59:59"]]
    }, fields=["time", "log_type"], order_by="time asc")

    if not logs:
        return

    # 2. Calculation of Working Hours
    working_hours = 0
    last_in = None
    for log in logs:
        if log.log_type == "IN":
            last_in = log.time
        elif log.log_type == "OUT" and last_in:
            working_hours += time_diff_in_hours(log.time, last_in)
            last_in = None

    
    in_time = get_time(logs[0].time)
    out_time = get_time(logs[-1].time)
    attendance_date = getdate(doc.attendance_date)
    is_saturday = attendance_date.weekday() == 5
    
    min_hd_hours = 4.0 if is_saturday else 4.5
    full_day_hours = 7.0 if is_saturday else 8.5
    
    final_status = "Absent"
    final_half_day_status = ""

  
    # CASE A: Full Day Present
    if working_hours >= full_day_hours:
        final_status = "Present"
        final_half_day_status = ""

    # CASE B: Half Day Eligibility (Hours + Timing Check)
    elif working_hours >= min_hd_hours:
        is_timing_ok = False
        
        # 1st Half: In <= 10:00 AM & Out >= 02:30 PM
        if in_time <= get_time("10:05:00") and out_time >= get_time("14:30:00"):
            is_timing_ok = True
            
        # 2nd Half: In <= 01:30 PM & Out >= 06:30 PM (Sat: 05:00 PM)
        elif in_time <= get_time("13:35:00"):
            target_out = "17:00:00" if is_saturday else "18:30:00"
            if out_time >= get_time(target_out):
                is_timing_ok = True

        if is_timing_ok:
            final_status = "Half Day"
            final_half_day_status = "Present"
        else:
           
            final_status = "Absent"
            final_half_day_status = ""

    # CASE C: Less than 4.5 Hours
    else:
        if doc.leave_application:
            final_status = "Half Day"
            final_half_day_status = "Absent"
        else:
            final_status = "Absent"
            final_half_day_status = ""

    frappe.db.set_value("Attendance", doc.name, {
        "working_hours": flt(working_hours),
        "status": final_status,
        "half_day_status": final_half_day_status
    }, update_modified=False)

   
    doc.working_hours = flt(working_hours)
    doc.status = final_status
    doc.half_day_status = final_half_day_status


def rectify_saturday_attendance(from_date=None, branch=None):
    """Re-apply attendance status for Saturday records using corrected full-day hours."""
    from_date = from_date or "2026-01-01"
    skip_statuses = ("On Leave", "Weekly Off", "Holiday", "Work From Home")

    filters = {
        "attendance_date": [">=", from_date],
        "docstatus": ["<", 2],
        "status": ["not in", list(skip_statuses)],
    }
    if branch:
        employees = frappe.get_all("Employee", filters={"branch": branch}, pluck="name")
        if not employees:
            return {"updated": 0, "checked": 0}
        filters["employee"] = ["in", employees]

    names = frappe.get_all("Attendance", filters=filters, pluck="name")
    updated = 0
    checked = 0

    for name in names:
        doc = frappe.get_doc("Attendance", name)
        if getdate(doc.attendance_date).weekday() != 5:
            continue
        if doc.leave_application and doc.status == "On Leave":
            continue

        old_status = doc.status
        old_half_day_status = doc.half_day_status or ""
        validate_half_day_attendance(doc)
        checked += 1

        if doc.status != old_status or (doc.half_day_status or "") != old_half_day_status:
            updated += 1

    frappe.db.commit()
    return {"checked": checked, "updated": updated}

