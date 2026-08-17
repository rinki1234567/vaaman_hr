# import frappe
# from frappe.utils import getdate

# from hrms.hr.doctype.leave_application.leave_application import LeaveApplication

# from vaaman_hr.overrides.attendance_utils import (
# 	cancel_leave_attendance,
# 	relink_checkins_from_cancelled_attendance,
# )

# class CustomLeaveApplication(LeaveApplication):
# 	def cancel_attendance(self):
# 		"""Properly cancel attendance so checkins are handled; avoid silent db_set-only cancel."""
# 		if self.docstatus != 2:
# 			return
# 		cancel_leave_attendance(
# 			self.employee, self.from_date, self.to_date, leave_application=self.name
# 		)

# 	def create_or_update_attendance(self, attendance_name, date):
# 		status = (
# 			"Half Day"
# 			if self.half_day_date and getdate(date) == getdate(self.half_day_date)
# 			else "On Leave"
# 		)

# 		if attendance_name:
# 			super().create_or_update_attendance(attendance_name, date)
# 			# If existing Half Day already had punches, keep Other Half Present locked
# 			doc = frappe.get_doc("Attendance", attendance_name)
# 			if doc.status == "Half Day" and (doc.in_time or doc.out_time):
# 				frappe.db.set_value(
# 					"Attendance",
# 					doc.name,
# 					{"half_day_status": "Present", "modify_half_day_status": 0},
# 					update_modified=False,
# 				)
# 			return

# 		# New attendance (e.g. after previous leave attendance was cancelled)
# 		doc = frappe.new_doc("Attendance")
# 		doc.employee = self.employee
# 		doc.employee_name = self.employee_name
# 		doc.attendance_date = date
# 		doc.company = self.company
# 		doc.leave_type = self.leave_type
# 		doc.leave_application = self.name
# 		doc.status = status
# 		doc.half_day_status = "Absent" if status == "Half Day" else None
# 		doc.modify_half_day_status = 1 if status == "Half Day" else 0
# 		branch = frappe.db.get_value("Employee", self.employee, "branch")
# 		if branch and hasattr(doc, "custom_branch"):
# 			doc.custom_branch = branch
# 		doc.flags.ignore_validate = True
# 		frappe.flags.skip_head_office_attendance_validation = True
# 		try:
# 			doc.insert(ignore_permissions=True)
# 			doc.submit()
# 		finally:
# 			frappe.flags.skip_head_office_attendance_validation = False
   
		
# 		relink_checkins_from_cancelled_attendance(self.employee, doc, date)
import frappe
from frappe.utils import getdate, flt

from hrms.hr.doctype.leave_application.leave_application import LeaveApplication

from vaaman_hr.overrides.attendance_utils import (
    cancel_leave_attendance,
    relink_checkins_from_cancelled_attendance,
)
from vaaman_hr.vaaman_hr.half_day_leaves import (
	apply_head_office_policy_to_attendance,
)

class CustomLeaveApplication(LeaveApplication):

    def cancel_attendance(self):
        """Properly cancel attendance so checkins are handled; avoid silent db_set-only cancel."""
        if self.docstatus != 2:
            return

        cancel_leave_attendance(
            self.employee, self.from_date, self.to_date, leave_application=self.name,
        )

    def create_or_update_attendance(self, attendance_name, date):
        status = (
            "Half Day"
            if self.half_day_date and getdate(date) == getdate(self.half_day_date)
            else "On Leave"
        )

        # ---------------------------------------------------------
        # EXISTING ATTENDANCE
        # ---------------------------------------------------------
        if attendance_name:
            super().create_or_update_attendance(attendance_name, date)

            doc = frappe.get_doc("Attendance", attendance_name)

            branch = frappe.db.get_value("Employee",self.employee,"branch",)

            # -----------------------------------------------------
            # HEAD OFFICE
            # Let Head Office policy decide Other Half status.
            # -----------------------------------------------------
            if branch == "Head Office":
                apply_head_office_policy_to_attendance(
                    doc,
                    enforce_lookback=False,
                )

            # -----------------------------------------------------
            # OTHER BRANCHES
            # Keep existing behavior unchanged.
            # -----------------------------------------------------
            elif doc.status == "Half Day" and (doc.in_time or doc.out_time):
                frappe.db.set_value(
                    "Attendance",
                    doc.name,
                    { "half_day_status": "Present", "modify_half_day_status": 0,},
                    update_modified=False,
                )

            return

        # ---------------------------------------------------------
        # NEW ATTENDANCE
        # ---------------------------------------------------------
        doc = frappe.new_doc("Attendance")
        doc.employee = self.employee
        doc.employee_name = self.employee_name
        doc.attendance_date = date
        doc.company = self.company
        doc.leave_type = self.leave_type
        doc.leave_application = self.name
        doc.status = status
        doc.half_day_status = "Absent" if status == "Half Day" else None
        doc.modify_half_day_status = 1 if status == "Half Day" else 0

        branch = frappe.db.get_value("Employee",self.employee,"branch",)

        if branch and hasattr(doc, "custom_branch"):
            doc.custom_branch = branch

        doc.flags.ignore_validate = True

        frappe.flags.skip_head_office_attendance_validation = True

        try:
            doc.insert(ignore_permissions=True)
            doc.submit()
        finally:
            frappe.flags.skip_head_office_attendance_validation = False

        relink_checkins_from_cancelled_attendance(self.employee, doc, date,)