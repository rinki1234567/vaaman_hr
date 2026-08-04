import json
import os
import time
import requests
import frappe
import pytz
from datetime import datetime, timedelta, timezone
from google.auth.transport.requests import Request
from google.oauth2 import service_account
from frappe.utils import (
    now, now_datetime, getdate, generate_hash, flt, 
    get_first_day, get_last_day, nowdate, get_datetime, 
    format_time, add_days, time_diff_in_hours, get_system_timezone
)

from frappe.model.workflow import apply_workflow
from frappe import _


import math

# --- MATH & GEOMETRY HELPERS ---



# @frappe.whitelist()
# def get_current_shift_summary(employee):
#     if not employee:
#         frappe.throw("Employee ID is required.")

#     today_str = get_local_now().strftime("%Y-%m-%d")
    
#     # Priority 1: Active Shift Assignment
#     shift_type_name = frappe.db.get_value(
#         "Shift Assignment",
#         {
#             "employee": employee,
#             "start_date": ("<=", today_str),
#             "end_date": (">=", today_str),
#             "status": "Active",
#             "docstatus": 1
#         },
#         "shift_type"
#     )

#     # Priority 2: Employee Default Shift
#     if not shift_type_name:
#         shift_type_name = frappe.db.get_value("Employee", employee, "default_shift")

#     if not shift_type_name:
#         return {"status": "none"}

#     shift = frappe.db.get_value("Shift Type", shift_type_name, ["name", "start_time", "end_time"], as_dict=True)
    
#     if not shift:
#         return {"status": "none"}

#     # return {
#     #     "status": "success",
#     #     "shift_name": shift.name,
#     #     # Format changed from "HH:mm:ss" to "hh:mm A" (e.g., 09:00 AM)
#     #     "start_time": format_time(shift.start_time, "hh:mm A") if shift.start_time else "--:--",
#     #     "end_time": format_time(shift.end_time, "hh:mm A") if shift.end_time else "--:--"
#     # }
#     return {
#         "status": "success",
#         "shift_name": shift.name,
#         # Send raw 24-hour time strings directly to the frontend
#         "start_time": str(shift.start_time) if shift.start_time else "--:--",
#         "end_time": str(shift.end_time) if shift.end_time else "--:--"
#     }


