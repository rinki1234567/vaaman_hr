import frappe
from frappe import _
from frappe.model.document import Document
from erpnext.accounts.doctype.process_payment_reconciliation.process_payment_reconciliation import trigger_job_for_doc

@frappe.whitelist()
def trigger_bulk_reconciliation(limit=100):
    queued_docs = frappe.get_all(
        "Process Payment Reconciliation",
        filters={
            "status": "Queued",
            "docstatus": 1
        },
        fields=["name"],
        limit=limit
    )

    triggered = []

    for doc in queued_docs:
        try:
            trigger_job_for_doc(doc.name)
            triggered.append(doc.name)
        except Exception as e:
            frappe.log_error(f"Error triggering PR: {doc.name}\n{str(e)}")

    return {"triggered_docs": triggered, "count": len(triggered)}
