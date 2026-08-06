"""Compensatory Off balance with FIFO consume + unused-only expiry.

OT Import credits Comp Off in 60-day ledger windows. Standard HRMS balance
subtracts all leave applications in the allocation period from currently-valid
credits, so used leave keeps eating new OT after old credits expire.

Correct rule:
- Each credit lot is consumed FIFO by leave days that fall inside its window
- Balance on a date = remaining on lots still valid that day
- Expired lots drop only their unused remainder (used leave is already settled)
"""

from __future__ import annotations

from copy import deepcopy

import frappe
from frappe.utils import add_days, cint, date_diff, flt, getdate

COMP_OFF_LEAVE_TYPE = "Compensatory Off"


def is_compensatory_off(leave_type: str) -> bool:
	return leave_type == COMP_OFF_LEAVE_TYPE


def get_net_comp_off_credit_lots(employee: str) -> list[dict]:
	"""Net OT/allocation credit lots by (from_date, to_date), oldest first."""
	rows = frappe.db.sql(
		"""
		SELECT leaves, from_date, to_date, creation
		FROM `tabLeave Ledger Entry`
		WHERE employee = %(employee)s
			AND leave_type = %(leave_type)s
			AND docstatus = 1
			AND transaction_type = 'Leave Allocation'
			AND IFNULL(is_expired, 0) = 0
			AND IFNULL(is_lwp, 0) = 0
		ORDER BY from_date ASC, creation ASC
		""",
		{"employee": employee, "leave_type": COMP_OFF_LEAVE_TYPE},
		as_dict=True,
	)

	netted: dict[tuple, float] = {}
	order: list[tuple] = []
	for row in rows:
		key = (getdate(row.from_date), getdate(row.to_date))
		if key not in netted:
			netted[key] = 0.0
			order.append(key)
		netted[key] = flt(netted[key] + flt(row.leaves), 9)

	lots = []
	for key in order:
		amount = flt(netted[key], 9)
		if amount <= 0:
			continue
		lots.append(
			frappe._dict(
				{
					"from_date": key[0],
					"to_date": key[1],
					"leaves": amount,
					"remaining": amount,
				}
			)
		)
	return lots


def get_comp_off_leave_day_entries(employee: str) -> list[tuple]:
	"""Expand submitted Comp Off leave applications into (date, days) chronologically."""
	from hrms.hr.doctype.leave_application.leave_application import get_number_of_leave_days

	applications = frappe.db.sql(
		"""
		SELECT name, from_date, to_date, total_leave_days, half_day, half_day_date
		FROM `tabLeave Application`
		WHERE employee = %(employee)s
			AND leave_type = %(leave_type)s
			AND docstatus = 1
			AND status = 'Approved'
		ORDER BY from_date ASC, creation ASC
		""",
		{"employee": employee, "leave_type": COMP_OFF_LEAVE_TYPE},
		as_dict=True,
	)

	day_entries: list[tuple] = []
	for app in applications:
		from_date = getdate(app.from_date)
		to_date = getdate(app.to_date)
		if from_date == to_date:
			days = flt(app.total_leave_days) or flt(
				get_number_of_leave_days(
					employee,
					COMP_OFF_LEAVE_TYPE,
					from_date,
					to_date,
					app.half_day,
					app.half_day_date,
				)
			)
			if days > 0:
				day_entries.append((from_date, days))
			continue

		current = from_date
		while current <= to_date:
			half_day = 0
			if cint(app.half_day) and app.half_day_date and getdate(app.half_day_date) == current:
				half_day = 1
			days = flt(
				get_number_of_leave_days(
					employee,
					COMP_OFF_LEAVE_TYPE,
					current,
					current,
					half_day,
					app.half_day_date if half_day else None,
				)
			)
			if days > 0:
				day_entries.append((current, days))
			current = add_days(current, 1)

	return day_entries


def apply_fifo_consumption(lots: list[dict], leave_days: list[tuple]) -> list[dict]:
	"""Consume leave days from credit lots valid on that leave day (oldest first)."""
	lots = deepcopy(lots)
	for leave_date, days in leave_days:
		remaining_to_consume = flt(days)
		leave_date = getdate(leave_date)
		for lot in lots:
			if remaining_to_consume <= 0:
				break
			if lot.remaining <= 0:
				continue
			if not (lot.from_date <= leave_date <= lot.to_date):
				continue
			used = min(flt(lot.remaining), remaining_to_consume)
			lot.remaining = flt(lot.remaining - used, 9)
			remaining_to_consume = flt(remaining_to_consume - used, 9)
		# leftover remaining_to_consume = historically overdrawn; do not attach to future lots
	return lots