@frappe.whitelist()
def get_current_shift_summary(employee):
    if not employee:
        frappe.throw("Employee ID is required.")

    today_str = get_local_now().strftime("%Y-%m-%d")
    
    # Priority 1: Active Shift Assignment
    shift_type_name = frappe.db.get_value(
        "Shift Assignment",
        {
            "employee": employee,
            "start_date": ("<=", today_str),
            "end_date": (">=", today_str),
            "status": "Active",
            "docstatus": 1
        },
        "shift_type"
    )

    # Priority 2: Employee Default Shift
    if not shift_type_name:
        shift_type_name = frappe.db.get_value("Employee", employee, "default_shift")

    if not shift_type_name:
        return {"status": "none"}

    shift = frappe.db.get_value("Shift Type", shift_type_name, ["name", "start_time", "end_time"], as_dict=True)
    
    if not shift:
        return {"status": "none"}

    # FIX: Helper to force a strict 24-hour string regardless of Frappe's cache state
    def safe_24h_format(time_val):
        if not time_val:
            return "--:--"
        try:
            # If it's a timedelta (Direct database hit)
            if isinstance(time_val, datetime.timedelta):
                total_seconds = int(time_val.total_seconds())
            else:
                # If it's a string (Redis cache hit, e.g., "18:30:00")
                time_str = str(time_val).strip()
                parts = time_str.split(':')
                hours = int(parts[0])
                minutes = int(parts[1])
                total_seconds = (hours * 3600) + (minutes * 60)
            
            # Extract hours and minutes, using % 24 to normalize overnight/shifted hours
            h = (total_seconds // 3600) % 24
            m = (total_seconds % 3600) // 60
            
            # Return strict HH:MM string for the frontend React Native regex
            return f"{h:02d}:{m:02d}"
        except Exception:
            # Fallback just in case parsing fails
            return str(time_val)

    return {
        "status": "success",
        "shift_name": shift.name,
        "start_time": safe_24h_format(shift.start_time),
        "end_time": safe_24h_format(shift.end_time)
    }


def get_local_now():
    """
    Safely gets the current time in the System Timezone.
    """
    try:
        system_tz_name = get_system_timezone() or "Asia/Kolkata"
        local_tz = pytz.timezone(system_tz_name)
        now_with_tz = datetime.now(local_tz)
        return now_with_tz.replace(tzinfo=None)
    except Exception:
        return now_datetime()

def get_shift_end_datetime(employee, checkin_time):
    """
    Finds the Shift End datetime.
    Priority 1: Active 'Shift Assignment' document.
    Priority 2: 'default_shift' field in Employee document.
    """
    checkin_date = get_datetime(checkin_time).date()
    shift_type_name = None

    # 1. Check for specific Shift Assignment (Overrides default)
    shift_type_name = frappe.db.get_value(
        "Shift Assignment",
        {
            "employee": employee,
            "start_date": ("<=", checkin_date),
            "end_date": (">=", checkin_date),
            "status": "Active",
            "docstatus": 1
        },
        "shift_type"
    )
    
    # 2. If no assignment, check Employee's Default Shift
    if not shift_type_name:
        shift_type_name = frappe.db.get_value("Employee", employee, "default_shift")

    if not shift_type_name:
        return None

    # 3. Get Shift Details
    shift = frappe.db.get_value("Shift Type", shift_type_name, ["start_time", "end_time"], as_dict=True)
    
    if not shift:
        return None

    # 4. Calculate End Datetime
    shift_end_dt = get_datetime(f"{checkin_date} {shift.end_time}")
    
    # Handle Night Shifts (e.g. 10 PM to 6 AM)
    if shift.end_time < shift.start_time:
        shift_end_dt = add_days(shift_end_dt, 1)
        
    return shift_end_dt
def determine_status(employee, last_checkin):
    """
    Decides status based strictly on the last valid log.
    NO STALE CHECKS HERE.
    """
    if not last_checkin or last_checkin.log_type == "OUT":
        return "OUT"

    shift_end_dt = get_shift_end_datetime(employee, last_checkin.time)
    current_time = get_local_now()

    if shift_end_dt:
        cushion_minutes = 60
        branch = frappe.db.get_value("Employee", employee, "branch")
        if branch:
            settings_name = frappe.db.get_value(
                "VaamanHR Settings",
                {"branch": branch},
                "name",
            )
            if settings_name:
                cushion_val = frappe.db.get_value(
                    "VaamanHR Settings",
                    settings_name,
                    "shift_end_cushion",
                )
                if cushion_val is not None:
                    cushion_minutes = int(cushion_val)

        cushioned_shift_end_dt = shift_end_dt + timedelta(minutes=cushion_minutes)

        if current_time < cushioned_shift_end_dt:
            return "IN"

    # Search for an automated geofence log that happened AFTER the manual check-in
    last_geo_log = frappe.db.get_value(
        "Employee Checkin",
        {
            "employee": employee,
            "time": (">", last_checkin.time),
            "custom_geofence_in_or_out": 1,  # Strictly looking for geofence logs
        },
        "log_type",
        order_by="time desc",
    )

    if last_geo_log == "OUT":
        return "OUT"

    return "IN"



@frappe.whitelist()
def get_employee_checkin_status(employee):
    last_checkin = frappe.db.get_value(
        "Employee Checkin",
        {
            "employee": employee,
            "docstatus": 0,
            "custom_geofence_in_or_out": 0,  # Gets ANY manual check-in (Face OR Click & Go)
        },
        ["log_type", "time", "custom_outdoor_duty"],
        order_by="time desc",
        as_dict=True,
    )

    real_status = determine_status(employee, last_checkin)

    outdoor_flag = False
    if last_checkin and last_checkin.get("custom_outdoor_duty"):
        outdoor_flag = True

    current_date = get_local_now().date()
    has_checked_in_today = frappe.db.exists(
        "Employee Checkin",
        {
            "employee": employee,
            "custom_geofence_in_or_out": 0,
            "time": (">=", current_date),
            "docstatus": 0,
        },
    )

    return {
        "status": real_status,
        "outdoor": outdoor_flag,
        "has_checked_in_today": bool(has_checked_in_today),
    }
@frappe.whitelist()
def mark_attendance(
    employee,
    log_type,
    latitude=None,
    longitude=None,
    custom_outdoor_duty=0,
    is_click_and_go=0,
):
    # Strictly require all required fields
    if not employee or not log_type or latitude is None or longitude is None:
        frappe.throw("Employee, Log Type, Latitude, and Longitude are all strictly required.")

    # Validate and convert coordinates
    try:
        lat_val = float(latitude)
        lon_val = float(longitude)
    except (ValueError, TypeError):
        frappe.throw("Invalid GPS coordinates provided.")

    last_checkin = frappe.db.get_value(
        "Employee Checkin",
        {
            "employee": employee,
            "custom_geofence_in_or_out": 0,  # Fetch last manual check-in
            "docstatus": 0,
        },
        ["log_type", "time"],
        order_by="time desc",
        as_dict=True,
    )

    current_status = determine_status(employee, last_checkin)

    if current_status == "IN" and log_type == "IN":
        frappe.throw(f"Employee {employee} is already checked in.")

    if current_status == "OUT" and log_type == "OUT":
        frappe.throw(f"Employee {employee} must check in before checking out.")

    checkin = frappe.new_doc("Employee Checkin")
    checkin.employee = employee
    checkin.log_type = log_type
    checkin.time = get_local_now()
    checkin.latitude = lat_val
    checkin.longitude = lon_val

    # 🚨 Crucial Fix: Only 1 if Face Punch!
    checkin.custom_face_checkin_or_checkout = 0 if int(is_click_and_go) else 1

    checkin.custom_geofence_in_or_out = 0
    checkin.custom_outdoor_duty = custom_outdoor_duty

    checkin.insert()

    return {
        "status": "success",
        "message": f"Successfully {log_type}.",
    }





@frappe.whitelist()
def get_vaamanhr_settings():
    DEFAULT_CUSHION = 180
    DEFAULT_APP_VERSION = 1

    response = {
        "shift_end_cushion": DEFAULT_CUSHION,
        "app_version": DEFAULT_APP_VERSION,
        "attendance_by": None,  # Branch-level setting
        "branch_features": [],
        "employee_helper": {},
        "employee_features": [],
    }

    current_user = frappe.session.user
    if current_user == "Guest":
        return response

    original_ignore = frappe.flags.ignore_permissions
    frappe.flags.ignore_permissions = True

    try:
        employee_id = frappe.db.get_value(
            "Employee", {"user_id": current_user}, "name"
        )

        if not employee_id:
            return response

        branch = frappe.db.get_value("Employee", employee_id, "branch")

        if branch:
            settings_name = frappe.db.get_value(
                "VaamanHR Settings",
                {"branch": branch},
                "name",
            )

            if settings_name:
                settings_doc = frappe.get_doc(
                    "VaamanHR Settings", settings_name
                )

                response["shift_end_cushion"] = (
                    int(settings_doc.shift_end_cushion)
                    if settings_doc.shift_end_cushion is not None
                    else DEFAULT_CUSHION
                )

                response["app_version"] = (
                    settings_doc.app_version
                    if settings_doc.app_version
                    else DEFAULT_APP_VERSION
                )

                # Branch-level attendance_by
                response["attendance_by"] = settings_doc.attendance_by

                if hasattr(settings_doc, "given_features"):
                    response["branch_features"] = [
                        row.feature
                        for row in settings_doc.given_features
                        if row.feature
                    ]

        helper_name = frappe.db.get_value(
            "Employee Helper",
            {"employee": employee_id},
            "name",
        )

        if helper_name:
            helper_doc = frappe.get_doc("Employee Helper", helper_name)

            response["employee_helper"] = {
                "name": helper_doc.name,
                "fcm_token": helper_doc.fcm_token,
                "face_embeddings": helper_doc.face_embeddings,
                "permission_banner_shown": helper_doc.permission_banner_shown,
                # Employee-level attendance_by
                "attendance_by": helper_doc.attendance_by,
            }

            if hasattr(helper_doc, "given_features"):
                response["employee_features"] = [
                    row.feature
                    for row in helper_doc.given_features
                    if row.feature
                ]

    except Exception as e:
        frappe.log_error(f"VaamanHR API Error: {e}")

    finally:
        frappe.flags.ignore_permissions = original_ignore

    return response




@frappe.whitelist()
def get_active_announcements():
    today = nowdate()
    try:
        query = """
            SELECT
                `name`, `title`, `content`, `image`
            FROM
                `tabApp Announcement`
            WHERE
                `docstatus` = 1
                AND (`start_date` IS NULL OR `start_date` <= %(today)s)
                AND (`end_date` IS NULL OR `end_date` >= %(today)s)
            ORDER BY
                `creation` DESC
            LIMIT 10
        """
        
        announcements = frappe.db.sql(query, {"today": today}, as_dict=True)
        
        for ann in announcements:
            if ann.image:
                ann.image = f"https://vidhi.vaaman.in{ann.image}"

        return announcements

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Get Active Announcements API Error")
        return []

@frappe.whitelist()
def get_expense_totals_for_employee(employee):
    if not employee:
        frappe.throw("Employee ID is required.")

    pending_data = frappe.db.sql("""
        SELECT SUM(total_claimed_amount)
        FROM `tabExpense Claim`
        WHERE employee = %s AND approval_status IN ('Draft', 'Submitted')
    """, (employee,))
    
    approved_data = frappe.db.sql("""
        SELECT SUM(total_claimed_amount)
        FROM `tabExpense Claim`
        WHERE employee = %s AND approval_status = 'Approved'
    """, (employee,))

    pending_total = pending_data[0][0] if pending_data and pending_data[0][0] else 0
    approved_total = approved_data[0][0] if approved_data and approved_data[0][0] else 0

    return {
        "pending_total": pending_total,
        "approved_total": approved_total
    }

@frappe.whitelist()
def get_employee_pending_requests(employee_id):
    try:
        pending_statuses = {
            "Leave Application": ["Open"],
            "Attendance Request": [0],
            "Shift Request": ["Draft"],
        }

        leave_requests = frappe.get_all(
            "Leave Application",
            fields=["name", "leave_type", "from_date", "status"],
            filters=[
                ["employee", "=", employee_id],
                ["status", "in", pending_statuses["Leave Application"]]
            ]
        )

        attendance_requests = frappe.get_all(
            "Attendance Request",
            fields=["name", "reason", "from_date", "docstatus"],
            filters=[
                ["employee", "=", employee_id],
                ["docstatus", "in", pending_statuses["Attendance Request"]]
            ]
        )
        
        shift_requests = frappe.get_all(
            "Shift Request",
            fields=["name", "shift_type", "from_date", "status"],
            filters=[
                ["employee", "=", employee_id],
                ["status", "in", pending_statuses["Shift Request"]]
            ]
        )
        
        return {
            "leave_requests": leave_requests,
            "attendance_requests": attendance_requests,
            "shift_requests": shift_requests,
        }

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Get Employee Pending Requests Error")
        return {"error": f"An error occurred: {str(e)}"}

@frappe.whitelist()
def get_user_login_data(user_id):
    user_doc = frappe.get_doc('User', user_id)
    user_roles = [d.role for d in user_doc.get('roles', [])]

    employee_id = frappe.db.get_value('Employee', {'user_id': user_id}, 'name')

    return {
        'user_doc': user_doc,
        'roles': user_roles,
        'employee_id': employee_id,
    }


@frappe.whitelist()
def get_pending_approvals():
    try:
        current_user = frappe.session.user
        
        if current_user == "Administrator":
            approvable_employees = [e.name for e in frappe.get_all("Employee", fields=["name"])]
        else:
            leave_employees = frappe.get_all("Employee", filters={"leave_approver": current_user}, pluck="name")
            shift_employees = frappe.get_all("Employee", filters={"shift_request_approver": current_user}, pluck="name")
            expense_approver = frappe.get_all("Employee", filters={"expense_approver": current_user}, pluck="name")
            approvable_employees = list(set(leave_employees + shift_employees + expense_approver))

        if not approvable_employees:
            return {
                "leave_requests": [],
                "attendance_requests": [],
                "shift_requests": [],
                "expense_approvals": [],
                "compoff_requests": [] 
            }

        leave_requests = frappe.get_all(
            "Leave Application",
            fields=["name", "leave_type", "from_date", "to_date", "status", "employee", "employee_name", "total_leave_days", "creation", "modified", "description"],
            filters=[
                ["employee", "in", approvable_employees],
                ["status", "in", ["Open", "Approved", "Rejected", "Cancelled"]]
            ],
            order_by="creation desc" 
        )

        attendance_requests = frappe.get_all(
            "Attendance Request",
            fields=["name", "reason", "from_date", "to_date", "explanation", "shift", "docstatus", "employee", "employee_name", "custom_attendance_request_status", "creation", "modified"],
            filters=[
                ["employee", "in", approvable_employees],
                ["docstatus", "in", [0, 1, 2]]
            ],
            order_by="creation desc"
        )
        
        shift_requests = frappe.get_all(
            "Shift Request",
            fields=["name", "shift_type", "from_date", "to_date", "status", "employee", "employee_name", "creation", "modified"],
            filters=[
                ["employee", "in", approvable_employees],
                ["status", "in", ["Draft", "Approved", "Rejected"]]
            ],
            order_by="creation desc"
        )
        
        expense_approvals = frappe.get_all(
            "Expense Claim",
            fields=["name", "posting_date", "total_claimed_amount", "status", "employee", "employee_name", "approval_status", "creation", "modified", "custom_rejection_reason","docstatus"],
            filters=[
                ["employee", "in", approvable_employees]
            ],
            order_by="creation desc"
        )
        
        compoff_requests = frappe.get_all(
            "Compensatory Leave Request",
            fields=["name", "leave_type", "work_from_date", "work_end_date", "reason", "half_day", "docstatus", "employee", "employee_name", "custom_comp_leave_req_status", "creation", "modified"],
            filters=[
                ["employee", "in", approvable_employees],
                ["docstatus", "in", [0, 1, 2]] 
            ],
            order_by="creation desc"
        )

        return {
            "leave_requests": leave_requests,
            "attendance_requests": attendance_requests,
            "shift_requests": shift_requests,
            "expense_approvals": expense_approvals,
            "compoff_requests": compoff_requests
        }

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Get Pending Approvals Error")
        frappe.throw(f"An error occurred while fetching approvals: {str(e)}")
        
@frappe.whitelist()
def update_approval_status(doctype, docname, action, rejection_reason=None):
    frappe.flags.ignore_permissions = True
    try:
        doc = frappe.get_doc(doctype, docname)
        user_roles = frappe.get_roles()
        is_admin = bool(set(user_roles) & set(['System Manager', 'Administrator']))

        employee_approver = None
        if doctype in ["Leave Application", "Attendance Request", "Compensatory Leave Request"]:
            employee_approver = frappe.db.get_value("Employee", doc.employee, "leave_approver")
        elif doctype == "Shift Request":
            employee_approver = frappe.db.get_value("Employee", doc.employee, "shift_request_approver")
        elif doctype == "Expense Claim":
            employee_approver = frappe.db.get_value("Employee", doc.employee, "expense_approver")
        
        if employee_approver != frappe.session.user and not is_admin:
            frappe.throw(_("You are not the designated approver for this employee."), frappe.PermissionError)

        if doctype == "Leave Application":
            doc.status = action
            if action == 'Rejected' and rejection_reason:
                doc.rejection_reason = rejection_reason
            doc.save(ignore_permissions=True)
            if action in ['Approved', 'Rejected']:
                doc.submit()

        elif doctype == "Attendance Request":
            if action == 'Approved':
                doc.custom_attendance_request_status = 'Approved'
                doc.save(ignore_permissions=True)
                doc.submit()
            elif action == 'Rejected':
                doc.custom_attendance_request_status = 'Rejected'
                doc.save(ignore_permissions=True) 
                doc.submit()
                
        elif doctype == "Shift Request":
            doc.status = action
            doc.save(ignore_permissions=True)
            if action in ['Approved', 'Rejected']:
                doc.submit()
                
        elif doctype == "Expense Claim":
            if action == 'Rejected' and not rejection_reason:
                frappe.throw(_("Rejection reason is mandatory"))

            doc.approval_status = action

            if action == 'Rejected':
                doc.custom_rejection_reason = rejection_reason
                
            doc.save(ignore_permissions=True)
            if action in ['Approved', 'Rejected']:
                doc.submit()
        
        elif doctype == "Compensatory Leave Request":
            if action == 'Approved':
                doc.custom_comp_leave_req_status= 'Approved'
                doc.save(ignore_permissions=True)
                doc.submit() 
            elif action == 'Rejected':
                doc.custom_comp_leave_req_status = 'Rejected'
                doc.save(ignore_permissions=True) 

        else:
            frappe.throw(_(f"Approval for doctype '{doctype}' is not handled by this function."))
        
        frappe.db.commit()
        return {"status": "success", "message": f"{doctype} {docname} has been {action.lower()}."}
    except Exception as e:
        frappe.db.rollback()
        frappe.log_error(frappe.get_traceback())
        frappe.throw(str(e))
    finally:
        frappe.flags.ignore_permissions = False

@frappe.whitelist()
def log_geofence_event(employee, log_type, latitude, longitude, timestamp,permission_revoked=0): 
    if not all([employee, log_type, latitude, longitude, timestamp]): 
        frappe.throw("Employee, Log Type, Latitude, Longitude, and Timestamp are required.")
    
    try:
        # last_log_type = frappe.db.get_value(
        #     "Employee Checkin",
        #     {"employee": employee},
        #     "log_type",
        #     order_by="time desc"
        # )

        # if last_log_type and last_log_type == log_type:
        #     return {"status": "skipped", "message": "Skipped redundant log; employee is already in this state."}
        
        try:
            kolkata_tz = pytz.timezone('Asia/Kolkata')
            utc_time = get_datetime(timestamp)
            ist_time = utc_time.astimezone(kolkata_tz)
            event_time = ist_time.replace(tzinfo=None)
        except Exception as e:
            frappe.log_error(f"Could not parse timestamp for geofence event: {timestamp}. Error: {e}", "Geofence Timestamp Error")
            frappe.throw(f"Invalid timestamp format provided: {timestamp}. Expected ISO 8601 string.")

        checkin = frappe.new_doc("Employee Checkin")
        checkin.employee = employee
        checkin.log_type = log_type
        checkin.time = event_time  
        checkin.latitude = latitude
        checkin.longitude = longitude
        checkin.custom_face_checkin_or_checkout = 0  
        checkin.custom_geofence_in_or_out = 1

        checkin.custom_permission_revoked = int(permission_revoked)
        checkin.insert(ignore_permissions=True)

        frappe.db.commit()
        if log_type == "OUT" or int(permission_revoked) == 1:
            frappe.enqueue(method=notify_managers_on_geofence_event, queue='short', employee=employee, log_type=log_type)
        return {"status": "success", "message": f"Geofence event '{log_type}' logged successfully."}
    
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Geofence Event Logging Failed")
        frappe.throw(f"An error occurred while logging geofence event: {str(e)}")
@frappe.whitelist()
def log_geofence_event_batch(employee, events):
    if not employee or not events:
        frappe.throw("Employee ID and a list of events are required.")

    try:
        events_list = json.loads(events) if isinstance(events, str) else events
        if not isinstance(events_list, list):
            raise ValueError
    except (json.JSONDecodeError, ValueError):
        frappe.throw("Events data is not a valid list.")

    kolkata_tz = pytz.timezone("Asia/Kolkata")
    logged_count = 0
    skipped_count = 0

    for event in events_list:
        try:
            event_log_type = event.get("log_type")

            timestamp_val = event.get("timestamp")
            if not timestamp_val:
                frappe.log_error(
                    f"Skipping geofence event due to missing timestamp: {event}",
                    "Geofence Batch Log"
                )
                continue

            utc_time = None
            if isinstance(timestamp_val, (int, float)) or (
                isinstance(timestamp_val, str) and timestamp_val.isdigit()
            ):
                timestamp_in_seconds = float(timestamp_val) / 1000.0
                utc_time = datetime.fromtimestamp(
                    timestamp_in_seconds, tz=timezone.utc
                )
            elif isinstance(timestamp_val, str):
                utc_time = get_datetime(timestamp_val)

            if not utc_time:
                frappe.log_error(
                    f"Could not parse timestamp for geofence event: {event}",
                    "Geofence Batch Log"
                )
                continue

            ist_time = utc_time.astimezone(kolkata_tz)
            event_time = ist_time.replace(tzinfo=None)

            checkin = frappe.new_doc("Employee Checkin")
            checkin.employee = employee
            checkin.log_type = event_log_type
            checkin.time = event_time
            checkin.latitude = flt(event.get("latitude"))
            checkin.longitude = flt(event.get("longitude"))
            checkin.custom_face_checkin_or_checkout = 0
            checkin.custom_geofence_in_or_out = 1

            # Read and save permission_revoked from batch queue payload
            perm_revoked = int(event.get("permission_revoked", 0))
            checkin.custom_permission_revoked = perm_revoked

            checkin.insert(ignore_permissions=True)
            logged_count += 1
            
            if event_log_type == "OUT" or perm_revoked == 1:
                frappe.enqueue(method=notify_managers_on_geofence_event, queue='short', employee=employee, log_type=event_log_type)

        except Exception:
            skipped_count += 1
            frappe.log_error(
                frappe.get_traceback(),
                f"Failed to process one event in geofence batch: {event}"
            )

    return {
        "logged": logged_count,
        "skipped": skipped_count,
    }




@frappe.whitelist()
def create_employee_with_user(first_name, last_name, email, company, date_of_joining, gender, date_of_birth, create_user):
    should_create_user = frappe.utils.cint(create_user) == 1
    if not all([first_name, email, company, date_of_joining, gender, date_of_birth]):
        frappe.throw("All mandatory fields are required.")
    if should_create_user and (frappe.db.exists("User", email) or frappe.db.exists("Employee", {"user_id": email})):
        frappe.throw(f"A user or employee with the email '{email}' already exists.")
    try:
        user_id = None; success_message = ""
        if should_create_user:
            temporary_password = generate_hash(length=12)
            user = frappe.new_doc("User"); user.email = email; user.first_name = first_name; user.last_name = last_name
            user.new_password = temporary_password; user.enabled = 1; user.insert(ignore_permissions=True)
            user_id = user.name
            success_message = (f"Employee and User created. Login Details: Username '{email}', Password: '{temporary_password}'")
        
        employee = frappe.new_doc("Employee"); employee.employee_name = f"{first_name} {last_name}".strip()
        employee.first_name = first_name; employee.last_name = last_name; employee.company = company
        employee.date_of_joining = date_of_joining; employee.gender = gender; employee.date_of_birth = date_of_birth
        employee.status = "Active"
        
        if user_id:
            employee.user_id = user_id; employee.company_email = email
        
        employee.insert(ignore_permissions=True) 

        helper_doc = frappe.new_doc("Employee Helper")
        helper_doc.employee = employee.name
        helper_doc.insert(ignore_permissions=True)
        
        frappe.db.commit() 
        
        if not should_create_user:
            success_message = f"Employee '{employee.employee_name}' created successfully without a user account."
        
        return {"status": "success", "message": success_message}
    
    except Exception as e:
        frappe.db.rollback(); frappe.log_error(frappe.get_traceback(), "Employee Creation Failed")
        raise frappe.ValidationError(f"An error occurred during employee creation: {e}")

@frappe.whitelist()
def get_employee_checkin_data(employee_id):
    if not employee_id:
        frappe.throw("Employee ID is required.")
    
    try:
        employee = frappe.get_doc("Employee", employee_id)
    except frappe.DoesNotExistError:
        frappe.throw(f"No employee found with ID: {employee_id}")

    geofence_data = None
    if employee.custom_branch_unit:
        try:
            branch_unit = frappe.get_doc("Branch Unit", employee.custom_branch_unit)
            
            if branch_unit.geofence_vertices:
                geofence_data = {
                    "vertices": json.loads(branch_unit.geofence_vertices)
                }
        except (frappe.DoesNotExistError, json.JSONDecodeError):
            geofence_data = None
            frappe.log_error(f"Could not load Branch Unit or parse geofence for employee {employee_id}")

    embeddings = []
    helper_name = frappe.db.get_value("Employee Helper", {"employee": employee_id}, "name")
    
    if helper_name:
        face_embeddings_val = frappe.db.get_value("Employee Helper", helper_name, "face_embeddings")
        if face_embeddings_val:
            try:
                embeddings = json.loads(face_embeddings_val)
            except (json.JSONDecodeError, TypeError):
                embeddings = [] 
    
    last_face_checkin = frappe.db.get_value(
        "Employee Checkin",
        {
            "employee": employee_id,
            "docstatus": 0,
            "custom_face_checkin_or_checkout": 1
        },
        ["log_type", "custom_outdoor_duty"], 
        order_by="time desc",
        as_dict=True
    )

    is_outdoor_duty = False
    if last_face_checkin and last_face_checkin.log_type == "IN":
        is_outdoor_duty = bool(last_face_checkin.custom_outdoor_duty)

    return {
        "full_name": employee.employee_name,
        "image": employee.image,
        "branch_unit":employee.custom_branch_unit,
        "geofence": geofence_data,
        "face_embeddings": embeddings, 
        "custom_outdoor_duty": is_outdoor_duty
    }

@frappe.whitelist()
def update_employee_data(employee, embeddings, latitude=None, longitude=None, radius=None):
    if not employee or not embeddings:
        frappe.throw("Employee and Embeddings data are required.")

    try:
        helper_name = frappe.db.get_value("Employee Helper", {"employee": employee}, "name")
        
        if not helper_name:
            helper_doc = frappe.new_doc("Employee Helper")
            helper_doc.employee = employee
            helper_doc.insert(ignore_permissions=True)
            helper_name = helper_doc.name

        frappe.db.set_value("Employee Helper", helper_name, "face_embeddings", embeddings)
        
        frappe.db.commit()
        return {"status": "success", "message": f"Face scan data updated for {employee}"}
    
    except Exception as e:
        frappe.db.rollback()
        frappe.log_error(frappe.get_traceback())
        frappe.throw(str(e))

def calculate_daily_worked_hours(logs):
    total_seconds = 0
    in_time = None

    for log in logs:
        # Assuming log['time'] is in "%I:%M %p" format like "09:30 AM"
        event_time = datetime.strptime(log['time'], "%I:%M %p").time()
        if log['event'] == 'IN' and not in_time:
            in_time = event_time
        elif log['event'] == 'OUT' and in_time:
            start_dt = datetime.combine(datetime.today(), in_time)
            end_dt = datetime.combine(datetime.today(), event_time)
            if end_dt > start_dt:
                total_seconds += (end_dt - start_dt).total_seconds()
            in_time = None 
            
    return flt(total_seconds / 3600, 2)
@frappe.whitelist()
def get_employee_attendance_data(employee_id, year, month):
    try:
        year, month = int(year), int(month)
        start_date = get_first_day(f"{year}-{month}-01")
        end_date = get_last_day(start_date)
        days_in_month = getdate(end_date).day
    except (ValueError, TypeError):
        frappe.throw("Year and month must be valid integers.")

    attendance_records = frappe.get_all(
        "Attendance",
        filters={
            "employee": employee_id,
            "attendance_date": ["between", (start_date, end_date)],
            "docstatus": 1,
        },
        fields=["attendance_date", "status"],
    )

    attendance_status_map = {
        d.attendance_date.strftime("%Y-%m-%d"): d.status
        for d in attendance_records
    }

    holiday_name_map = {}
    weekly_off_dates = set()

    holiday_list_name = frappe.db.get_value(
        "Employee", employee_id, "holiday_list"
    )

    if holiday_list_name:
        holidays_with_details = frappe.get_all(
            "Holiday",
            filters={
                "parent": holiday_list_name,
                "holiday_date": ["between", (start_date, end_date)],
            },
            fields=["holiday_date", "weekly_off", "description"],
        )

        for h in holidays_with_details:
            holiday_date_obj = getdate(h.holiday_date)
            date_str = holiday_date_obj.strftime("%Y-%m-%d")

            if h.weekly_off:
                weekly_off_dates.add(holiday_date_obj)
            else:
                holiday_name_map[date_str] = h.description

    checkins = frappe.get_all(
        "Employee Checkin",
        filters={
            "employee": employee_id,
            "time": ["between", (start_date, end_date)],
        },
        fields=[
            "time",
            "log_type",
            "custom_face_checkin_or_checkout",
            "custom_geofence_in_or_out",
            "custom_permission_revoked",
            "custom_outdoor_duty"
        ],
        order_by="time asc",
    )

    daily_logs = {}

    for checkin in checkins:
        checkin_date = getdate(checkin.time)
        date_str = checkin_date.strftime("%Y-%m-%d")

        if date_str not in daily_logs:
            daily_logs[date_str] = []

        daily_logs[date_str].append(
            {
                "time": checkin.time.strftime("%I:%M %p"),
                "event": checkin.log_type,
                "is_face_match": checkin.get("custom_face_checkin_or_checkout"),
                "is_geofence": checkin.get("custom_geofence_in_or_out"),
                "permission_revoked": checkin.get("custom_permission_revoked"),
                "is_outdoor_duty": checkin.get("custom_outdoor_duty"),
            }
        )

    processed_data = {}

    present_days = 0
    absent_days = 0
    holiday_days = 0
    on_leave_days = 0
    weekly_off_days = 0
    half_days = 0

    for day in range(1, days_in_month + 1):
        current_date = datetime(year, month, day).date()
        date_str = current_date.strftime("%Y-%m-%d")
        status = None

        if date_str in attendance_status_map:
            official_status = attendance_status_map[date_str]

            if official_status in ["Present", "Work From Home"]:
                status = "Present"
            elif official_status == "Half Day":
                status = "Half Day"
            elif official_status == "On Leave":
                status = "On Leave"
            elif official_status == "Absent":
                status = "Absent"

        elif current_date in weekly_off_dates:
            status = "Weekly Off"

        elif date_str in holiday_name_map:
            status = "Holiday"

        if status:
            if status == "Holiday":
                processed_data[date_str] = {
                    "status": status,
                    "holiday_name": holiday_name_map.get(
                        date_str, "Public Holiday"
                    ),
                    "logs": [],
                }
                holiday_days += 1
            else:
                processed_data[date_str] = {
                    "status": status,
                    "logs": [],
                }

            if status == "Present":
                present_days += 1
            elif status == "Half Day":
                half_days += 1
            elif status == "Absent":
                absent_days += 1
            elif status == "On Leave":
                on_leave_days += 1
            elif status == "Weekly Off":
                weekly_off_days += 1

    for date_str in daily_logs:
        if date_str not in processed_data:
            processed_data[date_str] = {
                "status": None,
                "logs": [],
            }

    for date_str, data in processed_data.items():
        if date_str in daily_logs:
            logs = daily_logs.get(date_str, [])

            first_checkin = next(
                (log for log in logs if log["event"] == "IN"),
                None,
            )

            last_checkout = next(
                (log for log in reversed(logs) if log["event"] == "OUT"),
                None,
            )

            worked_hours = calculate_daily_worked_hours(logs)

            data["check_in"] = (
                first_checkin["time"] if first_checkin else None
            )
            data["check_out"] = (
                last_checkout["time"] if last_checkout else None
            )
            data["worked_hours"] = worked_hours
            data["logs"] = logs

    return {
        "attendance_data": processed_data,
        "summary": {
            "present_days": present_days,
            "half_days": half_days,
            "absent_days": absent_days,
            "holidays": holiday_days,
            "on_leave_days": on_leave_days,
            "weekly_off_days": weekly_off_days,
        },
    }
@frappe.whitelist()
def log_employee_location_batch(employee, locations, branch_unit=None):
    if not employee or not locations:
        frappe.throw("Employee ID and a list of locations are required.")

    try:
        locations_list = json.loads(locations) if isinstance(locations, str) else locations
        if not isinstance(locations_list, list):
            raise ValueError
    except (json.JSONDecodeError, ValueError):
        frappe.throw("Locations data is not a valid list.")

    try:
        kolkata_tz = pytz.timezone('Asia/Kolkata')

        for loc in locations_list:
            timestamp_val = loc.get("timestamp")
            
            utc_time = None
            
            if isinstance(timestamp_val, (int, float)) or (isinstance(timestamp_val, str) and timestamp_val.isdigit()):
                timestamp_in_seconds = float(timestamp_val) / 1000.0
                utc_time_naive = datetime.fromtimestamp(timestamp_in_seconds)
                utc_time = pytz.utc.localize(utc_time_naive)
            
            elif isinstance(timestamp_val, str):
                utc_time = get_datetime(timestamp_val)

            if not utc_time:
                frappe.log_error(f"Could not parse timestamp for location: {loc}", "Location Batch Logging")
                continue
            ist_time = utc_time.astimezone(kolkata_tz)

            log_doc = frappe.new_doc("Location Log")
            log_doc.employee = employee
            log_doc.latitude = flt(loc.get("latitude"))
            log_doc.longitude = flt(loc.get("longitude"))
            log_doc.timestamp = ist_time.replace(tzinfo=None)
            log_doc.branch_unit = branch_unit
            
            log_doc.custom_activity = loc.get("activity", "UNKNOWN")
            
            log_doc.insert(ignore_permissions=True)
        
        frappe.db.commit()
        return {"status": "success", "message": f"Successfully logged {len(locations_list)} location points."}

    except Exception as e:
        frappe.db.rollback()
        frappe.log_error(frappe.get_traceback(), "Location Batch Logging Failed")
        frappe.throw(f"An error occurred while logging location batch: {str(e)}")


@frappe.whitelist()
def get_shift_time_range(employee_id, date_str):
    try:
        # Ensure we use the correct timezone
        company_tz = pytz.timezone("Asia/Kolkata")
    except Exception as e:
        frappe.log_error(f"Could not load timezone: {e}", "Get Shift Time Range Error")
        company_tz = pytz.timezone("UTC") 

    # --- FIX START: Prioritize Shift Assignment ---
    
    # 1. Check for an Active Shift Assignment for this specific date
    shift_type_name = frappe.db.get_value(
        "Shift Assignment",
        {
            "employee": employee_id,
            "start_date": ("<=", date_str),
            "end_date": (">=", date_str),
            "status": "Active",
            "docstatus": 1
        },
        "shift_type"
    )

    # 2. If no assignment found, fall back to Employee's Default Shift
    if not shift_type_name:
        shift_type_name = frappe.db.get_value("Employee", employee_id, "default_shift")

    if not shift_type_name:
        frappe.throw(f"Employee {employee_id} has no Shift Assignment and no Default Shift.")

    # --- FIX END ---

    shift = frappe.get_doc("Shift Type", shift_type_name)
    
    start_time_str = format_time(shift.start_time, "HH:mm:ss") if shift.start_time else "09:00:00"
    end_time_str = format_time(shift.end_time, "HH:mm:ss") if shift.end_time else "18:00:00"

    start_dt_naive = datetime.strptime(f"{date_str} {start_time_str}", "%Y-%m-%d %H:%M:%S")
    end_dt_naive = datetime.strptime(f"{date_str} {end_time_str}", "%Y-%m-%d %H:%M:%S")

    start_dt = company_tz.localize(start_dt_naive)
    end_dt = company_tz.localize(end_dt_naive)

    # Handle Night Shift (e.g., 10 PM to 6 AM)
    # if shift.is_night_shift or end_dt < start_dt:
    #     next_date_obj = (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=1))
    #     next_date_str = next_date_obj.strftime('%Y-%m-%d')
        
    #     end_dt_naive = datetime.strptime(f"{next_date_str} {end_time_str}", "%Y-%m-%d %H:%M:%S")
    #     end_dt = company_tz.localize(end_dt_naive)
    
    return (start_dt, end_dt)

@frappe.whitelist()
def get_salary_slip_details(employee, month, year):
    try:
        safe_month = str(month).zfill(2)
        start_date = get_first_day(f"{year}-{safe_month}-01")
        end_date = get_last_day(f"{year}-{safe_month}-01")

        slips = frappe.get_all(
            "Salary Slip",
            filters=[
                ["employee", "=", employee],
                ["docstatus", "=", 1],
                ["start_date", "<=", end_date],
                ["end_date", ">=", start_date],
            ],
            pluck="name",
            limit=1,
        )

        if not slips:
            frappe.throw(f"No submitted Salary Slip found for {employee} for the selected period.")

        salary_slip_name = slips[0]
        doc = frappe.get_doc("Salary Slip", salary_slip_name)

        response_data = {
            "name": doc.name,
            "employee": doc.employee,
            "employee_name": doc.employee_name,
            "company": doc.company,
            "branch": doc.branch,
            "start_date": doc.start_date,
            "end_date": doc.end_date,
            "working_days": doc.total_working_days,
            "leave_without_pay": doc.leave_without_pay,
            "payment_days": doc.payment_days,
            "gross_pay": doc.gross_pay,
            "total_deduction": doc.total_deduction,
            "net_pay": doc.net_pay,
            "rounded_total": doc.rounded_total,
            "total_in_words": doc.total_in_words,
            "bank_name": getattr(doc, "bank_name", ""),
            "bank_account_no": getattr(doc, "bank_account_no", ""),
            "earnings": [
                {
                    "salary_component": d.salary_component,
                    "amount": d.amount,
                }
                for d in doc.get("earnings", [])
            ],
            "deductions": [
                {
                    "salary_component": d.salary_component,
                    "amount": d.amount,
                }
                for d in doc.get("deductions", [])
            ],
        }

        return response_data

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "API: get_salary_slip_details")
        frappe.throw(f"An unexpected error occurred: {e}")


