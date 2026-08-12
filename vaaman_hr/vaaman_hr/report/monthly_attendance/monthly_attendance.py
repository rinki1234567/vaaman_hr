
# from calendar import monthrange
# from itertools import groupby

# import frappe
# from frappe import _
# from frappe.query_builder.functions import Count, Extract, Sum
# from frappe.utils import cint, cstr, getdate
# from frappe.utils.nestedset import get_descendants_of

# Filters = frappe._dict

# status_map = {
#     "Present": "P",
#     "Absent": "A",
#     "Half Day": "HD",
#     "Work From Home": "WFH",
#     "Half Day/Other Half Absent": "HD/A",
#     "Half Day/Other Half Present": "HD/P",
#     "On Leave": "L",
#     "Holiday": "H",
#     "Weekly Off": "WO",
# }

# day_abbr = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
# leave_type_abbr = {
#     "Casual Leave": "CL",
#     "Sick Leave": "SL",
#     "Sick Leave - Zinc": "SLZ",
#     "Privilege Leave": "PL",
#     "Compensatory Off": "COM",
#     "Maternity Leave":"ML",
#     "Special Leave":"SPL",
#     "Festival Leave":"FL",
#     "Leave Without Pay":"LWP"
#     # Add more leave types as needed
# }


# def execute(filters: Filters | None = None) -> tuple:
#     filters = frappe._dict(filters or {})

#     if not (filters.month and filters.year):
#         frappe.throw(_("Please select month and year."))

#     if not filters.company:
#         frappe.throw(_("Please select company."))

#     if filters.company:
#         filters.companies = [filters.company]
#         if filters.include_company_descendants:
#             filters.companies.extend(get_descendants_of("Company", filters.company))

#     attendance_map = get_attendance_map(filters)
#     if not attendance_map:
#         frappe.msgprint(_("No attendance records found."), alert=True, indicator="orange")
#         return [], [], None, None

#     columns = get_columns(filters)
#     data = get_data(filters, attendance_map)

#     if not data:
#         frappe.msgprint(_("No attendance records found for this criteria."), alert=True, indicator="orange")
#         return columns, [], None, None

#     message = get_message() if not filters.summarized_view else ""
#     chart = get_chart_data(attendance_map, filters)

#     return columns, data, message, chart


# def get_message() -> str:
#     message = ""
#     colors = ["green", "red", "orange", "green", "#318AD8","#878787",
#         "#878787", "", ""]

#     count = 0
#     for status, abbr in status_map.items():
#         message += f"""
#             <span style='border-left: 2px solid {colors[count]}; padding-right: 12px; padding-left: 5px; margin-right: 3px;'>
#                 {status} - {abbr}
#             </span>
#         """
#         count += 1

#     return message


# def get_columns(filters: Filters) -> list[dict]:
#     columns = []

#     if filters.group_by:
#         options_mapping = {
#             "Branch": "Branch",
#             "Grade": "Employee Grade",
#             "Department": "Department",
#             "Designation": "Designation",
#         }
#         options = options_mapping.get(filters.group_by)
#         columns.append(
#             {
#                 "label": _(filters.group_by),
#                 "fieldname": frappe.scrub(filters.group_by),
#                 "fieldtype": "Link",
#                 "options": options,
#                 "width": 120,
#             }
#         )

#     columns.extend(
#         [
#             {
#                 "label": _("Employee"),
#                 "fieldname": "employee",
#                 "fieldtype": "Link",
#                 "options": "Employee",
#                 "width": 135,
#             },
#             {"label": _("Employee Name"), "fieldname": "employee_name", "fieldtype": "Data", "width": 120},
#             {"label": _("Staff/Worker"), "fieldname": "custom_staffworker", "fieldtype":"Data", "width": 110},
#             {"label": _("Get Pass Number"), "fieldname": "attendance_device_id", "fieldtype": "Data", "width": 120},
#         ]
#     )

#     if filters.summarized_view:
#         columns.extend(
#             [
#                 {
#                     "label": _("Total Present"),
#                     "fieldname": "total_present",
#                     "fieldtype": "Float",
#                     "width": 110,
#                 },
#                 {"label": _("Total Leaves"), "fieldname": "total_leaves", "fieldtype": "Float", "width": 110},
#                 {"label": _("Total Absent"), "fieldname": "total_absent", "fieldtype": "Float", "width": 110},
#                 {
#                     "label": _("Total Holidays"),
#                     "fieldname": "total_holidays",
#                     "fieldtype": "Float",
#                     "width": 120,
#                 },
#                 {
#                     "label": _("Total Weekly Off"),
#                     "fieldname": "total_weekly_off",
#                     "fieldtype": "Float",
#                     "width": 120,
#                 },
                
#                 {
#                     "label": _("PPH"),
#                     "fieldname": "pph",
#                     "fieldtype": "Float",
#                     "width": 120,
#                     },
                
#                 {"label": "Total  Overtime", "fieldname": "total_overtime", "fieldtype": "Float", "width": 150},
#                 {
#                     "label": _("Unmarked Days"),
#                     "fieldname": "unmarked_days",
#                     "fieldtype": "Float",
#                     "width": 130,
#                 },
#             ]
#         )
#         columns.extend(get_columns_for_leave_types())
#         columns.extend(
#             [
#                 {
#                     "label": _("Total Late Entries"),
#                     "fieldname": "total_late_entries",
#                     "fieldtype": "Float",
#                     "width": 140,
#                 },
#                 {
#                     "label": _("Total Early Exits"),
#                     "fieldname": "total_early_exits",
#                     "fieldtype": "Float",
#                     "width": 140,
#                 },
#             ]
#         )
#     else:
#         # columns.append({"label": _("Shift"), "fieldname": "shift", "fieldtype": "Data", "width": 120})
#         columns.extend(get_columns_for_days(filters))
#         columns.extend([
            
#         {
#         "label": _("Total Present"),
#         "fieldname": "total_present",
#         "fieldtype": "Float",
#         "width": 120,
#         },
#           {"label": _("Total Leaves"), 
#         "fieldname": "total_leaves",
#         "fieldtype": "Float", 
#         "width": 110
#         },
#         {
#         "label": _("Total Absent"), 
#         "fieldname": "total_absent", 
#         "fieldtype": "Float",
#         "width": 110
#         },
#         {
#         "label": _("Total Holidays"),
#         "fieldname": "total_holidays",
#         "fieldtype": "Float",
#         "width": 120
#         },
#         {
#         "label": _("Total Weekly Off"),
#         "fieldname": "total_weekly_off",
#         "fieldtype": "Float",
#         "width": 120
#         },
#         {
#         "label": _("Unmarked Days"),
#         "fieldname": "unmarked_days",
#         "fieldtype": "Float",
#         "width": 120
#         },
#         {
#         "label": _("Leave Without Pay"),
#         "fieldname": "leave_without_pay",
#         "fieldtype": "Float",
#         "width": 150
#         },
#         {
#         "label": _("Privilege Leave"),
#         "fieldname": "privilege_leave",
#         "fieldtype": "Float",
#         "width": 140
#         },
#         {
#         "label": _("Sick Leave"),
#         "fieldname": "sick_leave",
#         "fieldtype": "Float",
#         "width": 130
#         },
#         {
#         "label": _("Casual Leave"),
#         "fieldname": "casual_leave",
#         "fieldtype": "Float",
#         "width": 130
#         },
#         {
#         "label": _("Special Leave"), # <--- Ye naya column header
#         "fieldname": "special_leave",
#         "fieldtype": "Float",
#         "width": 130
#         },
#         {
#         "label": _("Compensatory Off"),
#         "fieldname": "compensatory_off",
#         "fieldtype": "Float",
#         "width": 160
#         },
#         {
#         "label": _("Festival Leave"),
#         "fieldname": "festival_leave",
#         "fieldtype": "Float",
#         "width": 130
#         },
#         {
#         "label": _("Maternity Leave"),
#         "fieldname": "maternity_leave",
#         "fieldtype": "Float",
#         "width": 140
#         },
#         {
#         "label": _("PPH"),
#         "fieldname": "pph",
#         "fieldtype": "Float",
#         "width": 120
#         },
#         {
#         "label": _("Total Late Entries"),
#         "fieldname": "total_late_entries",
#         "fieldtype": "Float",
#         "width": 160
#         },
#         {
#         "label": _("Total Early Exits"),
#         "fieldname": "total_early_exits",
#         "fieldtype": "Int",
#         "width": 160
#         },
#         {"label": "Total  Overtime", "fieldname": "total_overtime", "fieldtype": "Float", "width": 150}
#         ])
#     return columns


# def get_columns_for_leave_types() -> list[dict]:
#     leave_types = frappe.db.get_all("Leave Type", pluck="name")
#     types = []
#     for entry in leave_types:
#         types.append({"label": entry, "fieldname": frappe.scrub(entry), "fieldtype": "Float", "width": 120})

#     return types


# def get_columns_for_days(filters: Filters) -> list[dict]:
#     total_days = get_total_days_in_month(filters)
#     days = []

