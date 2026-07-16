# Copyright (c) 2026, Pratul Tripathi and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import getdate


class EmployeeWeeklyOffMaster(Document):

    def validate(self):
        self.validate_dates()
        self.validate_active_record()
        self.validate_overlap()

    def validate_dates(self):
        """To Date cannot be before From Date"""

        if self.to_date and getdate(self.to_date) < getdate(self.from_date):
            frappe.throw("To Date cannot be before From Date.")

    def validate_active_record(self):
        """Only one active record (To Date blank) is allowed"""

        if not self.to_date:

            active_record = frappe.db.exists(
                "Employee Weekly Off Master",
                {
                    "employee": self.employee,
                    "company": self.company,
                    "branch": self.branch,
                    "to_date": ["is", "not set"],
                    "name": ["!=", self.name],
                },
            )

            if active_record:
                frappe.throw(
                    "Active Weekly Off already exists for this Employee, Company and Branch."
                )

    def validate_overlap(self):
        """Date ranges should not overlap"""

        records = frappe.get_all(
            "Employee Weekly Off Master",
            filters={
                "employee": self.employee,
                "company": self.company,
                "branch": self.branch,
                "name": ["!=", self.name],
            },
            fields=["name", "from_date", "to_date"],
        )

        current_from = getdate(self.from_date)

        current_to = (
            getdate(self.to_date)
            if self.to_date
            else getdate("2099-12-31")
        )

        for row in records:

            existing_from = getdate(row.from_date)

            existing_to = (
                getdate(row.to_date)
                if row.to_date
                else getdate("2099-12-31")
            )
            if current_from <= existing_to and current_to >= existing_from:
                frappe.throw(
                    f"Weekly Off already exists for this period. Existing Record: {row.name}"
                )