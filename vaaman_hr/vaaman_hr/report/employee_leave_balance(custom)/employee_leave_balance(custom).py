# Copyright (c) 2013, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from collections import defaultdict
from itertools import groupby

import frappe
from frappe import _
from frappe.query_builder.functions import Abs, Max, Min, Sum
from frappe.utils import add_days, cint, flt, getdate

Filters = frappe._dict


def execute(filters: Filters | None = None) -> tuple:
	filters = frappe._dict(filters or {})

	if filters.to_date <= filters.from_date:
		frappe.throw(_('"From Date" can not be greater than or equal to "To Date"'))

	columns = get_columns()
	data = get_data(filters)
	charts = get_chart_data(data, filters)
	return columns, data, None, charts


def get_columns() -> list[dict]:
	return [
		{
			"label": _("Leave Type"),
			"fieldtype": "Link",
			"fieldname": "leave_type",
			"width": 200,
			"options": "Leave Type",
		},
		{
			"label": _("Employee"),
			"fieldtype": "Link",
			"fieldname": "employee",
			"width": 100,
			"options": "Employee",
		},
		{
			"label": _("Employee Name"),
			"fieldtype": "Dynamic Link",
			"fieldname": "employee_name",
			"width": 100,
			"options": "employee",
		},
		{
			"label": _("Opening Balance"),
			"fieldtype": "float",
			"fieldname": "opening_balance",
			"width": 150,
		},
		{
			"label": _("New Leave(s) Allocated"),
			"fieldtype": "float",
			"fieldname": "leaves_allocated",
			"width": 200,
		},
		{
			"label": _("Leave(s) Taken"),
			"fieldtype": "float",
			"fieldname": "leaves_taken",
			"width": 150,
		},
		{
			"label": _("Leave(s) Expired"),
			"fieldtype": "float",
			"fieldname": "leaves_expired",
			"width": 150,
		},
		{
			"label": _("Closing Balance"),
			"fieldtype": "float",
			"fieldname": "closing_balance",
			"width": 150,
		},
	]


def get_data(filters: Filters) -> list:
	leave_types = get_leave_types()
	employees = get_employees(filters)
	if not employees:
		return []

	# Skip employees with no leave allocations — they only produce zero rows
	employees = filter_employees_with_allocations(employees)
	if not employees:
		return []

	precision = cint(frappe.db.get_single_value("System Settings", "float_precision"))
	consolidate_leave_types = len(employees) > 1 and filters.consolidate_leave_types
	employee_names = [e.name for e in employees]
	employee_map = {e.name: e for e in employees}

	from_date = getdate(filters.from_date)
	to_date = getdate(filters.to_date)
	opening_balance_date = add_days(from_date, -1)

	allocated_map = get_batched_allocated_leaves(employee_names, from_date, to_date)
	expired_map = get_batched_expired_leaves(employee_names, from_date, to_date)
	cf_map = get_batched_cf_leaves(employee_names, from_date, to_date)
	taken_map = get_batched_leaves_taken(employee_names, from_date, to_date)
	previous_allocations = get_batched_previous_allocations(employee_names, from_date)
	opening_map = get_batched_opening_balances(
		employee_names, opening_balance_date, previous_allocations
	)

	data = []
	for leave_type in leave_types:
		rows_for_leave_type = []

		for employee_name in employee_names:
			key = (employee_name, leave_type)
			new_allocation = flt(allocated_map.get(key, 0))
			expired_leaves = flt(expired_map.get(key, 0))
			leaves_taken = flt(taken_map.get(key, 0))
			carry_forwarded = flt(cf_map.get(key, 0))

			previous = previous_allocations.get(key)
			if (
				previous
				and previous.get("to_date")
				and getdate(previous.to_date) == getdate(opening_balance_date)
			):
				opening = carry_forwarded
			else:
				opening = flt(opening_map.get(key, 0))

			closing = new_allocation + opening - (expired_leaves + leaves_taken)

			# Skip empty rows to avoid noise and extra work in the UI
			if not any(
				flt(v, precision)
				for v in (opening, new_allocation, leaves_taken, expired_leaves, closing)
			):
				continue

			employee = employee_map[employee_name]
			row = frappe._dict(
				{
					"employee": employee.name,
					"employee_name": employee.employee_name,
					"leaves_allocated": flt(new_allocation, precision),
					"leaves_expired": flt(expired_leaves, precision),
					"opening_balance": flt(opening, precision),
					"leaves_taken": flt(leaves_taken, precision),
					"closing_balance": flt(closing, precision),
					"indent": 1,
				}
			)
			if not consolidate_leave_types:
				row.leave_type = leave_type
			rows_for_leave_type.append(row)

		if not rows_for_leave_type:
			continue

		if consolidate_leave_types:
			data.append({"leave_type": leave_type})
		data.extend(rows_for_leave_type)

	return data