#     for day in range(1, total_days + 1):
#         day = cstr(day)
#         # forms the dates from selected year and month from filters
#         date = f"{cstr(filters.year)}-{cstr(filters.month)}-{day}"
#         # gets abbr from weekday number
#         weekday = day_abbr[getdate(date).weekday()]
#         # sets days as 1 Mon, 2 Tue, 3 Wed
#         label = f"{day} {weekday}"
#         days.append({"label": label, "fieldtype": "Data", "fieldname": day, "width": 65})

#     return days


# def get_total_days_in_month(filters: Filters) -> int:
#     return monthrange(cint(filters.year), cint(filters.month))[1]


# def get_effective_start_day(date_of_joining, filters: Filters) -> int:
#     """Returns the first day (1-based) visible for an employee in the filter month.
#     Days before this are blanked because the employee had not yet joined."""
#     if not date_of_joining:
#         return 1

#     joining = getdate(date_of_joining)
#     filter_year = cint(filters.year)
#     filter_month = cint(filters.month)

#     # Joined after this month — all days blank
#     if joining.year > filter_year or (joining.year == filter_year and joining.month > filter_month):
#         return get_total_days_in_month(filters) + 1

#     # Joined during this month — days before joining day are blank
#     if joining.year == filter_year and joining.month == filter_month:
#         return joining.day

#     # Joined before this month — all days visible
#     return 1


# def get_data(filters: Filters, attendance_map: dict) -> list[dict]:
#     employee_details, group_by_param_values = get_employee_related_details(filters)
#     holiday_map = get_holiday_map(filters)
#     data = []

#     if filters.group_by:
#         group_by_column = frappe.scrub(filters.group_by)

#         for value in group_by_param_values:
#             if not value:
#                 continue

#             records = get_rows(employee_details[value], filters, holiday_map, attendance_map)

#             if records:
#                 data.append({group_by_column: value})
#                 data.extend(records)
#     else:
#         data = get_rows(employee_details, filters, holiday_map, attendance_map)

#     return data


# def get_attendance_map(filters: Filters) -> dict:
#     """Returns a dictionary of employee wise attendance map as per shifts for all the days of the month like
#     {
#         'employee1': {
#                 'Morning Shift': {1: 'Present', 2: 'Absent', ...}
#                 'Evening Shift': {1: 'Absent', 2: 'Present', ...}
#         },
#         'employee2': {
#                 'Afternoon Shift': {1: 'Present', 2: 'Absent', ...}
#                 'Night Shift': {1: 'Absent', 2: 'Absent', ...}
#         },
#         'employee3': {
#                 None: {1: 'On Leave'}
#         }
#     }
#     """
#     attendance_list = get_attendance_records(filters)
#     attendance_map = {}
#     leave_map = {}

#     for d in attendance_list:
#         if d.status == "On Leave":
#             leave_map.setdefault(d.employee, {}).setdefault(d.shift, []).append(d.day_of_month)
#             continue

#         if d.shift is None:
#             d.shift = ""

#         attendance_map.setdefault(d.employee, {}).setdefault(d.shift, {})
#         attendance_map[d.employee][d.shift][d.day_of_month] = d.status

#     # leave is applicable for the entire day so all shifts should show the leave entry
#     for employee, leave_days in leave_map.items():
#         for assigned_shift, days in leave_days.items():
#             # no attendance records exist except leaves
#             if employee not in attendance_map:
#                 attendance_map.setdefault(employee, {}).setdefault(assigned_shift, {})

#             for day in days:
#                 for shift in attendance_map[employee].keys():
#                     attendance_map[employee][shift][day] = "On Leave"

#     return attendance_map


# def get_attendance_records(filters: Filters) -> list[dict]:
#     Attendance = frappe.qb.DocType("Attendance")
#     status = (
#         frappe.qb.terms.Case()
#         .when(
#             ((Attendance.status == "Half Day") & (Attendance.half_day_status == "Present")),
#             "Half Day/Other Half Present",
#         )
#         .when(
#             ((Attendance.status == "Half Day") & (Attendance.half_day_status == "Absent")),
#             "Half Day/Other Half Absent",
#         )
#         .else_(Attendance.status)
#     )
#     query = (
#         frappe.qb.from_(Attendance)
#         .select(
#             Attendance.employee,
#             Extract("day", Attendance.attendance_date).as_("day_of_month"),
#             (status).as_("status"),
#             # Attendance.shift,  #
#         )
#         .where(
#             (Attendance.docstatus == 1)
#             & (Attendance.company.isin(filters.companies))
#             & (Attendance.custom_branch == filters.branch)	
#             & (Extract("month", Attendance.attendance_date) == filters.month)
#             & (Extract("year", Attendance.attendance_date) == filters.year)
#         )
#     )

#     if filters.employee:
#         query = query.where(Attendance.employee == filters.employee)
#     if filters.branch:
#         query = query.where(Attendance.custom_branch == filters.branch)
#     query = query.orderby(Attendance.employee, Attendance.attendance_date)

#     return query.run(as_dict=1)


# def get_employee_related_details(filters: Filters) -> tuple[dict, list]:
#     """Returns
#     1. nested dict for employee details
#     2. list of values for the group by filter
#     """
#     Employee = frappe.qb.DocType("Employee")
#     query = (
#         frappe.qb.from_(Employee)
#         .select(
#             Employee.name,
#             Employee.employee_name,
#             Employee.designation,
#             Employee.grade,
#             Employee.department,
#             Employee.branch,
#             Employee.company,
#             Employee.holiday_list,
#             Employee.custom_staffworker,
#             Employee.attendance_device_id,
#             Employee.date_of_joining,
#         )
#         .where(Employee.company.isin(filters.companies))
#     )

#     if filters.employee:
#         query = query.where(Employee.name == filters.employee)

#     group_by = filters.group_by
#     if group_by:
#         group_by = group_by.lower()
#         query = query.orderby(group_by)

#     employee_details = query.run(as_dict=True)

#     group_by_param_values = []
#     emp_map = {}

#     if group_by:
#         group_key = lambda d: "" if d[group_by] is None else d[group_by]  # noqa
#         for parameter, employees in groupby(sorted(employee_details, key=group_key), key=group_key):
#             group_by_param_values.append(parameter)
#             emp_map.setdefault(parameter, frappe._dict())

#             for emp in employees:
#                 emp_map[parameter][emp.name] = emp
#     else:
#         for emp in employee_details:
#             emp_map[emp.name] = emp

#     return emp_map, group_by_param_values


# def get_holiday_map(filters: Filters) -> dict[str, list[dict]]:
#     """
#     Returns a dict of holidays falling in the filter month and year
#     with list name as key and list of holidays as values like
#     {
#             'Holiday List 1': [
#                     {'day_of_month': '0' , 'weekly_off': 1},
#                     {'day_of_month': '1', 'weekly_off': 0}
#             ],
#             'Holiday List 2': [
#                     {'day_of_month': '0' , 'weekly_off': 1},
#                     {'day_of_month': '1', 'weekly_off': 0}
#             ]
#     }
#     """
#     # add default holiday list too
#     holiday_lists = frappe.db.get_all("Holiday List", pluck="name")
#     default_holiday_list = frappe.get_cached_value("Company", filters.company, "default_holiday_list")
#     holiday_lists.append(default_holiday_list)

#     holiday_map = frappe._dict()
#     Holiday = frappe.qb.DocType("Holiday")

#     for d in holiday_lists:
#         if not d:
#             continue

#         holidays = (
#             frappe.qb.from_(Holiday)
#             .select(Extract("day", Holiday.holiday_date).as_("day_of_month"), Holiday.weekly_off)
#             .where(
#                 (Holiday.parent == d)
#                 & (Extract("month", Holiday.holiday_date) == filters.month)
#                 & (Extract("year", Holiday.holiday_date) == filters.year)
#             )
#         ).run(as_dict=True)

#         holiday_map.setdefault(d, holidays)

#     return holiday_map


# def get_rows(employee_details: dict, filters: Filters, holiday_map: dict, attendance_map: dict) -> list[dict]:
#     records = []
#     default_holiday_list = frappe.get_cached_value("Company", filters.company, "default_holiday_list")

#     for employee, details in employee_details.items():
#         emp_holiday_list = details.holiday_list or default_holiday_list
#         holidays = holiday_map.get(emp_holiday_list)

#         if filters.summarized_view:
#             attendance = get_attendance_status_for_summarized_view(employee, filters, holidays, details.date_of_joining)
#             if not attendance:
#                 continue

#             leave_summary = get_leave_summary(employee, filters)
#             entry_exits_summary = get_entry_exits_summary(employee, filters)

#             row = {"employee": employee, "employee_name": details.employee_name, "custom_staffworker": details.custom_staffworker, "attendance_device_id": details.attendance_device_id,}
#             set_defaults_for_summarized_view(filters, row)
#             row.update(attendance)
#             row.update(leave_summary)
#             row.update(entry_exits_summary)
#             row["total_overtime"] = get_total_overtime(employee, filters)

#             records.append(row)
#         else:
#             employee_attendance = attendance_map.get(employee)
#             if not employee_attendance:
#                 continue

#             attendance_for_employee = get_attendance_status_for_detailed_view(
#                 employee, filters, employee_attendance, holidays, details.date_of_joining
#             )
#             # set employee details in the first row
#             attendance_for_employee[0].update({"employee": employee, "employee_name": details.employee_name, "custom_staffworker": details.custom_staffworker, "attendance_device_id": details.attendance_device_id,})

