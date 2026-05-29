# THIS CODE WILL MAKE USER LOG OUT. DO NOT USE IN HOOKS OR BACKGROUND JOBS. FOR TESING PURPOSES ONLY.

# import frappe
# from erpnext.stock.doctype.purchase_receipt.purchase_receipt import make_purchase_invoice

# def auto_create_purchase_invoice(doc, method):

#     current_user = frappe.session.user

#     try:

#         frappe.set_user("Administrator")


#         pi_doc = make_purchase_invoice(doc.name)

#         pi_doc.set_missing_values()


#         if doc.supplier_delivery_note:
#             base_bill_no = doc.supplier_delivery_note.strip()
#             unique_bill_no = base_bill_no
#             counter = 1


#             while frappe.db.exists("Purchase Invoice", {"bill_no": unique_bill_no}):
#                 unique_bill_no = f"{base_bill_no}-{counter}"
#                 counter += 1

#             pi_doc.bill_no = unique_bill_no
#             pi_doc.bill_date = doc.supplier_delivery_note_date or doc.posting_date

#         pi_doc.flags.ignore_mandatory = True


#         pi_doc.insert()


#         frappe.msgprint(
#             msg=f"Draft Purchase Invoice <b>{pi_doc.name}</b> was successfully auto-created by Administrator.",
#             title="Success",
#             indicator="green",
#             alert=True
#         )

#         pi_doc.add_comment("Comment", "System Auto-Generated Draft Purchase Invoice")
#         doc.add_comment("Comment", f"System Auto-Generated Draft Purchase Invoice: {pi_doc.name}")

#     except Exception as e:
#         frappe.log_error(title=f"Auto-PI Error for {doc.name}", message=str(e))
#         frappe.throw(f"Could not auto-generate the Draft Purchase Invoice: {str(e)}")

#     finally:

#         frappe.set_user(current_user)


# THIS IS THE FINAL VERSION: DRAFT PURCHASE INVOICE IN THE BACKGROUND WITHOUT LOGGING OUT THE USER.
# - Validates Expense Account in Item Master (Item Defaults / Item Group Defaults) before enqueue.
# - Fills PI expense account from PR line or Item Master before insert.
# - Notifies the submitter if the background job fails.


import frappe
from frappe import _
from frappe.utils import get_link_to_form

from erpnext.stock.doctype.item.item import get_item_defaults
from erpnext.stock.doctype.purchase_receipt.purchase_receipt import make_purchase_invoice
from erpnext.setup.doctype.item_group.item_group import get_item_group_defaults


def auto_create_purchase_invoice(doc, method):
    """
    Triggered on 'on_submit' of Purchase Receipt.
    Validates item-master expense accounts, then queues PI creation after commit.
    """
    validate_item_master_expense_accounts(doc)

    submitter = frappe.session.user

    def enqueue_background_job():
        frappe.enqueue(
            "vaaman_hr.draft_purchase_invoice.create_pi_as_admin",
            queue="short",
            receipt_name=doc.name,
            posting_date=doc.posting_date,
            supplier_delivery_note=doc.get("supplier_delivery_note"),
            supplier_delivery_note_date=doc.get("supplier_delivery_note_date"),
            submitter=submitter,
        )

    frappe.db.after_commit.add(enqueue_background_job)

    frappe.msgprint(
        _("A Draft Purchase Invoice is being auto-generated in the background."),
        alert=True,
    )


def validate_item_master_expense_accounts(purchase_receipt):
    """Block auto-PI when non-stock items lack Expense Account in Item Master."""
    missing_items = []

    for row in purchase_receipt.items:
        if not _requires_item_master_expense_account(row.item_code):
            continue
        if row.expense_account or get_expense_account_from_item_master(
            row.item_code, purchase_receipt.company
        ):
            continue
        missing_items.append(row.item_code)

    if not missing_items:
        return

    unique_items = list(dict.fromkeys(missing_items))
    item_links = ", ".join(get_link_to_form("Item", item_code) for item_code in unique_items)

    frappe.throw(
        _(
            "Expense Account is not set in Item Master for item(s) {0} "
            "against company {1}. Please open each Item, go to <b>Item Defaults</b>, "
            "and set <b>Expense Account</b> for this company before submitting the Purchase Receipt."
        ).format(item_links, frappe.bold(purchase_receipt.company)),
        title=_("Missing Expense Account in Item Master"),
    )


