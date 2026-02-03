frappe.query_reports["Overtime Report"] = {
    "filters": [
        {
            "fieldname": "employee",
            "label": __("Employee"),
            "fieldtype": "Link",
            "options": "Employee",
            "reqd": 0
        },
        {
            "fieldname": "branch",
            "label": __("Branch"),
            "fieldtype": "Link",
            "options": "Branch",
            "reqd": 0
        },
        {
            "fieldname": "attendance_date",
            "label": __("Date"),
            "fieldtype": "Date",
            "reqd": 0
        },
        {
            "fieldname": "month",
            "label": __("Month"),
            "fieldtype": "Select",
            "options": [" ",
                "January", "February", "March", "April", 
                "May", "June", "July", "August",
                "September", "October", "November", "December"
            ],
            "reqd": 0
        }
    ]
};
