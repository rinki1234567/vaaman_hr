# Copyright (c) 2025, Pratul Tripathi and contributors
# For license information, please see license.txt

import frappe
from frappe import _

from hrms.hr.doctype.leave_application.leave_application import get_leave_details


def execute(filters=None):
	leave_types = frappe.db.sql_list("select name from `tabLeave Type` order by name asc")

	columns = get_columns(leave_types)
	data = get_data(filters, leave_types)

	return columns, data


def get_columns(leave_types):
	columns = [
		_("Employee") + ":Link/Employee:150",
		_("Employee Name") + "::200",
		_("branch") + ":Link/branch:150",
	]

	for leave_type in leave_types:
		columns.append(_(leave_type) + ":Float:160")

	return columns


def get_conditions(filters):
	conditions = {
		"company": filters.company,
	}
	if filters.get("employee_status"):
		conditions.update({"status": filters.get("employee_status")})
	if filters.get("branch"):
		conditions.update({"branch": filters.get("branch")})
	if filters.get("employee"):
		conditions.update({"employee": filters.get("employee")})

	return conditions


def get_data(filters, leave_types):
	conditions = get_conditions(filters)

	active_employees = frappe.get_list(
		"Employee",
		filters=conditions,
		fields=["name", "employee_name", "branch", "user_id"],
	)

	data = []
	for employee in active_employees:
		row = [employee.name, employee.employee_name, employee.branch]
		available_leave = get_leave_details(employee.name, filters.date)
		for leave_type in leave_types:
			remaining = 0
			if leave_type in available_leave["leave_allocation"]:
				# opening balance
				remaining = available_leave["leave_allocation"][leave_type]["remaining_leaves"]

			row += [remaining]

		data.append(row)

	return data
