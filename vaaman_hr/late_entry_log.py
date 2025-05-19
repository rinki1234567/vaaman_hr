

import frappe
from hrms.hr.doctype.attendance.attendance import Attendance as ERPNextAttendance

class CustomAttendance(ERPNextAttendance):
    def on_trash(self):
        # Delete linked Attendance Policy Logs
        frappe.db.delete("Attendance Policy Log", {"attendance": self.name})
        frappe.db.commit()
       

    def on_cancel(self):
        # Delete linked Attendance Policy Logs
        frappe.db.delete("Attendance Policy Log", {"attendance": self.name})
        frappe.db.commit()
        super().on_cancel()
