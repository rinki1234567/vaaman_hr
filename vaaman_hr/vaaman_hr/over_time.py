import frappe
from frappe import _
from frappe.utils import add_days, cint, format_date, get_url_to_list
from hrms.hr.utils import create_additional_leave_ledger_entry
from frappe.utils.logger import set_log_level, get_logger

set_log_level("DEBUG")
logger = get_logger("compensatory_leave")


# ---------------------------------------------------------
# MAIN: Calculate Compensatory Leave (OverTime Import Hook)
# ---------------------------------------------------------
@frappe.whitelist()
def calculate_compensatory_leave(doc, method):
    """
    Triggered on OverTime Import submit
    Creates/updates Compensatory Off with 60 days validity from the day after OT.
    """
    try:
        logger.info(f"Starting compensatory leave calculation for OverTime Import: {doc.name}")
        logger.info(f"Total rows in overtime_import_details: {len(doc.overtime_import_details)}")
        
        for idx, row in enumerate(doc.overtime_import_details, 1):
            employee = row.employee
            attendance_date = row.attendance_date
            overtime_hours = row.over_time

            logger.info(f"Processing row {idx}: Employee={employee}, Date={attendance_date}, OT Hours={overtime_hours}")

            if not overtime_hours or overtime_hours <= 0:
                logger.warning(f"Row {idx} SKIPPED: No overtime hours or hours <= 0")
                continue

            # Employee eligibility check
            compoff_enabled = frappe.db.get_value("Employee", employee, "compensatory_off")
            logger.info(f"Row {idx}: Employee {employee} compensatory_off enabled = {compoff_enabled}")
            if not compoff_enabled:
                logger.warning(f"Row {idx} SKIPPED: Employee {employee} does not have compensatory_off enabled")
                continue

            # Attendance must exist and be submitted (not Absent or On Leave)
            attendance = frappe.db.exists(
                "Attendance",
                {
                    "employee": employee,
                    "attendance_date": attendance_date,
                    "status": ["not in", ["Absent", "On Leave"]],
                    "docstatus": 1
                }
            )
            logger.info(f"Row {idx}: Attendance found = {attendance}")
            if not attendance:
                logger.warning(f"Row {idx} SKIPPED: No valid attendance record found for employee {employee} on {attendance_date}")
                logger.warning(f"  Required: Submitted attendance with status not Absent/On Leave")
                continue

            total_leave_days = overtime_hours / 8  # 8 hrs = 1 Comp-Off
            comp_leave_valid_from = add_days(attendance_date, 1)

            logger.info(f"Row {idx}: Creating/updating comp-off: {total_leave_days} days from {comp_leave_valid_from}")

            # Check if allocation already exists for this exact period
            leave_allocation = get_existing_allocation_for_date(employee, comp_leave_valid_from)

            if leave_allocation:
                logger.info(f"Row {idx}: Updating existing allocation {leave_allocation.name}")
                update_leave_allocation(
                    leave_allocation,
                    total_leave_days,
                    comp_leave_valid_from
                )
            else:
                logger.info(f"Row {idx}: Creating new allocation")
                created_allocation = create_leave_allocation(
                    employee,
                    attendance_date,
                    total_leave_days
                )
                logger.info(f"Row {idx}: Successfully created allocation {created_allocation.name}")

        logger.info(f"Completed compensatory leave calculation for OverTime Import: {doc.name}")

    except frappe.ValidationError:
        logger.error(frappe.get_traceback(), "Validation Error in Compensatory Leave Calculation")
        raise
    except Exception:
        logger.error(frappe.get_traceback(), "Compensatory Leave Calculation Error")
        frappe.throw(_("An unexpected error occurred while calculating compensatory leave."))


# ---------------------------------------------------------
# HELPERS
# ---------------------------------------------------------
def get_existing_allocation_for_date(employee, comp_leave_valid_from):
    """
    Find the active Leave Allocation for Compensatory Off.
    Since we track 60-day expiry through ledger entries (not separate allocations),
    we look for any active allocation that covers the period.
    """
    leave_allocation = frappe.db.sql(
        """
        SELECT name
        FROM `tabLeave Allocation`
        WHERE employee = %(employee)s
            AND leave_type = %(leave_type)s
            AND docstatus = 1
            AND %(comp_leave_valid_from)s BETWEEN from_date AND to_date
        ORDER BY from_date DESC
        LIMIT 1
        """,
        {
            "employee": employee,
            "leave_type": "Compensatory Off",
            "comp_leave_valid_from": comp_leave_valid_from,
        },
        as_dict=True,
    )

    if leave_allocation:
        return frappe.get_doc("Leave Allocation", leave_allocation[0].name)
    return None