#             records.extend(attendance_for_employee)

#     return records


# def set_defaults_for_summarized_view(filters, row):
#     for entry in get_columns(filters):
#         if entry.get("fieldtype") == "Float":
#             row[entry.get("fieldname")] = 0.0


# def get_attendance_status_for_summarized_view(employee: str, filters: Filters, holidays: list, date_of_joining=None) -> dict:
#     # Sync with detailed view logic
#     detailed_data = get_attendance_status_for_detailed_view(employee, filters, {"": {}}, holidays, date_of_joining)
#     if not detailed_data: return {}
#     res = detailed_data[0]
#     return {
#         "total_present": res.get("total_present", 0),
#         "total_leaves": res.get("total_leaves", 0),
#         "total_absent": res.get("total_absent", 0),
#         "total_holidays": res.get("total_holidays", 0),
#         "total_weekly_off": res.get("total_weekly_off", 0),
#         "pph": res.get("pph", 0),
#         "unmarked_days": res.get("unmarked_days", 0),
#     }
# def get_attendance_summary_and_days(employee: str, filters: Filters) -> tuple[dict, list]:
#     Attendance = frappe.qb.DocType("Attendance")

#     present_case = (
#         frappe.qb.terms.Case()
#         .when(((Attendance.status == "Present") | (Attendance.status == "Work From Home")), 1)
#         .else_(0)
#     )
#     sum_present = Sum(present_case).as_("total_present")

#     absent_case = frappe.qb.terms.Case().when(Attendance.status == "Absent", 1).else_(0)
#     sum_absent = Sum(absent_case).as_("total_absent")

#     leave_case = frappe.qb.terms.Case().when(Attendance.status == "On Leave", 1).else_(0)
#     sum_leave = Sum(leave_case).as_("total_leaves")

#     half_day_case = frappe.qb.terms.Case().when(Attendance.status == "Half Day", 0.5).else_(0)
#     sum_half_day = Sum(half_day_case).as_("total_half_days")

#     summary = (
#         frappe.qb.from_(Attendance)
#         .select(
#             sum_present,
#             sum_absent,
#             sum_leave,
#             sum_half_day,
#         )
#         .where(
#             (Attendance.docstatus == 1)
#             & (Attendance.employee == employee)
#             & (Attendance.company.isin(filters.companies))
#             & (Attendance.custom_branch == filters.branch)	
#             & (Extract("month", Attendance.attendance_date) == filters.month)
#             & (Extract("year", Attendance.attendance_date) == filters.year)
#         )
#     ).run(as_dict=True)

#     days = (
#         frappe.qb.from_(Attendance)
#         .select(Extract("day", Attendance.attendance_date).as_("day_of_month"))
#         .distinct()
#         .where(
#             (Attendance.docstatus == 1)
#             & (Attendance.employee == employee)
#             & (Attendance.company.isin(filters.companies))
#             & (Attendance.custom_branch == filters.branch)	
#             & (Extract("month", Attendance.attendance_date) == filters.month)
#             & (Extract("year", Attendance.attendance_date) == filters.year)
#         )
#     ).run(pluck=True)

#     return summary[0], days


# def get_attendance_status_for_detailed_view(
#     employee: str, filters: Filters, employee_attendance: dict, holidays: list, date_of_joining=None
# ) -> list[dict]:
#     total_days = get_total_days_in_month(filters)
#     effective_start = get_effective_start_day(date_of_joining, filters)
#     attendance_values = []
#     leave_summary = get_leave_summary(employee, filters)
#     entry_exit = get_entry_exits_summary(employee, filters)

#     # Leave Application Map
#     leave_day_map = {}
#     leave_apps = frappe.db.get_all(
#         "Leave Application",
#         filters={
#             "employee": employee, "docstatus": 1, "status": "Approved",
#             "from_date": ["<=", f"{filters.year}-{filters.month}-{total_days}"],
#             "to_date": [">=", f"{filters.year}-{filters.month}-01"]
#         },
#         fields=["from_date", "to_date", "leave_type"]
#     )
#     for app in leave_apps:
#         if app.leave_type == "Sick Leave - Zinc":
#             abbr = "SLZ"
#         else:
#             abbr = leave_type_abbr.get(app.leave_type, "L")
            
#         curr = getdate(app.from_date)
#         while curr <= getdate(app.to_date):
#             if curr.month == cint(filters.month):
#                 leave_day_map.setdefault(curr.day, []).append(abbr)
#             curr = frappe.utils.add_days(curr, 1)

#     att_info = frappe.db.get_all(
#         "Attendance",
#         filters={
#             "employee": employee, "docstatus": 1,
#             "attendance_date": ["between", [f"{filters.year}-{filters.month}-01", f"{filters.year}-{filters.month}-{total_days}"]]
#         },
#         fields=["attendance_date", "status", "half_day_status", "leave_type"]
#     )
#     att_map = {getdate(d.attendance_date).day: d for d in att_info}

#     for shift, status_dict in employee_attendance.items():
#         row = {}
#         t_p = t_a = t_l = t_h = t_wo = t_un = t_pph = 0.0

#         for day in range(1, total_days + 1):
#             if day < effective_start:
#                 row[cstr(day)] = ""
#                 continue

#             day_att = att_map.get(day)
#             h_status = get_holiday_status(day, holidays)
#             day_leaves = list(set(leave_day_map.get(day, [])))
            
#             abbr = ""
#             if h_status == "Holiday":
#                 if day_att and day_att.status == "Absent":
#                     abbr = "A"; t_a += 1
#                 elif day_att and day_att.status in ["Present", "Half Day"]:
#                     abbr = "H/P"; t_pph += 1
#                 else:
#                     abbr = "H"; t_h += 1
#             elif h_status == "Weekly Off":
#                 if day_att and day_att.status == "Absent":
#                     abbr = "A"; t_a += 1
#                 else:
#                     abbr = "WO"; t_wo += 1
#             elif day_att:
#                 if day_att.status == "Half Day":
#                     # =============================================================
#                     # YOUR EXACT REQUIREMENT:
#                     # If half_day_status == "Absent"  → Show "HD/A"  (Half Absent + Other Present)
#                     # If half_day_status == "Present" → Show "HD/P"  (Half Present + Other Absent)
#                     # =============================================================
#                     if day_att.half_day_status == "Absent":
#                         # Half day is Absent → Other half is Present
#                         abbr = "HD/A"
#                         t_a += 0.5      # Half day Absent
#                         t_p += 0.5      # Other half Present
#                     else:
#                         # Half day is Present → Other half is Absent
#                         abbr = "HD/P"
#                         t_p += 0.5      # Half day Present
#                         t_a += 0.5      # Other half Absent

#                     # Handle Leave Type if linked with this Half Day
#                     if day_att.leave_type == "Sick Leave - Zinc":
#                         m_leave = "SLZ"
#                     else:
#                         m_leave = leave_type_abbr.get(day_att.leave_type, "")

#                     if m_leave and m_leave not in day_leaves:
#                         day_leaves.append(m_leave)

#                     l_str = "/".join(day_leaves) if day_leaves else ""

#                     # If there is any leave on this day, combine it
#                     if day_leaves:
#                         if len(day_leaves) > 1:
#                             abbr = f"HD/{l_str}"                    # e.g. HD/CL/SL
#                         else:
#                             abbr = f"{abbr}/{l_str}"                # e.g. HD/A/CL  or  HD/P/SL

#                 elif day_att.status == "On Leave":
#                     if day_att.leave_type == "Sick Leave - Zinc":
#                         m_leave = "SLZ"
#                     else:
#                         m_leave = leave_type_abbr.get(day_att.leave_type, "L") if day_att.leave_type else "L"

#                     abbr = m_leave if m_leave not in day_leaves else "/".join(day_leaves)
#                     t_l += 1.0

#                 else:
#                     # Normal attendance: Present, Absent, Work From Home, etc.
#                     abbr = status_map.get(day_att.status, "")
#                     if abbr in ("P", "WFH") or day_att.status == "Work From Home":
#                         t_p += 1.0
#                     elif abbr == "A":
#                         t_a += 1.0

#             else:
#                 # No attendance record → Unmarked day
#                 t_un += 1

#             # --- COLOR LOGIC ADDED HERE ---
#             if "HD" in abbr:
#                 row[cstr(day)] = f"<span style='color:orange; font-weight:bold'>{abbr}</span>"
#             elif abbr == "SLZ":
#                 # Sick Leave - Zinc (Purple color)
#                 row[cstr(day)] = f"<span style='color:#8e44ad; font-weight:bold'>{abbr}</span>"
#             elif abbr == "SL":
#                 # Sick Leave (Red color)
#                 row[cstr(day)] = f"<span style='color:red; font-weight:bold'>{abbr}</span>"
#             elif abbr == "CL":
#                 # Casual Leave (Blue color)
#                 row[cstr(day)] = f"<span style='color:#2980b9; font-weight:bold'>{abbr}</span>"
#             elif abbr == "H" or abbr == "H/P":
#                 row[cstr(day)] = f"<span style='color:green; font-weight:bold'>{abbr}</span>"
#             elif abbr == "A":
#                 row[cstr(day)] = f"<span style='color:red; font-weight:bold'>{abbr}</span>"
#             else:
#                 row[cstr(day)] = abbr