@frappe.whitelist()
def download_salary_slip_pdf(docname):
    try:
        if not frappe.has_permission("Salary Slip", "read", docname):
            frappe.throw(
                "You do not have permission to access this document.",
                frappe.PermissionError,
            )

        pdf_content = frappe.get_print(
            "Salary Slip",
            docname,
            print_format="Vaaman Salary Slip Head Office",
            as_pdf=True,
        )

        frappe.local.response.filename = f"{docname.replace('/', '_')}.pdf"
        frappe.local.response.filecontent = pdf_content
        frappe.local.response.type = "pdf"

    except frappe.PermissionError:
        frappe.response.http_status_code = 403
        frappe.response["message"] = "Permission Denied"

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "download_salary_slip_pdf Error",
        )
        frappe.response.http_status_code = 500
        frappe.response["message"] = (
            "An error occurred while generating the PDF. Please contact support."
        )

@frappe.whitelist(allow_guest=True)
def get_filtered_historical_paths(date, department=None, branch=None, employee_id=None):
    try:
        # Ensure date is a date object
        target_date = frappe.utils.getdate(date)
    except Exception:
        frappe.throw(_("Invalid date format. Use YYYY-MM-DD."))

    emp_filters = {"status": "Active"}
    if department:
        emp_filters["department"] = department
    if branch:
        emp_filters["branch"] = branch
    if employee_id:
        emp_filters["name"] = employee_id

    # Fetch Employees
    employees = frappe.get_all("Employee",
        filters=emp_filters,
        fields=["name", "first_name", "last_name", "employee_name", "default_shift"]
    )

    if not employees:
        return {"paths": []}

    # Create Mapping and Get Shift Ranges
    employee_names = [e.name for e in employees]
    employee_display_map = {}
    time_ranges = {}

    for emp in employees:
        # Create display name safely
        display_name = emp.get("employee_name") or f"{emp.get('first_name', '')} {emp.get('last_name', '')}".strip() or emp.name
        employee_display_map[emp.name] = display_name
        
        # Get shift range (assuming this might return aware datetimes)
        time_ranges[emp.name] = get_shift_time_range(emp.name, date)

    # Calculate Overall Start/End for DB Query
    valid_shifts = [tr for tr in time_ranges.values() if tr is not None]
    
    if valid_shifts:
        # We strip tzinfo here too, just to be safe for the SQL query parameters
        overall_start = min(tr[0] for tr in valid_shifts)
        overall_end = max(tr[1] for tr in valid_shifts)
        
        if overall_start.tzinfo: overall_start = overall_start.replace(tzinfo=None)
        if overall_end.tzinfo: overall_end = overall_end.replace(tzinfo=None)
    else:
        # Fallback to full day
        overall_start = datetime.strptime(f"{date} 00:00:00", "%Y-%m-%d %H:%M:%S")
        overall_end = datetime.strptime(f"{date} 23:59:59", "%Y-%m-%d %H:%M:%S")

    # Fetch Location Logs
    locations = frappe.get_all("Location Log",
        filters=[
            ["employee", "in", employee_names],
            ["timestamp", "between", [overall_start, overall_end]]
        ],
        fields=["employee", "latitude", "longitude", "timestamp", "branch_unit"],
        order_by="employee, timestamp asc"
    )

    if not locations:
        return {"paths": []}

    branch_geofences = {}
    result_paths = []

    for loc in locations:
        emp_id = loc.employee
        shift_window = time_ranges.get(emp_id)

        # --- FIX START: Handle Timezone Comparison ---
        if shift_window:
            start, end = shift_window
            
            # loc.timestamp is usually Naive (from DB). 
            # We must make start/end Naive as well to compare them.
            if start and start.tzinfo:
                start = start.replace(tzinfo=None)
            if end and end.tzinfo:
                end = end.replace(tzinfo=None)

            if not (start <= loc.timestamp <= end):
                continue
        # --- FIX END ---

        geofence = None
        branch_unit = loc.get("branch_unit")
        
        # Geofence Logic
        if branch_unit:
            if branch_unit not in branch_geofences:
                try:
                    branch_doc = frappe.get_doc("Branch Unit", branch_unit)
                    if branch_doc.geofence_vertices:
                        branch_geofences[branch_unit] = {
                            "vertices": json.loads(branch_doc.geofence_vertices)
                        }
                    else:
                        branch_geofences[branch_unit] = None
                except (frappe.DoesNotExistError, json.JSONDecodeError, TypeError) as e:
                    # Added TypeError to catch issues if vertices aren't valid JSON strings
                    # Using console print instead of log_error to prevent flooding error logs in loops
                    print(f"Geofence load failed for {branch_unit}: {str(e)}")
                    branch_geofences[branch_unit] = None
            
            geofence = branch_geofences[branch_unit]

        result_paths.append({
            "employee": loc.employee,
            "employee_name": employee_display_map.get(loc.employee, loc.employee),  
            "latitude": loc.latitude,
            "longitude": loc.longitude,
            "timestamp": loc.timestamp,
            "geofence": geofence
        })

    return {"paths": result_paths}

