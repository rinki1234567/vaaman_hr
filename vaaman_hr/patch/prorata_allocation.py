import frappe
from frappe.utils import getdate, nowdate
from dateutil.relativedelta import relativedelta

def prorata_leave_allocation(leave_type: str, fiscal_year: str):
    fiscal = frappe.get_doc("Fiscal Year", fiscal_year)
    fy_start = getdate(fiscal.year_start_date)
    fy_end = getdate(fiscal.year_end_date)

    employees = frappe.get_all("Employee", filters={"status": "Active"}, fields=["name", "date_of_joining"])

    for emp in employees:
        doj = getdate(emp.date_of_joining)
        alloc_start = doj if doj > fy_start else fy_start

        # Skip if employee joined after fiscal  year ends
        if alloc_start > fy_end:
            continue

        # --- Fetch assigned leave policy ---
        policy_assignment = frappe.db.get_value(
            "Leave Policy Assignment",
            {
                "employee": emp.name,
                "leave_policy": ("is", "set"),
            },
            "leave_policy",
            order_by="creation desc"
        )

        if not policy_assignment:
            frappe.msgprint(f"⚠️ No Leave Policy assigned to employee {emp.name}. Skipping.")
            continue

        # --- Fetch leave quota from policy ---
        leave_allocation_row = frappe.get_all(
            "Leave Allocation",
            filters={
                "parent": policy_assignment,
                "leave_type": leave_type
            },
            fields=["leave_type", "annual_allocation"]
        )

        if not leave_allocation_row:
            frappe.msgprint(f"⚠️ No quota for '{leave_type}' in policy '{policy_assignment}' for {emp.name}")
            continue

        annual_allocation = leave_allocation_row[0].annual_allocation

        # --- Calculate pro-rata leave ---
        days_in_fy = (fy_end - fy_start).days + 1
        eligible_days = (fy_end - alloc_start).days + 1
        prorated_leave = round((eligible_days / days_in_fy) * annual_allocation, 2)

        # --- Check if leave already allocated ---
        existing = frappe.db.exists("Leave Allocation", {
            "employee": emp.name,
            "leave_type": leave_type,
            "from_date": fy_start,
            "to_date": fy_end,
            "docstatus": ("<", 2)
        })

        if existing:
            frappe.msgprint(f"Leave already allocated for {emp.name} for {leave_type}. Skipping.")
            continue

        # --- Create Leave Allocation ---
        doc = frappe.new_doc("Leave Allocation")
        doc.employee = emp.name
        doc.leave_type = leave_type
        doc.from_date = fy_start
        doc.to_date = fy_end
        doc.new_leaves_allocated = prorated_leave
        doc.fiscal_year = fiscal_year
        doc.description = "Auto allocated based on pro-rata from Leave Policy"
        doc.submit()

        frappe.msgprint(f"✅ Allocated {prorated_leave} {leave_type} to {emp.name}")

