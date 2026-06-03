import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate


def validate_overtime_import_comp_off(doc, method):
    """Show duplicate comp-off errors on Save/Submit, before on_submit runs."""
    if doc.docstatus != 0 or not doc.name:
        return
    _raise_if_duplicate_comp_off_rows(doc)


def calculate_compensatory_leave(doc, method):
    """OverTime Import on_submit: credit comp-off with 60-day validity."""
    for row in doc.overtime_import_details:
        _create_comp_off_for_row(row, import_name=doc.name)


def get_existing_allocation_for_date(employee, comp_leave_valid_from):
    """Active Leave Allocation for Compensatory Off covering valid_from."""
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


def get_comp_leave_validity_period(start_date):
    """60-day window: valid_from is day after OT; valid_to is 59 days later."""
    valid_from = add_days(getdate(start_date), 1)
    valid_to = add_days(valid_from, 59)
    return valid_from, valid_to


def get_net_comp_off_credit(employee, comp_leave_valid_from):
    """Net credited comp-off in the ledger for a given validity start date."""
    return flt(
        frappe.db.sql(
            """
            SELECT COALESCE(SUM(leaves), 0)
            FROM `tabLeave Ledger Entry`
            WHERE employee = %(employee)s
                AND leave_type = %(leave_type)s
                AND from_date = %(from_date)s
                AND docstatus = 1
                AND is_expired = 0
            """,
            {
                "employee": employee,
                "leave_type": "Compensatory Off",
                "from_date": getdate(comp_leave_valid_from),
            },
        )[0][0]
    )


def ot_already_credited_in_other_import(employee, attendance_date, exclude_parent):
    """True if another submitted OverTime Import already has this employee/date."""
    return bool(
        frappe.db.sql(
            """
            SELECT oi.name
            FROM `tabOvertime Import Item` oii
            INNER JOIN `tabOverTime Import` oi ON oi.name = oii.parent
            WHERE oii.employee = %(employee)s
                AND oii.attendance_date = %(attendance_date)s
                AND oi.docstatus = 1
                AND oi.name != %(exclude_parent)s
            LIMIT 1
            """,
            {
                "employee": employee,
                "attendance_date": getdate(attendance_date),
                "exclude_parent": exclude_parent,
            },
        )
    )


def create_leave_allocation(employee, attendance_date, total_leave_days):
    comp_leave_valid_from, comp_leave_valid_to = get_comp_leave_validity_period(attendance_date)

    existing_allocation = get_existing_allocation_for_date(employee, comp_leave_valid_from)
    if existing_allocation:
        update_leave_allocation(existing_allocation, total_leave_days, comp_leave_valid_from)
        return existing_allocation

    is_carry_forward = frappe.db.get_value("Leave Type", "Compensatory Off", "is_carry_forward")
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
    leave_allocation.new_leaves_allocated += total_leave_days
    leave_allocation.total_leaves_allocated += total_leave_days
    leave_allocation.validate()
    leave_allocation.db_set("new_leaves_allocated", leave_allocation.new_leaves_allocated)
    leave_allocation.db_set("total_leaves_allocated", leave_allocation.total_leaves_allocated)

    _, comp_leave_valid_to = get_comp_leave_validity_period(add_days(comp_leave_valid_from, -1))
    create_compensatory_ledger_entry(
        leave_allocation,
        total_leave_days,
        comp_leave_valid_from,
        comp_leave_valid_to,
    )


def create_compensatory_ledger_entry(leave_allocation, leaves, from_date, to_date):
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
        "docstatus": 1,
    })
    ledger.flags.ignore_permissions = True
    ledger.insert()
    return ledger


def cancel_compensatory_leave(doc, method):
    """OverTime Import on_cancel: reverse comp-off only when it was credited."""
    for row in doc.overtime_import_details:
        _reverse_comp_off_for_row(row)


def recalculate_compensatory_leave_on_update(doc, method):
    """OverTime Import on_update_after_submit: delta credit/reverse on row changes."""
    old_doc = doc._doc_before_save
    if not old_doc:
        return

    old_rows = {
        (str(row.employee), str(row.attendance_date)): row
        for row in old_doc.overtime_import_details
        if row.employee and row.attendance_date
    }
    new_rows = {
        (str(row.employee), str(row.attendance_date)): row
        for row in doc.overtime_import_details
        if row.employee and row.attendance_date
    }

    for key in set(old_rows) | set(new_rows):
        old_row = old_rows.get(key)
        new_row = new_rows.get(key)

        if old_row and not new_row:
            _reverse_comp_off_for_row(old_row)
        elif new_row and not old_row:
            _create_comp_off_for_row(new_row, import_name=doc.name)
        elif old_row and new_row and (old_row.over_time or 0) != (new_row.over_time or 0):
            _reverse_comp_off_for_row(old_row)
            _create_comp_off_for_row(new_row, import_name=doc.name)


