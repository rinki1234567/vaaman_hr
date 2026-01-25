import frappe
from frappe import _
from frappe.utils import add_days, cint, format_date, get_url_to_list
from hrms.hr.utils import create_additional_leave_ledger_entry, get_leave_period
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
    """
    try:
        for row in doc.overtime_import_details:

            employee = row.employee
            attendance_date = row.attendance_date
            overtime_hours = row.over_time

            if not overtime_hours or overtime_hours <= 0:
                continue

            # Employee eligibility
            compoff = frappe.db.get_value(
                "Employee", employee, "compensatory_off"
            )
            if not compoff:
                continue

            # Attendance must exist and be submitted
            attendance = frappe.db.exists(
                "Attendance",
                {
                    "employee": employee,
                    "attendance_date": attendance_date,
                    "status": ["in", ["Present", "Weekly Off"]],
                    "docstatus": 1
                }
            )
            if not attendance:
                continue

            total_leave_days = overtime_hours / 8  # 8 hrs = 1 Comp-Off

            company = frappe.db.get_value(
                "Employee", employee, "company"
            )

            comp_leave_valid_from = add_days(attendance_date, 1)

            leave_period = get_leave_period(
                comp_leave_valid_from,
                comp_leave_valid_from,
                company
            )

            if leave_period:
                leave_allocation = get_existing_allocation_for_period(
                    employee, leave_period
                )

                if leave_allocation:
                    update_leave_allocation(
                        leave_allocation,
                        total_leave_days,
                        comp_leave_valid_from
                    )
                else:
                    leave_allocation = create_leave_allocation(
                        employee,
                        attendance_date,
                        leave_period,
                        total_leave_days
                    )

            else:
                handle_no_leave_period(comp_leave_valid_from)

    except frappe.ValidationError:
        raise

    except Exception:
        logger.error(
            frappe.get_traceback(),
            "Compensatory Leave Calculation Error"
        )
        frappe.throw(
            _("An unexpected error occurred while calculating compensatory leave.")
        )


# ---------------------------------------------------------
# HELPERS (UNCHANGED LOGIC)
# ---------------------------------------------------------
def get_existing_allocation_for_period(employee, leave_period):
    leave_allocation = frappe.db.sql(
        """
        select name
        from `tabLeave Allocation`
        where employee=%(employee)s
            and leave_type=%(leave_type)s
            and docstatus=1
            and (
                from_date between %(from_date)s and %(to_date)s
                or to_date between %(from_date)s and %(to_date)s
                or (from_date < %(from_date)s and to_date > %(to_date)s)
            )
        """,
        {
            "from_date": leave_period[0].from_date,
            "to_date": leave_period[0].to_date,
            "employee": employee,
            "leave_type": "Compensatory Off",
        },
        as_dict=1,
    )

    return (
        frappe.get_doc("Leave Allocation", leave_allocation[0].name)
        if leave_allocation else False
    )


def create_leave_allocation(employee, attendance_date, leave_period, total_leave_days):
    is_carry_forward = frappe.db.get_value(
        "Leave Type", "Compensatory Off", "is_carry_forward"
    )

    emp = frappe.get_doc("Employee", employee)

    allocation = frappe.get_doc(
        dict(
            doctype="Leave Allocation",
            employee=employee,
            employee_name=emp.employee_name,
            leave_type="Compensatory Off",
            from_date=leave_period[0].from_date,
            to_date=leave_period[0].to_date,
            carry_forward=cint(is_carry_forward),
            new_leaves_allocated=total_leave_days,
            total_leaves_allocated=total_leave_days,
            description=_(
                "Compensatory leave allocated due to overtime on {0}"
            ).format(attendance_date),
        )
    )
    allocation.insert(ignore_permissions=True)
    allocation.submit()
    return allocation


def update_leave_allocation(leave_allocation, total_leave_days, comp_leave_valid_from):
    leave_allocation.new_leaves_allocated += total_leave_days
    leave_allocation.validate()

    leave_allocation.db_set(
        "new_leaves_allocated",
        leave_allocation.new_leaves_allocated
    )
    leave_allocation.db_set(
        "total_leaves_allocated",
        leave_allocation.total_leaves_allocated
    )

    create_additional_leave_ledger_entry(
        leave_allocation,
        total_leave_days,
        comp_leave_valid_from
    )


def handle_no_leave_period(comp_leave_valid_from):
    comp_leave_valid_from = frappe.bold(
        format_date(comp_leave_valid_from)
    )

    msg = _("This compensatory leave will be applicable from {0}.").format(
        comp_leave_valid_from
    )
    msg += " " + _(
        "Currently, there is no active Leave Period for this date."
    )

    msg += "<br><br>" + _(
        "Please create a new {0} first."
    ).format(
        f"""<a href='{get_url_to_list("Leave Period")}'>Leave Period</a>"""
    )

    frappe.throw(msg, title=_("No Leave Period Found"))


# ---------------------------------------------------------
# CANCEL COMPENSATORY LEAVE (OverTime Import Cancel)
# ---------------------------------------------------------
@frappe.whitelist()
def cancel_compensatory_leave(doc, method):
    try:
        for row in doc.overtime_import_details:

            employee = row.employee
            attendance_date = row.attendance_date
            overtime_hours = row.over_time

            if not overtime_hours or overtime_hours <= 0:
                continue

            total_leave_days = overtime_hours / 8

            leave_allocation = frappe.db.get_value(
                "Leave Allocation",
                {
                    "employee": employee,
                    "leave_type": "Compensatory Off",
                    "docstatus": 1
                },
                "name"
            )

            if not leave_allocation:
                continue

            leave_allocation = frappe.get_doc(
                "Leave Allocation", leave_allocation
            )

            leave_allocation.new_leaves_allocated -= total_leave_days
            if leave_allocation.new_leaves_allocated < 0:
                leave_allocation.new_leaves_allocated = 0

            leave_allocation.validate()

            leave_allocation.db_set(
                "new_leaves_allocated",
                leave_allocation.new_leaves_allocated
            )
            leave_allocation.db_set(
                "total_leaves_allocated",
                leave_allocation.total_leaves_allocated
            )

            create_additional_leave_ledger_entry(
                leave_allocation,
                total_leave_days * -1,
                add_days(attendance_date, 1)
            )

    except Exception:
        logger.error(
            frappe.get_traceback(),
            "Compensatory Leave Cancellation Error"
        )
        raise
