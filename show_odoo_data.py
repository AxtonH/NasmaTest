#!/usr/bin/env python3
"""
Script to show what data we're retrieving from Odoo
"""

import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), 'backend'))

from backend.services.odoo_service import OdooService
from backend.services.employee_service import EmployeeService

def show_odoo_data():
    print("=== Odoo Data Retrieval Demo ===")
    print()
    
    # Initialize services
    odoo_service = OdooService()
    employee_service = EmployeeService(odoo_service)
    
    print("🔧 **Standard Employee Fields We're Fetching:**")
    print("=" * 50)
    for i, field in enumerate(employee_service.employee_fields, 1):
        print(f"{i:2d}. {field}")
    
    print()
    print("🎯 **Custom Fields We're Testing:**")
    print("=" * 50)
    for i, field in enumerate(employee_service.custom_fields, 1):
        print(f"{i:2d}. {field}")
    
    print()
    print("🔗 **Related Data We're Expanding:**")
    print("=" * 50)
    for field, sub_fields in employee_service.related_fields.items():
        print(f"• {field}: {', '.join(sub_fields)}")
    
    print()
    print("📊 **Field Categories:**")
    print("=" * 50)
    
    # Categorize fields
    basic_info = ['name', 'job_title', 'work_email', 'work_phone', 'mobile_phone', 'identification_id']
    personal_info = ['gender', 'birthday', 'marital', 'tz']
    work_info = ['department_id', 'work_location_id', 'parent_id', 'coach_id', 'job_id', 'company_id']
    system_info = ['address_id', 'resource_calendar_id', 'category_ids', 'planning_role_ids']
    
    print("👤 **Basic Information:**")
    for field in basic_info:
        if field in employee_service.employee_fields:
            print(f"   ✓ {field}")
    
    print("\n🏠 **Personal Information:**")
    for field in personal_info:
        if field in employee_service.employee_fields:
            print(f"   ✓ {field}")
    
    print("\n💼 **Work Information:**")
    for field in work_info:
        if field in employee_service.employee_fields:
            print(f"   ✓ {field}")
    
    print("\n⚙️ **System Information:**")
    for field in system_info:
        if field in employee_service.employee_fields:
            print(f"   ✓ {field}")
    
    print("\n🎨 **Custom Fields (if they exist):**")
    for field in employee_service.custom_fields:
        print(f"   ? {field} (will be tested)")
    
    print()
    print("📝 **Example of What Nasma Will See:**")
    print("=" * 50)
    print("""
Your Name: [Employee Name]
Your Job Title: [Job Title]
Your Work Email: [work@email.com]
Your Work Phone: [Phone Number]
Your Mobile Phone: [Mobile Number]
Your Employee ID: [EMP001]
Your Department: [Department Name]
Your Manager: [Manager Name] ([Manager Job Title])
Your Company: [Company Name]
Your Address: [Street, City, State, Country, ZIP]
Your Gender: [Gender]
Your Birthday: [Birth Date]
Your Marital Status: [Status]
Your Timezone: [Timezone]
Your Arabic Name: [Arabic Name] (if custom field exists)
Your Joining Date: [Joining Date] (if custom field exists)
Your Contract End Date: [End Date] (if custom field exists)
    """)
    
    print("🔍 **How It Works:**")
    print("=" * 50)
    print("1. System tests which fields exist in your Odoo instance")
    print("2. Only requests fields that actually exist")
    print("3. Fetches your employee data using those fields")
    print("4. Expands related data (department, manager, company, etc.)")
    print("5. Formats the data for Nasma to understand")
    print("6. Nasma uses this data to answer your questions personally")
    
    print()
    print("🚀 **To see your actual data:**")
    print("=" * 50)
    print("1. Log into the web interface")
    print("2. Check the debug sidebar")
    print("3. Ask Nasma: 'Who am I?' or 'What's my department?'")

if __name__ == "__main__":
    show_odoo_data()