def get_consumed_comp_off_lots(employee: str) -> list[dict]:
	lots = get_net_comp_off_credit_lots(employee)
	leave_days = get_comp_off_leave_day_entries(employee)
	return apply_fifo_consumption(lots, leave_days)


def sum_remaining_on_date(lots: list[dict], date) -> float:
	date = getdate(date)
	total = 0.0
	for lot in lots:
		if lot.from_date <= date <= lot.to_date:
			total += max(flt(lot.remaining), 0)
	return flt(total, 9)


def get_earliest_remaining_expiry(lots: list[dict], date):
	date = getdate(date)
	expiry = None
	for lot in lots:
		if lot.remaining > 0 and lot.from_date <= date <= lot.to_date:
			if expiry is None or lot.to_date < expiry:
				expiry = lot.to_date
	return expiry


def get_comp_off_balance_on(
	employee: str,
	date,
	to_date=None,
	for_consumption: bool = False,
):
	"""Return Comp Off balance using FIFO consume + unused-only expiry."""
	date = getdate(date)
	lots = get_consumed_comp_off_lots(employee)
	leave_balance = sum_remaining_on_date(lots, date)

	if not for_consumption:
		return flt(leave_balance)

	# Consumable balance cannot exceed days left until earliest still-valid lot expires
	leave_balance_for_consumption = leave_balance
	expiry = get_earliest_remaining_expiry(lots, date)
	if expiry and leave_balance_for_consumption > 0:
		# If caller passes leave to_date, also ensure lots can cover that period
		end = getdate(to_date) if to_date else date
		consumable = _consumable_days_between(lots, date, end)
		days_to_expiry = date_diff(expiry, date) + 1
		leave_balance_for_consumption = flt(
			min(leave_balance, consumable, max(days_to_expiry, 0)), 9
		)

	return frappe._dict(
		leave_balance=flt(leave_balance),
		leave_balance_for_consumption=flt(leave_balance_for_consumption),
	)


def _consumable_days_between(lots: list[dict], from_date, to_date) -> float:
	"""How many leave days from_date..to_date can still be covered by remaining lots."""
	lots = deepcopy(lots)
	from_date = getdate(from_date)
	to_date = getdate(to_date)
	covered = 0.0
	current = from_date
	while current <= to_date:
		day_need = 1.0
		for lot in lots:
			if day_need <= 0:
				break
			if lot.remaining <= 0:
				continue
			if not (lot.from_date <= current <= lot.to_date):
				continue
			used = min(flt(lot.remaining), day_need)
			lot.remaining = flt(lot.remaining - used, 9)
			day_need = flt(day_need - used, 9)
		covered += 1.0 - day_need
		current = add_days(current, 1)
	return flt(covered, 9)


def get_comp_off_leave_details_bucket(employee: str, date) -> dict:
	"""Allocation summary row for get_leave_details dashboard."""
	date = getdate(date)
	precision = cint(frappe.db.get_single_value("System Settings", "float_precision")) or 2
	lots = get_consumed_comp_off_lots(employee)

	valid_lots = [lot for lot in lots if lot.from_date <= date <= lot.to_date]
	total_leaves = flt(sum(flt(lot.leaves) for lot in valid_lots), precision)
	remaining = flt(sum(max(flt(lot.remaining), 0) for lot in valid_lots), precision)
	leaves_taken = flt(total_leaves - remaining, precision)
	if leaves_taken < 0:
		leaves_taken = 0

	pending = flt(
		frappe.db.sql(
			"""
			SELECT COALESCE(SUM(total_leave_days), 0)
			FROM `tabLeave Application`
			WHERE employee = %s
				AND leave_type = %s
				AND status = 'Open'
				AND docstatus < 2
			""",
			(employee, COMP_OFF_LEAVE_TYPE),
		)[0][0],
		precision,
	)

	return {
		"total_leaves": total_leaves,
		"expired_leaves": 0,
		"leaves_taken": leaves_taken,
		"leaves_pending_approval": pending,
		"remaining_leaves": remaining,
	}