def get_leave_types() -> list[str]:
	LeaveType = frappe.qb.DocType("Leave Type")
	return (frappe.qb.from_(LeaveType).select(LeaveType.name).orderby(LeaveType.name)).run(pluck="name")


def get_employees(filters: Filters) -> list[dict]:
	Employee = frappe.qb.DocType("Employee")
	query = frappe.qb.from_(Employee).select(
		Employee.name,
		Employee.employee_name,
		Employee.branch,
	)

	for field in ["company", "branch"]:
		if filters.get(field):
			query = query.where(getattr(Employee, field) == filters.get(field))

	if filters.get("staff_worker"):
		query = query.where(Employee.custom_staffworker == filters.staff_worker)

	if filters.get("employee"):
		query = query.where(Employee.name == filters.get("employee"))

	if filters.get("employee_status"):
		query = query.where(Employee.status == filters.get("employee_status"))

	return query.run(as_dict=True)


def filter_employees_with_allocations(employees: list[dict]) -> list[dict]:
	if not employees:
		return []

	names = [e.name for e in employees]
	allocated = set(
		frappe.get_all(
			"Leave Allocation",
			filters={"employee": ["in", names], "docstatus": 1},
			pluck="employee",
			distinct=True,
		)
	)
	return [e for e in employees if e.name in allocated]


def get_batched_allocated_leaves(employees: list[str], from_date, to_date) -> dict:
	ledger = frappe.qb.DocType("Leave Ledger Entry")
	rows = (
		frappe.qb.from_(ledger)
		.select(ledger.employee, ledger.leave_type, Sum(ledger.leaves).as_("leaves"))
		.where(
			(ledger.docstatus == 1)
			& (ledger.transaction_type == "Leave Allocation")
			& (ledger.employee.isin(employees))
			& ((ledger.from_date[from_date:to_date]) | (ledger.to_date[from_date:to_date]))
			& (ledger.is_expired == 0)
			& (ledger.is_carry_forward == 0)
		)
		.groupby(ledger.employee, ledger.leave_type)
	).run(as_dict=True)

	return {(r.employee, r.leave_type): flt(r.leaves) for r in rows}


def get_batched_expired_leaves(employees: list[str], from_date, to_date) -> dict:
	ledger = frappe.qb.DocType("Leave Ledger Entry")
	rows = (
		frappe.qb.from_(ledger)
		.select(ledger.employee, ledger.leave_type, Abs(Sum(ledger.leaves)).as_("leaves"))
		.where(
			(ledger.docstatus == 1)
			& (ledger.transaction_type == "Leave Allocation")
			& (ledger.employee.isin(employees))
			& ((ledger.from_date[from_date:to_date]) | (ledger.to_date[from_date:to_date]))
			& (ledger.is_expired == 1)
		)
		.groupby(ledger.employee, ledger.leave_type)
	).run(as_dict=True)

	return {(r.employee, r.leave_type): flt(r.leaves) for r in rows}