def get_expense_account_from_item_master(item_code, company):
    """Expense account from Item Defaults or Item Group Defaults (not company default)."""
    item_defaults = get_item_defaults(item_code, company)
    if item_defaults.get("expense_account"):
        return item_defaults.get("expense_account")

    item_group_defaults = get_item_group_defaults(item_code, company)
    return item_group_defaults.get("expense_account")


def _requires_item_master_expense_account(item_code):
    """Non-stock, non-fixed-asset lines need an expense account on Purchase Invoice."""
    if frappe.get_cached_value("Item", item_code, "is_stock_item"):
        return False
    if frappe.get_cached_value("Item", item_code, "is_fixed_asset"):
        return False
    return True


def _set_expense_accounts_on_pi(pi_doc, pr_doc):
    for pi_item in pi_doc.items:
        if pi_item.expense_account:
            continue

        pr_item = next((row for row in pr_doc.items if row.name == pi_item.pr_detail), None)
        if pr_item and pr_item.expense_account:
            pi_item.expense_account = pr_item.expense_account
            continue

        pi_item.expense_account = get_expense_account_from_item_master(pi_item.item_code, pi_doc.company)


def create_pi_as_admin(
    receipt_name,
    posting_date,
    supplier_delivery_note,
    supplier_delivery_note_date,
    submitter=None,
):
    """Runs in the background worker. Creates the PI as Administrator."""
    frappe.set_user("Administrator")

    try:
        pr_doc = frappe.get_doc("Purchase Receipt", receipt_name)
        validate_item_master_expense_accounts(pr_doc)

        pi_doc = make_purchase_invoice(receipt_name)
        _set_expense_accounts_on_pi(pi_doc, pr_doc)
        pi_doc.set_missing_values()
        _set_expense_accounts_on_pi(pi_doc, pr_doc)

        if supplier_delivery_note:
            base_bill_no = supplier_delivery_note.strip()
            unique_bill_no = base_bill_no
            counter = 1

            while frappe.db.exists("Purchase Invoice", {"bill_no": unique_bill_no}):
                unique_bill_no = f"{base_bill_no}-{counter}"
                counter += 1

            pi_doc.bill_no = unique_bill_no
            pi_doc.bill_date = supplier_delivery_note_date or posting_date

        pi_doc.flags.ignore_mandatory = True
        pi_doc.custom_auto_generated = 1
        pi_doc.purchase_order_number=pr_doc.custom_purchase_order_number
        if (
            pr_doc.name.startswith("SR") or pr_doc.name.startswith("GR")
            and (
                pr_doc.custom_purchase_order_number.startswith("PO/CP")
                or pr_doc.custom_purchase_order_number.startswith("WO/CP")
            )
        ):
            pi_doc.naming_series = "PCP/.FY./.#####"
        else:
            pi_doc.naming_series = "PINV/.FY./.#####"

        pi_doc.insert()

        pi_doc.add_comment("Comment", "System Auto-Generated Draft Purchase Invoice")
        pr_doc.add_comment("Comment", f"System Auto-Generated Draft Purchase Invoice: {pi_doc.name}")

    except Exception as e:
        frappe.log_error(title=f"Auto-PI Error for {receipt_name}", message=frappe.get_traceback())
        _notify_auto_pi_failure(submitter, str(e))
        raise


def _notify_auto_pi_failure(submitter, message):
    if not submitter or submitter in ("Administrator", "Guest"):
        return

    frappe.publish_realtime(
        "msgprint",
        {
            "message": message,
            "title": _("Auto Purchase Invoice Failed"),
            "indicator": "red",
        },
        user=submitter,
    )
