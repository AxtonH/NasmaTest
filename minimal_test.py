#!/usr/bin/env python3
"""
Minimal test to isolate the hanging issue
"""

import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), 'backend'))

print("Testing minimal imports...")

# Test 1: Basic imports
try:
    from backend.config.settings import Config
    print("✅ Config imported")
except Exception as e:
    print(f"❌ Config failed: {e}")
    exit(1)

# Test 2: Session manager only
try:
    from backend.services.session_manager import SessionManager
    sm = SessionManager()
    print("✅ SessionManager created")
except Exception as e:
    print(f"❌ SessionManager failed: {e}")
    exit(1)

# Test 3: Time-off detection only (without Odoo)
try:
    from backend.services.timeoff_service import TimeOffService
    # Create with None services to avoid Odoo
    tos = TimeOffService(None, None)
    is_timeoff, confidence, extracted = tos.detect_timeoff_intent("i want time off")
    print(f"✅ Time-off detection: {is_timeoff}, confidence: {confidence}")
except Exception as e:
    print(f"❌ Time-off detection failed: {e}")
    exit(1)

print("✅ All minimal tests passed!")




