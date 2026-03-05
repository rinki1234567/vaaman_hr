import frappe
from frappe.custom.doctype.property_setter.property_setter import make_property_setter


def set_select_options(doctype, fieldname, options):
    """Update Select field options using Property Setter (safe in production)"""
    make_property_setter(
        doctype,
        fieldname,
        "options",
        options,
        "Text",
        validate_fields_for_doctype=False
    )


def create_custom_field_if_not_exists(dt, field):
    """Create custom field safely"""
    if not frappe.db.exists("Custom Field", {"dt": dt, "fieldname": field["fieldname"]}):
        field["dt"] = dt
        frappe.get_doc({
            "doctype": "Custom Field",
            **field
        }).insert()


def update_earned_leave_frequency_options():
    set_select_options(
        "Leave Type",
        "earned_leave_frequency",
        "Monthly\nQuarterly\nHalf-Yearly\nYearly\n20 Days"
    )


def update_attendance_request_reason_options():
    set_select_options(
        "Attendance Request",
        "reason",
        "Work From Home\nOn Duty\nWeekly Off\nSystem Error\nGatepass in Process\nChange State of Holiday"
    )


def update_attendance_status_options():
    set_select_options(
        "Attendance",
        "status",
        "Present\nAbsent\nOn Leave\nHalf Day\nWork From Home\nWeekly Off\nHoliday"
    )

    # ---------- Custom Fields ----------
    create_custom_field_if_not_exists("Attendance", {
        "fieldname": "custom_holiday_list",
        "label": "Holiday List",
        "fieldtype": "Link",
        "options": "Holiday List",
        "insert_after": "status"
    })

    create_custom_field_if_not_exists("Attendance", {
        "fieldname": "custom_branch",
        "label": "Branch",
        "fieldtype": "Link",
        "options": "Branch",
        "insert_after": "custom_holiday_list"
    })

    create_custom_field_if_not_exists("Attendance", {
        "fieldname": "custom_attendance_type",
        "label": "Attendance Type",
        "fieldtype": "Select",
        "options": "Outdoor duty\nTraining Days\nMedical Gatepass\nHoliday\nPresent on Weekly off\nPresent on Weekly off Holiday\nOT Days",
        "insert_after": "custom_branch"
    })

    create_custom_field_if_not_exists("Attendance", {
        "fieldname": "custom_over_time",
        "label": "Over Time",
        "fieldtype": "Float",
        "insert_after": "custom_attendance_type"
    })
