


import frappe
from frappe import _
from frappe.utils import flt, now_datetime

def on_salary_assignment_update(doc, method=None):
    """
    Hook handler triggered when Salary Assignment is updated.
    """
    create_salary_component_history(doc)

def create_salary_component_history(doc):
    """
    Tracks changes in custom currency fields and creates history records.
    """
    try:
        # Identify custom currency fields starting with 'custom_'
        fields_to_track = [
            df.fieldname for df in doc.meta.fields 
            if df.fieldname.startswith('custom_') and df.fieldtype == 'Currency'
        ]

        if not fields_to_track:
            return

        # Fetch the previous state of the document before current save
        old_doc = doc.get_doc_before_save()
        
        for fieldname in fields_to_track:
            new_value = flt(doc.get(fieldname))
            old_value = flt(old_doc.get(fieldname)) if old_doc else 0.0

            # Proceed if the value has changed or if it is a new document
            if not old_doc or (new_value != old_value):
                field_meta = doc.meta.get_field(fieldname)
                label = field_meta.label if field_meta else fieldname

                # Determine the action type for remarks
                current_action = "Updated"
                if not old_doc:
                    current_action = "Initial Assignment"

                # Prepare the data for the history record
                history_data = {
                    "doctype": "Salary Component History",
                    "employee_id": doc.employee,
                    "component_name": label,
                    "amount": new_value,
                    # "from_date": doc.from_date,
                    "modified_on": now_datetime(),
                    "remarks": f"{current_action}: Value changed from {old_value} to {new_value}"
                }

                try:
                    # Verify if the custom history Doctype exists
                    if not frappe.db.exists("DocType", "Salary Component History"):
                        frappe.log_error("Doctype Missing", "Salary Component History does not exist.")
                        return

                    # Insert the history record
                    new_hist = frappe.get_doc(history_data)
                    new_hist.insert(ignore_permissions=True)
                    
                except Exception:
                    # Log error for specific field insertion failure
                    frappe.log_error(
                        title="Salary History Field Insert Error", 
                        message=f"Field: {fieldname}\n{frappe.get_traceback()}"
                    )

    except Exception:
        # Log any critical logic errors
        frappe.log_error(
            title="Salary History Main Logic Error", 
            message=frappe.get_traceback()
        )
        
          
        



import frappe
from frappe.utils import today, add_days 


def update_history():

    current_date = today()

    # PART 1: Apply component amount when from_date is today
    history_records = frappe.get_all(
        "Salary Component History",
        filters={
            "from_date": current_date
        },
        fields=[
            "name",
            "employee_id",
            "component_name",
            "amount"
        ]
    )

    for row in history_records:
        field_name = (
            "custom_"
            + row.component_name.lower().replace(" ", "_")
        )

        ssa_name = frappe.db.get_value(
            "Salary Structure Assignment",
            {
                "employee": row.employee_id,
                "docstatus": 1
            },
            "name"
        )

        if not ssa_name:
            continue

        if frappe.get_meta("Salary Structure Assignment").has_field(field_name):
            frappe.db.set_value(
                "Salary Structure Assignment",
                ssa_name,
                {field_name: row.amount},
                update_modified=True
            )
            frappe.logger("salary_update").info(f"Updated {field_name} for {row.employee_id}")

    yesterday = add_days(current_date, -1)

    expired_records = frappe.get_all(
        "Salary Component History",
        filters={
            "to_date": yesterday 
        },
        fields=[
            "name",
            "employee_id",
            "component_name",
            "to_date"
        ]
    )

    for row in expired_records:
        field_name = (
            "custom_"
            + row.component_name.lower().replace(" ", "_")
        )

        # Check future record
        future_record_exists = frappe.db.exists(
            "Salary Component History",
            {
                "employee_id": row.employee_id,
                "component_name": row.component_name,
                "from_date": [">", row.to_date]
            }
        )

        if future_record_exists:
            continue

        ssa_name = frappe.db.get_value(
            "Salary Structure Assignment",
            {
                "employee": row.employee_id,
                "docstatus": 1
            },
            "name"
        )

        if ssa_name and frappe.get_meta("Salary Structure Assignment").has_field(field_name):
            frappe.db.set_value(
                "Salary Structure Assignment",
                ssa_name,
                {field_name: 0},
                update_modified=True
            )
            frappe.logger("salary_update").info(f"Reset {field_name} to 0 for {row.employee_id}")