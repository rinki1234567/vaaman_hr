
import frappe

def update_branch(doc, method=None):
    if not doc.has_value_changed("branch"):
        return

    if not doc.branch:
        return

    # Update only master/assignment doctypes
    updates = [
        ("Salary Structure Assignment", "custom_branch"),
        ("Leave Policy Assignment", "custom_branch"),
        ("Leave Allocation", "custom_branch"),
        ("Leave Policy Assignment", "custom_branch"),
    ]

    for doctype, branch_field in updates:
        if not frappe.db.exists("DocType", doctype):
            continue

        meta = frappe.get_meta(doctype)

        if not meta.has_field("employee") or not meta.has_field(branch_field):
            continue

        frappe.db.sql(
            f"""
            UPDATE `tab{doctype}`
            SET `{branch_field}`=%s
            WHERE employee=%s
            """,
            (doc.branch, doc.name),
        )

    frappe.clear_cache()