def get_google_access_token():
    key_file_path = frappe.get_site_path("private", "files", "firebase-service-account.json")
    
    if not os.path.exists(key_file_path):
        frappe.log_error("Firebase Service Account JSON file not found.")
        return None

    creds = service_account.Credentials.from_service_account_file(
        key_file_path,
        scopes=['https://www.googleapis.com/auth/cloud-platform']
    )

    transport_request = Request()
    creds.refresh(transport_request)
    return creds.token

@frappe.whitelist()
def save_fcm_token(employee, fcm_token):
    try:
        if not employee or not fcm_token:
            frappe.throw("Employee ID and FCM Token are required.")
            
        helper_name = frappe.db.get_value("Employee Helper", {"employee": employee}, "name")
        
        if not helper_name:
            helper_doc = frappe.new_doc("Employee Helper")
            helper_doc.employee = employee
            helper_doc.insert(ignore_permissions=True)
            helper_name = helper_doc.name

        frappe.db.set_value("Employee Helper", helper_name, "fcm_token", fcm_token)
        frappe.db.commit()
        
        return {"status": "success", "message": "Token saved"}

    except Exception as e:
        frappe.db.rollback() 
        frappe.log_error(f"Failed to save FCM token for {employee}: {e}")
        frappe.local.response.http_status_code = 500
        return {"status": "error", "message": str(e)}

