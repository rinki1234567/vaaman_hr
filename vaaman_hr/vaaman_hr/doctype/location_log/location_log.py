import frappe
import json
from frappe.model.document import Document

class LocationLog(Document):
    def before_save(self):
        if self.latitude and self.longitude:
            try:
                geo_data = {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                           "properties": {"point_type": "marker"},
                            "geometry": {
                                "type": "Point",
                                "coordinates": [
                                    float(self.longitude), 
                                    float(self.latitude)
                                ]
                            }
                        }
                    ]
                }
                
                self.map = json.dumps(geo_data)
                
            except Exception as e:
                frappe.log_error("Map Generation Error", str(e))