#         row.update({
#             "total_present": t_p, "total_leaves": t_l, "total_absent": t_a,
#             "total_holidays": t_h, "total_weekly_off": t_wo, "pph": t_pph,
#             "unmarked_days": t_un, "total_overtime": get_total_overtime(employee, filters),
#             "total_late_entries": entry_exit.get("total_late_entries", 0),
#             "total_early_exits": entry_exit.get("total_early_exits", 0)
#         })
        
#         for lt_key, lt_val in leave_summary.items():
#             row[lt_key] = lt_val

#         attendance_values.append(row)
#     return attendance_values



# def get_holiday_status(day: int, holidays: list) -> str:
#     """Returns holiday status for a given day.
    
#     Args:
#         day: Day of month (1-31)
#         holidays: List of holiday dicts containing day_of_month and weekly_off
        
#     Returns:
#         "Weekly Off" if it's a weekly off day
#         "Holiday" if it's a regular holiday
#         None if it's not a holiday
#     """
#     if not holidays:
#         return None
        
#     for holiday in holidays:
#         if day == holiday.get("day_of_month"):
#             if holiday.get("weekly_off"):
#                 return "Weekly Off"
#             return "Holiday"
#     return None

# def get_leave_summary(employee: str, filters: Filters) -> dict:
#     leaves = {}
#     total_days = monthrange(cint(filters.year), cint(filters.month))[1]
#     from_date = f"{filters.year}-{filters.month}-01"
#     to_date = f"{filters.year}-{filters.month}-{total_days}"

#     # 1. Pehle Leave Applications se data uthao (Double Half Day handle karne ke liye)
#     leave_apps = frappe.db.get_all(
#         "Leave Application",
#         filters={
#             "employee": employee,
#             "docstatus": 1,
#             "status": "Approved",
#             "from_date": ["<=", to_date],
#             "to_date": [">=", from_date]
#         },
#         fields=["leave_type", "total_leave_days"]
#     )

#     for app in leave_apps:
#         l_type_name = "Sick Leave" if app.leave_type == "Sick Leave - Zinc" else app.leave_type
#         lt_key = frappe.scrub(l_type_name)
#         # total_leave_days field already handling half day (0.5) logic
#         leaves[lt_key] = leaves.get(lt_key, 0.0) + float(app.total_leave_days)

#     att_records = frappe.db.get_all("Attendance", 
#         filters={
#             "employee": employee, 
#             "docstatus": 1, 
#             "attendance_date": ["between", [from_date, to_date]], 
#             "status": ["in", ["On Leave", "Half Day"]],
#             "leave_application": ["is", "not set"] # Sirf manual waali
#         },
#         fields=["status", "leave_type"])

#     for d in att_records:
#         if d.leave_type:
#             current_type = "Sick Leave" if d.leave_type == "Sick Leave - Zinc" else d.leave_type
#             lt = frappe.scrub(current_type)
#             val = 0.5 if d.status == "Half Day" else 1.0
#             leaves[lt] = leaves.get(lt, 0.0) + val
    
#     return leaves

# def get_entry_exits_summary(employee: str, filters: Filters) -> dict[str, float]:
#     """Returns total late entries and total early exits for employee like:
#     {'total_late_entries': 5, 'total_early_exits': 2}
#     """
#     Attendance = frappe.qb.DocType("Attendance")

#     late_entry_case = frappe.qb.terms.Case().when(Attendance.late_entry == "1", "1")
#     count_late_entries = Count(late_entry_case).as_("total_late_entries")

#     early_exit_case = frappe.qb.terms.Case().when(Attendance.early_exit == "1", "1")
#     count_early_exits = Count(early_exit_case).as_("total_early_exits")

#     entry_exits = (
#         frappe.qb.from_(Attendance)
#         .select(count_late_entries, count_early_exits)
#         .where(
#             (Attendance.docstatus == 1)
#             & (Attendance.employee == employee)
#             & (Attendance.company.isin(filters.companies))
#             & (Attendance.custom_branch == filters.branch)	
#             & (Extract("month", Attendance.attendance_date) == filters.month)
#             & (Extract("year", Attendance.attendance_date) == filters.year)
#         )
#     ).run(as_dict=True)

#     return entry_exits[0]


# @frappe.whitelist()
# def get_attendance_years() -> str:
#     """Returns all the years for which attendance records exist"""
#     Attendance = frappe.qb.DocType("Attendance")
#     year_list = (
#         frappe.qb.from_(Attendance).select(Extract("year", Attendance.attendance_date).as_("year")).distinct()
#     ).run(as_dict=True)

#     if year_list:
#         year_list.sort(key=lambda d: d.year, reverse=True)
#     else:
#         year_list = [frappe._dict({"year": getdate().year})]

#     return "\n".join(cstr(entry.year) for entry in year_list)


# def get_chart_data(attendance_map: dict, filters: Filters) -> dict:
#     days = get_columns_for_days(filters)
#     labels = []
#     absent = []
#     present = []
#     leave = []

#     for day in days:
#         labels.append(day["label"])
#         total_absent_on_day = total_leaves_on_day = total_present_on_day = 0

#         for __, attendance_dict in attendance_map.items():
#             for __, attendance in attendance_dict.items():
#                 attendance_on_day = attendance.get(cint(day["fieldname"]))

#                 if attendance_on_day == "On Leave":
#                     # leave should be counted only once for the entire day
#                     total_leaves_on_day += 1
#                     break
#                 elif attendance_on_day == "Absent":
#                     total_absent_on_day += 1
#                 elif attendance_on_day in ["Present", "Work From Home"]:
#                     total_present_on_day += 1
#                 elif attendance_on_day == "Half Day":
#                     total_present_on_day += 0.5
#                     total_leaves_on_day += 0.5

#         absent.append(total_absent_on_day)
#         present.append(total_present_on_day)
#         leave.append(total_leaves_on_day)

#     return {
#         "data": {
#             "labels": labels,
#             "datasets": [
#                 {"name": _("Absent"), "values": absent},
#                 {"name": _("Present"), "values": present},
#                 {"name": _("Leave"), "values": leave},
#             ],
#         },
#         "type": "line",
#         "colors": ["red", "green", "blue"],
#     }


# def get_total_overtime(employee: str, filters: Filters) -> float:
#     from_date = f"{filters.year}-{filters.month}-01"
#     to_date = f"{filters.year}-{filters.month}-{get_total_days_in_month(filters)}"

#     total = frappe.db.sql("""
#         SELECT SUM(ot.over_time)
#         FROM `tabOvertime Import Item` ot
#         INNER JOIN `tabOverTime Import` oi
#             ON oi.name = ot.parent
#         WHERE ot.employee = %s
#           AND oi.docstatus = 1
#           AND ot.attendance_date BETWEEN %s AND %s
#     """, (employee, from_date, to_date))[0][0]

#     return total or 0



from calendar import monthrange
from itertools import groupby

import frappe
from frappe import _
from frappe.query_builder.functions import Count, Extract, Sum
from frappe.utils import cint, cstr, getdate
from frappe.utils.nestedset import get_descendants_of

Filters = frappe._dict

status_map = {
    "Present": "P",
    "Absent": "A",
    "Half Day": "HD",
    "Work From Home": "WFH",
    "Half Day/Other Half Absent": "HD/A",
    "Half Day/Other Half Present": "HD/P",
    "On Leave": "L",
    "Holiday": "H",
    "Weekly Off": "WO",
}

day_abbr = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
leave_type_abbr = {
    "Casual Leave": "CL",
    "Sick Leave": "SL",
    "Sick Leave - Zinc": "SLZ",
    "Privilege Leave": "PL",
    "Compensatory Off": "COM",
    "Maternity Leave":"ML",
    "Special Leave":"SPL",
    "Festival Leave":"FL",
    "Leave Without Pay":"LWP"
    # Add more leave types as needed
}


def execute(filters: Filters | None = None) -> tuple:
    filters = frappe._dict(filters or {})

    if not (filters.month and filters.year):
        frappe.throw(_("Please select month and year."))

    if not filters.company:
        frappe.throw(_("Please select company."))

    if filters.company:
        filters.companies = [filters.company]
        if filters.include_company_descendants:
            filters.companies.extend(get_descendants_of("Company", filters.company))

    attendance_map = get_attendance_map(filters)
    if not attendance_map:
        frappe.msgprint(_("No attendance records found."), alert=True, indicator="orange")
        return [], [], None, None

    columns = get_columns(filters)
    data = get_data(filters, attendance_map)

    if not data:
        frappe.msgprint(_("No attendance records found for this criteria."), alert=True, indicator="orange")
        return columns, [], None, None

    message = get_message() if not filters.summarized_view else ""
    chart = get_chart_data(attendance_map, filters)

    return columns, data, message, chart