def get_batched_cf_leaves(employees: list[str], from_date, to_date) -> dict:
	ledger = frappe.qb.DocType("Leave Ledger Entry")
	rows = (
		frappe.qb.from_(ledger)
		.select(ledger.employee, ledger.leave_type, Sum(ledger.leaves).as_("leaves"))
		.where(
			(ledger.docstatus == 1)
			& (ledger.transaction_type == "Leave Allocation")
			& (ledger.employee.isin(employees))
			& ((ledger.from_date[from_date:to_date]) | (ledger.to_date[from_date:to_date]))
			& (ledger.is_expired == 0)
			& (ledger.is_carry_forward == 1)
		)
		.groupby(ledger.employee, ledger.leave_type)
	).run(as_dict=True)

	return {(r.employee, r.leave_type): flt(r.leaves) for r in rows}


def get_batched_leaves_taken(employees: list[str], from_date, to_date) -> dict:
	"""Leaves taken as positive days (matches get_leaves_for_period * -1 for applications)."""
	ledger = frappe.qb.DocType("Leave Ledger Entry")
	rows = (
		frappe.qb.from_(ledger)
		.select(ledger.employee, ledger.leave_type, Sum(ledger.leaves).as_("leaves"))
		.where(
			(ledger.docstatus == 1)
			& (ledger.employee.isin(employees))
			& (ledger.transaction_type.isin(["Leave Application", "Leave Encashment"]))
			& (
				(ledger.from_date[from_date:to_date])
				| (ledger.to_date[from_date:to_date])
				| ((ledger.from_date < from_date) & (ledger.to_date > to_date))
			)
		)
		.groupby(ledger.employee, ledger.leave_type)
	).run(as_dict=True)

	# Application/encashment leaves are stored as negative in ledger
	return {(r.employee, r.leave_type): abs(flt(r.leaves)) for r in rows}


def get_batched_previous_allocations(employees: list[str], from_date) -> dict:
	"""Latest Leave Allocation ending before from_date, per employee + leave type."""
	Allocation = frappe.qb.DocType("Leave Allocation")
	rows = (
		frappe.qb.from_(Allocation)
		.select(
			Allocation.employee,
			Allocation.leave_type,
			Allocation.name,
			Allocation.from_date,
			Allocation.to_date,
		)
		.where(
			(Allocation.employee.isin(employees))
			& (Allocation.to_date < from_date)
			& (Allocation.docstatus == 1)
		)
		.orderby(Allocation.to_date, order=frappe.qb.desc)
	).run(as_dict=True)

	previous = {}
	for row in rows:
		key = (row.employee, row.leave_type)
		if key not in previous:
			previous[key] = row
	return previous


def get_batched_opening_balances(
	employees: list[str],
	opening_balance_date,
	previous_allocations: dict,
) -> dict:
	"""
	Opening balance on (from_date - 1), batched.
	Mirrors get_leave_balance_on without per-employee permission checks / N+1 queries.
	"""
	opening_balance_date = getdate(opening_balance_date)
	allocation_records = get_batched_leave_allocation_records(employees, opening_balance_date)
	if not allocation_records:
		return {}

	# Prefetch leaves taken / manually expired / CF expiry across all relevant allocation windows
	min_from = min(a.from_date for a in allocation_records.values())
	taken_by_range = get_batched_leaves_taken_by_entry(employees, min_from, opening_balance_date)
	manual_expired = get_batched_manually_expired_leaves(employees, min_from, opening_balance_date)
	cf_expiry_map = get_batched_cf_expiry(employees, min_from, opening_balance_date)

	opening_map = {}
	for (employee, leave_type), allocation in allocation_records.items():
		# Boundary case handled by caller using previous allocation to_date == opening date
		previous = previous_allocations.get((employee, leave_type))
		if previous and getdate(previous.to_date) == opening_balance_date:
			continue

		entries = taken_by_range.get((employee, leave_type), [])
		cf_expiry = cf_expiry_map.get((employee, leave_type))
		manually_expired = sum_manual_expired(
			manual_expired.get((employee, leave_type), []),
			allocation.from_date,
			opening_balance_date,
		)

		opening_map[(employee, leave_type)] = calculate_leave_balance(
			allocation,
			entries,
			opening_balance_date,
			cf_expiry,
			manually_expired,
		)

	return opening_map


