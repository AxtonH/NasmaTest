"""
Service for calculating remaining leave time for employees.
Handles leave allocations and taken leave calculations.
"""
from datetime import datetime, date
from typing import Dict, List, Optional, Tuple, Any
from decimal import Decimal

try:
    from ..config.settings import Config
except Exception:
    from config.settings import Config

def debug_log(message: str, category: str = "general"):
    """Conditional debug logging based on configuration"""
    if category == "odoo_data" and Config.DEBUG_ODOO_DATA:
        print(f"DEBUG: {message}")
    elif category == "bot_logic" and Config.DEBUG_BOT_LOGIC:
        print(f"DEBUG: {message}")
    elif category == "knowledge_base" and Config.DEBUG_KNOWLEDGE_BASE:
        print(f"DEBUG: {message}")
    elif category == "general" and Config.VERBOSE_LOGS:
        print(f"DEBUG: {message}")


class LeaveBalanceService:
    """Service for calculating remaining leave balances"""

    def __init__(self, odoo_service):
        self.odoo_service = odoo_service

    def _make_odoo_request(self, model: str, method: str, params: Dict, odoo_session_data: Dict = None) -> Tuple[bool, Any]:
        """Make authenticated request to Odoo using web session or stateless request."""
        try:
            # If session data provided, use stateless request (preferred)
            if odoo_session_data and odoo_session_data.get('session_id') and odoo_session_data.get('user_id'):
                try:
                    result_dict = self.odoo_service.make_authenticated_request(
                        model=model,
                        method=method,
                        args=params.get('args', []),
                        kwargs=params.get('kwargs', {}),
                        session_id=odoo_session_data['session_id'],
                        user_id=odoo_session_data['user_id'],
                        username=odoo_session_data.get('username'),
                        password=odoo_session_data.get('password')
                    )
                    
                    # Check if session was renewed
                    renewed_session = result_dict.pop('_renewed_session', None) if isinstance(result_dict, dict) else None
                    if renewed_session:
                        # Update Flask session if renewed
                        try:
                            from flask import session as flask_session
                            flask_session['odoo_session_id'] = renewed_session['session_id']
                            flask_session['user_id'] = renewed_session['user_id']
                            flask_session.modified = True
                        except Exception:
                            pass
                    
                    if 'error' in result_dict:
                        error_msg = result_dict.get('error', 'Unknown error')
                        debug_log(f"Odoo API error (stateless): {error_msg}", "odoo_data")
                        return False, error_msg
                    
                    return True, result_dict.get('result', [])
                except Exception as e:
                    debug_log(f"Stateless request failed, falling back to regular request: {str(e)}", "odoo_data")
                    # Fall through to regular request
            
            # Fallback to regular request using OdooService session
            # Ensure session is active before making request
            session_ok, session_msg = self.odoo_service.ensure_active_session()
            if not session_ok:
                return False, f"Session error: {session_msg}"

            url = f"{self.odoo_service.odoo_url}/web/dataset/call_kw"

            data = {
                "jsonrpc": "2.0",
                "method": "call",
                "params": {
                    "model": model,
                    "method": method,
                    "args": params.get('args', []),
                    "kwargs": params.get('kwargs', {})
                },
                "id": 1
            }

            cookies = {'session_id': self.odoo_service.session_id} if self.odoo_service.session_id else {}

            # Use OdooService retry-aware post
            post = getattr(self.odoo_service, 'post_with_retry', None)
            if callable(post):
                response = post(url, json=data, cookies=cookies, timeout=30)
            else:
                import requests
                response = requests.post(
                    url,
                    json=data,
                    cookies=cookies,
                    timeout=30
                )

            if response.status_code == 200:
                result = response.json()
                if 'error' in result:
                    debug_log(f"Odoo API error: {result.get('error')}", "odoo_data")
                    return False, result.get('error', 'Unknown error')
                return True, result.get('result', [])
            else:
                return False, f"HTTP {response.status_code}: {response.text}"

        except Exception as e:
            debug_log(f"Error making Odoo request: {str(e)}", "odoo_data")
            return False, f"Request error: {str(e)}"

    def _parse_duration_display(self, duration_str: str) -> float:
        """
        Parse duration_display field to extract days as float.
        Examples: "5.0 Days", "10 Days", "0.5 Days" -> 5.0, 10.0, 0.5
        """
        try:
            if not duration_str:
                return 0.0
            
            # Extract number from string like "5.0 Days" or "10 Days"
            import re
            match = re.search(r'([\d.]+)', str(duration_str))
            if match:
                return float(match.group(1))
            return 0.0
        except Exception:
            return 0.0

    def _extract_leave_type_name(self, holiday_status_id) -> Optional[str]:
        """Extract leave type name from Many2one field format [id, 'name']"""
        if isinstance(holiday_status_id, (list, tuple)) and len(holiday_status_id) >= 2:
            return holiday_status_id[1]
        elif isinstance(holiday_status_id, dict):
            return holiday_status_id.get('name')
        return None

    def _extract_leave_type_id(self, holiday_status_id) -> Optional[int]:
        """Extract leave type ID from Many2one field format [id, 'name']"""
        if isinstance(holiday_status_id, (list, tuple)) and len(holiday_status_id) >= 1:
            return holiday_status_id[0]
        elif isinstance(holiday_status_id, dict):
            return holiday_status_id.get('id')
        return None

    def get_total_allocated_leave(self, employee_id: int, current_year: int, odoo_session_data: Dict = None) -> Tuple[Dict[str, float], Optional[str]]:
        """
        Get total allocated leave for the current year from hr.leave.allocation.
        
        Returns:
            Tuple of (allocated_dict, error_message)
            - allocated_dict: Dict mapping leave type names to allocated days (float)
              Example: {'Annual Leave': 21.0, 'Sick Leave': 10.0}
            - error_message: None if successful, error string if there was a problem fetching data
        """
        try:
            # Domain: filter by employee and year
            # We need to check allocations that are valid for the current year
            domain = [
                ('employee_id', '=', employee_id),
                ('state', '=', 'validate')  # Only validated allocations
            ]

            params = {
                'args': [domain],
                'kwargs': {
                    'fields': ['holiday_status_id', 'number_of_days', 'date_from', 'date_to'],
                    'limit': 500
                }
            }

            success, allocations = self._make_odoo_request('hr.leave.allocation', 'search_read', params, odoo_session_data)
            
            if not success:
                error_msg = f"Failed to fetch allocations: {allocations}" if isinstance(allocations, str) else "Failed to fetch allocations"
                debug_log(error_msg, "odoo_data")
                # Return empty dict with error message to distinguish from "no allocations"
                return {}, error_msg

            allocated = {}
            current_year_start = date(current_year, 1, 1)
            current_year_end = date(current_year, 12, 31)

            for allocation in allocations:
                try:
                    holiday_status_id = allocation.get('holiday_status_id')
                    leave_type_name = self._extract_leave_type_name(holiday_status_id)
                    
                    if not leave_type_name:
                        continue

                    # Check if allocation is valid for current year
                    # Some allocations might have date_from/date_to, check if they overlap with current year
                    date_from_str = allocation.get('date_from')
                    date_to_str = allocation.get('date_to')
                    
                    valid_for_year = True
                    if date_from_str or date_to_str:
                        try:
                            if date_from_str:
                                date_from = datetime.strptime(date_from_str.split(' ')[0], '%Y-%m-%d').date()
                                if date_from > current_year_end:
                                    valid_for_year = False
                            if date_to_str:
                                date_to = datetime.strptime(date_to_str.split(' ')[0], '%Y-%m-%d').date()
                                if date_to < current_year_start:
                                    valid_for_year = False
                        except Exception:
                            pass  # If date parsing fails, assume valid

                    if not valid_for_year:
                        continue

                    # Get number_of_days directly from the allocation
                    number_of_days = allocation.get('number_of_days', 0)
                    try:
                        days = float(number_of_days)
                    except Exception:
                        days = 0.0

                    if days <= 0:
                        continue

                    # Sum allocations for the same leave type
                    if leave_type_name in allocated:
                        allocated[leave_type_name] += days
                    else:
                        allocated[leave_type_name] = days

                except Exception as e:
                    debug_log(f"Error processing allocation: {str(e)}", "odoo_data")
                    continue

            debug_log(f"Total allocated leave for employee {employee_id}: {allocated}", "odoo_data")
            return allocated, None

        except Exception as e:
            error_msg = f"Error getting total allocated leave: {str(e)}"
            debug_log(error_msg, "odoo_data")
            return {}, error_msg

    def _count_days_in_year(self, start_date: date, end_date: date, target_year: int) -> float:
        """
        Count how many days of a leave fall within the target year.
        Handles leaves that span across years.
        """
        try:
            # Clamp dates to target year boundaries
            year_start = date(target_year, 1, 1)
            year_end = date(target_year, 12, 31)

            # If leave is completely outside the year, return 0
            if end_date < year_start or start_date > year_end:
                return 0.0

            # Calculate overlap
            effective_start = max(start_date, year_start)
            effective_end = min(end_date, year_end)

            # Calculate days (inclusive)
            days = (effective_end - effective_start).days + 1
            return float(max(0, days))

        except Exception:
            return 0.0

    def get_taken_leave(self, employee_id: int, current_year: int, odoo_session_data: Dict = None) -> Tuple[Dict[str, float], Optional[str]]:
        """
        Get total taken leave for the current year from hr.leave.
        Only includes leaves with state 'validate' (Approved) or 'validate1' (Second Approval).
        Handles leaves spanning multiple years.
        
        Returns:
            Tuple of (taken_dict, error_message)
            - taken_dict: Dict mapping leave type names to taken days (float)
              Example: {'Annual Leave': 5.0, 'Sick Leave': 2.0}
            - error_message: None if successful, error string if there was a problem fetching data
        """
        try:
            # Domain: filter by employee, approved states, and dates within current year
            current_year_start = date(current_year, 1, 1)
            current_year_end = date(current_year, 12, 31)

            domain = [
                ('employee_id', '=', employee_id),
                ('state', 'in', ['validate', 'validate1']),  # Approved or Second Approval
                ('date_from', '<=', current_year_end.strftime('%Y-%m-%d')),
                ('date_to', '>=', current_year_start.strftime('%Y-%m-%d'))
            ]

            params = {
                'args': [domain],
                'kwargs': {
                    'fields': ['holiday_status_id', 'number_of_days', 'date_from', 'date_to'],
                    'limit': 500
                }
            }

            success, leaves = self._make_odoo_request('hr.leave', 'search_read', params, odoo_session_data)

            if not success:
                error_msg = f"Failed to fetch taken leaves: {leaves}" if isinstance(leaves, str) else "Failed to fetch taken leaves"
                debug_log(error_msg, "odoo_data")
                # Return empty dict with error message
                return {}, error_msg

            taken = {}

            for leave in leaves:
                try:
                    holiday_status_id = leave.get('holiday_status_id')
                    leave_type_name = self._extract_leave_type_name(holiday_status_id)

                    if not leave_type_name:
                        continue

                    # Get Odoo's calculated number_of_days (based on working days)
                    number_of_days = leave.get('number_of_days', 0)
                    try:
                        total_days = float(number_of_days)
                    except Exception:
                        total_days = 0.0

                    if total_days <= 0:
                        continue

                    date_from_str = leave.get('date_from')
                    date_to_str = leave.get('date_to')
                    
                    if not date_from_str or not date_to_str:
                        # No dates available, use number_of_days directly
                        days = total_days
                    else:
                        try:
                            date_from = datetime.strptime(date_from_str.split(' ')[0], '%Y-%m-%d').date()
                            date_to = datetime.strptime(date_to_str.split(' ')[0], '%Y-%m-%d').date()
                            
                            # Check if leave spans multiple years
                            if date_from.year == date_to.year == current_year:
                                # Leave is entirely within current year - use number_of_days directly
                                days = total_days
                                debug_log(f"Leave entirely in year {current_year}: using number_of_days={total_days}", "odoo_data")
                            elif date_from.year != current_year and date_to.year != current_year:
                                # Leave is entirely outside current year - skip
                                days = 0.0
                            else:
                                # Leave spans across years - apportion number_of_days proportionally
                                # Calculate total calendar days in the leave period
                                total_calendar_days = (date_to - date_from).days + 1
                                if total_calendar_days <= 0:
                                    days = 0.0
                                else:
                                    # Calculate calendar days within current year
                                    calendar_days_in_year = self._count_days_in_year(date_from, date_to, current_year)
                                    # Apportion number_of_days proportionally
                                    days = (total_days * calendar_days_in_year) / total_calendar_days
                                    debug_log(f"Leave spans years: total_days={total_days}, calendar_days_in_year={calendar_days_in_year}, total_calendar_days={total_calendar_days}, apportioned={days}", "odoo_data")
                        except Exception as e:
                            # Fallback to number_of_days if date parsing fails
                            debug_log(f"Date parsing error, using number_of_days directly: {str(e)}", "odoo_data")
                            days = total_days

                    if days > 0:
                        # Sum taken days for the same leave type
                        if leave_type_name in taken:
                            taken[leave_type_name] += days
                        else:
                            taken[leave_type_name] = days

                except Exception as e:
                    debug_log(f"Error processing leave: {str(e)}", "odoo_data")
                    continue

            debug_log(f"Total taken leave for employee {employee_id}: {taken}", "odoo_data")
            return taken, None

        except Exception as e:
            error_msg = f"Error getting taken leave: {str(e)}"
            debug_log(error_msg, "odoo_data")
            return {}, error_msg

    def calculate_remaining_leave(self, employee_id: int, leave_type_name: Optional[str] = None, odoo_session_data: Dict = None) -> Tuple[Dict[str, float], Optional[str]]:
        """
        Calculate remaining leave time for an employee.
        
        Args:
            employee_id: Employee ID
            leave_type_name: Optional specific leave type to calculate for (e.g., 'Annual Leave', 'Sick Leave')
                           If None, calculates for all leave types
        
        Returns:
            Tuple of (remaining_dict, error_message)
            - remaining_dict: Dict mapping leave type names to remaining days (float)
              Example: {'Annual Leave': 16.0, 'Sick Leave': 8.0}
              Always returns at least the requested leave type with 0.0 if no allocations exist
            - error_message: None if successful, error string if there was a problem fetching data
        """
        try:
            current_year = datetime.now().year

            # Get allocations and taken leave
            allocated, alloc_error = self.get_total_allocated_leave(employee_id, current_year, odoo_session_data)
            taken, taken_error = self.get_taken_leave(employee_id, current_year, odoo_session_data)

            # Check if we got errors
            if alloc_error:
                return {}, alloc_error
            if taken_error:
                return {}, taken_error

            # Calculate remaining per leave type
            remaining = {}

            # If specific leave type requested, always include it (even if 0)
            if leave_type_name:
                allocated_days = allocated.get(leave_type_name, 0.0)
                taken_days = taken.get(leave_type_name, 0.0)
                remaining[leave_type_name] = max(0.0, allocated_days - taken_days)
            else:
                # Calculate for all leave types
                # Include all types that have allocations or taken leave
                all_types = set(allocated.keys()) | set(taken.keys())
                for leave_type in all_types:
                    allocated_days = allocated.get(leave_type, 0.0)
                    taken_days = taken.get(leave_type, 0.0)
                    remaining[leave_type] = max(0.0, allocated_days - taken_days)

            debug_log(f"Remaining leave for employee {employee_id}: {remaining}", "bot_logic")
            return remaining, None

        except Exception as e:
            error_msg = f"Error calculating remaining leave: {str(e)}"
            debug_log(error_msg, "odoo_data")
            return {}, error_msg

    def format_remaining_leave_message(self, remaining: Dict[str, float]) -> str:
        """
        Format remaining leave data into a user-friendly message line.
        
        Args:
            remaining: Dict mapping leave type names to remaining days
        
        Returns:
            Formatted message string, e.g., "Available Annual Leave: 16.0 days | Available Sick Leave: 8.0 days"
        """
        if not remaining:
            return ""

        lines = []
        for leave_type, days in sorted(remaining.items()):
            # Format days with 1 decimal place, but show as integer if whole number
            if days == int(days):
                days_str = str(int(days))
            else:
                days_str = f"{days:.1f}"
            lines.append(f"Available {leave_type}: {days_str} days")

        return " | ".join(lines)

