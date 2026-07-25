# Copyright (c) 2026, Pratul Tripathi and contributors
# For license information, please see license.txt

import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, add_months, cint, flt, formatdate, get_link_to_form, getdate

from hrms.hr.doctype.leave_ledger_entry.leave_ledger_entry import create_leave_ledger_entry

from vaaman_hr.overrides.leave_policy_assignment import (
	PRORATED_LEAVE_TYPES,
	calculate_monthly_prorated_leaves,
)


PROBATION_DEFAULT_MONTHS = 6


class LeavePolicyChange(Document):
	def validate(self):
		self.set_employee_details()
		self.set_dates()
		self.load_old_assignment()
		self.set_new_policy_from_master()
		self.validate_change()

	def on_submit(self):
		if self.is_replace_from_start():
			# Same-day (or from-start) change: cancel old LPA entirely — nothing to close.
			self.replace_old_assignment_from_start()
		else:
			self.close_old_assignment()
		if self.override_existing_assignments:
			self.override_overlapping_assignments()
		self.create_new_assignment()
		if self.carry_forward and not self.is_replace_from_start():
			self.carry_forward_unused_to_new()
		self.update_employee_type()
		self.add_audit_comments()

	def is_replace_from_start(self):
		"""True when Change Date is on/before old LPA start — cannot shorten, must replace."""
		if not (self.old_effective_from and self.change_date):
			return False
		return getdate(self.old_effective_from) >= getdate(self.change_date)

	def on_cancel(self):
		self.validate_cancel_allowed()
		self.cancel_new_assignment()
		self.restore_old_assignment()
		self.restore_overridden_assignments()
		self.restore_employee_type()
		self.add_audit_comments(reversed_entry=True)

	def set_employee_details(self):
		if not self.employee:
			return

		emp = frappe.db.get_value(
			"Employee",
			self.employee,
			["employee_name", "company", "branch", "employment_type", "custom_staffworker"],
			as_dict=True,
		)
		if not emp:
			frappe.throw(_("Employee {0} not found").format(self.employee))

		self.employee_name = emp.employee_name
		self.company = emp.company
		self.branch = emp.branch
		self.old_employment_type = emp.employment_type
		self.staff_worker = emp.custom_staffworker

	def set_dates(self):
		if not self.change_date:
			frappe.throw(_("Change Date is required"))

		change_date = getdate(self.change_date)
		self.old_end_date = add_days(change_date, -1)
		self.new_effective_from = change_date

		if not self.new_effective_to:
			self.new_effective_to = get_default_new_effective_to(
				self.new_employment_type, change_date, self.company
			)

	def load_old_assignment(self):
		"""Find submitted LPA covering the day before change (still active on old_end)."""
		if not self.employee or not self.old_end_date:
			return

		lpa = frappe.db.get_value(
			"Leave Policy Assignment",
			{
				"employee": self.employee,
				"docstatus": 1,
				"effective_from": ["<=", self.old_end_date],
				"effective_to": [">=", self.old_end_date],
			},
			["name", "leave_policy", "effective_from", "effective_to"],
			as_dict=True,
		)

		if not lpa:
			# Also allow finding LPA that still covers change_date (full-year case)
			lpa = frappe.db.get_value(
				"Leave Policy Assignment",
				{
					"employee": self.employee,
					"docstatus": 1,
					"effective_from": ["<=", self.change_date],
					"effective_to": [">=", self.change_date],
				},
				["name", "leave_policy", "effective_from", "effective_to"],
				as_dict=True,
			)

		if not lpa:
			frappe.throw(
				_("No submitted Leave Policy Assignment found for {0} covering {1}").format(
					frappe.bold(self.employee), frappe.bold(formatdate(self.change_date))
				)
			)

		self.old_leave_policy_assignment = lpa.name
		self.old_leave_policy = lpa.leave_policy
		self.old_effective_from = lpa.effective_from
		self.old_effective_to = lpa.effective_to

	def set_new_policy_from_master(self):
		"""Fill new_leave_policy from Master if empty."""
		if self.new_leave_policy or not self.new_employment_type:
			return

		policy = get_master_leave_policy(
			self.branch, self.new_employment_type, self.staff_worker
		)
		if policy:
			self.new_leave_policy = policy

	def validate_change(self):
		if not self.new_leave_policy:
			frappe.throw(
				_(
					"New Leave Policy is required. Set it manually or maintain "
					"Master Leave Policy Assignment for Branch {0}, Employment Type {1}, Staff/Worker {2}."
				).format(
					frappe.bold(self.branch or _("(missing)")),
					frappe.bold(self.new_employment_type),
					frappe.bold(self.staff_worker or _("(missing)")),
				)
			)

		if getdate(self.new_effective_to) < getdate(self.new_effective_from):
			frappe.throw(_("New Effective To cannot be before New Effective From"))

		if getdate(self.old_effective_from) > getdate(self.old_end_date):
			# Same-day / from-start: old LPA starts on Change Date — replace via override.
			if not self.override_existing_assignments:
				frappe.throw(
					_(
						"Change Date {0} is on or before the old assignment start {1}. "
						"Enable <b>Override Existing Assignments</b> to replace that assignment "
						"on the same day, or pick a later Change Date."
					).format(
						frappe.bold(formatdate(self.change_date)),
						frappe.bold(formatdate(self.old_effective_from)),
					)
				)
			frappe.msgprint(
				_(
					"Change Date equals old assignment start ({0}). "
					"Old Leave Policy Assignment {1} will be <b>fully cancelled/replaced</b> "
					"(not shortened) and a new assignment will start the same day."
				).format(
					formatdate(self.old_effective_from),
					frappe.bold(self.old_leave_policy_assignment),
				),
				indicator="orange",
				alert=True,
			)

		if getdate(self.old_effective_to) < getdate(self.change_date):
			frappe.throw(
				_("Old Leave Policy Assignment already ended on {0}").format(
					formatdate(self.old_effective_to)
				)
			)

		# Leaves already approved on/after change_date stay in the ledger and will
		# count against the NEW policy period (retrospective switch). Do not block.
		future_apps = frappe.get_all(
			"Leave Application",
			filters={
				"employee": self.employee,
				"docstatus": 1,
				"from_date": [">=", self.change_date],
			},
			fields=["name", "leave_type", "from_date", "to_date"],
			order_by="from_date",
			limit=10,
		)
		if future_apps:
			examples = ", ".join(
				f"{a.name} ({a.leave_type} {formatdate(a.from_date)})" for a in future_apps[:5]
			)
			frappe.msgprint(
				_(
					"Note: {0} approved Leave Application(s) exist on/after Change Date "
					"(e.g. {1}). They will remain as-is and reduce balance under the "
					"<b>new</b> leave policy from {2}."
				).format(len(future_apps), examples, formatdate(self.change_date)),
				indicator="blue",
				alert=True,
			)

		# Leave apps that fall only in the closed old period must remain covered
		# (from_date <= old_end). Nothing to do — old allocation still covers them.

		# Draft LPC overlap guard
		existing = frappe.db.exists(
			"Leave Policy Change",
			{
				"employee": self.employee,
				"docstatus": ["<", 2],
				"name": ["!=", self.name],
				"change_date": self.change_date,
			},
		)
		if existing:
			frappe.throw(
				_("Leave Policy Change {0} already exists for this employee and change date").format(
					get_link_to_form("Leave Policy Change", existing)
				)
			)

		if self.old_employment_type == self.new_employment_type:
			frappe.msgprint(
				_(
					"Employment type is unchanged ({0}). This will close the current assignment "
					"and open a new period — use this to extend Probation / re-periodize leaves."
				).format(frappe.bold(self.new_employment_type)),
				indicator="blue",
				alert=True,
			)

		if self.old_leave_policy == self.new_leave_policy:
			frappe.msgprint(
				_(
					"New Leave Policy is the same as the old one ({0}). "
					"That is OK for period extension; otherwise check Master mapping."
				).format(frappe.bold(self.new_leave_policy)),
				indicator="orange",
				alert=True,
			)

		overlapping = self.get_overlapping_assignments()
		if overlapping:
			names = ", ".join(
				f"{r.name} ({frappe.db.get_value('Leave Policy', r.leave_policy, 'title') or r.leave_policy}: "
				f"{formatdate(r.effective_from)} → {formatdate(r.effective_to)})"
				for r in overlapping
			)
			if self.override_existing_assignments:
				frappe.msgprint(
					_(
						"These Leave Policy Assignment(s) overlap the new period and will be "
						"<b>cancelled/overridden</b> on submit: {0}"
					).format(names),
					indicator="orange",
					alert=True,
				)
			else:
				frappe.throw(
					_(
						"Cannot assign new policy — overlapping Leave Policy Assignment(s) exist: {0}. "
						"Enable <b>Override Existing Assignments</b> to replace them."
					).format(names)
				)

	def get_overlapping_assignments(self):
		"""Other submitted LPAs that overlap [new_effective_from, new_effective_to]."""
		if not (self.employee and self.new_effective_from and self.new_effective_to):
			return []

		return frappe.get_all(
			"Leave Policy Assignment",
			filters={
				"employee": self.employee,
				"docstatus": 1,
				"name": ["not in", [self.old_leave_policy_assignment or "", self.name]],
				"effective_from": ["<=", self.new_effective_to],
				"effective_to": [">=", self.new_effective_from],
			},
			fields=["name", "leave_policy", "effective_from", "effective_to", "carry_forward"],
			order_by="effective_from",
		)

	def replace_old_assignment_from_start(self):
		"""Cancel old LPA that starts on Change Date (same-day policy switch)."""
		if not self.old_leave_policy_assignment:
			return

		snapshot = json.loads(self.closure_snapshot or "{}")
		snapshot["replace_from_start"] = 1
		snapshot["lpa"] = {
			"name": self.old_leave_policy_assignment,
			"effective_to": str(getdate(self.old_effective_to)),
			"effective_from": str(getdate(self.old_effective_from)),
			"cancelled": 1,
		}
		info = self._cancel_lpa_with_allocations(self.old_leave_policy_assignment)
		overridden = snapshot.get("overridden_assignments") or []
		overridden.append(info)
		snapshot["overridden_assignments"] = overridden
		self.db_set("closure_snapshot", json.dumps(snapshot, default=str))
		self.db_set(
			"overridden_assignments",
			", ".join(o["name"] for o in overridden),
		)

	def override_overlapping_assignments(self):
		"""Cancel overlapping LPAs + their allocations so the new assignment can replace them."""
		overlapping = self.get_overlapping_assignments()
		if not overlapping:
			return

		snapshot = json.loads(self.closure_snapshot or "{}")
		overridden = list(snapshot.get("overridden_assignments") or [])
		already = {o.get("name") for o in overridden}

		for lpa_row in overlapping:
			if lpa_row.name in already:
				continue
			info = self._cancel_lpa_with_allocations(lpa_row.name)
			overridden.append(info)

		snapshot["overridden_assignments"] = overridden
		self.db_set("closure_snapshot", json.dumps(snapshot, default=str))
		self.db_set(
			"overridden_assignments",
			", ".join(o["name"] for o in overridden),
		)

	def _cancel_lpa_with_allocations(self, lpa_name):
		"""Cancel one LPA + its allocations. Returns snapshot info dict."""
		lpa = frappe.get_doc("Leave Policy Assignment", lpa_name)
		alloc_names = frappe.get_all(
			"Leave Allocation",
			filters={"leave_policy_assignment": lpa.name, "docstatus": 1},
			pluck="name",
		)
		info = {
			"name": lpa.name,
			"leave_policy": lpa.leave_policy,
			"effective_from": str(getdate(lpa.effective_from)),
			"effective_to": str(getdate(lpa.effective_to)),
			"carry_forward": cint(lpa.carry_forward),
			"allocations": alloc_names,
		}

		for alloc_name in alloc_names:
			alloc = frappe.get_doc("Leave Allocation", alloc_name)
			alloc.flags.ignore_permissions = True
			try:
				alloc.cancel()
			except Exception:
				frappe.db.set_value("Leave Allocation", alloc_name, "docstatus", 2, update_modified=False)
				frappe.db.sql(
					"""
					update `tabLeave Ledger Entry`
					set docstatus = 2
					where transaction_type = 'Leave Allocation'
						and transaction_name = %s
						and docstatus = 1
					""",
					alloc_name,
				)

		try:
			lpa.flags.ignore_permissions = True
			lpa.cancel()
		except Exception:
			frappe.db.set_value("Leave Policy Assignment", lpa.name, "docstatus", 2, update_modified=False)

		try:
			frappe.get_doc("Leave Policy Assignment", lpa.name).add_comment(
				"Info",
				_("Cancelled/overridden by Leave Policy Change {0}").format(
					get_link_to_form(self.doctype, self.name)
				),
			)
		except Exception:
			pass

		return info

	def restore_overridden_assignments(self):
		"""Best-effort recreate LPAs that were overridden (allocations re-granted on submit)."""
		if not self.closure_snapshot:
			return
		snapshot = json.loads(self.closure_snapshot)
		for info in snapshot.get("overridden_assignments") or []:
			if frappe.db.exists("Leave Policy Assignment", info["name"]):
				# Already exists (cancelled) — amend/recreate via new doc
				pass
			lpa = frappe.get_doc(
				{
					"doctype": "Leave Policy Assignment",
					"employee": self.employee,
					"leave_policy": info["leave_policy"],
					"assignment_based_on": "",
					"leave_period": "",
					"effective_from": info["effective_from"],
					"effective_to": info["effective_to"],
					"carry_forward": cint(info.get("carry_forward")),
				}
			)
			lpa.flags.ignore_permissions = True
			try:
				lpa.insert()
				lpa.submit()
			except Exception as e:
				frappe.log_error(
					title=_("Leave Policy Change: could not restore overridden LPA"),
					message=f"{info}\n{e}",
				)
				frappe.msgprint(
					_(
						"Could not auto-restore overridden assignment {0}. Please recreate manually."
					).format(info.get("name")),
					indicator="orange",
				)

	def validate_cancel_allowed(self):
		"""Cancel only if nothing was consumed from the new assignment."""
		if not self.new_leave_policy_assignment:
			return

		alloc_names = frappe.get_all(
			"Leave Allocation",
			filters={"leave_policy_assignment": self.new_leave_policy_assignment, "docstatus": 1},
			pluck="name",
		)
		if not alloc_names:
			return

		apps = frappe.get_all(
			"Leave Application",
			filters={
				"employee": self.employee,
				"docstatus": 1,
				"from_date": [">=", self.new_effective_from],
				"to_date": ["<=", self.new_effective_to],
			},
			fields=["name", "leave_type", "from_date"],
			limit=5,
		)
		# Narrow to leave types allocated by the new LPA
		new_types = set(
			frappe.get_all(
				"Leave Allocation",
				filters={"name": ["in", alloc_names]},
				pluck="leave_type",
			)
		)
		blocking = [a for a in apps if a.leave_type in new_types]
		if blocking:
			examples = ", ".join(
				f"{a.name} ({a.leave_type} {formatdate(a.from_date)})" for a in blocking
			)
			frappe.throw(
				_(
					"Cannot cancel: leave already taken against the new policy period. "
					"Cancel/amend these Leave Applications first: {0}"
				).format(examples)
			)

	def close_old_assignment(self):
		"""Shorten old LPA + allocations to old_end_date and rebalance CL/SL to period months.

		Without rebalance, a full-year front-loaded allocation (e.g. 2.5 CL) shortened to
		2 months would still carry ~2.5 into the new policy via carry-forward — double counting
		against the new prorata. Rebalance reduces old credits to months actually covered.
		"""
		old_end = getdate(self.old_end_date)
		snapshot = {
			"lpa": {
				"name": self.old_leave_policy_assignment,
				"effective_to": str(getdate(self.old_effective_to)),
			},
			"allocations": [],
			"ledgers": [],
			"rebalance_ledgers": [],
			"old_employment_type": self.old_employment_type,
		}

		allocations = frappe.get_all(
			"Leave Allocation",
			filters={
				"leave_policy_assignment": self.old_leave_policy_assignment,
				"docstatus": 1,
			},
			fields=[
				"name",
				"leave_type",
				"from_date",
				"to_date",
				"expired",
				"total_leaves_allocated",
				"new_leaves_allocated",
				"unused_leaves",
				"carry_forwarded_leaves_count",
			],
		)

		for alloc in allocations:
			snapshot["allocations"].append(
				{
					"name": alloc.name,
					"leave_type": alloc.leave_type,
					"to_date": str(getdate(alloc.to_date)),
					"expired": cint(alloc.expired),
					"total_leaves_allocated": flt(alloc.total_leaves_allocated),
					"new_leaves_allocated": flt(alloc.new_leaves_allocated),
					"unused_leaves": flt(alloc.unused_leaves),
					"carry_forwarded_leaves_count": flt(alloc.carry_forwarded_leaves_count),
				}
			)

			if getdate(alloc.to_date) <= old_end:
				continue

			if getdate(alloc.from_date) > old_end:
				frappe.throw(
					_(
						"Leave Allocation {0} starts after the old end date. "
						"Resolve this allocation manually before submitting."
					).format(get_link_to_form("Leave Allocation", alloc.name))
				)

			ledgers = frappe.get_all(
				"Leave Ledger Entry",
				filters={
					"transaction_type": "Leave Allocation",
					"transaction_name": alloc.name,
					"docstatus": 1,
					"is_expired": 0,
				},
				fields=["name", "to_date", "from_date"],
			)
			for led in ledgers:
				snapshot["ledgers"].append({"name": led.name, "to_date": str(getdate(led.to_date))})
				if getdate(led.to_date) > old_end:
					if getdate(led.from_date) > old_end:
						frappe.throw(
							_(
								"Leave Ledger Entry {0} starts after old end date {1}. Cannot close safely."
							).format(led.name, formatdate(old_end))
						)
					frappe.db.set_value(
						"Leave Ledger Entry",
						led.name,
						"to_date",
						old_end,
						update_modified=False,
					)

			# Cancel expiry ledgers dated after old_end (created when old LPA originally ended later).
			# Otherwise Employee Leave Balance double-counts Expired and closing goes negative.
			orphan_expiry = frappe.get_all(
				"Leave Ledger Entry",
				filters={
					"transaction_type": "Leave Allocation",
					"transaction_name": alloc.name,
					"docstatus": 1,
					"is_expired": 1,
					"from_date": [">", old_end],
				},
				fields=["name", "leaves", "from_date", "to_date"],
			)
			for led in orphan_expiry:
				snapshot.setdefault("cancelled_orphan_expiry", []).append(
					{
						"name": led.name,
						"leaves": flt(led.leaves),
						"from_date": str(getdate(led.from_date)),
						"to_date": str(getdate(led.to_date)),
					}
				)
				frappe.db.set_value(
					"Leave Ledger Entry",
					led.name,
					"docstatus",
					2,
					update_modified=False,
				)

			frappe.db.set_value(
				"Leave Allocation",
				alloc.name,
				"to_date",
				old_end,
				update_modified=False,
			)

			rebalance_name = self._rebalance_allocation_for_shortened_period(alloc, old_end)
			if rebalance_name:
				snapshot["rebalance_ledgers"].append(rebalance_name)

			# Without CF: expire leftovers now. With CF: expire+add on new after new LPA exists.
			if not self.carry_forward:
				expiry_info = self._expire_closed_allocation(alloc, old_end)
				if expiry_info:
					snapshot.setdefault("expiry_ledgers", []).append(expiry_info)

		frappe.db.set_value(
			"Leave Policy Assignment",
			self.old_leave_policy_assignment,
			"effective_to",
			old_end,
			update_modified=False,
		)

		self.db_set("closure_snapshot", json.dumps(snapshot, default=str))

	def _rebalance_allocation_for_shortened_period(self, alloc, old_end):
		"""Reduce CL/SL credits to monthly prorata for [from_date, old_end]. Returns ledger name if created."""
		if alloc.leave_type not in PRORATED_LEAVE_TYPES:
			return None

		annual = frappe.db.get_value(
			"Leave Policy Detail",
			{"parent": self.old_leave_policy, "leave_type": alloc.leave_type},
			"annual_allocation",
		)
		if annual is None:
			return None

		target = flt(calculate_monthly_prorated_leaves(annual, alloc.from_date, old_end), 3)

		current = flt(
			frappe.db.sql(
				"""
				select coalesce(sum(leaves), 0)
				from `tabLeave Ledger Entry`
				where docstatus = 1
					and transaction_type = 'Leave Allocation'
					and transaction_name = %s
					and is_expired = 0
					and is_carry_forward = 0
				""",
				alloc.name,
			)[0][0],
			3,
		)

		delta = flt(target - current, 3)
		if not delta:
			return None

		alloc_doc = frappe.get_doc("Leave Allocation", alloc.name)
		args = {
			"leaves": delta,
			"from_date": old_end,
			"to_date": old_end,
			"is_carry_forward": 0,
			"is_expired": 0,
		}
		create_leave_ledger_entry(alloc_doc, args, submit=True)

		ledger_name = frappe.db.get_value(
			"Leave Ledger Entry",
			{
				"transaction_type": "Leave Allocation",
				"transaction_name": alloc.name,
				"leaves": delta,
				"from_date": old_end,
				"to_date": old_end,
				"is_expired": 0,
				"docstatus": 1,
			},
			"name",
			order_by="creation desc",
		)

		frappe.db.set_value(
			"Leave Allocation",
			alloc.name,
			{
				"total_leaves_allocated": flt(alloc_doc.total_leaves_allocated) + delta,
				"new_leaves_allocated": max(flt(alloc_doc.new_leaves_allocated) + delta, 0),
			},
			update_modified=False,
		)
		return ledger_name

	def _expire_closed_allocation(self, alloc, old_end):
		"""Expire unused leaves on the shortened allocation (no carry to new policy).

		Converts open CF credits to normal credits first so Employee Leave Balance
		(year) reports that only sum non-CF allocation credits still balance:
		allocated - expired - taken = closing.
		"""
		from hrms.hr.doctype.leave_ledger_entry.leave_ledger_entry import get_remaining_leaves

		cf_cleared = []
		for led_name in frappe.get_all(
			"Leave Ledger Entry",
			filters={
				"transaction_type": "Leave Allocation",
				"transaction_name": alloc.name,
				"docstatus": 1,
				"is_expired": 0,
				"is_carry_forward": 1,
			},
			pluck="name",
		):
			frappe.db.set_value(
				"Leave Ledger Entry",
				led_name,
				"is_carry_forward",
				0,
				update_modified=False,
			)
			cf_cleared.append(led_name)

		alloc_doc = frappe.get_doc("Leave Allocation", alloc.name)
		# Ensure to_date is old_end for remaining-leaves calc
		alloc_doc.to_date = old_end
		remaining = flt(get_remaining_leaves(alloc_doc))
		if not remaining:
			if cf_cleared:
				return {"allocation": alloc.name, "cf_cleared": cf_cleared, "expiry_ledger": None}
			return None

		args = {
			"leaves": -remaining,
			"transaction_name": alloc.name,
			"transaction_type": "Leave Allocation",
			"from_date": old_end,
			"to_date": old_end,
			"is_carry_forward": 0,
			"is_expired": 1,
		}
		create_leave_ledger_entry(alloc_doc, args, submit=True)

		expiry_ledger = frappe.db.get_value(
			"Leave Ledger Entry",
			{
				"transaction_type": "Leave Allocation",
				"transaction_name": alloc.name,
				"is_expired": 1,
				"from_date": old_end,
				"to_date": old_end,
				"leaves": -remaining,
				"docstatus": 1,
			},
			"name",
			order_by="creation desc",
		)

		frappe.db.set_value(
			"Leave Allocation",
			alloc.name,
			{
				"expired": 1,
				"new_leaves_allocated": 0,
				"total_leaves_allocated": 0,
				"unused_leaves": 0,
			},
			update_modified=False,
		)

		return {
			"allocation": alloc.name,
			"cf_cleared": cf_cleared,
			"expiry_ledger": expiry_ledger,
			"expired_leaves": remaining,
		}

	def create_new_assignment(self):
		# Always create without HRMS carry-forward. We add unused ourselves after rebalance
		# so CL (often non-CF leave type) also carries, and amounts are period-correct.
		lpa = frappe.get_doc(
			{
				"doctype": "Leave Policy Assignment",
				"employee": self.employee,
				"leave_policy": self.new_leave_policy,
				"assignment_based_on": "",
				"leave_period": "",
				"effective_from": self.new_effective_from,
				"effective_to": self.new_effective_to,
				"carry_forward": 0,
			}
		)
		lpa.flags.ignore_permissions = True
		lpa.insert()
		lpa.submit()
		self.db_set("new_leave_policy_assignment", lpa.name)

	def carry_forward_unused_to_new(self):
		"""Expire unused on each old allocation and credit the same amount on the new allocation."""
		from hrms.hr.doctype.leave_ledger_entry.leave_ledger_entry import get_remaining_leaves

		if not self.new_leave_policy_assignment:
			return

		snapshot = json.loads(self.closure_snapshot or "{}")
		old_end = getdate(self.old_end_date)
		new_from = getdate(self.new_effective_from)
		new_to = getdate(self.new_effective_to)
		cf_transfers = []

		old_allocs = frappe.get_all(
			"Leave Allocation",
			filters={
				"leave_policy_assignment": self.old_leave_policy_assignment,
				"docstatus": 1,
			},
			fields=["name", "leave_type"],
		)

		for old in old_allocs:
			# Normalize any open CF flags so remaining calc + year reports are consistent
			for led_name in frappe.get_all(
				"Leave Ledger Entry",
				filters={
					"transaction_type": "Leave Allocation",
					"transaction_name": old.name,
					"docstatus": 1,
					"is_expired": 0,
					"is_carry_forward": 1,
				},
				pluck="name",
			):
				frappe.db.set_value(
					"Leave Ledger Entry", led_name, "is_carry_forward", 0, update_modified=False
				)

			old_doc = frappe.get_doc("Leave Allocation", old.name)
			old_doc.to_date = old_end
			remaining = flt(get_remaining_leaves(old_doc), 3)
			if remaining <= 0:
				# Still mark expired if nothing left
				if not cint(old_doc.expired):
					frappe.db.set_value(
						"Leave Allocation",
						old.name,
						{"expired": 1},
						update_modified=False,
					)
				continue

			# Transfer out on old (NOT is_expired) so reports don't show "Expired"
			# while the same amount is added on the new LPA allocation.
			create_leave_ledger_entry(
				old_doc,
				{
					"leaves": -remaining,
					"transaction_name": old.name,
					"transaction_type": "Leave Allocation",
					"from_date": old_end,
					"to_date": old_end,
					"is_carry_forward": 0,
					"is_expired": 0,
				},
				submit=True,
			)
			transfer_out_ledger = frappe.db.get_value(
				"Leave Ledger Entry",
				{
					"transaction_name": old.name,
					"is_expired": 0,
					"from_date": old_end,
					"to_date": old_end,
					"leaves": -remaining,
					"docstatus": 1,
				},
				"name",
				order_by="creation desc",
			)
			frappe.db.set_value(
				"Leave Allocation",
				old.name,
				{
					"expired": 1,
					"new_leaves_allocated": 0,
					"total_leaves_allocated": 0,
					"unused_leaves": 0,
					"carry_forwarded_leaves_count": remaining,
				},
				update_modified=False,
			)

			# Credit on new allocation for same leave type
			new_alloc_name = frappe.db.get_value(
				"Leave Allocation",
				{
					"leave_policy_assignment": self.new_leave_policy_assignment,
					"leave_type": old.leave_type,
					"docstatus": 1,
				},
				"name",
			)
			cf_ledger = None
			if new_alloc_name:
				new_doc = frappe.get_doc("Leave Allocation", new_alloc_name)
				# Post as normal allocation credit (not is_carry_forward) so year
				# leave-balance reports that sum non-CF credits still include it.
				create_leave_ledger_entry(
					new_doc,
					{
						"leaves": remaining,
						"from_date": new_from,
						"to_date": new_to,
						"is_carry_forward": 0,
						"is_expired": 0,
					},
					submit=True,
				)
				cf_ledger = frappe.db.get_value(
					"Leave Ledger Entry",
					{
						"transaction_name": new_alloc_name,
						"is_carry_forward": 0,
						"leaves": remaining,
						"from_date": new_from,
						"to_date": new_to,
						"docstatus": 1,
					},
					"name",
					order_by="creation desc",
				)
				frappe.db.set_value(
					"Leave Allocation",
					new_alloc_name,
					{
						"new_leaves_allocated": flt(new_doc.new_leaves_allocated) + remaining,
						"total_leaves_allocated": flt(new_doc.total_leaves_allocated) + remaining,
					},
					update_modified=False,
				)
				new_doc.add_comment(
					"Info",
					_("Carried {0} {1} from closed policy period ending {2} via Leave Policy Change").format(
						remaining, old.leave_type, formatdate(old_end)
					),
				)

			cf_transfers.append(
				{
					"leave_type": old.leave_type,
					"old_allocation": old.name,
					"new_allocation": new_alloc_name,
					"leaves": remaining,
					"transfer_out_ledger": transfer_out_ledger,
					"cf_ledger": cf_ledger,
				}
			)

		snapshot["cf_transfers"] = cf_transfers
		self.db_set("closure_snapshot", json.dumps(snapshot, default=str))

	def update_employee_type(self):
		if not self.update_employee_employment_type:
			return
		if not self.new_employment_type:
			return
		if self.old_employment_type == self.new_employment_type:
			return

		frappe.db.set_value(
			"Employee",
			self.employee,
			"employment_type",
			self.new_employment_type,
			update_modified=True,
		)

	def cancel_new_assignment(self):
		if not self.new_leave_policy_assignment:
			return

		if not frappe.db.exists("Leave Policy Assignment", self.new_leave_policy_assignment):
			return

		lpa = frappe.get_doc("Leave Policy Assignment", self.new_leave_policy_assignment)
		if lpa.docstatus != 1:
			return

		# Cancel allocations first (LPA itself may not reverse allocations on cancel in all versions)
		allocations = frappe.get_all(
			"Leave Allocation",
			filters={"leave_policy_assignment": lpa.name, "docstatus": 1},
			pluck="name",
		)
		for alloc_name in allocations:
			alloc = frappe.get_doc("Leave Allocation", alloc_name)
			alloc.flags.ignore_permissions = True
			alloc.cancel()

		# Leave Policy Assignment is usually not cancellable after leaves_allocated;
		# mark as cancelled via db if cancel fails, but try standard cancel first.
		try:
			lpa.flags.ignore_permissions = True
			lpa.cancel()
		except Exception:
			frappe.db.set_value(
				"Leave Policy Assignment",
				lpa.name,
				"docstatus",
				2,
				update_modified=False,
			)

	def restore_old_assignment(self):
		if not self.closure_snapshot:
			return

		snapshot = json.loads(self.closure_snapshot)
		# Same-day replace cancelled the old LPA entirely — restore via overridden list.
		if snapshot.get("replace_from_start"):
			return

		alloc_names = [a["name"] for a in snapshot.get("allocations", [])]

		# Remove expiry ledgers created when new CF allocation expired the old one
		if alloc_names:
			expiry_entries = frappe.get_all(
				"Leave Ledger Entry",
				filters={
					"transaction_type": "Leave Allocation",
					"transaction_name": ["in", alloc_names],
					"is_expired": 1,
					"docstatus": 1,
				},
				pluck="name",
			)
			for name in expiry_entries:
				frappe.db.delete("Leave Ledger Entry", {"name": name})

		# Remove rebalance adjustment ledgers created on close
		for name in snapshot.get("rebalance_ledgers", []):
			if frappe.db.exists("Leave Ledger Entry", name):
				frappe.db.delete("Leave Ledger Entry", {"name": name})

		# Remove expiry ledgers created on close; restore CF flags
		for info in snapshot.get("expiry_ledgers", []):
			expiry_name = info.get("expiry_ledger") if isinstance(info, dict) else None
			if expiry_name and frappe.db.exists("Leave Ledger Entry", expiry_name):
				frappe.db.delete("Leave Ledger Entry", {"name": expiry_name})
			for led_name in (info.get("cf_cleared") or []) if isinstance(info, dict) else []:
				if frappe.db.exists("Leave Ledger Entry", led_name):
					frappe.db.set_value(
						"Leave Ledger Entry",
						led_name,
						"is_carry_forward",
						1,
						update_modified=False,
					)

		# Remove transfer-out (old) and carried-in (new) ledgers from CF path
		for t in snapshot.get("cf_transfers", []):
			for key in ("transfer_out_ledger", "expiry_ledger", "cf_ledger"):
				led = t.get(key)
				if led and frappe.db.exists("Leave Ledger Entry", led):
					frappe.db.delete("Leave Ledger Entry", {"name": led})
			new_alloc = t.get("new_allocation")
			leaves = flt(t.get("leaves"))
			if new_alloc and frappe.db.exists("Leave Allocation", new_alloc) and leaves:
				cur = frappe.db.get_value(
					"Leave Allocation",
					new_alloc,
					["total_leaves_allocated", "new_leaves_allocated"],
					as_dict=True,
				)
				frappe.db.set_value(
					"Leave Allocation",
					new_alloc,
					{
						"total_leaves_allocated": max(flt(cur.total_leaves_allocated) - leaves, 0),
						"new_leaves_allocated": max(flt(cur.new_leaves_allocated) - leaves, 0),
					},
					update_modified=False,
				)

		for led in snapshot.get("ledgers", []):
			if frappe.db.exists("Leave Ledger Entry", led["name"]):
				frappe.db.set_value(
					"Leave Ledger Entry",
					led["name"],
					"to_date",
					getdate(led["to_date"]),
					update_modified=False,
				)

		for alloc in snapshot.get("allocations", []):
			if not frappe.db.exists("Leave Allocation", alloc["name"]):
				continue
			values = {
				"to_date": getdate(alloc["to_date"]),
			}
			# Restore totals wiped by carry-forward expiry
			if "total_leaves_allocated" in alloc:
				values.update(
					{
						"expired": cint(alloc.get("expired", 0)),
						"total_leaves_allocated": flt(alloc.get("total_leaves_allocated")),
						"new_leaves_allocated": flt(alloc.get("new_leaves_allocated")),
						"unused_leaves": flt(alloc.get("unused_leaves")),
						"carry_forwarded_leaves_count": flt(alloc.get("carry_forwarded_leaves_count")),
					}
				)
			frappe.db.set_value(
				"Leave Allocation",
				alloc["name"],
				values,
				update_modified=False,
			)

		lpa = snapshot.get("lpa") or {}
		if lpa.get("name") and frappe.db.exists("Leave Policy Assignment", lpa["name"]):
			frappe.db.set_value(
				"Leave Policy Assignment",
				lpa["name"],
				"effective_to",
				getdate(lpa["effective_to"]),
				update_modified=False,
			)

	def restore_employee_type(self):
		if not self.update_employee_employment_type:
			return

		old_type = None
		if self.closure_snapshot:
			old_type = json.loads(self.closure_snapshot).get("old_employment_type")
		old_type = old_type or self.old_employment_type
		if not old_type:
			return

		frappe.db.set_value(
			"Employee",
			self.employee,
			"employment_type",
			old_type,
			update_modified=True,
		)

	def add_audit_comments(self, reversed_entry=False):
		targets = []
		if self.old_leave_policy_assignment:
			targets.append(("Leave Policy Assignment", self.old_leave_policy_assignment))
		if self.new_leave_policy_assignment:
			targets.append(("Leave Policy Assignment", self.new_leave_policy_assignment))

		if reversed_entry:
			text = _("Leave Policy Change {0} cancelled by {1}.").format(
				get_link_to_form(self.doctype, self.name),
				frappe.session.user,
			)
		else:
			text = _(
				"Leave policy changed via {0} by {1}. Old ended {2}, new {3} from {4} to {5}."
			).format(
				get_link_to_form(self.doctype, self.name),
				frappe.session.user,
				formatdate(self.old_end_date),
				frappe.bold(self.new_leave_policy),
				formatdate(self.new_effective_from),
				formatdate(self.new_effective_to),
			)

		for doctype, name in targets:
			try:
				frappe.get_doc(doctype, name).add_comment(comment_type="Info", text=text)
			except Exception:
				pass