def create_leave_allocation(employee, attendance_date, total_leave_days):
    """
    Add compensatory leave to existing allocation (if found) or create new one.
    The 60-day validity is tracked via ledger entries, not separate allocations.
    """
    comp_leave_valid_from = add_days(attendance_date, 1)
    comp_leave_valid_to = add_days(comp_leave_valid_from, 59)   # 60 days validity (inclusive)
    
    # Check if an allocation already exists for this period
    existing_allocation = get_existing_allocation_for_date(employee, comp_leave_valid_from)
    
    if existing_allocation:
        logger.info(f"Found existing allocation {existing_allocation.name}, updating it instead of creating new one")
        # Update the existing allocation
        update_leave_allocation(
            existing_allocation,
            total_leave_days,
            comp_leave_valid_from
        )
        return existing_allocation
    
    # No existing allocation found, create a new one with 60-day validity
    is_carry_forward = frappe.db.get_value(
        "Leave Type", "Compensatory Off", "is_carry_forward"
    )
    emp = frappe.get_doc("Employee", employee)

    allocation = frappe.get_doc({
        "doctype": "Leave Allocation",
        "employee": employee,
        "employee_name": emp.employee_name,
        "leave_type": "Compensatory Off",
        "from_date": comp_leave_valid_from,
        "to_date": comp_leave_valid_to,
        "carry_forward": cint(is_carry_forward),
        "new_leaves_allocated": total_leave_days,
        "total_leaves_allocated": total_leave_days,
        "description": _("Compensatory leave for overtime on {0}. Valid for 60 days from {1} to {2}").format(
            attendance_date, comp_leave_valid_from, comp_leave_valid_to
        ),
    })

    allocation.insert(ignore_permissions=True)
    allocation.submit()
    return allocation


def update_leave_allocation(leave_allocation, total_leave_days, comp_leave_valid_from):
    """
    Add more leaves to an existing allocation.
    Creates ledger entry with 60-day validity to enforce expiry.
    """
    leave_allocation.new_leaves_allocated += total_leave_days
    leave_allocation.total_leaves_allocated += total_leave_days   # Important: keep both in sync

    leave_allocation.validate()
    
    leave_allocation.db_set("new_leaves_allocated", leave_allocation.new_leaves_allocated)
    leave_allocation.db_set("total_leaves_allocated", leave_allocation.total_leaves_allocated)

    # Create ledger entry with 60-day validity (not allocation's to_date)
    comp_leave_valid_to = add_days(comp_leave_valid_from, 59)
    create_compensatory_ledger_entry(
        leave_allocation,
        total_leave_days,
        comp_leave_valid_from,
        comp_leave_valid_to
    )


def create_compensatory_ledger_entry(leave_allocation, leaves, from_date, to_date):
    """
    Create leave ledger entry with custom 60-day validity period.
    This ensures comp-off expires after 60 days, regardless of allocation period.
    """
    ledger = frappe.get_doc({
        "doctype": "Leave Ledger Entry",
        "employee": leave_allocation.employee,
        "leave_type": leave_allocation.leave_type,
        "transaction_type": "Leave Allocation",
        "transaction_name": leave_allocation.name,
        "leaves": leaves,
        "from_date": from_date,
        "to_date": to_date,
        "is_carry_forward": 0,
        "docstatus": 1
    })
    ledger.flags.ignore_permissions = True
    ledger.insert()
    logger.info(f"Created ledger entry {ledger.name}: {leaves} days valid from {from_date} to {to_date}")
    return ledger


# ---------------------------------------------------------
# CANCEL COMPENSATORY LEAVE (OverTime Import Cancel Hook)
# ---------------------------------------------------------
@frappe.whitelist()
def cancel_compensatory_leave(doc, method):
    """
    Triggered on OverTime Import cancel - reverses the comp-off allocated.
    """
    try:
        for row in doc.overtime_import_details:
            employee = row.employee
            attendance_date = row.attendance_date
            overtime_hours = row.over_time

            if not overtime_hours or overtime_hours <= 0:
                continue

            total_leave_days = overtime_hours / 8
            comp_leave_valid_from = add_days(attendance_date, 1)

            # Find the allocation that was created/updated for this OT
            leave_allocation = get_existing_allocation_for_date(employee, comp_leave_valid_from)

            if not leave_allocation:
                continue

            # Reduce the allocated leaves
            leave_allocation.new_leaves_allocated -= total_leave_days
            if leave_allocation.new_leaves_allocated < 0:
                leave_allocation.new_leaves_allocated = 0

            leave_allocation.total_leaves_allocated = leave_allocation.new_leaves_allocated

            leave_allocation.validate()

            leave_allocation.db_set("new_leaves_allocated", leave_allocation.new_leaves_allocated)
            leave_allocation.db_set("total_leaves_allocated", leave_allocation.total_leaves_allocated)

            # Reverse ledger entry with 60-day validity
            comp_leave_valid_to = add_days(comp_leave_valid_from, 59)
            create_compensatory_ledger_entry(
                leave_allocation,
                total_leave_days * -1,  # Negative to reverse
                comp_leave_valid_from,
                comp_leave_valid_to
            )

    except Exception:
        logger.error(frappe.get_traceback(), "Compensatory Leave Cancellation Error")
        raise