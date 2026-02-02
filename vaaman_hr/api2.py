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

    return {
        "status": "success",
        "shift_name": shift.name,
        # Format changed from "HH:mm:ss" to "hh:mm A" (e.g., 09:00 AM)
        "start_time": format_time(shift.start_time, "hh:mm A") if shift.start_time else "--:--",
        "end_time": format_time(shift.end_time, "hh:mm A") if shift.end_time else "--:--"
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

    if shift_end_dt and current_time < shift_end_dt:
        return "IN"

    last_geo_log = frappe.db.get_value(
        "Employee Checkin", 
        {
            "employee": employee,
            "time": (">", last_checkin.time), 
            "custom_face_checkin_or_checkout": 0 
        },
        "log_type", 
        order_by="time desc"
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
            "custom_face_checkin_or_checkout": 1 
        },
        ["log_type", "time", "custom_outdoor_duty"],
        order_by="time desc", 
        as_dict=True
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
            "custom_face_checkin_or_checkout": 1,
            "time": (">=", current_date),
            "docstatus": 0
        }
    )

    return {
        "status": real_status,
        "outdoor": outdoor_flag,
        "has_checked_in_today": bool(has_checked_in_today)
    }

@frappe.whitelist()
def mark_attendance(employee, log_type, latitude, longitude, custom_outdoor_duty):
    if not all([employee, log_type, latitude, longitude]):
        frappe.throw("Employee, Log Type, Latitude, and Longitude are required.")

    last_checkin = frappe.db.get_value(
        "Employee Checkin",
        {
            "employee": employee,
            "custom_face_checkin_or_checkout": 1,
            "docstatus": 0
        },
        ["log_type", "time"], 
        order_by="time desc",
        as_dict=True 
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
    checkin.latitude = latitude
    checkin.longitude = longitude
    checkin.custom_face_checkin_or_checkout = 1
    checkin.custom_outdoor_duty = custom_outdoor_duty
    checkin.insert()

    return {"status": "success", "message": f"Successfully {log_type}."}

@frappe.whitelist()
def get_vaamanhr_settings():
    DEFAULT_CUSHION = 180
    DEFAULT_APP_VERSION = 1

    response = {
        "shift_end_cushion": DEFAULT_CUSHION,
        "app_version": DEFAULT_APP_VERSION,
        "branch_features": [],   
        "employee_helper": {},
        "employee_features": []  
    }
    
    original_user = frappe.session.user
    frappe.set_user("Administrator")

    try:
        if original_user == "Guest":
            return response

        employee_id = frappe.db.get_value("Employee", {"user_id": original_user}, "name")
        
        if not employee_id:
            return response

        branch = frappe.db.get_value("Employee", employee_id, "branch")
        
        if branch:
            settings_name = frappe.db.get_value("VaamanHR Settings", {"branch": branch}, "name")
            
            if settings_name:
                settings_doc = frappe.get_doc("VaamanHR Settings", settings_name)
                
                response["shift_end_cushion"] = int(settings_doc.shift_end_cushion) if settings_doc.shift_end_cushion is not None else DEFAULT_CUSHION
                response["app_version"] = settings_doc.app_version if settings_doc.app_version else DEFAULT_APP_VERSION
                
                if hasattr(settings_doc, "given_features"):
                    response["branch_features"] = [row.feature for row in settings_doc.given_features if row.feature]

        helper_name = frappe.db.get_value("Employee Helper", {"employee": employee_id}, "name")
        
        if helper_name:
            helper_doc = frappe.get_doc("Employee Helper", helper_name)
            
            response["employee_helper"] = {
                "name": helper_doc.name,
                "fcm_token": helper_doc.fcm_token,
                "face_embeddings": helper_doc.face_embeddings 
            }

            if hasattr(helper_doc, "given_features"):
                response["employee_features"] = [row.feature for row in helper_doc.given_features if row.feature]

    except Exception as e:
        frappe.log_error(f"VaamanHR API Error: {e}")
    
    finally:
        frappe.set_user(original_user)

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
            fields=["name", "leave_type", "from_date", "to_date", "status", "employee", "employee_name", "total_leave_days", "creation","description"],
            filters=[
                ["employee", "in", approvable_employees],
                ["status", "in", ["Open", "Approved", "Rejected", "Cancelled"]]
            ],
            order_by="creation desc" 
        )

        attendance_requests = frappe.get_all(
            "Attendance Request",
            fields=["name", "reason", "from_date", "to_date", "explanation", "shift", "docstatus", "employee", "employee_name", "custom_attendance_request_status", "creation"],
            filters=[
                ["employee", "in", approvable_employees],
                ["docstatus", "in", [0, 1, 2]]
            ],
            order_by="creation desc"
        )
        
        shift_requests = frappe.get_all(
            "Shift Request",
            fields=["name", "shift_type", "from_date", "to_date", "status", "employee", "employee_name", "creation"],
            filters=[
                ["employee", "in", approvable_employees],
                ["status", "in", ["Draft", "Approved", "Rejected"]]
            ],
            order_by="creation desc"
        )
        
        expense_approvals = frappe.get_all(
            "Expense Claim",
            fields=["name", "posting_date", "total_claimed_amount", "status", "employee", "employee_name", "approval_status", "creation", "custom_rejection_reason"],
            filters=[
                ["employee", "in", approvable_employees],
                ["status", "in", ["Draft", "Unpaid", "Rejected", "Paid"]]
            ],
            order_by="creation desc"
        )
        
        compoff_requests = frappe.get_all(
            "Compensatory Leave Request",
            fields=["name", "leave_type", "work_from_date", "work_end_date", "reason", "half_day", "docstatus", "employee", "employee_name", "custom_comp_leave_req_status", "creation"],
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
def log_geofence_event(employee, log_type, latitude, longitude, timestamp): 
    if not all([employee, log_type, latitude, longitude, timestamp]): 
        frappe.throw("Employee, Log Type, Latitude, Longitude, and Timestamp are required.")
    
    try:
        last_log_type = frappe.db.get_value(
            "Employee Checkin",
            {"employee": employee},
            "log_type",
            order_by="time desc"
        )

        if last_log_type and last_log_type == log_type:
            return {"status": "skipped", "message": "Skipped redundant log; employee is already in this state."}
        
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
        checkin.insert(ignore_permissions=True)

        frappe.db.commit()
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

    kolkata_tz = pytz.timezone('Asia/Kolkata')
    logged_count = 0
    skipped_count = 0 

    last_known_log_type = frappe.db.get_value(
        "Employee Checkin",
        {"employee": employee},
        "log_type", 
        order_by="time desc"
    )

    for event in events_list:
        try:
            event_log_type = event.get("log_type")
            
            if last_known_log_type and last_known_log_type == event_log_type:
                skipped_count += 1
                continue

            timestamp_val = event.get("timestamp")
            if not timestamp_val:
                frappe.log_error(f"Skipping geofence event due to missing timestamp: {event}", "Geofence Batch Log")
                continue
            
            utc_time = None
            if isinstance(timestamp_val, (int, float)) or (isinstance(timestamp_val, str) and timestamp_val.isdigit()):
                timestamp_in_seconds = float(timestamp_val) / 1000.0
                utc_time_naive = datetime.fromtimestamp(timestamp_in_seconds, tz=timezone.utc)
                utc_time = utc_time_naive 
            elif isinstance(timestamp_val, str):
                utc_time = get_datetime(timestamp_val)

            if not utc_time:
                frappe.log_error(f"Could not parse timestamp for geofence event: {event}", "Geofence Batch Log")
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
            checkin.insert(ignore_permissions=True)
            logged_count += 1

            last_known_log_type = event_log_type

        except Exception as e:
            frappe.log_error(f"Failed to process one event in geofence batch: {event}. Error: {e}", "Geofence Batch Error")
    
    frappe.db.commit()
    return {"status": "success", "message": f"Successfully logged {logged_count} and skipped {skipped_count} geofence events."}

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

    # UPDATED: Added docstatus: 1 to filter out draft (Saved) records
    attendance_records = frappe.get_all(
        "Attendance",
        filters={
            "employee": employee_id, 
            "attendance_date": ["between", (start_date, end_date)],
            "docstatus": 1  
        },
        fields=["attendance_date", "status"]
    )
    
    attendance_status_map = {
        d.attendance_date.strftime("%Y-%m-%d"): d.status for d in attendance_records
    }
    
    holiday_name_map = {} 
    weekly_off_dates = set()
    holiday_list_name = frappe.db.get_value("Employee", employee_id, "holiday_list")
    if holiday_list_name:
        holidays_with_details = frappe.get_all(
            "Holiday",
            filters={"parent": holiday_list_name, "holiday_date": ["between", (start_date, end_date)]},
            fields=["holiday_date", "weekly_off", "description"] 
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
        filters={"employee": employee_id, "time": ["between", (start_date, end_date)]},
        fields=["time", "log_type", "custom_face_checkin_or_checkout"],
        order_by="time asc"
    )
    daily_logs = {}
    for checkin in checkins:
        checkin_date = getdate(checkin.time)
        date_str = checkin_date.strftime("%Y-%m-%d")
        if date_str not in daily_logs:
            daily_logs[date_str] = []
        daily_logs[date_str].append({
            "time": checkin.time.strftime("%I:%M %p"), "event": checkin.log_type,
            "is_face_match": checkin.get("custom_face_checkin_or_checkout")
        })

    processed_data = {}
    present_days, absent_days, holiday_days, on_leave_days, weekly_off_days, half_days = 0, 0, 0, 0, 0, 0

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
                    "holiday_name": holiday_name_map.get(date_str, "Public Holiday"),
                    "logs": []
                }
                holiday_days += 1
            else:
                processed_data[date_str] = {"status": status, "logs": []}

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
                "logs": []
            }


    for date_str, data in processed_data.items():
        if date_str in daily_logs:
            logs = daily_logs.get(date_str, [])
            first_checkin = next((log for log in logs if log['event'] == 'IN'), None)
            last_checkout = next((log for log in reversed(logs) if log['event'] == 'OUT'), None)
            worked_hours = calculate_daily_worked_hours(logs)

            data["check_in"] = first_checkin['time'] if first_checkin else None
            data["check_out"] = last_checkout['time'] if last_checkout else None
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
            "weekly_off_days": weekly_off_days
        }
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
        start_date = get_first_day(f"{year}-{month}-01")
        end_date = get_last_day(f"{year}-{month}-01")
        salary_slip_name = frappe.db.get_value("Salary Slip", {
            "employee": employee,
            "start_date": start_date,
            "end_date": end_date,
            "docstatus": 1 
        })

        if not salary_slip_name:
            frappe.throw(f"No submitted Salary Slip found for {employee} for the selected period.")

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
            "bank_name": doc.bank_name,
            "bank_account_no": doc.bank_account_no,
            "earnings": [
                {"salary_component": d.salary_component, "amount": d.amount}
                for d in doc.get("earnings", [])
            ],
            "deductions": [
                {"salary_component": d.salary_component, "amount": d.amount}
                for d in doc.get("deductions", [])
            ],
        }
        
        return response_data

    except frappe.DoesNotExistError:
        frappe.throw(f"Salary Slip {salary_slip_name} not found.")
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "API: get_salary_slip_details")
        frappe.throw(f"An unexpected error occurred: {e}")

