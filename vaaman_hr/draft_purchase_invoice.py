#THIS CODE WILL MAKE USER LOG OUT. DO NOT USE IN HOOKS OR BACKGROUND JOBS. FOR TESING PURPOSES ONLY.

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















#THIS IS THE FINAL VERSION THIS CREATES THE DRAFT PURCHAS INVOICE IN THE BACKGROUND WITHOUT LOGGING OUT THE USER



import frappe
from erpnext.stock.doctype.purchase_receipt.purchase_receipt import make_purchase_invoice

def auto_create_purchase_invoice(doc, method):
    """
    Triggered on 'on_submit' of Purchase Receipt.
    Queues the PI creation in the background AFTER the database commit finishes.
    """
    def enqueue_background_job():
        frappe.enqueue(
            "vaaman_hr.draft_purchase_invoice.create_pi_as_admin",
            queue="short",
            receipt_name=doc.name,
            posting_date=doc.posting_date,
            supplier_delivery_note=doc.supplier_delivery_note,
            supplier_delivery_note_date=doc.supplier_delivery_note_date
        )
        

    frappe.db.after_commit.add(enqueue_background_job)
    
    frappe.msgprint(
        "A Draft Purchase Invoice is being auto-generated in the background by Administrator.", 
        alert=True
    )


def create_pi_as_admin(receipt_name, posting_date, supplier_delivery_note, supplier_delivery_note_date):
    """
    Runs in the background worker. Creates the PI as Administrator.
    """

    frappe.set_user("Administrator")
    
    try:

        pi_doc = make_purchase_invoice(receipt_name)
        pi_doc.set_missing_values()


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
        

        pi_doc.insert() 


        pi_doc.add_comment("Comment", "System Auto-Generated Draft Purchase Invoice")
        

        pr_doc = frappe.get_doc("Purchase Receipt", receipt_name)
        pr_doc.add_comment("Comment", f"System Auto-Generated Draft Purchase Invoice: {pi_doc.name}")

    except Exception as e:
        frappe.log_error(title=f"Auto-PI Error for {receipt_name}", message=str(e))