def calculate_leave_balance(
	allocation, leave_entries: list, date, cf_expiry, manually_expired_leaves: float
) -> float:
	"""In-memory equivalent of get_leave_balance_on / get_remaining_leaves (balance only)."""
	date = getdate(date)

	if cf_expiry and allocation.unused_leaves:
		cf_expiry = getdate(cf_expiry)
		cf_leaves_taken = sum_leaves_for_period(leave_entries, allocation.from_date, cf_expiry)
		new_leaves_taken = sum_leaves_for_period(
			leave_entries, add_days(cf_expiry, 1), allocation.to_date
		)

		# Cap CF taken to unused CF leaves (same as get_new_and_cf_leaves_taken)
		if abs(cf_leaves_taken) > allocation.unused_leaves:
			new_leaves_taken += -(abs(cf_leaves_taken) - allocation.unused_leaves)
			cf_leaves_taken = -allocation.unused_leaves

		if date > cf_expiry:
			cf_leaves = 0
		else:
			cf_leaves = flt(allocation.unused_leaves) + flt(cf_leaves_taken)

		return (
			(flt(allocation.new_leaves_allocated) + flt(new_leaves_taken))
			+ flt(cf_leaves)
			+ flt(manually_expired_leaves)
		)

	leaves_taken = sum_leaves_for_period(leave_entries, allocation.from_date, date)
	return flt(allocation.total_leaves_allocated) + flt(leaves_taken) + flt(manually_expired_leaves)


def get_batched_leave_allocation_records(employees: list[str], date) -> dict:
	"""Multi-employee version of get_leave_allocation_records."""
	Ledger = frappe.qb.DocType("Leave Ledger Entry")
	LeaveAllocation = frappe.qb.DocType("Leave Allocation")

	cf_leave_case = frappe.qb.terms.Case().when(Ledger.is_carry_forward == "1", Ledger.leaves).else_(0)
	sum_cf_leaves = Sum(cf_leave_case).as_("cf_leaves")

	new_leaves_case = frappe.qb.terms.Case().when(Ledger.is_carry_forward == "0", Ledger.leaves).else_(0)
	sum_new_leaves = Sum(new_leaves_case).as_("new_leaves")

	rows = (
		frappe.qb.from_(Ledger)
		.inner_join(LeaveAllocation)
		.on(Ledger.transaction_name == LeaveAllocation.name)
		.select(
			sum_cf_leaves,
			sum_new_leaves,
			Min(Ledger.from_date).as_("from_date"),
			Max(Ledger.to_date).as_("to_date"),
			Ledger.leave_type,
			Ledger.employee,
		)
		.where(
			(Ledger.from_date <= date)
			& (Ledger.docstatus == 1)
			& (Ledger.transaction_type == "Leave Allocation")
			& (Ledger.employee.isin(employees))
			& (Ledger.is_expired == 0)
			& (Ledger.is_lwp == 0)
			& (
				((Ledger.is_carry_forward == 0) & (Ledger.to_date >= date))
				| (
					(Ledger.is_carry_forward == 1)
					& (Ledger.to_date.between(LeaveAllocation.from_date, LeaveAllocation.to_date))
					& (LeaveAllocation.from_date <= date)
					& (date <= LeaveAllocation.to_date)
				)
			)
		)
		.groupby(Ledger.employee, Ledger.leave_type)
	).run(as_dict=True)

	allocated = {}
	for d in rows:
		allocated[(d.employee, d.leave_type)] = frappe._dict(
			{
				"from_date": d.from_date,
				"to_date": d.to_date,
				"total_leaves_allocated": flt(d.cf_leaves) + flt(d.new_leaves),
				"unused_leaves": d.cf_leaves,
				"new_leaves_allocated": d.new_leaves,
				"leave_type": d.leave_type,
				"employee": d.employee,
			}
		)
	return allocated