@frappe.whitelist()
def download_salary_slip_pdf(docname):
    try:
        if not frappe.has_permission("Salary Slip", "read", docname):
            frappe.throw("You do not have permission to access this document.", frappe.PermissionError)
        pdf_content = frappe.get_print("Salary Slip", docname, as_pdf=True)
        frappe.local.response.filename = f"{docname.replace('/', '_')}.pdf"
        frappe.local.response.filecontent = pdf_content
        frappe.local.response.type = "pdf"

    except frappe.PermissionError:
        frappe.response.http_status_code = 403
        frappe.response['message'] = "Permission Denied"
    
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "download_salary_slip_pdf Error")
        frappe.response.http_status_code = 500
        frappe.response['message'] = "An error occurred while generating the PDF. Please contact support."

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
def send_fcm_notification(doc, method=None):
    # 1. Get Access Token (Original Function)
    access_token = get_google_access_token()
    if not access_token:
        frappe.log_error("FCM Token Error", "Could not get Google Access Token")
        return

    # 2. Get Data
    title = getattr(doc, "title", "New Notification")
    content = getattr(doc, "content", "You have a new update")

    data_payload = {
        "id": str(doc.name),
        "doctype": doc.doctype,
        "title": title,
        "body": content,
        "click_action": "FLUTTER_NOTIFICATION_CLICK" 
    }

    target = None
    token = None

    # 3. Determine Target
    if doc.doctype == "App Announcement":
        target = {"topic": "all_users"}
    elif doc.doctype == "App Push Notification":
        if getattr(doc, "send_to_all", False):
            target = {"topic": "all_users"}
        elif getattr(doc, "send_to_employee", None):
            employee_id = doc.send_to_employee
            helper_name = frappe.db.get_value("Employee Helper", {"employee": employee_id}, "name")
            if helper_name:
                token = frappe.db.get_value("Employee Helper", helper_name, "fcm_token")
                
            if not token:
                frappe.log_error(f"Skipping {employee_id}: No FCM Token saved.")
                return
            target = {"token": token}

    if not target:
        frappe.log_error(f"No valid target for {doc.doctype}. Skipping.")
        return

    message = {
        "message": {
            **target,
            "notification": {
                "title": title,
                "body": content
            },
            "android": {
                "priority": "high",
                "notification": {
                    "channel_id": "geofence-channel-id",
                    "visibility": "PUBLIC",
                    "sound": "default"
                }
            },
            "data": data_payload
        }
    }

    # 4. Get Project ID (Safety Added for File Read)
    try:
        creds_dict = json.load(open(frappe.get_site_path("private", "files", "firebase-service-account.json")))
        project_id = creds_dict.get("project_id")
        if not project_id:
            frappe.log_error("FCM Config Error", "project_id not found in JSON")
            return
    except Exception as e:
        frappe.log_error("FCM JSON Error", str(e))
        return

    url = f"https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"
    headers = {
        "Authorization": "Bearer " + access_token,
        "Content-Type": "application/json"
    }

    # 5. SEND SAFELY (Prevents Server Crashes on 404s)
    try:
        response = requests.post(url, headers=headers, data=json.dumps(message))
        
        if response.status_code == 404:
            frappe.log_error(f"⚠️ FCM Error 404 (Bad Token) for {doc.name}. Skipping.")
            return

        if response.status_code != 200:
            frappe.log_error("FCM Send Error", f"Status: {response.status_code}, Response: {response.text}")
            return

    except Exception as e:
        frappe.log_error("FCM Connection Error", str(e))