def get_message() -> str:
    message = ""
    colors = ["green", "red", "orange", "green", "#318AD8","#878787",
        "#878787", "", ""]

    count = 0
    for status, abbr in status_map.items():
        color = colors[count] if count < len(colors) else "#878787"
        message += f"""
           <span style='border-left: 2px solid {color}; padding-right: 12px; padding-left: 5px; margin-right: 3px;'>
                {status} - {abbr}
            </span>
        """
        count += 1
    
    for leave_name, abbr in leave_type_abbr.items():
        color = colors[count] if count < len(color) else "#878787"
        message += f"""
           <span style='border-left: 2px solid {color}; padding-right: 12px; padding-left: 5px; margin-right: 3px;'>
                {leave_name} - {abbr}
            </span>
        """
    special_items = {"Paid Public Holiday": "PPH", "Holiday Present": "H/P"}
    for label, abbr in special_items.items():
        message += f"""
            <span style='border-left: 2px solid #8e44ad; padding-right: 12px; padding-left: 5px; margin-right: 3px;'>
                {label} - {abbr}
            </span>
        """
      

    return message


def get_columns(filters: Filters) -> list[dict]:
    columns = []

    if filters.group_by:
        options_mapping = {
            "Branch": "Branch",
            "Grade": "Employee Grade",
            "Department": "Department",
            "Designation": "Designation",
        }
        options = options_mapping.get(filters.group_by)
        columns.append(
            {
                "label": _(filters.group_by),
                "fieldname": frappe.scrub(filters.group_by),
                "fieldtype": "Link",
                "options": options,
                "width": 120,
            }
        )

    columns.extend(
        [
            {
                "label": _("Employee"),
                "fieldname": "employee",
                "fieldtype": "Link",
                "options": "Employee",
                "width": 135,
            },
            {"label": _("Employee Name"), "fieldname": "employee_name", "fieldtype": "Data", "width": 120},
            {"label": _("Staff/Worker"), "fieldname": "custom_staffworker", "fieldtype":"Data", "width": 110},
            {"label": _("Get Pass Number"), "fieldname": "attendance_device_id", "fieldtype": "Data", "width": 120},
        ]
    )

    if filters.summarized_view:
        columns.extend(
            [
                {
                    "label": _("Total Present"),
                    "fieldname": "total_present",
                    "fieldtype": "Float",
                    "width": 110,
                },
                {"label": _("Total Leaves"), "fieldname": "total_leaves", "fieldtype": "Float", "width": 110},
                {"label": _("Total Absent"), "fieldname": "total_absent", "fieldtype": "Float", "width": 110},
                {
                    "label": _("Total Holidays"),
                    "fieldname": "total_holidays",
                    "fieldtype": "Float",
                    "width": 120,
                },
                {
                    "label": _("Total Weekly Off"),
                    "fieldname": "total_weekly_off",
                    "fieldtype": "Float",
                    "width": 120,
                },
                
                {
                    "label": _("PPH"),
                    "fieldname": "pph",
                    "fieldtype": "Float",
                    "width": 120,
                    },
                
                {"label": "Total  Overtime", "fieldname": "total_overtime", "fieldtype": "Float", "width": 150},
               
                {
                    "label": _("Unmarked Days"),
                    "fieldname": "unmarked_days",
                    "fieldtype": "Float",
                    "width": 130,
                },
                {
                    "label": _("Additional OT"),
                    "fieldname": "additinal_ot",
                    "fieldtype": "Float",
                    "width": 140
                },
            ]
        )
        columns.extend(get_columns_for_leave_types())
        columns.extend(
            [
                {
                    "label": _("Total Late Entries"),
                    "fieldname": "total_late_entries",
                    "fieldtype": "Float",
                    "width": 140,
                },
                {
                    "label": _("Total Early Exits"),
                    "fieldname": "total_early_exits",
                    "fieldtype": "Float",
                    "width": 140,
                },
            ]
        )
    else:
        # columns.append({"label": _("Shift"), "fieldname": "shift", "fieldtype": "Data", "width": 120})
        columns.extend(get_columns_for_days(filters))
        columns.extend([
            
        {
        "label": _("Total Present"),
        "fieldname": "total_present",
        "fieldtype": "Float",
        "width": 120,
        },
        {"label": _("Total Leaves"), 
        "fieldname": "total_leaves",
        "fieldtype": "Float", 
        "width": 110
        },
        {
        "label": _("Total Absent"), 
        "fieldname": "total_absent", 
        "fieldtype": "Float",
        "width": 110
        },
        {
        "label": _("Total Holidays"),
        "fieldname": "total_holidays",
        "fieldtype": "Float",
        "width": 120
        },
        {
        "label": _("Total Weekly Off"),
        "fieldname": "total_weekly_off",
        "fieldtype": "Float",
        "width": 120
        },
        {
        "label": _("Unmarked Days"),
        "fieldname": "unmarked_days",
        "fieldtype": "Float",
        "width": 120
        },
        {
        "label": _("Leave Without Pay"),
        "fieldname": "leave_without_pay",
        "fieldtype": "Float",
        "width": 150
        },
        {
        "label": _("Privilege Leave"),
        "fieldname": "privilege_leave",
        "fieldtype": "Float",
        "width": 140
        },
        {
        "label": _("Sick Leave"),
        "fieldname": "sick_leave",
        "fieldtype": "Float",
        "width": 130
        },
        {
        "label": _("Casual Leave"),
        "fieldname": "casual_leave",
        "fieldtype": "Float",
        "width": 130
        },
        {
        "label": _("Special Leave"), 
        "fieldname": "special_leave",
        "fieldtype": "Float",
        "width": 130
        },
        {
        "label": _("Compensatory Off"),
        "fieldname": "compensatory_off",
        "fieldtype": "Float",
        "width": 160
        },
        {
        "label": _("Festival Leave"),
        "fieldname": "festival_leave",
        "fieldtype": "Float",
        "width": 130
        },
        {
        "label": _("Maternity Leave"),
        "fieldname": "maternity_leave",
        "fieldtype": "Float",
        "width": 140
        },
        {
        "label": _("PPH"),
        "fieldname": "pph",
        "fieldtype": "Float",
        "width": 120
        },
        {
        "label": _("Total Late Entries"),
        "fieldname": "total_late_entries",
        "fieldtype": "Float",
        "width": 160
        },
        {
        "label": _("Total Early Exits"),
        "fieldname": "total_early_exits",
        "fieldtype": "Int",
        "width": 160
        },
        {"label": "Total  Overtime", "fieldname": "total_overtime", "fieldtype": "Float", "width": 150},
        {
        "label": _("Additional OT"),
        "fieldname": "additinal_ot",
        "fieldtype": "Float",
        "width": 140
        },
       
        ])
    return columns


def get_columns_for_leave_types() -> list[dict]:
    leave_types = frappe.db.get_all("Leave Type", pluck="name")
    types = []
    for entry in leave_types:
        types.append({"label": entry, "fieldname": frappe.scrub(entry), "fieldtype": "Float", "width": 120})

    return types


def get_columns_for_days(filters: Filters) -> list[dict]:
    total_days = get_total_days_in_month(filters)
    days = []

    for day in range(1, total_days + 1):
        day = cstr(day)
        # forms the dates from selected year and month from filters
        date = f"{cstr(filters.year)}-{cstr(filters.month)}-{day}"
        # gets abbr from weekday number
        weekday = day_abbr[getdate(date).weekday()]
        # sets days as 1 Mon, 2 Tue, 3 Wed
        label = f"{day} {weekday}"
        days.append({"label": label, "fieldtype": "Data", "fieldname": day, "width": 65})

    return days


def get_total_days_in_month(filters: Filters) -> int:
    return monthrange(cint(filters.year), cint(filters.month))[1]

def get_effective_start_day(date_of_joining, filters: Filters) -> int:
    if not date_of_joining:
        return 1
    joining = getdate(date_of_joining)
    filter_year = cint(filters.year)
    filter_month = cint(filters.month)
    if joining.year > filter_year or (joining.year == filter_year and joining.month > filter_month):
        return get_total_days_in_month(filters) + 1
    if joining.year == filter_year and joining.month == filter_month:
        return joining.day
    return 1

def get_effective_end_day(relieving_date, filters: Filters) -> int:
    """Returns the last day (1-based) visible for an employee in the filter month.
    Days after this are blanked because the employee had already been relieved."""
    total_days = get_total_days_in_month(filters)
    if not relieving_date:
        return total_days

    relieving = getdate(relieving_date)
    filter_year = cint(filters.year)
    filter_month = cint(filters.month)

    # Relieved before this month — all days blank
    if relieving.year < filter_year or (relieving.year == filter_year and relieving.month < filter_month):
        return 0

    # Relieved during this month — days after relieving day are blank
    if relieving.year == filter_year and relieving.month == filter_month:
        return relieving.day

    # Relieved after this month — all days visible
    return total_days

def get_data(filters: Filters, attendance_map: dict) -> list[dict]:
    employee_details, group_by_param_values = get_employee_related_details(filters)
    holiday_map = get_holiday_map(filters)
    data = []

    if filters.group_by:
        group_by_column = frappe.scrub(filters.group_by)

        for value in group_by_param_values:
            if not value:
                continue

            records = get_rows(employee_details[value], filters, holiday_map, attendance_map)

            if records:
                data.append({group_by_column: value})
                data.extend(records)
    else:
        data = get_rows(employee_details, filters, holiday_map, attendance_map)

    return data


