
import frappe
from frappe.utils import getdate, get_time, flt, time_diff_in_hours

def validate_half_day_attendance(doc, method=None):

    if doc.status == "Half Day":

        #  Fetch Checkin Logs
        logs = frappe.get_all(
            "Employee Checkin",
            filters={
                "employee": doc.employee,
                "time": [
                    "between",
                    [
                        str(doc.attendance_date) + " 00:00:00",
                        str(doc.attendance_date) + " 23:59:59",
                    ],
                ],
            },
            fields=["time", "log_type"],
            order_by="time asc",
        )

        working_hours = 0
        in_time = None
        out_time = None

        #  Session-wise calculation
        last_in = None
        
        for log in logs:
            if log.log_type == "IN":
                last_in = log.time

            elif log.log_type == "OUT" and last_in:
                working_hours += time_diff_in_hours(log.time, last_in)
                last_in = None

        if logs:
            in_time = logs[0].time
            out_time = logs[-1].time

            

        # Set values
        doc.in_time = in_time
        doc.out_time = out_time
        doc.working_hours = flt(working_hours)

        # Date & Day
        attendance_date = getdate(doc.attendance_date)
        is_saturday = attendance_date.weekday() == 5

        act_in = get_time(in_time) if in_time else None
        act_out = get_time(out_time) if out_time else None

        # First / Second Half Detection
        is_first_half = act_in and act_in <= get_time("12:00:00")

        # Minimum Hours
        min_hours = 4.0 if is_saturday else 4.5

        status_ok = False
        error_msg = ""
    
        

        # No Punch
        if working_hours == 0:
            error_msg = "No Punches found."

        # Less Hours
        elif working_hours < min_hours:
            error_msg = f"Working Hours ({round(working_hours, 2)}) less than required {min_hours}"

        else:
           
            #  FIRST HALF LOGIC
            if is_first_half:

                if not is_saturday:
                   
                    status_ok = True

                else:
                   
                    if act_out and act_out >= get_time("14:00:00"):
                        status_ok = True
                    else:
                        error_msg = "Saturday first half logout must be after 2:00 PM"

           
            # SECOND HALF LOGIC
          
            else:

                if not is_saturday:
                    #  Mon–Fri → logout ≥ 6:30 PM
                    if act_out and act_out >= get_time("18:30:00"):
                        status_ok = True
                    else:
                        error_msg = "Must logout after 6:30 PM"

                else:
                    # Saturday → logout ≥ 5 PM
                    if act_out and act_out >= get_time("17:00:00"):
                        status_ok = True
                    else:
                        error_msg = "Saturday logout must be after 5:00 PM"

        #  Final Status
        if status_ok:
            doc.half_day_status = "Present"
        else:
            doc.half_day_status = "Absent"
            if error_msg:
                frappe.msgprint(error_msg)

        #  Save safely (NULL error avoid)
        doc.db_set({
            "in_time": doc.in_time,
            "out_time": doc.out_time,
            "working_hours": doc.working_hours or 0,
            "half_day_status": doc.half_day_status
        })




