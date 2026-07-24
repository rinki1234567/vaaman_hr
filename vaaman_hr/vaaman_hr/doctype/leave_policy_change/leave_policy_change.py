# Copyright (c) 2026, Pratul Tripathi and contributors
# For license information, please see license.txt

import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, add_months, cint, flt, formatdate, get_link_to_form, getdate


PROBATION_DEFAULT_MONTHS = 6


class LeavePolicyChange(Document):
	def validate(self):
		self.set_employee_details()
		self.set_dates()
		self.load_old_assignment()
		self.set_new_policy_from_master()
		self.validate_change()

	def on_submit(self):
		self.close_old_assignment()
		self.create_new_assignment()
		self.update_employee_type()
		self.add_audit_comments()

	def on_cancel(self):
		self.validate_cancel_allowed()
		self.cancel_new_assignment()
		self.restore_old_assignment()
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
			frappe.throw(
				_(
					"Change Date {0} is on or before the old assignment start {1}. "
					"Nothing to close — create a fresh Leave Policy Assignment instead."
				).format(
					frappe.bold(formatdate(self.change_date)),
					frappe.bold(formatdate(self.old_effective_from)),
				)
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
		"""Shorten old LPA + allocations + allocation ledgers to old_end_date (no cancel)."""
		old_end = getdate(self.old_end_date)
		snapshot = {
			"lpa": {
				"name": self.old_leave_policy_assignment,
				"effective_to": str(getdate(self.old_effective_to)),
			},
			"allocations": [],
			"ledgers": [],
			"old_employment_type": self.old_employment_type,
		}

		# Snapshot + update allocations
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

			# Shorten allocation ledgers first
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
					# Keep from_date <= to_date
					new_to = old_end
					if getdate(led.from_date) > new_to:
						frappe.throw(
							_(
								"Leave Ledger Entry {0} starts after old end date {1}. Cannot close safely."
							).format(led.name, formatdate(old_end))
						)
					frappe.db.set_value(
						"Leave Ledger Entry",
						led.name,
						"to_date",
						new_to,
						update_modified=False,
					)

			frappe.db.set_value(
				"Leave Allocation",
				alloc.name,
				"to_date",
				old_end,
				update_modified=False,
			)

		# Shorten LPA
		frappe.db.set_value(
			"Leave Policy Assignment",
			self.old_leave_policy_assignment,
			"effective_to",
			old_end,
			update_modified=False,
		)

		self.db_set("closure_snapshot", json.dumps(snapshot, default=str))

	def create_new_assignment(self):
		lpa = frappe.get_doc(
			{
				"doctype": "Leave Policy Assignment",
				"employee": self.employee,
				"leave_policy": self.new_leave_policy,
				"assignment_based_on": "",
				"leave_period": "",
				"effective_from": self.new_effective_from,
				"effective_to": self.new_effective_to,
				"carry_forward": 1 if self.carry_forward else 0,
			}
		)
		lpa.flags.ignore_permissions = True
		# Prevent set_dates from wiping custom dates when based_on is blank — already blank
		lpa.insert()
		lpa.submit()
		self.db_set("new_leave_policy_assignment", lpa.name)

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
	}