def get_attendance_map(filters: Filters) -> dict:
    """Returns a dictionary of employee wise attendance map as per shifts for all the days of the month like
    {
        'employee1': {
                'Morning Shift': {1: 'Present', 2: 'Absent', ...}
                'Evening Shift': {1: 'Absent', 2: 'Present', ...}
        },
        'employee2': {
                'Afternoon Shift': {1: 'Present', 2: 'Absent', ...}
                'Night Shift': {1: 'Absent', 2: 'Absent', ...}
        },
        'employee3': {
                None: {1: 'On Leave'}
        }
    }
    """
    attendance_list = get_attendance_records(filters)
    attendance_map = {}
    leave_map = {}

    for d in attendance_list:
        if d.status == "On Leave":
            leave_map.setdefault(d.employee, {}).setdefault(d.shift, []).append(d.day_of_month)
            continue

        if d.shift is None:
            d.shift = ""

        attendance_map.setdefault(d.employee, {}).setdefault(d.shift, {})
        attendance_map[d.employee][d.shift][d.day_of_month] = d.status

    # leave is applicable for the entire day so all shifts should show the leave entry
    for employee, leave_days in leave_map.items():
        for assigned_shift, days in leave_days.items():
            # no attendance records exist except leaves
            if employee not in attendance_map:
                attendance_map.setdefault(employee, {}).setdefault(assigned_shift, {})

            for day in days:
                for shift in attendance_map[employee].keys():
                    attendance_map[employee][shift][day] = "On Leave"

    return attendance_map


def get_attendance_records(filters: Filters) -> list[dict]:
    Attendance = frappe.qb.DocType("Attendance")
    status = (
        frappe.qb.terms.Case()
        .when(
            ((Attendance.status == "Half Day") & (Attendance.half_day_status == "Present")),
            "Half Day/Other Half Present",
        )
        .when(
            ((Attendance.status == "Half Day") & (Attendance.half_day_status == "Absent")),
            "Half Day/Other Half Absent",
        )
        .else_(Attendance.status)
    )
    query = (
        frappe.qb.from_(Attendance)
        .select(
            Attendance.employee,
            Extract("day", Attendance.attendance_date).as_("day_of_month"),
            (status).as_("status"),
            # Attendance.shift,  #
        )
        .where(
            (Attendance.docstatus == 1)
            & (Attendance.company.isin(filters.companies))
            & (Attendance.custom_branch == filters.branch)	
            & (Extract("month", Attendance.attendance_date) == filters.month)
            & (Extract("year", Attendance.attendance_date) == filters.year)
        )
    )

    if filters.employee:
        query = query.where(Attendance.employee == filters.employee)
    
    if filters.branch:
        query = query.where(Attendance.custom_branch == filters.branch)
    query = query.orderby(Attendance.employee, Attendance.attendance_date)

    return query.run(as_dict=1)


def get_employee_related_details(filters: Filters) -> tuple[dict, list]:
    """Returns
    1. nested dict for employee details
    2. list of values for the group by filter
    """
    Employee = frappe.qb.DocType("Employee")
    query = (
        frappe.qb.from_(Employee)
        .select(
            Employee.name,
            Employee.employee_name,
            Employee.designation,
            Employee.grade,
            Employee.department,
            Employee.branch,
            Employee.company,
            Employee.holiday_list,
            Employee.custom_staffworker,
            Employee.attendance_device_id,
            Employee.date_of_joining,
            Employee.relieving_date,

        )
        .where(Employee.company.isin(filters.companies))
    )

    if filters.employee:
        query = query.where(Employee.name == filters.employee)
    if filters.staff_worker:
        query = query.where(Employee.custom_staffworker == filters.staff_worker)
    group_by = filters.group_by
    if group_by:
        group_by = group_by.lower()
        query = query.orderby(group_by)

    employee_details = query.run(as_dict=True)

    group_by_param_values = []
    emp_map = {}

    if group_by:
        group_key = lambda d: "" if d[group_by] is None else d[group_by]  # noqa
        for parameter, employees in groupby(sorted(employee_details, key=group_key), key=group_key):
            group_by_param_values.append(parameter)
            emp_map.setdefault(parameter, frappe._dict())

            for emp in employees:
                emp_map[parameter][emp.name] = emp
    else:
        for emp in employee_details:
            emp_map[emp.name] = emp

    return emp_map, group_by_param_values


def get_holiday_map(filters: Filters) -> dict[str, list[dict]]:
    """
    Returns a dict of holidays falling in the filter month and year
    with list name as key and list of holidays as values like
    {
            'Holiday List 1': [
                    {'day_of_month': '0' , 'weekly_off': 1},
                    {'day_of_month': '1', 'weekly_off': 0}
            ],
            'Holiday List 2': [
                    {'day_of_month': '0' , 'weekly_off': 1},
                    {'day_of_month': '1', 'weekly_off': 0}
            ]
    }
    """
    # add default holiday list too
    holiday_lists = frappe.db.get_all("Holiday List", pluck="name")
    default_holiday_list = frappe.get_cached_value("Company", filters.company, "default_holiday_list")
    holiday_lists.append(default_holiday_list)

    holiday_map = frappe._dict()
    Holiday = frappe.qb.DocType("Holiday")

    for d in holiday_lists:
        if not d:
            continue

        holidays = (
            frappe.qb.from_(Holiday)
            .select(Extract("day", Holiday.holiday_date).as_("day_of_month"), Holiday.weekly_off)
            .where(
                (Holiday.parent == d)
                & (Extract("month", Holiday.holiday_date) == filters.month)
                & (Extract("year", Holiday.holiday_date) == filters.year)
            )
        ).run(as_dict=True)

        holiday_map.setdefault(d, holidays)

    return holiday_map


def get_rows(employee_details: dict, filters: Filters, holiday_map: dict, attendance_map: dict) -> list[dict]:
    records = []
    default_holiday_list = frappe.get_cached_value("Company", filters.company, "default_holiday_list")

    for employee, details in employee_details.items():
        emp_holiday_list = details.holiday_list or default_holiday_list
        holidays = holiday_map.get(emp_holiday_list)

        if filters.summarized_view:
            attendance = get_attendance_status_for_summarized_view(employee, filters, holidays)
            if not attendance:
                continue

            leave_summary = get_leave_summary(employee, filters)
            entry_exits_summary = get_entry_exits_summary(employee, filters)

            row = {"employee": employee, "employee_name": details.employee_name, "custom_staffworker": details.custom_staffworker, "attendance_device_id": details.attendance_device_id,}
            set_defaults_for_summarized_view(filters, row)
            row.update(attendance)
            row.update(leave_summary)
            row.update(entry_exits_summary)
            row["total_overtime"] = get_total_overtime(employee, filters)
            row["additinal_ot"] = get_additional_ot(employee, filters)
          

            records.append(row)
        else:
            employee_attendance = attendance_map.get(employee)
            if not employee_attendance:
                continue

            attendance_for_employee = get_attendance_status_for_detailed_view(
                employee, filters, employee_attendance, holidays, details.date_of_joining, details.relieving_date
            )
            # set employee details in the first row
            attendance_for_employee[0].update({"employee": employee, "employee_name": details.employee_name, "custom_staffworker": details.custom_staffworker, "attendance_device_id": details.attendance_device_id,})

            records.extend(attendance_for_employee)

    return records


def set_defaults_for_summarized_view(filters, row):
    for entry in get_columns(filters):
        if entry.get("fieldtype") == "Float":
            row[entry.get("fieldname")] = 0.0


def get_attendance_status_for_summarized_view(employee: str, filters: Filters, holidays: list) -> dict:
    # Sync with detailed view logic
    detailed_data = get_attendance_status_for_detailed_view(employee, filters, {"": {}}, holidays)
    if not detailed_data: return {}
    res = detailed_data[0]
    return {
        "total_present": res.get("total_present", 0),
        "total_leaves": res.get("total_leaves", 0),
        "total_absent": res.get("total_absent", 0),
        "total_holidays": res.get("total_holidays", 0),
        "total_weekly_off": res.get("total_weekly_off", 0),
        "pph": res.get("pph", 0),
        "unmarked_days": res.get("unmarked_days", 0),
    }
def get_attendance_summary_and_days(employee: str, filters: Filters) -> tuple[dict, list]:
    Attendance = frappe.qb.DocType("Attendance")

    present_case = (
        frappe.qb.terms.Case()
        .when(((Attendance.status == "Present") | (Attendance.status == "Work From Home")), 1)
        .else_(0)
    )
    sum_present = Sum(present_case).as_("total_present")

    absent_case = frappe.qb.terms.Case().when(Attendance.status == "Absent", 1).else_(0)
    sum_absent = Sum(absent_case).as_("total_absent")

    leave_case = frappe.qb.terms.Case().when(Attendance.status == "On Leave", 1).else_(0)
    sum_leave = Sum(leave_case).as_("total_leaves")

    half_day_case = frappe.qb.terms.Case().when(Attendance.status == "Half Day", 0.5).else_(0)
    sum_half_day = Sum(half_day_case).as_("total_half_days")

    summary = (
        frappe.qb.from_(Attendance)
        .select(
            sum_present,
            sum_absent,
            sum_leave,
            sum_half_day,
        )
        .where(
            (Attendance.docstatus == 1)
            & (Attendance.employee == employee)
            & (Attendance.company.isin(filters.companies))
            & (Attendance.custom_branch == filters.branch)	
            & (Extract("month", Attendance.attendance_date) == filters.month)
            & (Extract("year", Attendance.attendance_date) == filters.year)
        )
    ).run(as_dict=True)

    days = (
        frappe.qb.from_(Attendance)
        .select(Extract("day", Attendance.attendance_date).as_("day_of_month"))
        .distinct()
        .where(
            (Attendance.docstatus == 1)
            & (Attendance.employee == employee)
            & (Attendance.company.isin(filters.companies))
            & (Attendance.custom_branch == filters.branch)	
            & (Extract("month", Attendance.attendance_date) == filters.month)
            & (Extract("year", Attendance.attendance_date) == filters.year)
        )
    ).run(pluck=True)

    return summary[0], days