def _reverse_comp_off_for_row(row):
    overtime_hours = row.over_time or 0
    if overtime_hours <= 0:
        return

    total_leave_days = flt(overtime_hours / 8, 3)
    comp_leave_valid_from, comp_leave_valid_to = get_comp_leave_validity_period(row.attendance_date)

    net_credit = get_net_comp_off_credit(row.employee, comp_leave_valid_from)
    if net_credit <= 0:
        return

    days_to_reverse = min(total_leave_days, net_credit)
    leave_allocation = get_existing_allocation_for_date(row.employee, comp_leave_valid_from)
    if not leave_allocation:
        return

    leave_allocation.new_leaves_allocated = max(
        0, flt(leave_allocation.new_leaves_allocated) - days_to_reverse
    )
    leave_allocation.total_leaves_allocated = leave_allocation.new_leaves_allocated
    leave_allocation.flags.ignore_validate = True
    leave_allocation.db_set("new_leaves_allocated", leave_allocation.new_leaves_allocated)
    leave_allocation.db_set("total_leaves_allocated", leave_allocation.total_leaves_allocated)

    create_compensatory_ledger_entry(
        leave_allocation,
        days_to_reverse * -1,
        comp_leave_valid_from,
        comp_leave_valid_to,
    )


def _raise_if_duplicate_comp_off_rows(doc):
    """frappe.throw with a clear message for all duplicate comp-off rows."""
    duplicates = []
    for row in doc.overtime_import_details:
        if not row.employee or not row.attendance_date:
            continue
        if not frappe.db.get_value("Employee", row.employee, "compensatory_off"):
            continue
        if not ot_already_credited_in_other_import(row.employee, row.attendance_date, doc.name):
            continue
        other_import = frappe.db.sql(
            """
            SELECT oi.name
            FROM `tabOvertime Import Item` oii
            INNER JOIN `tabOverTime Import` oi ON oi.name = oii.parent AND oi.docstatus = 1
            WHERE oii.employee = %(employee)s
                AND oii.attendance_date = %(attendance_date)s
                AND oi.name != %(exclude_parent)s
            LIMIT 1
            """,
            {
                "employee": row.employee,
                "attendance_date": getdate(row.attendance_date),
                "exclude_parent": doc.name,
            },
        )
        duplicates.append(
            _("{0} on {1} (already in {2})").format(
                row.employee,
                row.attendance_date,
                other_import[0][0] if other_import else "?",
            )
        )

    if duplicates:
        frappe.throw(
            _("Compensatory off is already credited for:<br>{0}").format("<br>".join(duplicates)),
            title=_("Duplicate OverTime Import"),
        )


def _create_comp_off_for_row(row, import_name=None):
    overtime_hours = row.over_time or 0
    if overtime_hours <= 0:
        return

    if not frappe.db.get_value("Employee", row.employee, "compensatory_off"):
        return

    if import_name and ot_already_credited_in_other_import(
        row.employee, row.attendance_date, import_name
    ):
        frappe.throw(
            _(
                "Compensatory off for employee {0} on {1} is already credited via another submitted OverTime Import."
            ).format(row.employee, row.attendance_date),
            title=_("Duplicate OverTime Import"),
        )

    if not frappe.db.exists(
        "Attendance",
        {
            "employee": row.employee,
            "attendance_date": row.attendance_date,
            "status": ["not in", ["Absent", "On Leave"]],
            "docstatus": 1,
        },
    ):
        return

    total_leave_days = flt(overtime_hours / 8, 3)
    comp_leave_valid_from = get_comp_leave_validity_period(row.attendance_date)[0]

    leave_allocation = get_existing_allocation_for_date(row.employee, comp_leave_valid_from)
    if leave_allocation:
        update_leave_allocation(leave_allocation, total_leave_days, comp_leave_valid_from)
    else:
        create_leave_allocation(row.employee, row.attendance_date, total_leave_days)
