import frappe

def calculate_overtime_hours(doc, method):
    if not doc.start_date or not doc.end_date:
        return

    # OT from submitted Overtime Import records
    overtime_import_ot = frappe.db.sql("""
        SELECT SUM(oti.over_time)
        FROM `tabOvertime Import Item` oti
        INNER JOIN `tabOverTime Import` oti_parent
            ON oti.parent = oti_parent.name
        WHERE oti.employee = %s
        AND oti.attendance_date BETWEEN %s AND %s
        AND oti_parent.docstatus = 1
    """, (doc.employee, doc.start_date, doc.end_date))[0][0] or 0

    # OT from OT Adjustment records (matched by month falling in salary period)
    ot_adjustment_ot = frappe.db.sql("""
        SELECT SUM(oai.additinal_ot)
        FROM `tabot adjustment item` oai
        INNER JOIN `tabOT Adjustment` oa
            ON oai.parent = oa.name
        WHERE oai.employee = %s
        AND oa.month BETWEEN %s AND %s
        AND oa.docstatus = 1
    """, (doc.employee, doc.start_date, doc.end_date))[0][0] or 0

    doc.total_overtime_hours = overtime_import_ot + ot_adjustment_ot
    doc.custom_rounded_gross_pay = round(doc.gross_pay or 0)