@frappe.whitelist()
def get_notifications_for_employee(employee_id):
    if not employee_id:
        frappe.throw("Employee ID is required.")

    try:
        all_notifications = frappe.get_all(
            "App Push Notification",
            filters={"send_to_all": 1, "docstatus": 1},
            fields=["name", "title", "content", "creation", "send_to_employee", "send_to_all"],
            order_by="creation desc",
            limit=20
        )

        employee_notifications = frappe.get_all(
            "App Push Notification",
            filters={"send_to_employee": employee_id, "docstatus": 1},
            fields=["name", "title", "content", "creation", "send_to_employee", "send_to_all"],
            order_by="creation desc",
            limit=20
        )

        announcements = frappe.get_all(
            "App Announcement",
            filters={"docstatus": 1},              
            fields=["name", "title", "content", "creation"],
            order_by="creation desc",
            limit=20
        )

        combined_notifications = {}

        for notif in all_notifications:
            combined_notifications[notif.name] = notif

        for notif in employee_notifications:
            combined_notifications[notif.name] = notif

        for ann in announcements:
            ann["send_to_employee"] = None
            ann["send_to_all"] = None
            ann["type"] = "announcement"
            combined_notifications[ann.name] = ann

        final_list = list(combined_notifications.values())
        final_list.sort(key=lambda x: x.creation, reverse=True)

        return final_list[:30]

    except Exception as e:
        frappe.log_error(f"Failed to get notifications for {employee_id}: {e}", "Get Notifications Error")
        frappe.throw(f"An error occurred while fetching notifications: {str(e)}")

