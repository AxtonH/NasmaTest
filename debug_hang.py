#!/usr/bin/env python3
"""
Debug script to find what's causing the hang
"""

import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), 'backend'))

print("Step 1: Testing imports...")
try:
    from backend.config.settings import Config
    print("✅ Config imported")
except Exception as e:
    print(f"❌ Config import failed: {e}")
    exit(1)

try:
    from backend.services.session_manager import SessionManager
    print("✅ SessionManager imported")
except Exception as e:
    print(f"❌ SessionManager import failed: {e}")
    exit(1)

try:
    from backend.services.odoo_service import OdooService
    print("✅ OdooService imported")
except Exception as e:
    print(f"❌ OdooService import failed: {e}")
    exit(1)

try:
    from backend.services.employee_service import EmployeeService
    print("✅ EmployeeService imported")
except Exception as e:
    print(f"❌ EmployeeService import failed: {e}")
    exit(1)

try:
    from backend.services.timeoff_service import TimeOffService
    print("✅ TimeOffService imported")
except Exception as e:
    print(f"❌ TimeOffService import failed: {e}")
    exit(1)

print("\nStep 2: Testing service creation...")
try:
    odoo_service = OdooService()
    print("✅ OdooService created")
except Exception as e:
    print(f"❌ OdooService creation failed: {e}")
    exit(1)

try:
    employee_service = EmployeeService(odoo_service)
    print("✅ EmployeeService created")
except Exception as e:
    print(f"❌ EmployeeService creation failed: {e}")
    exit(1)

try:
    timeoff_service = TimeOffService(odoo_service, employee_service)
    print("✅ TimeOffService created")
except Exception as e:
    print(f"❌ TimeOffService creation failed: {e}")
    exit(1)

try:
    session_manager = SessionManager()
    print("✅ SessionManager created")
except Exception as e:
    print(f"❌ SessionManager creation failed: {e}")
    exit(1)

print("\nStep 3: Testing ChatGPT service...")
try:
    from backend.services.chatgpt_service import ChatGPTService
    print("✅ ChatGPTService imported")
except Exception as e:
    print(f"❌ ChatGPTService import failed: {e}")
    exit(1)

print("Creating ChatGPTService (this might hang)...")
try:
    chatgpt_service = ChatGPTService()
    print("✅ ChatGPTService created")
except Exception as e:
    print(f"❌ ChatGPTService creation failed: {e}")
    exit(1)

print("\nStep 4: Testing time-off detection...")
try:
    is_timeoff, confidence, extracted = timeoff_service.detect_timeoff_intent("i want time off")
    print(f"✅ Time-off detection: {is_timeoff}, confidence: {confidence}")
except Exception as e:
    print(f"❌ Time-off detection failed: {e}")
    exit(1)

print("\n✅ All tests passed! The issue might be elsewhere.")




