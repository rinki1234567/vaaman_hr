import frappe

def update_earned_leave_frequency_options():
    # Load the Leave Type DocType
    doctype = frappe.get_doc("DocType", "Leave Type")
    
    # Find the earned_leave_frequency field
    for field in doctype.fields:
        if field.fieldname == "earned_leave_frequency":
            # Update the options
            field.options = "Monthly\nQuarterly\nHalf-Yearly\nYearly\n20 Days"
            break
    
    # Save the changes to the DocType
    doctype.save()
    
    # Ensure the changes are committed
    frappe.db.commit()

def update_attendance_request_reason_options():
    doctype = frappe.get_doc("DocType", "Attendance Request")
    for field in doctype.fields:
        if field.fieldname == "reason":
            if "Weekly Off" not in (field.options or ""):
                field.options = "Work From Home\nOn Duty\nWeekly Off"
            break
    doctype.save()
    frappe.db.commit()

def update_attendance_status_options():
    # Load the Attendance DocType
    doctype = frappe.get_doc("DocType", "Attendance")
    
    # Find the Status field and update its options
    for field in doctype.fields:
        if field.fieldname == "status":
            field.options = "Present\nAbsent\nOn Leave\nHalf Day\nWork From Home\nWeekly Off\nHoliday"
            break
    
    # Add the custom fields if they don't already exist
    if not any(field.fieldname == "custom_holiday_list" for field in doctype.fields):
        new_field = frappe._dict(
            fieldname="custom_holiday_list",
            fieldtype="Link",
            label="Holiday List",
            options="Holiday List"
        )
        doctype.append("fields", new_field)
    
    if not any(field.fieldname == "custom_branch" for field in doctype.fields):
        new_field = frappe._dict(
            fieldname="custom_branch",
            fieldtype="Link",
            label="Branch",
            options="Branch"
        )
        doctype.append("fields", new_field)
    
    if not any(field.fieldname == "custom_attendance_type" for field in doctype.fields):
        new_field = frappe._dict(
            fieldname="custom_attendance_type",
            fieldtype="Select",
            label="Attendance Type",
            options="Outdoor duty\nTraining Days\nMedical Gatepass\nHoliday\nPresent on Weekly off\nPresent on Weekly off Holiday\nOT Days"
        )
        doctype.append("fields", new_field)
    
    if not any(field.fieldname == "custom_over_time" for field in doctype.fields):
        new_field = frappe._dict(
            fieldname="custom_over_time",
            fieldtype="Float",
            label="Over Time",
            options=""
        )
        doctype.append("fields", new_field)
    
    # Save the changes to the DocType
    doctype.save()
    
    # Ensure the changes are committed
    frappe.db.commit()