def get_batched_leaves_taken_by_entry(employees: list[str], from_date, to_date) -> dict:
	"""Raw leave application/encashment ledger rows for in-memory period filtering."""
	ledger = frappe.qb.DocType("Leave Ledger Entry")
	rows = (
		frappe.qb.from_(ledger)
		.select(
			ledger.employee,
			ledger.leave_type,
			ledger.from_date,
			ledger.to_date,
			ledger.leaves,
			ledger.transaction_type,
		)
		.where(
			(ledger.docstatus == 1)
			& (ledger.employee.isin(employees))
			& (ledger.transaction_type.isin(["Leave Application", "Leave Encashment"]))
			& (
				(ledger.from_date[from_date:to_date])
				| (ledger.to_date[from_date:to_date])
				| ((ledger.from_date < from_date) & (ledger.to_date > to_date))
			)
		)
	).run(as_dict=True)

	grouped = defaultdict(list)
	for row in rows:
		grouped[(row.employee, row.leave_type)].append(row)
	return grouped


def sum_leaves_for_period(entries: list, from_date, to_date) -> float:
	"""Approximate get_leaves_for_period using ledger leaves (applications/encashment)."""
	from_date, to_date = getdate(from_date), getdate(to_date)
	total = 0.0
	for entry in entries:
		entry_from, entry_to = getdate(entry.from_date), getdate(entry.to_date)
		if entry_to < from_date or entry_from > to_date:
			continue
		# Ledger already stores signed leave days for applications/encashment
		total += flt(entry.leaves)
	return total


def get_batched_manually_expired_leaves(employees: list[str], from_date, to_date) -> dict:
	ledger = frappe.qb.DocType("Leave Ledger Entry")
	rows = (
		frappe.qb.from_(ledger)
		.select(
			ledger.employee,
			ledger.leave_type,
			ledger.from_date,
			ledger.to_date,
			ledger.leaves,
		)
		.where(
			(ledger.docstatus == 1)
			& (ledger.employee.isin(employees))
			& (ledger.transaction_type == "Leave Allocation")
			& (ledger.is_expired == 1)
			& (ledger.is_carry_forward == 0)
			& (ledger.from_date >= from_date)
			& (ledger.to_date < to_date)
		)
	).run(as_dict=True)

	grouped = defaultdict(list)
	for row in rows:
		grouped[(row.employee, row.leave_type)].append(row)
	return grouped


def sum_manual_expired(entries: list, from_date, end_date) -> float:
	from_date, end_date = getdate(from_date), getdate(end_date)
	total = 0.0
	for entry in entries:
		if getdate(entry.from_date) >= from_date and getdate(entry.to_date) < end_date:
			total += flt(entry.leaves)
	return total


def get_batched_cf_expiry(employees: list[str], from_date, to_date) -> dict:
	ledger = frappe.qb.DocType("Leave Ledger Entry")
	rows = (
		frappe.qb.from_(ledger)
		.select(ledger.employee, ledger.leave_type, ledger.to_date)
		.where(
			(ledger.docstatus == 1)
			& (ledger.employee.isin(employees))
			& (ledger.is_carry_forward == 1)
			& (ledger.transaction_type == "Leave Allocation")
			& (ledger.to_date.between(from_date, to_date))
		)
		.orderby(ledger.to_date)
	).run(as_dict=True)

	expiry = {}
	for row in rows:
		key = (row.employee, row.leave_type)
		if key not in expiry:
			expiry[key] = row.to_date
	return expiry


def get_chart_data(data: list, filters: Filters) -> dict:
	labels = []
	datasets = []
	employee_data = data

	if not data:
		return None

	if data and filters.employee:
		get_dataset_for_chart(employee_data, datasets, labels)

	chart = {
		"data": {"labels": labels, "datasets": datasets},
		"type": "bar",
		"colors": ["#456789", "#EE8888", "#7E77BF"],
	}

	return chart


def get_dataset_for_chart(employee_data: list, datasets: list, labels: list) -> list:
	leaves = []
	employee_data = sorted(
		[d for d in employee_data if d.get("employee_name")],
		key=lambda k: k["employee_name"],
	)

	for key, group in groupby(employee_data, lambda x: x["employee_name"]):
		for grp in group:
			if grp.get("closing_balance"):
				leaves.append(
					frappe._dict({"leave_type": grp.get("leave_type"), "closing_balance": grp.closing_balance})
				)

		if leaves:
			labels.append(key)

	for leave in leaves:
		datasets.append({"name": leave.leave_type, "values": [leave.closing_balance]})