@frappe.whitelist()
def check_and_send_shift_reminders():
    try:
        now_dt = get_local_now()
        current_date_str = now_dt.strftime("%Y-%m-%d")
        current_time = now_dt.time()
        
        dummy_date = datetime.today().date()
        now_dummy = datetime.combine(dummy_date, current_time)

        all_shift_types = frappe.get_all("Shift Type", fields=["name", "start_time"])
        
        shifts_to_process = {}

        for shift in all_shift_types:
            if not shift.start_time: continue
            
            s_time = (datetime.min + shift.start_time).time() if isinstance(shift.start_time, timedelta) else shift.start_time
            shift_start_dummy = datetime.combine(dummy_date, s_time)
            
            diff_minutes = (shift_start_dummy - now_dummy).total_seconds() / 60.0
            
            if 3 <= diff_minutes <= 7:
                shifts_to_process[shift.name] = {
                    "title": "Shift Reminder ⏰",
                    "body": "Your shift starts in 5 minutes. Please check in now."
                }

            elif -12 <= diff_minutes <= -8:
                shifts_to_process[shift.name] = {
                    "title": "Missed Check-in Alert ⚠️",
                    "body": "Your shift has started. Please mark your attendance immediately."
                }


        if not shifts_to_process:
            return

        access_token = get_google_access_token()
        if not access_token:
            return
        

        try:
            creds = json.load(open(frappe.get_site_path("private", "files", "firebase-service-account.json")))
            project_id = creds.get("project_id")
        except:
            return

        fcm_url = f"https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"
        headers = {
            "Authorization": "Bearer " + access_token,
            "Content-Type": "application/json"
        }


        target_shifts = list(shifts_to_process.keys())

        checked_in_employees = set(frappe.get_all(
            "Employee Checkin",
            filters={"time": (">=", f"{current_date_str} 00:00:00")},
            pluck="employee",
            limit_page_length=None
        ))

        # EFFICIENT SQL: Fetch Employees + Their Shift + Their FCM Tokens in ONE query.
        # This replaces looping through 25,000 users.
        users = frappe.db.sql("""
            SELECT 
                emp.name as employee, 
                IFNULL(assign.shift_type, emp.default_shift) as shift_type,
                helper.fcm_token
            FROM `tabEmployee` emp
            LEFT JOIN `tabShift Assignment` assign 
                ON (assign.employee = emp.name 
                    AND assign.status = 'Active' 
                    AND %(today)s BETWEEN assign.start_date AND assign.end_date)
            LEFT JOIN `tabEmployee Helper` helper 
                ON helper.employee = emp.name
            WHERE 
                emp.status = 'Active'
                AND helper.fcm_token IS NOT NULL
                AND IFNULL(assign.shift_type, emp.default_shift) IN %(target_shifts)s
        """, {
            "today": current_date_str, 
            "target_shifts": target_shifts
        }, as_dict=True)


        session = requests.Session()
        session.headers.update(headers)

        sent_count = 0
        
        for user in users:
            if user.employee in checked_in_employees:
                continue

            msg_data = shifts_to_process.get(user.shift_type)
            if not msg_data: continue

            message = {
                "message": {
                    "token": user.fcm_token,
                    "notification": {
                        "title": msg_data["title"],
                        "body": msg_data["body"]
                    },
                    "android": {
                        "priority": "high",
                        "notification": {
                            "channel_id": "geofence-channel-id"
                        }
                    },
                    "data": {
                        "click_action": "FLUTTER_NOTIFICATION_CLICK",
                        "type": "shift_reminder"
                    }
                }
            }

            try:
                session.post(fcm_url, data=json.dumps(message), timeout=2)
                sent_count += 1
            except Exception:
                pass

        if sent_count > 0:
            frappe.log_error(f"Sent {sent_count} shift reminders via FCM", "Shift Reminder Job Success")

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Shift Reminder Optimized Error")

def has_employee_checked_in(employee, date_str):
    """
    Lightweight check for attendance today.
    """
    return frappe.db.exists(
        "Employee Checkin", 
        {
            "employee": employee,
            "time": (">=", f"{date_str} 00:00:00")
        }
    )