def get_attendance_status_for_detailed_view(
    employee: str, filters: Filters, employee_attendance: dict, holidays: list, date_of_joining=None, relieving_date=None

) -> list[dict]:

    total_days = get_total_days_in_month(filters)
    effective_start = get_effective_start_day(date_of_joining, filters)
    effective_end = get_effective_end_day(relieving_date, filters)
    attendance_values = []
    
   
    leave_day_map = {}
    leave_apps = frappe.db.get_all(
        "Leave Application",
        filters={
            "employee": employee, "docstatus": 1, "status": "Approved",
            "from_date": ["<=", f"{filters.year}-{filters.month}-{total_days}"],
            "to_date": [">=", f"{filters.year}-{filters.month}-01"]
        },
        fields=["from_date", "to_date", "leave_type"]
    )
    
    for app in leave_apps:
        abbr = "SLZ" if app.leave_type == "Sick Leave - Zinc" else leave_type_abbr.get(app.leave_type, "L")
        curr = getdate(app.from_date)
        while curr <= getdate(app.to_date):
            if curr.month == cint(filters.month):
                if abbr not in leave_day_map.get(curr.day, []):
                    leave_day_map.setdefault(curr.day, []).append(abbr)
            curr = frappe.utils.add_days(curr, 1)

    att_info = frappe.db.get_all(
        "Attendance",
        filters={
            "employee": employee, "docstatus": 1,
            "attendance_date": ["between", [f"{filters.year}-{filters.month}-01", f"{filters.year}-{filters.month}-{total_days}"]]
        },
        fields=["attendance_date", "status", "half_day_status", "leave_type", "leave_application", "in_time", "out_time", "attendance_request"]
    )
    att_map = {getdate(d.attendance_date).day: d for d in att_info}

    # 3. Main Calculation Loop
    for shift, status_dict in employee_attendance.items():
        row = {}
        t_p = t_a = t_l = t_h = t_wo = t_un = t_pph = 0.0

        
        for day in range(1, total_days + 1):
            if day < effective_start or day > effective_end:
                row[cstr(day)] = ""
                continue
            day_att = att_map.get(day)
            h_status = get_holiday_status(day, holidays)
            day_leaves = list(set(leave_day_map.get(day, []))) # CL/SL list
            
            
            
            # abbr = ""
            # if h_status == "Holiday":
            #     abbr = "H/P" if day_att and day_att.status in ["Present", "Half Day"] else "H"
            #     if abbr == "H": t_h += 1
            #     else: t_pph += 1
            # elif h_status == "Weekly Off":
            #     abbr = "WO"; t_wo += 1

            # elif day_att:
            #     m_leave_type = day_att.leave_type
            #     m_leave_abbr = "SLZ" if m_leave_type == "Sick Leave - Zinc" else leave_type_abbr.get(m_leave_type, "L") if m_leave_type else ""
                
            #     target_col = "Sick Leave" if m_leave_type == "Sick Leave - Zinc" else m_leave_type
            #     lt_key = frappe.scrub(target_col) if target_col else None
                
            #     # Punch check 
            #     has_punch = True if day_att.in_time or day_att.out_time else False

            #     if day_att.status == "Half Day":
                   
                    
                   
            #         if len(day_leaves) > 1:
            #             abbr = f"HD/{'/'.join(day_leaves)}"
            #             t_l += 1.0
            #             if lt_key: row[lt_key] = row.get(lt_key, 0.0) + 1.0
                    
                    
            #         elif has_punch:
            #             if day_att.half_day_status == "Present":
            #                 t_p += 0.5
            #                 if day_att.leave_application or m_leave_abbr:
            #                     abbr = f"HD/P/{m_leave_abbr}"
            #                     t_l += 0.5
            #                     if lt_key: row[lt_key] = row.get(lt_key, 0.0) + 0.5
            #                 else:
                               
            #                     abbr = "HD/P/A"
            #                     t_a += 0.5
            #             else:
                            
            #                 abbr = f"HD/{m_leave_abbr}/A" if (day_att.leave_application or m_leave_abbr) else "A"
            #                 t_a += 0.5 if (day_att.leave_application or m_leave_abbr) else 1.0
            #                 t_l += 0.5 if (day_att.leave_application or m_leave_abbr) else 0.0
            #                 if lt_key and (day_att.leave_application or m_leave_abbr): 
            #                     row[lt_key] = row.get(lt_key, 0.0) + 0.5

            #         else:
                        
            #             status_part = "P" if day_att.half_day_status == "Present" else "A"
            #             if m_leave_abbr:
            #                 abbr = f"HD/{m_leave_abbr}/{status_part}"
            #                 t_l += 0.5
            #                 if lt_key: row[lt_key] = row.get(lt_key, 0.0) + 0.5
            #             else:
            #                 abbr = f"HD/{status_part}"
                        
            #             if status_part == "P": t_p += 0.5
            #             else: t_a += 0.5
                    

            #     elif day_att.status == "On Leave":
            #         abbr = "/".join(day_leaves) if day_leaves else (m_leave_abbr or "L")
            #         t_l += 1.0
            #         if lt_key: row[lt_key] = row.get(lt_key, 0.0) + 1.0

            #     else:
            #         abbr = status_map.get(day_att.status, "")
            #         if abbr in ("P", "WFH"): t_p += 1.0
                    
            #         elif abbr == "WO": t_wo += 1.0
                    
            #         elif abbr == "A": t_a += 1.0
            # else:
            #     t_un += 1
            
            abbr = ""
            if day_att:
                
                m_leave_type = day_att.leave_type
                m_leave_abbr = "SLZ" if m_leave_type == "Sick Leave - Zinc" else leave_type_abbr.get(m_leave_type, "L") if m_leave_type else ""
                
                target_col = "Sick Leave" if m_leave_type == "Sick Leave - Zinc" else m_leave_type
                lt_key = frappe.scrub(target_col) if target_col else None
                
                # Punch check
                has_punch = True if day_att.in_time or day_att.out_time else False
                
                #######################################################################
                # fist check if it's holiday and present/half day, then mark as H/P and count in PPH
                if h_status == "Holiday":
                    if day_att.status == "Present":
                        abbr = "H/P"
                        t_pph += 1
                        t_p += 1
                    elif day_att.status == "Half Day":
                        if day_att.half_day_status == "Present":
                            abbr = "H/P"
                            t_pph += 0.5
                            t_p += 0.5
                        else:
                            abbr = "H/P/A"
                            t_pph += 0.5
                            t_p += 0.5
                            t_a += 0.5
                    elif day_att.status == "Weekly Off":
                        abbr = "WO"
                        t_wo  += 1
                    elif day_att.status == "On Leave":
                        abbr = "/".join(day_leaves) if day_leaves else (m_leave_abbr or "L")
                        if m_leave_type != "Leave Without Pay":
                            t_l += 1.0
                            if lt_key:
                                row[lt_key] = row.get(lt_key, 0.0) + 1.0
                    elif day_att.status == "Absent":
                        abbr = "A"
                        t_a += 1.0
                    else:
                        abbr = "H"
                        t_h += 1.0
                #####################################################################################
                
                # check attendance from attendance request
                elif day_att.status == "Half Day":
                    if day_att.attendance_request:
                        t_p += 0.5
                        if day_att.leave_application or m_leave_abbr:
                            abbr = f"HD/P/{m_leave_abbr}"
                            if m_leave_type != "Leave Without Pay":
                                t_l += 0.5
                            if lt_key:
                                row[lt_key] = row.get(lt_key, 0.0) + 0.5
                                
                        else:
                            abbr = "HD/P/A"
                            t_a += 0.5
                    else:
                        if len(day_leaves) > 1:
                            abbr = f"HD/{'/'.join(day_leaves)}"
                            t_l += 1.0
                            if lt_key: 
                                row[lt_key] = row.get(lt_key, 0.0) + 1.0
                ###########################################################################        
                        elif has_punch:
                            if day_att.half_day_status == "Present":
                                t_p += 0.5
                                if day_att.leave_application or m_leave_abbr:
                                    abbr = f"HD/P/{m_leave_abbr}"
                                    if m_leave_type != "Leave Without Pay":
                                        t_l += 0.5
                                    if lt_key: 
                                        row[lt_key] = row.get(lt_key, 0.0) + 0.5
                                else:
                                    abbr = "HD/P/A"
                                    t_a += 0.5
                            else:
                                if day_att.leave_application or m_leave_abbr:
                                    abbr = f"HD/{m_leave_abbr}/A"
                                    t_a += 0.5
                                    if m_leave_type != "Leave Without Pay":
                                        
                                        t_l += 0.5
                                    if lt_key: 
                                        row[lt_key] = row.get(lt_key, 0.0) + 0.5
                                else:
                                    abbr = "HD/P/A"
                                    t_a += 0.5
                                    t_p += 0.5
                        else:
                            status_part = "P" if day_att.half_day_status == "Present" else "A"
                            if m_leave_abbr:
                                abbr = f"HD/{m_leave_abbr}/{status_part}"
                                t_l += 0.5
                                if lt_key:
                                    row[lt_key] = row.get(lt_key, 0.0) + 0.5
                            else:
                                abbr = f"HD/{status_part}"
                            if status_part == "P":
                                t_p += 0.5
                            else:
                                t_a += 0.5
                                t_p += 0.5
                elif day_att.status == "On Leave":
                    abbr = "/".join(day_leaves) if day_leaves else (m_leave_abbr or "L")
                    if m_leave_type != "Leave Without Pay":
                        t_l += 1.0
                    if lt_key:
                        row[lt_key] = row.get(lt_key, 0.0) + 1.0
                else:
                    abbr = status_map.get(day_att.status, "")
                    if abbr in ("P", "WFH"):
                        t_p += 1.0
                    elif abbr == "WO":
                        t_wo += 1.0
                    elif abbr == "A":
                        t_a += 1.0
                    elif abbr == "H":
                        t_h += 1.0

            else:
                if h_status == "Holiday":
                    
                    abbr = "H"
                    t_h += 1
                    # abbr = "H/P" if day_att and day_att.status in ["Present", "Half Day"] else "H"
                    # if abbr == "H": 
                        # t_h += 1
                    # else: 
                    #     t_pph += 1
                    
                elif h_status == "Weekly Off":
                    abbr = "WO"
                    t_wo += 1
                else:
                    abbr = "A"
                    t_a += 1


            # HTML Formatting
            if "HD" in abbr:
                row[cstr(day)] = f"<span style='color:orange; font-weight:bold'>{abbr}</span>"
            elif abbr == "A":
                row[cstr(day)] = f"<span style='color:red; font-weight:bold'>{abbr}</span>"
            elif abbr in ["P", "WFH"]:
                row[cstr(day)] = f"<span style='color:green; font-weight:bold'>{abbr}</span>"
            else:
                row[cstr(day)] = abbr

        row.update({
            "total_present": t_p, 
            "total_leaves": t_l, 
            "total_absent": t_a,
            "total_holidays": t_h, 
            "total_weekly_off": t_wo,
            "pph": t_pph, 
            "unmarked_days": t_un ,
            "total_overtime": get_total_overtime(employee, filters),
            "additinal_ot": get_additional_ot(employee, filters),    #new 
            "total_late_entries": get_entry_exits_summary(employee, filters).get("total_late_entries", 0),
            "total_early_exits": get_entry_exits_summary(employee, filters).get("total_early_exits", 0),
        })
        attendance_values.append(row)

    return attendance_values

