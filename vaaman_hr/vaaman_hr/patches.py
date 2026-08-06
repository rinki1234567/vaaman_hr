import frappe
from frappe.utils import flt, getdate, get_last_day, get_first_day
from hrms.hr.utils import calculate_pro_rated_leaves, round_earned_leaves

def custom_get_monthly_earned_leave(
    date_of_joining,
    annual_leaves,
    frequency,
    rounding,
    period_start_date=None,
    period_end_date=None,
    pro_rated=True,
):
    earned_leaves = 0.0
    divide_by_frequency = {"Yearly": 1, "Half-Yearly": 2, "Quarterly": 4, "Monthly": 12, "20_Days": 18}
    if annual_leaves:
        earned_leaves = flt(annual_leaves) / divide_by_frequency[frequency]

        if pro_rated:
            if not (period_start_date or period_end_date):
                today_date = frappe.flags.current_date or getdate()
                period_end_date = get_last_day(today_date)
                period_start_date = get_first_day(today_date)

            earned_leaves = calculate_pro_rated_leaves(
                earned_leaves, date_of_joining, period_start_date, period_end_date, is_earned_leave=True
            )

        earned_leaves = round_earned_leaves(earned_leaves, rounding)

    return earned_leaves

def apply_monkey_patch(app_name=None):
    """
    Apply monkey patches for HRMS helpers.
    Called by Frappe's before_app_install hook with app_name parameter
    """
    import hrms.hr.utils
    hrms.hr.utils.get_monthly_earned_leave = custom_get_monthly_earned_leave

    from vaaman_hr.overrides.leave_balance import apply_comp_off_balance_patch

    apply_comp_off_balance_patch()

# Ensure the patch is applied at the appropriate point in your application
apply_monkey_patch()
