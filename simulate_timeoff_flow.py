import sys
import os
from typing import Tuple, Any

# Ensure backend package is importable
sys.path.append(os.path.join(os.path.dirname(__file__), 'backend'))

from backend.services.session_manager import SessionManager
from backend.services.timeoff_service import TimeOffService
from backend.services.chatgpt_service import ChatGPTService


def _safe_text(s: str) -> str:
    try:
        enc = sys.stdout.encoding or 'utf-8'
        return s.encode(enc, errors='replace').decode(enc, errors='replace')
    except Exception:
        return s


class StubTimeOffService(TimeOffService):
    def __init__(self):
        # Initialize with None dependencies; we won't hit Odoo
        super().__init__(odoo_service=None, employee_service=None)

    # Override network-dependent methods
    def get_leave_types(self) -> Tuple[bool, Any]:
        # Provide core types expected by the flow
        return True, [
            {'id': 10, 'name': 'Annual Leave', 'active': True},
            {'id': 11, 'name': 'Sick Leave', 'active': True},
            {'id': 13, 'name': 'Unpaid Leave', 'active': True},
            {'id': 12, 'name': 'Custom Hours', 'active': True},
        ]

    def submit_leave_request(self, employee_id: int, leave_type_id: int,
                             start_date: str, end_date: str, description: str = None,
                             extra_fields: dict = None, supporting_attachments=None):
        # Simulate success; echo back inputs for verification
        return True, {
            'leave_id': 999,
            'message': f"Leave request submitted (emp={employee_id}, type={leave_type_id}, {start_date}..{end_date})"
        }


def run_flow_sequence():
    sm = SessionManager()
    tos = StubTimeOffService()

    chat = ChatGPTService()
    chat.set_services(timeoff_service=tos, session_manager=sm)

    employee = {'id': 123, 'name': 'Test User'}

    def step(msg: str, thread: str):
        resp = chat.get_response(msg, thread_id=thread, employee_data=employee)
        print(_safe_text(f"U> {msg}"))
        if isinstance(resp, dict):
            print(_safe_text(f"A> {resp.get('message', '')}"))
            print()
        else:
            print(_safe_text(f"A> {resp}"))
            print()
        return resp

    print("=== Flow 1: Annual Leave ===")
    tid1 = 'thread-test-1'
    step("I want to take time off", tid1)
    step("1", tid1)  # choose Annual Leave from buttons
    step("29/10/2025 to 30/10/2025", tid1)  # dates
    step("yes", tid1)  # confirm

    print("=== Flow 2: Sick Leave (new flow) ===")
    tid2 = 'thread-test-2'
    step("I need sick leave", tid2)
    step("2", tid2)  # choose Sick Leave
    step("Full Days", tid2)  # choose mode
    step("05/11/2025 to 06/11/2025", tid2)
    step("yes", tid2)

    print("=== Flow 3: Unpaid Leave ===")
    tid3 = 'thread-test-3'
    step("I need unpaid leave", tid3)
    step("3", tid3)  # choose Unpaid Leave
    step("Full Days", tid3)  # choose mode
    step("10/12/2025 to 12/12/2025", tid3)
    step("yes", tid3)


if __name__ == '__main__':
    run_flow_sequence()
