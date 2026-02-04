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

                    # If the stateless call returned an error, fall back to the stateful path below
                    result_error = result_dict.get('error') if isinstance(result_dict, dict) else None
                    has_result = isinstance(result_dict, dict) and 'result' in result_dict
                    if result_error and not has_result:
                        debug_log(f"Odoo API error (stateless): {result_error} - retrying with stateful request", "odoo_data")
                    else:
                        return True, result_dict.get('result', []) if isinstance(result_dict, dict) else result_dict
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

    def _extract_year_from_date_str(self, date_str: str) -> Optional[int]:
        """Extract year from a date string like '2025-01-03', '01/03/2025', or with time."""
        try:
            if not date_str:
                return None
            import re
            match = re.search(r'(\d{4})', str(date_str))
            if match:
                return int(match.group(1))
        except Exception:
            return None
        return None

    def get_total_allocated_leave(self, employee_id: int, start_year: int, end_year: int, odoo_session_data: Dict = None) -> Tuple[Dict[str, float], Optional[str]]:
        """
        Get total allocated leave for the specified period (start_year to end_year) from hr.leave.allocation.
        Allocations are counted if their end date (date_to) is within start_year/end_year,
        or if they have no end date ("No limit"/NULL).
        
        Returns:
            Tuple of (allocated_dict, error_message)
            - allocated_dict: Dict mapping leave type names to allocated days (float)
              Example: {'Annual Leave': 21.0, 'Sick Leave': 10.0}
            - error_message: None if successful, error string if there was a problem fetching data
        """
        try:
            # Domain: filter by employee
            # We only count allocations whose end date is in the current or previous year,
            # or allocations with no end date ("No limit"/NULL).
            domain = [
                ('employee_id', '=', employee_id),
                ('state', '=', 'validate')  # Only validated allocations
            ]

            params = {
                'args': [domain],
                'kwargs': {
                    'fields': ['id', 'holiday_status_id', 'number_of_days', 'date_from', 'date_to'],
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
            for allocation in allocations:
                try:
                    holiday_status_id = allocation.get('holiday_status_id')
                    leave_type_name = self._extract_leave_type_name(holiday_status_id)
                    
                    if not leave_type_name:
                        continue

                    # Only count allocations whose end date is within current/previous year,
                    # or with no end date ("No limit"/NULL) which are always valid.
                    date_to_str = allocation.get('date_to')
                    date_to_raw = str(date_to_str).strip() if date_to_str is not None else ""
                    date_to_lower = date_to_raw.lower()

                    if not date_to_raw or "no limit" in date_to_lower or date_to_lower in ("none", "null", "false"):
                        valid_for_year = True
                    else:
                        date_to_year = self._extract_year_from_date_str(date_to_raw)
                        if date_to_year is None:
                            debug_log(f"Unable to parse allocation date_to '{date_to_raw}', treating as valid", "odoo_data")
                            valid_for_year = True
                        else:
                            valid_for_year = date_to_year in (start_year, end_year)

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

                    # Debug: show which allocation contributed to which leave type and by how much
                    alloc_id = allocation.get('id')
                    debug_log(
                        f"Allocation contribution - id={alloc_id}, type='{leave_type_name}', "
                        f"days={days}, date_to='{date_to_raw or None}', "
                        f"running_total={allocated.get(leave_type_name)}",
                        "odoo_data"
                    )

                except Exception as e:
                    debug_log(f"Error processing allocation: {str(e)}", "odoo_data")
                    continue

            debug_log(f"Total allocated leave for employee {employee_id}: {allocated}", "odoo_data")
            return allocated, None

        except Exception as e:
            error_msg = f"Error getting total allocated leave: {str(e)}"
            debug_log(error_msg, "odoo_data")
            return {}, error_msg

    def _count_days_in_period(self, start_date: date, end_date: date, period_start: date, period_end: date) -> float:
        """
        Count how many days of a leave fall within the target period.
        Handles leaves that span across periods.
        """
        try:
            # If leave is completely outside the period, return 0
            if end_date < period_start or start_date > period_end:
                return 0.0

            # Calculate overlap
            effective_start = max(start_date, period_start)
            effective_end = min(end_date, period_end)

            # Calculate days (inclusive)
            days = (effective_end - effective_start).days + 1
            return float(max(0, days))

        except Exception:
            return 0.0

    def get_taken_leave(self, employee_id: int, start_year: int, end_year: int, odoo_session_data: Dict = None) -> Tuple[Dict[str, float], Optional[str]]:
        """
        Get total taken leave for the specified period (start_year to end_year) from hr.leave.
        Includes leaves with state 'validate' (Approved), 'validate1' (Second Approval), or 'confirm' (To Approve).
        Handles leaves spanning multiple periods.
        
        Returns:
            Tuple of (taken_dict, error_message)
            - taken_dict: Dict mapping leave type names to taken days (float)
              Example: {'Annual Leave': 5.0, 'Sick Leave': 2.0}
            - error_message: None if successful, error string if there was a problem fetching data
        """
        try:
            # Domain: filter by employee, approved states (including 'confirm' - To Approve), and dates within period
            period_start = date(start_year, 1, 1)
            period_end = date(end_year, 12, 31)

            domain = [
                ('employee_id', '=', employee_id),
                ('state', 'in', ['validate', 'validate1', 'confirm']),  # Approved, Second Approval, or To Approve
                ('date_from', '<=', period_end.strftime('%Y-%m-%d')),
                ('date_to', '>=', period_start.strftime('%Y-%m-%d'))
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
                            
                            # Check if leave spans multiple years/periods
                            if date_from >= period_start and date_to <= period_end:
                                # Leave is entirely within period - use number_of_days directly
                                days = total_days
                                debug_log(f"Leave entirely in period {start_year}-{end_year}: using number_of_days={total_days}", "odoo_data")
                            elif date_from > period_end or date_to < period_start:
                                # Leave is entirely outside period - skip (should be caught by domain but safety check)
                                days = 0.0
                            else:
                                # Leave spans across period boundaries - apportion number_of_days proportionally
                                # Calculate total calendar days in the leave period
                                total_calendar_days = (date_to - date_from).days + 1
                                if total_calendar_days <= 0:
                                    days = 0.0
                                else:
                                    # Calculate calendar days within period
                                    calendar_days_in_period = self._count_days_in_period(date_from, date_to, period_start, period_end)
                                    # Apportion number_of_days proportionally
                                    days = (total_days * calendar_days_in_period) / total_calendar_days
                                    debug_log(f"Leave spans period: total_days={total_days}, calendar_days_in_period={calendar_days_in_period}, total_calendar_days={total_calendar_days}, apportioned={days}", "odoo_data")
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
            start_year = current_year - 1
            end_year = current_year

            # Get allocations and taken leave for the 2-year period
            allocated, alloc_error = self.get_total_allocated_leave(employee_id, start_year, end_year, odoo_session_data)
            taken, taken_error = self.get_taken_leave(employee_id, start_year, end_year, odoo_session_data)

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

    def _days_to_hours_minutes(self, days: float, hours_per_day: float = 8.0) -> Tuple[int, int]:
        """
        Convert days to hours and minutes.
        
        Args:
            days: Number of days (can be fractional)
            hours_per_day: Hours per work day (default 8)
        
        Returns:
            Tuple of (hours, minutes)
        """
        try:
            total_hours = days * hours_per_day
            hours = int(total_hours)
            minutes = int((total_hours - hours) * 60)
            return hours, minutes
        except Exception:
            return 0, 0

    def format_remaining_leave_message(self, remaining: Dict[str, float]) -> str:
        """
        Format remaining leave data into a user-friendly message line.
        
        Args:
            remaining: Dict mapping leave type names to remaining days
        
        Returns:
            Formatted message string, e.g., "Available Annual Leave: 16 days (128:00) | Available Sick Leave: 8 days (64:00)"
        """
        if not remaining:
            return ""

        lines = []
        for leave_type, days in sorted(remaining.items()):
            # Exclude Unpaid Leave from balance display (unlimited, no balance concept)
            if leave_type == 'Unpaid Leave':
                continue
            
            # Convert to hours and minutes
            hours, minutes = self._days_to_hours_minutes(days)
            
            # Format days - show decimal if not whole number, otherwise show as integer
            if days == int(days):
                days_str = str(int(days))
            else:
                days_str = f"{days:.1f}"
            
            # Format hours:minutes
            hours_minutes_str = f"{hours}:{minutes:02d}"
            
            lines.append(f"Available {leave_type}: {days_str} days ({hours_minutes_str})")

        return " | ".join(lines)