def get_holiday_status(day: int, holidays: list) -> str:
    """Returns holiday status for a given day.
    
    Args:
        day: Day of month (1-31)
        holidays: List of holiday dicts containing day_of_month and weekly_off
        
    Returns:
        "Weekly Off" if it's a weekly off day
        "Holiday" if it's a regular holiday
        None if it's not a holiday
    """
    if not holidays:
        return None
        
    for holiday in holidays:
        if day == holiday.get("day_of_month"):
            if holiday.get("weekly_off"):
                return "Weekly Off"
            return "Holiday"
    return None

def get_leave_summary(employee: str, filters: Filters) -> dict:
    leaves = {}
    total_days = monthrange(cint(filters.year), cint(filters.month))[1]
    from_date = f"{filters.year}-{filters.month}-01"
    to_date = f"{filters.year}-{filters.month}-{total_days}"

   
    leave_apps = frappe.db.get_all(
        "Leave Application",
        filters={
            "employee": employee,
            "docstatus": 1,
            "status": "Approved",
            "from_date": ["<=", to_date],
            "to_date": [">=", from_date]
        },
        fields=["leave_type", "total_leave_days"]
    )

    for app in leave_apps:
        l_type_name = "Sick Leave" if app.leave_type == "Sick Leave - Zinc" else app.leave_type
        lt_key = frappe.scrub(l_type_name)
        # total_leave_days field already handling half day (0.5) logic
        leaves[lt_key] = leaves.get(lt_key, 0.0) + float(app.total_leave_days)

    att_records = frappe.db.get_all("Attendance", 
        filters={
            "employee": employee, 
            "docstatus": 1, 
            "attendance_date": ["between", [from_date, to_date]], 
            "status": ["in", ["On Leave", "Half Day"]],
            "leave_application": ["is", "not set"] # Sirf manual waali
        },
        fields=["status", "leave_type"])

    for d in att_records:
        if d.leave_type:
            current_type = "Sick Leave" if d.leave_type == "Sick Leave - Zinc" else d.leave_type
            lt = frappe.scrub(current_type)
            val = 0.5 if d.status == "Half Day" else 1.0
            leaves[lt] = leaves.get(lt, 0.0) + val
    
    return leaves

def get_entry_exits_summary(employee: str, filters: Filters) -> dict[str, float]:
    """Returns total late entries and total early exits for employee like:
    {'total_late_entries': 5, 'total_early_exits': 2}
    """
    Attendance = frappe.qb.DocType("Attendance")

    late_entry_case = frappe.qb.terms.Case().when(Attendance.late_entry == "1", "1")
    count_late_entries = Count(late_entry_case).as_("total_late_entries")

    early_exit_case = frappe.qb.terms.Case().when(Attendance.early_exit == "1", "1")
    count_early_exits = Count(early_exit_case).as_("total_early_exits")

    entry_exits = (
        frappe.qb.from_(Attendance)
        .select(count_late_entries, count_early_exits)
        .where(
            (Attendance.docstatus == 1)
            & (Attendance.employee == employee)
            & (Attendance.company.isin(filters.companies))
            & (Attendance.custom_branch == filters.branch)	
            & (Extract("month", Attendance.attendance_date) == filters.month)
            & (Extract("year", Attendance.attendance_date) == filters.year)
        )
    ).run(as_dict=True)

    return entry_exits[0]


@frappe.whitelist()
def get_attendance_years() -> str:
    """Returns all the years for which attendance records exist"""
    Attendance = frappe.qb.DocType("Attendance")
    year_list = (
        frappe.qb.from_(Attendance).select(Extract("year", Attendance.attendance_date).as_("year")).distinct()
    ).run(as_dict=True)

    if year_list:
        year_list.sort(key=lambda d: d.year, reverse=True)
    else:
        year_list = [frappe._dict({"year": getdate().year})]

    return "\n".join(cstr(entry.year) for entry in year_list)


def get_chart_data(attendance_map: dict, filters: Filters) -> dict:
    days = get_columns_for_days(filters)
    labels = []
    absent = []
    present = []
    leave = []

    for day in days:
        labels.append(day["label"])
        total_absent_on_day = total_leaves_on_day = total_present_on_day = 0

        for __, attendance_dict in attendance_map.items():
            for __, attendance in attendance_dict.items():
                attendance_on_day = attendance.get(cint(day["fieldname"]))

                if attendance_on_day == "On Leave":
                    # leave should be counted only once for the entire day
                    total_leaves_on_day += 1
                    break
                elif attendance_on_day == "Absent":
                    total_absent_on_day += 1
                elif attendance_on_day in ["Present", "Work From Home"]:
                    total_present_on_day += 1
                elif attendance_on_day == "Half Day":
                    total_present_on_day += 0.5
                    total_leaves_on_day += 0.5

        absent.append(total_absent_on_day)
        present.append(total_present_on_day)
        leave.append(total_leaves_on_day)

    return {
        "data": {
            "labels": labels,
            "datasets": [
                {"name": _("Absent"), "values": absent},
                {"name": _("Present"), "values": present},
                {"name": _("Leave"), "values": leave},
            ],
        },
        "type": "line",
        "colors": ["red", "green", "blue"],
    }


def get_total_overtime(employee: str, filters: Filters) -> float:
    from_date = f"{filters.year}-{filters.month}-01"
    to_date = f"{filters.year}-{filters.month}-{get_total_days_in_month(filters)}"

    total = frappe.db.sql("""
        SELECT SUM(ot.over_time)
        FROM `tabOvertime Import Item` ot
        INNER JOIN `tabOverTime Import` oi
            ON oi.name = ot.parent
        WHERE ot.employee = %s
          AND oi.docstatus = 1
          AND ot.attendance_date BETWEEN %s AND %s
    """, (employee, from_date, to_date))[0][0]

    return total or 0


def get_additional_ot(employee, filters):
    month_date = f"{filters.year}-{filters.month}-01"

    total = frappe.db.sql("""
        SELECT SUM(additinal_ot)
        FROM `tabot adjustment item`
        WHERE employee = %s
          AND parent IN (
              SELECT name
              FROM `tabOT Adjustment`
              WHERE docstatus = 1
                AND month = %s
          )
    """, (employee, month_date))

    return total[0][0] if total and total[0][0] else 0