def get_default_new_effective_to(employment_type, change_date, company=None):
	"""Probation → +6 months; otherwise active Leave Period end (fallback: year end)."""
	change_date = getdate(change_date)
	is_probation = (employment_type or "").strip().lower() == "probation"

	if is_probation:
		return add_days(add_months(change_date, PROBATION_DEFAULT_MONTHS), -1)

	# Prefer active leave period end for company
	filters = {"is_active": 1}
	if company:
		filters["company"] = company

	periods = frappe.get_all(
		"Leave Period",
		filters=filters,
		fields=["to_date"],
		order_by="to_date desc",
		limit=1,
	)
	if periods and getdate(periods[0].to_date) >= change_date:
		return getdate(periods[0].to_date)

	# Fallback: 31 Dec of change year
	return getdate(f"{change_date.year}-12-31")


def get_master_leave_policy(branch, employment_type, staff_worker):
	if not (branch and employment_type and staff_worker):
		return None

	if not frappe.db.exists("DocType", "Master Leave Policy Assignment"):
		return None

	return frappe.db.get_value(
		"Master Leave Policy Assignment",
		{
			"branch": branch,
			"master_employment_type": employment_type,
			"staff_worker": staff_worker,
		},
		"master_leave_policy",
	)


@frappe.whitelist()
def get_change_preview(employee: str, change_date: str, new_employment_type: str) -> dict:
	"""Return old LPA + suggested new policy/dates for the form."""
	if not (employee and change_date and new_employment_type):
		return {}

	emp = frappe.db.get_value(
		"Employee",
		employee,
		["employee_name", "company", "branch", "employment_type", "custom_staffworker"],
		as_dict=True,
	)
	if not emp:
		frappe.throw(_("Employee not found"))

	change_date = getdate(change_date)
	old_end = add_days(change_date, -1)

	lpa = frappe.db.get_value(
		"Leave Policy Assignment",
		{
			"employee": employee,
			"docstatus": 1,
			"effective_from": ["<=", change_date],
			"effective_to": [">=", change_date],
		},
		["name", "leave_policy", "effective_from", "effective_to"],
		as_dict=True,
	)

	new_policy = get_master_leave_policy(emp.branch, new_employment_type, emp.custom_staffworker)
	new_to = get_default_new_effective_to(new_employment_type, change_date, emp.company)
	new_policy_title = (
		frappe.db.get_value("Leave Policy", new_policy, "title") if new_policy else None
	)

	replace_from_start = bool(
		lpa and getdate(lpa.effective_from) >= change_date
	)

	overlapping = frappe.get_all(
		"Leave Policy Assignment",
		filters={
			"employee": employee,
			"docstatus": 1,
			"name": ["!=", lpa.name if lpa else ""],
			"effective_from": ["<=", new_to],
			"effective_to": [">=", change_date],
		},
		fields=["name", "leave_policy", "effective_from", "effective_to"],
		order_by="effective_from",
	)
	# Same-day replace: the "old" LPA itself must also be overridden
	if replace_from_start and lpa:
		overlapping = [
			frappe._dict(
				{
					"name": lpa.name,
					"leave_policy": lpa.leave_policy,
					"effective_from": lpa.effective_from,
					"effective_to": lpa.effective_to,
				}
			)
		] + overlapping

	for row in overlapping:
		row["leave_policy_title"] = frappe.db.get_value("Leave Policy", row.leave_policy, "title")

	return {
		"employee_name": emp.employee_name,
		"company": emp.company,
		"branch": emp.branch,
		"old_employment_type": emp.employment_type,
		"staff_worker": emp.custom_staffworker,
		"old_leave_policy_assignment": lpa.name if lpa else None,
		"old_leave_policy": lpa.leave_policy if lpa else None,
		"old_effective_from": lpa.effective_from if lpa else None,
		"old_effective_to": lpa.effective_to if lpa else None,
		"old_end_date": old_end,
		"new_effective_from": change_date,
		"new_effective_to": new_to,
		"new_leave_policy": new_policy,
		"new_leave_policy_title": new_policy_title,
		"master_policy_found": bool(new_policy),
		"overlapping_assignments": overlapping,
		"replace_from_start": replace_from_start,
	}