@frappe.whitelist()
def send_fcm_notification(doc, method=None):
    """
    Unified Production Handler:
    1. Detects Broadcasts (Announcements OR 'Send to All').
    2. Blasts 25k+ notifications in background (No UI freeze).
    3. Handles Single Employee notifications instantly.
    """
    
    # --- 1. SETUP ---
    access_token = get_google_access_token()
    if not access_token:
        frappe.log_error("FCM Token Error", "Could not get Google Access Token")
        return

    try:
        creds = json.load(open(frappe.get_site_path("private", "files", "firebase-service-account.json")))
        project_id = creds.get("project_id")
    except Exception as e:
        frappe.log_error("FCM Config Error", str(e))
        return

    fcm_url = f"https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"
    headers = {
        "Authorization": "Bearer " + access_token,
        "Content-Type": "application/json"
    }

    # Common Data
    title = getattr(doc, "title", "New Notification")
    content = getattr(doc, "content", "You have a new update")
    
    # Check for Image (Specific to Announcements)
    image_url = None
    if doc.doctype == "App Announcement" and getattr(doc, "image", None):
        if doc.image.startswith("/"):
            image_url = f"{frappe.utils.get_url()}{doc.image}"
        else:
            image_url = doc.image

    # --- SCENARIO A: BROADCAST (ROBUST BLAST) ---
    is_announcement = (doc.doctype == "App Announcement")
    is_broadcast_notif = (doc.doctype == "App Push Notification" and getattr(doc, "send_to_all", 0))

    if is_announcement or is_broadcast_notif:
        
        # 1. Fetch Tokens + Employee Names (for cleanup logic)
        users = frappe.db.sql("""
            SELECT helper.name as helper_id, helper.fcm_token
            FROM `tabEmployee` emp
            JOIN `tabEmployee Helper` helper ON helper.employee = emp.name
            WHERE emp.status = 'Active' AND helper.fcm_token IS NOT NULL
        """, as_dict=True)

        if not users:
            return

        # 2. Construct Base Message
        base_message = {
            "notification": {
                "title": title,
                "body": content
            },
            "android": {
                "priority": "high",
                "notification": {
                    "channel_id": "geofence-channel-id",
                    "sound": "default"
                }
            },
            "data": {
                "click_action": "FLUTTER_NOTIFICATION_CLICK", 
                "notification_id": str(doc.name),
                "type": "announcement" if is_announcement else "general"
            }
        }

        if image_url:
            base_message["notification"]["image"] = image_url

        # 3. Fire Background Worker
        frappe.enqueue(
            method=send_broadcast_background, 
            queue='long', 
            users=users, 
            base_message=base_message, 
            fcm_url=fcm_url, 
            headers=headers
        )
        return

    # --- SCENARIO B: SINGLE EMPLOYEE TARGET ---
    target_token = None
    
    if doc.doctype == "App Push Notification" and getattr(doc, "send_to_employee", None):
        employee_id = doc.send_to_employee
        target_token = frappe.db.get_value("Employee Helper", {"employee": employee_id}, "fcm_token")

    if not target_token: 
        return

    # Send Single Message Instantly
    message = {
        "message": {
            "token": target_token,
            "notification": {"title": title, "body": content},
            "android": {"priority": "high", "notification": {"channel_id": "geofence-channel-id"}},
            "data": {"click_action": "FLUTTER_NOTIFICATION_CLICK", "id": str(doc.name), "type": "personal"}
        }
    }

    try:
        requests.post(fcm_url, headers=headers, data=json.dumps(message))
    except Exception as e:
        frappe.log_error("FCM Single Send Error", str(e))

def send_broadcast_background(users, base_message, fcm_url, headers):
    """
    Worker to blast 25k+ notifications via persistent session.
    Includes SELF-CLEANING logic for invalid tokens.
    """
    session = requests.Session()
    session.headers.update(headers)
    
    tokens_to_remove = []

    for user in users:
        payload = {"message": {"token": user.fcm_token, **base_message}}
        try:
            resp = session.post(fcm_url, data=json.dumps(payload), timeout=2)
            
            if resp.status_code in [400, 404, 410]:
                tokens_to_remove.append(user.helper_id)

        except:
            pass
    
    if tokens_to_remove:
        try:
            frappe.db.sql("""
                UPDATE `tabEmployee Helper` 
                SET fcm_token = NULL 
                WHERE name IN %(ids)s
            """, {"ids": tokens_to_remove})
            frappe.db.commit()
        except Exception:
            pass

def haversine_distance(lat1, lon1, lat2, lon2):
    """Calculate distance between two coordinates in meters."""
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def perpendicular_distance(pt, start, end):
    """Used for Douglas-Peucker simplification."""
    dx = end['longitude'] - start['longitude']
    dy = end['latitude'] - start['latitude']
    mag_sq = dx**2 + dy**2

    if mag_sq == 0:
        return haversine_distance(pt['latitude'], pt['longitude'], start['latitude'], start['longitude'])

    pvx = pt['longitude'] - start['longitude']
    pvy = pt['latitude'] - start['latitude']
    u = max(0, min(1, (pvx * dx + pvy * dy) / mag_sq))

    ix = start['longitude'] + u * dx
    iy = start['latitude'] + u * dy
    return haversine_distance(pt['latitude'], pt['longitude'], iy, ix)

def simplify_path(points, tolerance_meters=5):
    """Douglas-Peucker algorithm to remove redundant points in a straight line."""
    if len(points) <= 2:
        return points

    max_dist = 0
    index = 0
    end = len(points) - 1

    for i in range(1, end):
        dist = perpendicular_distance(points[i], points[0], points[end])
        if dist > max_dist:
            max_dist = dist
            index = i

    if max_dist > tolerance_meters:
        left = simplify_path(points[:index+1], tolerance_meters)
        right = simplify_path(points[index:], tolerance_meters)
        return left[:-1] + right
    else:
        return [points[0], points[end]]

def smooth_path(points, iterations=2):
    """Rounds out sharp edges using a corner-cutting algorithm."""
    if len(points) <= 2 or iterations == 0:
        return points
        
    smoothed = points.copy()
    
    for _ in range(iterations):
        temp = [smoothed[0]]
        for j in range(len(smoothed) - 1):
            p0 = smoothed[j]
            p1 = smoothed[j + 1]
            
            # Create a point at 25% of the line segment
            temp.append({
                "latitude": p0['latitude'] * 0.75 + p1['latitude'] * 0.25,
                "longitude": p0['longitude'] * 0.75 + p1['longitude'] * 0.25,
                "timestamp": p0.get('timestamp'),
                "custom_activity": p0.get('custom_activity')
            })
            # Create a point at 75% of the line segment
            temp.append({
                "latitude": p0['latitude'] * 0.25 + p1['latitude'] * 0.75,
                "longitude": p0['longitude'] * 0.25 + p1['longitude'] * 0.75,
                "timestamp": p1.get('timestamp'),
                "custom_activity": p1.get('custom_activity')
            })
        temp.append(smoothed[-1])
        smoothed = temp
        
    return smoothed


# ==========================================
# MAIN API METHOD
# ==========================================

@frappe.whitelist(allow_guest=True)
def get_filtered_historical_paths_with_sql(date, department=None, branch=None, custom_branch_unit=None, employee_id=None):
    try:
        date_str = getdate(date).strftime("%Y-%m-%d")
    except Exception:
        frappe.throw(_("Invalid date format. Use YYYY-MM-DD."))

    filters = ["e.status = 'Active'"]
    params = {"date_str": date_str}

    if department:
        filters.append("e.department = %(department)s")
        params["department"] = department
    if branch:
        filters.append("e.branch = %(branch)s")
        params["branch"] = branch
    if custom_branch_unit:
        filters.append("e.custom_branch_unit = %(custom_branch_unit)s")
        params["custom_branch_unit"] = custom_branch_unit
    if employee_id:
        filters.append("e.name = %(employee_id)s")
        params["employee_id"] = employee_id

    where_clause = " AND ".join(filters)

    query = f"""
        SELECT 
            l.employee, 
            COALESCE(e.employee_name, CONCAT_WS(' ', e.first_name, e.last_name), e.name) AS employee_name, 
            l.latitude, 
            l.longitude, 
            l.timestamp, 
            l.custom_activity, 
            bu.geofence_vertices 
        FROM `tabLocation Log` l 
        JOIN `tabEmployee` e ON e.name = l.employee 
        LEFT JOIN `tabShift Assignment` sa 
            ON sa.employee = e.name 
            AND sa.status = 'Active' 
            AND sa.docstatus = 1 
            AND %(date_str)s BETWEEN sa.start_date AND sa.end_date 
        LEFT JOIN `tabShift Type` st 
            ON st.name = COALESCE(sa.shift_type, e.default_shift) 
        LEFT JOIN `tabBranch Unit` bu 
            ON bu.name = l.branch_unit 
        LEFT JOIN `tabVaamanHR Settings` vhs 
            ON vhs.branch = e.branch 
        WHERE 
            {where_clause} 
            AND l.timestamp BETWEEN 
                CASE 
                    WHEN st.name IS NULL THEN CONCAT(%(date_str)s, ' 00:00:00') 
                    ELSE CONCAT(%(date_str)s, ' ', TIME(st.start_time)) 
                END 
            AND 
                DATE_ADD( 
                    CASE 
                        WHEN st.name IS NULL THEN CONCAT(%(date_str)s, ' 23:59:59') 
                        WHEN TIME(st.end_time) <= TIME(st.start_time) 
                            THEN DATE_ADD(CONCAT(%(date_str)s, ' ', TIME(st.end_time)), INTERVAL 1 DAY) 
                        ELSE CONCAT(%(date_str)s, ' ', TIME(st.end_time)) 
                    END, 
                    INTERVAL COALESCE(vhs.shift_end_cushion, 0) MINUTE 
                ) 
        ORDER BY l.employee, l.timestamp
    """

    rows = frappe.db.sql(query, params, as_dict=True)
    if not rows:
        return {"paths": []}

    # 1. Group points by Employee and extract the Geofence
    grouped_data = {}
    geofence = None

    for r in rows:
        # Extract geofence once if available
        if not geofence and r.geofence_vertices:
            try:
                geofence = {"vertices": json.loads(r.geofence_vertices)}
            except Exception:
                pass

        emp_id = r.employee
        if emp_id not in grouped_data:
            grouped_data[emp_id] = {
                "employee": emp_id,
                "employee_name": r.employee_name,
                "raw_points": []
            }
            
        grouped_data[emp_id]["raw_points"].append({
            "latitude": float(r.latitude),
            "longitude": float(r.longitude),
            "timestamp": str(r.timestamp),
            "custom_activity": r.custom_activity
        })

    # 2. Process, Clean, Simplify, and Smooth the Data
    final_paths = []
    
    for emp_id, data in grouped_data.items():
        raw_points = data["raw_points"]
        
        # Step A: Basic deduplication (minimum 5 meters between points)
        cleaned_points = []
        last_pt = None
        for pt in raw_points:
            if not last_pt:
                cleaned_points.append(pt)
                last_pt = pt
            else:
                dist = haversine_distance(last_pt['latitude'], last_pt['longitude'], pt['latitude'], pt['longitude'])
                if dist >= 5.0:  
                    cleaned_points.append(pt)
                    last_pt = pt

        # Step B: Advanced shape simplification (Douglas-Peucker)
        simplified_points = simplify_path(cleaned_points, tolerance_meters=6)
        
        # Step C: Smooth out the sharp edges (Your custom algorithm)
        beautiful_points = smooth_path(simplified_points, iterations=2)

        final_paths.append({
            "employee": data["employee"],
            "employee_name": data["employee_name"],
            "coordinates": beautiful_points,
            "geofence": geofence
        })

    return {"paths": final_paths}



