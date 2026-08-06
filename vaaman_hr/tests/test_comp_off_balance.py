import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, getdate

from vaaman_hr.overrides.comp_off_balance import (
	apply_fifo_consumption,
	sum_remaining_on_date,
)


class TestCompOffFifoBalance(FrappeTestCase):
	def test_unused_only_expires_new_ot_stays_clean(self):
		"""+5 in June, use 3, expire remainder, +3.5 in July => balance 3.5."""
		june_start = getdate("2026-06-01")
		june_end = getdate("2026-07-30")  # ~60-day window
		july_start = getdate("2026-07-02")
		july_end = getdate("2026-08-30")

		lots = [
			frappe._dict(
				from_date=june_start, to_date=june_end, leaves=5.0, remaining=5.0
			),
			frappe._dict(
				from_date=july_start, to_date=july_end, leaves=3.5, remaining=3.5
			),
		]
		leave_days = [
			(getdate("2026-06-10"), 1.0),
			(getdate("2026-06-11"), 1.0),
			(getdate("2026-06-12"), 1.0),
		]

		consumed = apply_fifo_consumption(lots, leave_days)
		self.assertEqual(consumed[0].remaining, 2.0)
		self.assertEqual(consumed[1].remaining, 3.5)

		# After June lot expiry date, only July lot remains
		after_expiry = add_days(june_end, 1)
		self.assertEqual(sum_remaining_on_date(consumed, after_expiry), 3.5)
		self.assertEqual(sum_remaining_on_date(consumed, getdate("2026-07-15")), 5.5)