@frappe.whitelist()
def check_and_send_shift_reminders():
    """
    Optimized Hybrid: Checks Shift Assignments AND Default Shifts.
    Run Frequency: Every 5 minutes.
    """
    try:
        now_dt = frappe.utils.now_datetime()
        current_date_str = now_dt.strftime("%Y-%m-%d")

        # Step A: Get Explicit Shift Assignments (Priority 1)
        assignments = frappe.get_all(
            "Shift Assignment",
            filters={
                "start_date": ("<=", current_date_str),
                "end_date": (">=", current_date_str),
                "status": "Active",
                "docstatus": 1
            },
            fields=["employee", "shift_type"]
        )
        
        assigned_employee_ids = [d.employee for d in assignments]

        # Step B: Get Default Shifts (Priority 2)
        # Find active employees with a Default Shift who are NOT in the assignment list
        filters = {
            "default_shift": ["is", "set"],
            "status": "Active"
        }
        
        if assigned_employee_ids:
            filters["name"] = ["not in", assigned_employee_ids]

        defaults = frappe.get_all(
            "Employee",
            filters=filters,
            fields=["name as employee", "default_shift as shift_type"]
        )

        # Step C: Combine & Bulk Fetch Timings
        all_shifts = assignments + defaults
        
        if not all_shifts:
            return

        shift_type_names = list(set([d.shift_type for d in all_shifts]))
        
        shift_types = frappe.get_all(
            "Shift Type",
            filters={"name": ["in", shift_type_names]},
            fields=["name", "start_time"]
        )
        
        shift_timing_map = {st.name: st.start_time for st in shift_types}

        # Step D: Process Loop
        for shift in all_shifts:
            try:
                start_time_str = shift_timing_map.get(shift.shift_type)
                if not start_time_str: 
                    continue
                
                # Combine Date + Time
                shift_start_dt = frappe.utils.get_datetime(f"{current_date_str} {start_time_str}")
                
                # Calculate difference in Minutes
                diff_seconds = (shift_start_dt - now_dt).total_seconds()
                diff_minutes = diff_seconds / 60.0

                # --- Condition A: 5 minutes before shift (3 to 7 mins) ---
                if 3 <= diff_minutes <= 7:
                    if not has_employee_checked_in(shift.employee, current_date_str):
                        emp_name = frappe.get_value("Employee", shift.employee, "employee_name")
                        send_shift_notification(
                            shift.employee, 
                            "Shift Reminder", 
                            f"Hi {emp_name}, your shift starts in 5 minutes. Please check in now."
                        )

                # --- Condition B: 10 minutes late (-12 to -8 mins) ---
                elif -12 <= diff_minutes <= -8:
                    if not has_employee_checked_in(shift.employee, current_date_str):
                        emp_name = frappe.get_value("Employee", shift.employee, "employee_name")
                        send_shift_notification(
                            shift.employee, 
                            "Missed Check-in Alert", 
                            f"Hi {emp_name}, your shift started 10 minutes ago. Please mark your attendance immediately."
                        )

            except Exception as e:
                frappe.log_error(f"Shift reminder error for {shift.employee}: {e}")
                continue

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Shift Reminder Job Failed")


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


def send_shift_notification(employee_id, title, message):
    try:
        doc = frappe.new_doc("App Push Notification")
        doc.send_to_all = 0
        doc.send_to_employee = employee_id
        doc.title = title
        doc.content = message
        doc.insert(ignore_permissions=True)
        doc.submit()
        
        # NOTE: Commit removed here to allow Cron to handle transaction efficiently
        # send_fcm_notification is called via Hooks, which is correct.
        
    except Exception as e:
        frappe.log_error(f"Failed to create notification for {employee_id}: {str(e)}", "Shift Notification Error")