def notify_managers_on_geofence_event(employee, log_type):
    try:
        emp_doc = frappe.get_doc("Employee", employee)
        cost_center = emp_doc.payroll_cost_center
        
        if not cost_center:
            return
            
        permitted_users = frappe.get_all("User Permission", 
            filters={"allow": "Cost Center", "for_value": cost_center}, 
            pluck="user",
            ignore_permissions=True
        )
        
        if not permitted_users:
            return
            
        target_roles = ["Site Head", "Site HR User", "Site HR Manager"]
        
        valid_users = frappe.get_all("Has Role", 
            filters={"parent": ["in", permitted_users], "role": ["in", target_roles]}, 
            pluck="parent",
            ignore_permissions=True
        )
        
        valid_users = list(set(valid_users))
        if not valid_users:
            return
            
        emp_name = emp_doc.employee_name or emp_doc.first_name
        action = "entered" if log_type == "IN" else "exited"
        title = "Geofence Alert 📍"
        body = f"{emp_name} ({employee}) has {action} the geofence."
        
        for user_id in valid_users:
            manager_emp_id = frappe.db.get_value("Employee", {"user_id": user_id}, "name")
            if manager_emp_id and manager_emp_id != employee:
                notification = frappe.new_doc("App Push Notification")
                notification.title = title
                notification.content = body
                notification.send_to_employee = manager_emp_id
                
                notification.insert(ignore_permissions=True)
                notification.submit()
        
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Geofence Notification Error")

@frappe.whitelist()
def get_approver_team_status():
    current_user = frappe.session.user
    
    # 1. Find the Employee ID of the currently logged-in manager
    current_emp_id = frappe.db.get_value("Employee", {"user_id": current_user}, "name")
    
    employees = []
    
    # 2. Get employees for this manager based on cost center permissions
    if current_user == "Administrator" or "System Manager" in frappe.get_roles(current_user):
        employees = frappe.get_all("Employee", filters={"status": "Active"}, fields=["name", "employee_name", "image", "custom_branch_unit"])
    else:
        user_cost_centers = frappe.get_all("User Permission", 
            filters={"user": current_user, "allow": "Cost Center"}, 
            pluck="for_value",
            ignore_permissions=True
        )
        
        if user_cost_centers:
            employees = frappe.get_all("Employee", 
                filters={"payroll_cost_center": ["in", user_cost_centers], "status": "Active"}, 
                fields=["name", "employee_name", "image", "custom_branch_unit"],
                ignore_permissions=True
            )
            
    # MINOR CHANGE: Filter out the manager from their own team list
    if current_emp_id:
        employees = [e for e in employees if e.name != current_emp_id]
        
    if not employees:
        return []
        
    emp_ids = [e.name for e in employees]
    emp_map = {e.name: e for e in employees}
    
    branch_units = list(set([e.custom_branch_unit for e in employees if e.custom_branch_unit]))
    geofences = {}
    if branch_units:
        bu_docs = frappe.get_all("Branch Unit", filters={"name": ["in", branch_units]}, fields=["name", "geofence_vertices"])
        for bu in bu_docs:
            if bu.geofence_vertices:
                try:
                    geofences[bu.name] = {"vertices": json.loads(bu.geofence_vertices)}
                except Exception:
                    pass
    
    # 3. Fetch the latest check-in for these employees securely and efficiently
    format_string = ', '.join(['%s'] * len(emp_ids))
    query = f"""
        SELECT 
            c.employee, c.log_type, c.time, c.latitude, c.longitude, 
            c.custom_geofence_in_or_out, c.custom_permission_revoked, 
            c.custom_face_checkin_or_checkout, c.custom_outdoor_duty
        FROM `tabEmployee Checkin` c
        INNER JOIN (
            SELECT employee, MAX(time) as max_time
            FROM `tabEmployee Checkin`
            WHERE employee IN ({format_string})
            GROUP BY employee
        ) latest ON c.employee = latest.employee AND c.time = latest.max_time
    """
    
    latest_checkins = frappe.db.sql(query, tuple(emp_ids), as_dict=True)
    
    seen_employees = set()
    result = []
    
    for checkin in latest_checkins:
        emp_id = checkin.employee
        if emp_id in seen_employees:
            continue
        seen_employees.add(emp_id)
        
        # 4. Determine status text based on rules
        status_text = "Unknown"
        if checkin.custom_permission_revoked:
            status_text = "Location Permission Revoked"
        elif checkin.custom_geofence_in_or_out:
            status_text = "Geofence Entry" if checkin.log_type == "IN" else "Geofence Exit"
        else:
            action = "Checked In" if checkin.log_type == "IN" else "Checked Out"
            method = "with Face" if checkin.custom_face_checkin_or_checkout else "without Face"
            
            if checkin.custom_outdoor_duty and checkin.log_type == "IN":
                status_text = f"{action} (Outdoor) {method}"
            else:
                status_text = f"{action} {method}"
                
        emp_info = emp_map.get(emp_id)
        
        result.append({
            "employee": emp_id,
            "employee_name": emp_info.employee_name,
            "image": emp_info.image,
            "status": status_text,
            "time": checkin.time,
            "latitude": checkin.latitude,
            "longitude": checkin.longitude,
            "geofence": geofences.get(emp_info.custom_branch_unit) if emp_info.custom_branch_unit else None
        })
        
    # 5. Include employees who have NO checkins at all
    for emp_id, emp_info in emp_map.items():
        if emp_id not in seen_employees:
            result.append({
                "employee": emp_id,
                "employee_name": emp_info.employee_name,
                "image": emp_info.image,
                "status": "No check-in records",
                "time": None,
                "latitude": None,
                "longitude": None,
                "geofence": geofences.get(emp_info.custom_branch_unit) if emp_info.custom_branch_unit else None
            })
            
    # Sort by time descending (newest activity first), pushing null times to the bottom
    result.sort(key=lambda x: x["time"] or datetime.min, reverse=True)
    
    return result



def validate_device_on_login(login_manager):
    user = login_manager.user
    
    # Bypass for administrators or system managers
    if user == "Administrator" or "System Manager" in frappe.get_roles(user):
        return
        
    # 1. Fetch the Employee document and their branch
    emp_data = frappe.db.get_value(
        "Employee", 
        {"user_id": user, "status": "Active"}, 
        ["name", "branch"], 
        as_dict=True
    )
    
    if not emp_data or not emp_data.branch:
        return # Skip if not an active employee or if they have no branch assigned
        
    emp_id = emp_data.name
    branch = emp_data.branch
    
    # 2. Check if device binding is enabled for this branch in VaamanHR Settings
    device_binding_enabled = frappe.db.get_value(
        "VaamanHR Settings", 
        {"branch": branch}, 
        "device_binding"
    )
    
    if not device_binding_enabled:
        return # Simply return if device binding is not checked for this branch
        
    # Get the device ID sent from the mobile app during login
    device_id = frappe.form_dict.get("device_id")
    
    frappe.log_error(f"Login trace for {user}. Received device_id: {device_id}", "Device Validation Log")
    
    # Fetch the Employee Helper document
    emp_helper = frappe.db.get_value("Employee Helper", {"employee": emp_id}, "name")
    if not emp_helper:
        return # Cannot bind if helper document does not exist yet
        
    # 3. Fetch their currently registered device ID
    registered_device = frappe.db.get_value("Employee Helper", emp_helper, "registered_device_id")
    
    # 4. If they don't have a device registered yet, and they passed one, bind it!
    if not registered_device:
        if device_id:
            frappe.db.set_value("Employee Helper", emp_helper, "registered_device_id", device_id)
        return
        
    # 5. If they DO have a registered device, check if the incoming one matches
    if device_id and device_id != registered_device:
        # Throw an AuthenticationError to forcefully block the login
        frappe.throw(
            "This account is registered to another device. Please contact HR to reset your device binding.", 
            frappe.AuthenticationError
        )