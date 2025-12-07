from typing import Dict, Any, List, Tuple, Set, Optional
from datetime import datetime, timedelta, date, timezone
import re
from .manager_helper import _make_odoo_request, _current_month_range

try:
    from ..config.settings import Config
except Exception:
    from config.settings import Config

def debug_log(message: str, category: str = "general"):
    """Conditional debug logging based on configuration"""
    if "ERROR" in message.upper() or "FAILED" in message.upper() or "FAIL" in message.upper():
        print(f"ERROR: {message}", flush=True)
    elif "WARNING" in message.upper() or "WARN" in message.upper():
        print(f"WARNING: {message}", flush=True)
    elif category == "odoo_data" and Config.DEBUG_ODOO_DATA:
        print(f"DEBUG: {message}")
    elif category == "bot_logic" and Config.DEBUG_BOT_LOGIC:
        print(f"DEBUG: {message}")
    elif category == "general" and Config.VERBOSE_LOGS:
        print(f"DEBUG: {message}")


def _parse_hours_from_text(text: str) -> Optional[float]:
    """
    Parse hours from natural language text.
    
    Examples:
    - "five" -> 5.0
    - "five hours" -> 5.0
    - "five hours and 30 minutes" -> 5.5
    - "5 hours 30 minutes" -> 5.5
    - "5.5" -> 5.5
    - "5" -> 5.0
    - "half an hour" -> 0.5
    - "one hour" -> 1.0
    
    Returns:
        Float hours or None if parsing fails
    """
    if not text:
        return None
    
    text = text.strip().lower()
    
    # Remove common words that don't affect parsing
    text = re.sub(r'\b(spent|on|this|task|work|for)\b', '', text)
    text = text.strip()
    
    # Check for "X:Y" format FIRST (e.g., "7:20" meaning 7 hours 20 minutes)
    # This must be checked before standalone number parsing to avoid matching just "7"
    match_time = re.search(r'(\d+):(\d+)', text)
    if match_time:
        try:
            h = int(match_time.group(1))
            m = int(match_time.group(2))
            # Validate minutes are between 0-59
            if 0 <= m < 60:
                return float(h) + (m / 60.0)
        except (ValueError, IndexError):
            pass
    
    # Try direct float parsing (for decimal hours like "5.5")
    try:
        return float(text)
    except ValueError:
        pass
    
    # Number word mapping
    number_words = {
        'zero': 0, 'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
        'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10,
        'eleven': 11, 'twelve': 12, 'thirteen': 13, 'fourteen': 14,
        'fifteen': 15, 'sixteen': 16, 'seventeen': 17, 'eighteen': 18,
        'nineteen': 19, 'twenty': 20, 'thirty': 30, 'forty': 40, 'fifty': 50,
        'sixty': 60
    }
    
    # Handle "half an hour" or "half hour"
    if 'half' in text and ('hour' in text or 'hr' in text):
        return 0.5
    
    # Extract numeric values (digits or number words)
    hours_value = None
    minutes_value = None
    
    # Pattern 1: "X hours and Y minutes" or "X hours Y minutes"
    # First try to find hours
    number_words_str = '|'.join(number_words.keys()) if number_words else 'never_match_this_pattern'
    # Pattern: number (digit or word) followed by hours keyword
    hours_pattern = r'\b(\d+(?:\.\d+)?|' + number_words_str + r')\s*(?:hours?|hrs?|h)'
    hours_match = re.search(hours_pattern, text, re.IGNORECASE)
    if hours_match:
        hours_str = hours_match.group(1).strip().lower()
        if hours_str in number_words:
            hours_value = float(number_words[hours_str])
        else:
            try:
                hours_value = float(hours_str)
            except ValueError:
                pass
    
    # Then try to find minutes
    minutes_pattern = r'\b(?:and\s+)?(\d+(?:\.\d+)?|' + number_words_str + r')\s*(?:minutes?|mins?|m)'
    minutes_match = re.search(minutes_pattern, text, re.IGNORECASE)
    if minutes_match:
        minutes_str = minutes_match.group(1).strip().lower()
        if minutes_str in number_words:
            minutes_value = float(number_words[minutes_str])
        else:
            try:
                minutes_value = float(minutes_str)
            except ValueError:
                pass
    
    # Pattern 2: Just a number word or digit (assume hours) - BUT only if no minutes were found
    # If minutes were found but no hours, don't treat standalone numbers as hours
    if hours_value is None:
        # Only look for standalone numbers/words if we haven't found minutes yet
        if minutes_value is None:
            # Try to find standalone numbers
            numbers = re.findall(r'\d+(?:\.\d+)?', text)
            if numbers:
                try:
                    # Check if there's a minutes keyword - if so, treat as minutes
                    if re.search(r'\b(minutes?|mins?|m)\b', text, re.IGNORECASE):
                        minutes_value = float(numbers[0])
                    else:
                        # No minutes keyword, assume it's hours
                        hours_value = float(numbers[0])
                except ValueError:
                    pass
            
            # Try to find number words (only if we still don't have hours or minutes)
            if hours_value is None and minutes_value is None:
                for word, num in number_words.items():
                    if word in text:
                        # Check if there's a minutes keyword - if so, treat as minutes
                        if re.search(r'\b(minutes?|mins?|m)\b', text, re.IGNORECASE):
                            minutes_value = float(num)
                        else:
                            hours_value = float(num)
                        break
    
    # Pattern 4: If only minutes were found (no hours), convert to hours
    # This handles cases like "30 minutes" -> 0.5 hours
    if minutes_value is not None and hours_value is None:
        return minutes_value / 60.0
    
    # Calculate total hours
    if hours_value is not None:
        total_hours = hours_value
        if minutes_value is not None:
            total_hours += minutes_value / 60.0
        return total_hours
    
    return None


def _match_activity_name(text: str, activity_options: List[Dict]) -> Optional[str]:
    """
    Match user text input to an activity option by name (case-insensitive).
    
    Args:
        text: User input text
        activity_options: List of activity dicts with 'value' and 'label' keys
    
    Returns:
        Activity ID (value) if match found, None otherwise
    """
    if not text or not activity_options:
        return None
    
    text_lower = text.strip().lower()
    
    # Exact match (case-insensitive)
    for opt in activity_options:
        label = opt.get('label', '').strip().lower()
        if label == text_lower:
            return opt.get('value')
    
    # Partial match (contains)
    for opt in activity_options:
        label = opt.get('label', '').strip().lower()
        if text_lower in label or label in text_lower:
            return opt.get('value')
    
    return None


def _generate_hours_options(max_hours: float = 24.0) -> List[Dict[str, str]]:
    """
    Generate hours options for dropdown widget in 30-minute intervals.
    
    Args:
        max_hours: Maximum hours to include (default 24.0)
    
    Returns:
        List of dicts with 'value' and 'label' keys
    """
    options = []
    current = 0.0
    
    while current <= max_hours:
        # Format the label
        hours_int = int(current)
        minutes = int((current - hours_int) * 60)
        
        if hours_int == 0:
            if minutes == 0:
                label = "0 hours"
            else:
                label = f"{minutes} minutes"
        elif minutes == 0:
            if hours_int == 1:
                label = "1 hour"
            else:
                label = f"{hours_int} hours"
        else:
            hours_str = f"{hours_int} hour{'s' if hours_int != 1 else ''}"
            minutes_str = f"{minutes} minute{'s' if minutes != 1 else ''}"
            label = f"{hours_str} {minutes_str}"
        
        # Value is the decimal hours (e.g., "0.5", "1.0", "1.5")
        value = f"{current:.1f}"
        
        options.append({
            'value': value,
            'label': label
        })
        
        current += 0.5
    
    return options


def _escape_html(text: str) -> str:
    """Escape HTML special characters."""
    if not text:
        return ''
    return (str(text)
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;')
            .replace("'", '&#39;'))


def _fetch_timesheet_entries(odoo_service, employee_id: int, start_date: date, end_date: date, subtask_id: int = None) -> Tuple[bool, Any]:
    """
    Fetch timesheet entries from account.analytic.line for a given employee and date range.
    Optionally filter by subtask (project.task) ID.
    
    Args:
        odoo_service: Active Odoo service instance
        employee_id: Employee ID to match against employee_id field
        start_date: Start date (inclusive)
        end_date: End date (inclusive)
        subtask_id: Optional subtask (project.task) ID to filter by
    
    Returns:
        Tuple of (success: bool, data: set of dates with entries or error message)
    """
    try:
        ok_session, msg = odoo_service.ensure_active_session()
        if not ok_session:
            return False, msg
        
        # Convert dates to strings for Odoo domain
        start_date_str = start_date.strftime('%Y-%m-%d')
        end_date_str = end_date.strftime('%Y-%m-%d')
        
        # Domain to find timesheet entries:
        # - employee_id matches the employee
        # - date is within the range (inclusive)
        # - task_id matches the subtask if provided
        domain = [
            ('employee_id', '=', employee_id),
            ('date', '>=', start_date_str),
            ('date', '<=', end_date_str)
        ]
        
        if subtask_id:
            domain.append(('task_id', '=', subtask_id))
        
        params = {
            'args': [domain],
            'kwargs': {
                'fields': ['id', 'date', 'employee_id', 'task_id'],
                'limit': 1000,
            }
        }
        
        ok, data = _make_odoo_request(odoo_service, 'account.analytic.line', 'search_read', params)
        
        if not ok:
            return False, data
        
        # Extract unique dates from the timesheet entries
        logged_dates = set()
        if isinstance(data, list):
            for entry in data:
                entry_date = entry.get('date')
                if entry_date:
                    # Parse date string (usually YYYY-MM-DD format)
                    try:
                        if isinstance(entry_date, str):
                            parsed_date = datetime.strptime(entry_date[:10], '%Y-%m-%d').date()
                            logged_dates.add(parsed_date)
                        elif isinstance(entry_date, date):
                            logged_dates.add(entry_date)
                    except Exception:
                        pass
        
        return True, logged_dates
        
    except Exception as e:
        return False, f"Error fetching timesheet entries: {str(e)}"


def _fetch_timesheet_entry_counts(odoo_service, employee_id: int, start_date: date, end_date: date, subtask_id: int = None) -> Tuple[bool, Any]:
    """
    Fetch timesheet entry counts per date from account.analytic.line for a given employee and date range.
    Optionally filter by subtask (project.task) ID.
    
    Args:
        odoo_service: Active Odoo service instance
        employee_id: Employee ID to match against employee_id field
        start_date: Start date (inclusive)
        end_date: End date (inclusive)
        subtask_id: Optional subtask (project.task) ID to filter by
    
    Returns:
        Tuple of (success: bool, data: dict mapping date -> count or error message)
    """
    try:
        ok_session, msg = odoo_service.ensure_active_session()
        if not ok_session:
            return False, msg
        
        # Convert dates to strings for Odoo domain
        start_date_str = start_date.strftime('%Y-%m-%d')
        end_date_str = end_date.strftime('%Y-%m-%d')
        
        # Domain to find timesheet entries:
        # - employee_id matches the employee
        # - date is within the range (inclusive)
        # - task_id matches the subtask if provided
        domain = [
            ('employee_id', '=', employee_id),
            ('date', '>=', start_date_str),
            ('date', '<=', end_date_str)
        ]
        
        if subtask_id:
            domain.append(('task_id', '=', subtask_id))
        
        params = {
            'args': [domain],
            'kwargs': {
                'fields': ['id', 'date', 'employee_id', 'task_id'],
                'limit': 1000,
            }
        }
        
        ok, data = _make_odoo_request(odoo_service, 'account.analytic.line', 'search_read', params)
        
        if not ok:
            return False, data
        
        # Count entries per date
        date_counts = {}
        if isinstance(data, list):
            for entry in data:
                entry_date = entry.get('date')
                if entry_date:
                    # Parse date string (usually YYYY-MM-DD format)
                    try:
                        if isinstance(entry_date, str):
                            parsed_date = datetime.strptime(entry_date[:10], '%Y-%m-%d').date()
                        elif isinstance(entry_date, date):
                            parsed_date = entry_date
                        else:
                            continue
                        
                        date_counts[parsed_date] = date_counts.get(parsed_date, 0) + 1
                    except Exception:
                        pass
        
        return True, date_counts
        
    except Exception as e:
        return False, f"Error fetching timesheet entry counts: {str(e)}"


def _get_date_range_days(start_date: date, end_date: date) -> List[date]:
    """
    Get all dates in a range (inclusive).
    
    Args:
        start_date: Start date (inclusive)
        end_date: End date (inclusive)
    
    Returns:
        List of date objects
    """
    days = []
    current = start_date
    while current <= end_date:
        days.append(current)
        current += timedelta(days=1)
    return days


def _get_ordinal_suffix(day: int) -> str:
    """
    Get the ordinal suffix for a day number (1st, 2nd, 3rd, 4th, etc.)
    
    Args:
        day: Day of the month (1-31)
    
    Returns:
        Ordinal suffix string ('st', 'nd', 'rd', or 'th')
    """
    if 10 <= day % 100 <= 20:
        return 'th'
    else:
        return {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th')


def _format_date_with_ordinal(date_obj: datetime, include_year: bool = True) -> str:
    """
    Format a date with ordinal suffix for the day.
    
    Args:
        date_obj: datetime object to format
        include_year: Whether to include the year in the format
    
    Returns:
        Formatted date string (e.g., "Oct 30th" or "Oct 30th, 2025")
    """
    day = date_obj.day
    suffix = _get_ordinal_suffix(day)
    month = date_obj.strftime('%b')
    
    if include_year:
        year = date_obj.year
        return f"{month} {day}{suffix}, {year}"
    else:
        return f"{month} {day}{suffix}"


def _normalize_resource_name(employee_name: str) -> str:
    """
    Normalize employee name for resource matching.
    Resource names in planning.slot may include job titles in parentheses.

    Args:
        employee_name: Full employee name from hr.employee

    Returns:
        Normalized name for matching
    """
    return (employee_name or '').strip()


def _fetch_current_month_tasks(odoo_service, employee_name: str, employee_id: int = None) -> Tuple[bool, Any]:
    """
    Fetch tasks from planning.slot for the current user within the current month and previous month.

    This includes:
    - Tasks where employee_id matches (preferred) or resource_id contains the employee name (fallback)
    - Tasks within current month and previous month (start_datetime to end_datetime overlapping either month)
    - Tasks spanning multiple months are included if they overlap with current or previous month
    - Shift status must be 'Planned' (capital P matches Odoo selection key)

    Args:
        odoo_service: Active Odoo service instance
        employee_name: Employee full name to match against resource_id (fallback)
        employee_id: Employee ID to match against employee_id (preferred)

    Returns:
        Tuple of (success: bool, data: list or error message)
    """
    try:
        # Ensure session is active
        ok_session, msg = odoo_service.ensure_active_session()
        if not ok_session:
            return False, msg

        # Get current month range
        month_start, month_end = _current_month_range()
        
        # Calculate previous month start date
        current_start = datetime.strptime(month_start, '%Y-%m-%d')
        if current_start.month == 1:
            # If current month is January, previous month is December of previous year
            previous_month_start = datetime(current_start.year - 1, 12, 1)
        else:
            # Previous month is the same year, previous month number
            previous_month_start = datetime(current_start.year, current_start.month - 1, 1)
        
        # Use previous month start as the beginning of our range
        range_start = previous_month_start.strftime('%Y-%m-%d')
        month_start_dt = f"{range_start} 00:00:00"
        month_end_dt = f"{month_end} 23:59:59"

        # Build domain: prefer employee_id match, fallback to resource_id name match
        domain_parts = [
            ('start_datetime', '<=', month_end_dt),
            ('end_datetime', '>=', month_start_dt),
            ('x_studio_shift_status', '=', 'Planned')  # Note: capital P (matches selection key)
        ]
        
        # Add employee matching (prefer employee_id, fallback to resource_id)
        if employee_id:
            domain_parts.insert(0, ('employee_id', '=', employee_id))
        else:
            # Fallback to resource_id name matching
            normalized_name = _normalize_resource_name(employee_name)
            domain_parts.insert(0, ('resource_id', 'ilike', normalized_name))

        domain = domain_parts

        params = {
            'args': [domain],
            'kwargs': {
                'fields': [
                    'id',
                    'name',  # Task name (kept for reference)
                    'x_studio_sub_task_1',  # Sub task field
                    'resource_id',  # Resource (employee with job title)
                    'employee_id',  # Employee ID (Many2one to hr.employee)
                    'start_datetime',
                    'end_datetime',
                    'allocated_hours',
                    'allocated_percentage',
                    'project_id',  # Many2one to project.project
                    'role_id',  # Role/position
                    'state',  # draft, published, etc.
                    'x_studio_shift_status'  # Shift status (planned/forecasted)
                ],
                'limit': 500,
                'order': 'start_datetime desc'
            }
        }

        ok, data = _make_odoo_request(odoo_service, 'planning.slot', 'search_read', params)

        if not ok:
            return False, data

        return True, data

    except Exception as e:
        return False, f"Error fetching tasks: {str(e)}"


def start_log_hours_flow(odoo_service, employee_data: dict) -> Dict[str, Any]:
    """
    Start the log hours flow by fetching and displaying user's tasks for the current month and previous month.

    Args:
        odoo_service: Active Odoo service instance
        employee_data: Current employee data dict with 'name' field

    Returns:
        Response dict with message and optional widgets
    """
    try:
        employee_name = employee_data.get('name', '')
        if not employee_name:
            return {
                'message': 'Unable to identify your employee profile. Please contact support.',
                'success': False
            }

        # Fetch tasks for current month
        employee_id = employee_data.get('id')
        ok, tasks_data = _fetch_current_month_tasks(odoo_service, employee_name, employee_id=employee_id)

        if not ok:
            return {
                'message': f'Failed to retrieve your tasks: {tasks_data}',
                'success': False
            }

        if not tasks_data or len(tasks_data) == 0:
            # No tasks found for current month and previous month
            month_start, month_end = _current_month_range()
            current_month_name = datetime.strptime(month_start, '%Y-%m-%d').strftime('%B %Y')
            # Calculate previous month name
            current_start = datetime.strptime(month_start, '%Y-%m-%d')
            if current_start.month == 1:
                previous_month_start = datetime(current_start.year - 1, 12, 1)
            else:
                previous_month_start = datetime(current_start.year, current_start.month - 1, 1)
            previous_month_name = previous_month_start.strftime('%B %Y')
            return {
                'message': f'You have no tasks assigned for {previous_month_name} or {current_month_name}.',
                'success': True
            }

        # Format tasks for display
        message = _format_tasks_message(tasks_data, employee_name)
        
        # Build table widget for tasks (filter out logged days)
        tasks_table = build_tasks_table_widget(odoo_service, employee_data, tasks_data)

        return {
            'message': message,
            'success': True,
            'tasks': tasks_data,  # Include raw data for potential future use
            'widgets': {
                'tasks_table': tasks_table
            }
        }

    except Exception as e:
        return {
            'message': f'An error occurred while retrieving your tasks: {str(e)}',
            'success': False
        }


def _format_tasks_message(tasks: List[Dict], employee_name: str) -> str:
    """
    Format tasks data into a user-friendly message.

    Args:
        tasks: List of planning.slot records
        employee_name: Employee name for context

    Returns:
        Formatted message string
    """
    try:
        month_start, month_end = _current_month_range()
        current_month_name = datetime.strptime(month_start, '%Y-%m-%d').strftime('%B %Y')

        return f'**Your tasks for {current_month_name}:**\n\n*What would you like to do next?*'

    except Exception as e:
        return f'Your tasks for this month:\n\n*What would you like to do next?*'


def _fetch_subtask_details(odoo_service, subtask_ids: List[int]) -> Dict[int, Dict[str, Any]]:
    """
    Fetch subtask details by getting parent task's partner_id and sale_line_id from project.task model.
    
    Args:
        odoo_service: Active Odoo service instance
        subtask_ids: List of subtask (project.task) IDs to fetch
    
    Returns:
        Dict mapping subtask_id -> {'client': client_name, 'project_id': project_id}
    """
    if not subtask_ids:
        return {}
    
    try:
        ok_session, msg = odoo_service.ensure_active_session()
        if not ok_session:
            return {}
        
        # Remove duplicates and None values
        unique_ids = list(set([sid for sid in subtask_ids if sid]))
        if not unique_ids:
            return {}
        
        # Step 1: Fetch subtasks to get their parent_id
        domain = [('id', 'in', unique_ids)]
        params = {
            'args': [domain],
            'kwargs': {
                'fields': ['id', 'parent_id'],
                'limit': len(unique_ids)
            }
        }
        
        ok, subtask_data = _make_odoo_request(odoo_service, 'project.task', 'search_read', params)
        
        if not ok or not isinstance(subtask_data, list):
            return {}
        
        # Build mapping of subtask_id -> parent_id
        subtask_to_parent = {}
        parent_ids_to_fetch = set()
        
        for subtask in subtask_data:
            subtask_id = subtask.get('id')
            if not subtask_id:
                continue
            
            # Extract parent_id (Many2one format: [id, 'name'] or False)
            parent_id = None
            parent_field = subtask.get('parent_id')
            if parent_field:
                if isinstance(parent_field, (list, tuple)) and len(parent_field) > 0:
                    parent_id = parent_field[0] if isinstance(parent_field[0], int) else None
            
            if parent_id:
                subtask_to_parent[subtask_id] = parent_id
                parent_ids_to_fetch.add(parent_id)
        
        # If no parent tasks found, return empty result
        if not parent_ids_to_fetch:
            return {}
        
        # Step 2: Fetch parent tasks to get partner_id and sale_line_id
        parent_domain = [('id', 'in', list(parent_ids_to_fetch))]
        parent_params = {
            'args': [parent_domain],
            'kwargs': {
                'fields': ['id', 'partner_id', 'sale_line_id'],
                'limit': len(parent_ids_to_fetch)
            }
        }
        
        ok_parent, parent_data = _make_odoo_request(odoo_service, 'project.task', 'search_read', parent_params)
        
        if not ok_parent or not isinstance(parent_data, list):
            return {}
        
        # Build mapping of parent_id -> {client, project_id}
        parent_details = {}
        for parent_task in parent_data:
            parent_id = parent_task.get('id')
            if not parent_id:
                continue
            
            # Extract client name from partner_id (Many2one format: [id, 'name'])
            client_name = '—'
            partner_id = parent_task.get('partner_id')
            if partner_id:
                if isinstance(partner_id, (list, tuple)) and len(partner_id) > 1:
                    client_name = str(partner_id[1]).strip()
                elif isinstance(partner_id, (list, tuple)) and len(partner_id) == 1:
                    if not isinstance(partner_id[0], int):
                        client_name = str(partner_id[0]).strip()
            
            # Extract project ID (display name) from sale_line_id (Many2one format: [id, 'name'])
            project_id = '—'
            sale_line_id = parent_task.get('sale_line_id')
            if sale_line_id:
                if isinstance(sale_line_id, (list, tuple)) and len(sale_line_id) > 1:
                    # Get the display name (second element) like "S02118 - Concept Creation  (AL TOUFEEQ CONTRACTING & GENERAL MAINTENANCE COMPANY WLL)"
                    project_id = str(sale_line_id[1]).strip() if sale_line_id[1] else '—'
                elif isinstance(sale_line_id, (list, tuple)) and len(sale_line_id) == 1:
                    # Only ID available, use it as fallback
                    project_id = str(sale_line_id[0]) if sale_line_id[0] else '—'
            
            parent_details[parent_id] = {
                'client': client_name,
                'project_id': project_id
            }
        
        # Step 3: Map subtask_id -> parent details
        result = {}
        for subtask_id, parent_id in subtask_to_parent.items():
            if parent_id in parent_details:
                result[subtask_id] = parent_details[parent_id]
            else:
                # Parent task not found, use defaults
                result[subtask_id] = {
                    'client': '—',
                    'project_id': '—'
                }
        
        return result
        
    except Exception as e:
        debug_log(f"Error fetching subtask details: {str(e)}", "bot_logic")
        return {}


def build_tasks_table_widget(odoo_service, employee_data: dict, tasks: List[Dict]) -> Dict[str, Any]:
    """Build a table widget payload to render tasks in the frontend.

    Columns: Sub Task, Client, Project, Project ID, Dates, Action
    Rows: one per unlogged day (tasks split by day if multi-day).

    Args:
        odoo_service: Active Odoo service instance
        employee_data: Employee data dict with 'id' field
        tasks: List of planning.slot records

    Returns:
        Dict with 'columns' and 'rows' keys
    """
    columns = [
        { 'key': 'project_id', 'label': 'Project ID', 'align': 'center' },
        { 'key': 'project', 'label': 'Project', 'align': 'center' },
        { 'key': 'client', 'label': 'Client', 'align': 'center' },
        { 'key': 'task_name', 'label': 'Sub Task', 'align': 'center' },
        { 'key': 'dates', 'label': 'Dates', 'align': 'center' },
        { 'key': 'log_hours', 'label': 'Action', 'align': 'center' },
    ]

    rows: List[Dict[str, str]] = []
    
    # Get employee ID
    employee_id = employee_data.get('id')
    if not employee_id:
        # If no employee ID, return empty table
        return { 'columns': columns, 'rows': [] }
    
    # Get current year for date formatting
    current_year = datetime.now().year

    # First pass: Count tasks per (subtask_id, date) combination
    # This helps us identify when multiple tasks share the same subtask on the same day
    task_counts_by_subtask_date = {}
    task_data_list = []
    subtask_ids_to_fetch = set()
    
    for task in tasks or []:
        # Get sub task from x_studio_sub_task_1 field
        sub_task = task.get('x_studio_sub_task_1')
        subtask_id = None
        # Handle Odoo Many2one format: [id, 'name'] or just the name string
        if sub_task and sub_task is not False:
            if isinstance(sub_task, (list, tuple)) and len(sub_task) > 1:
                # Extract the ID and name from the tuple/list format [id, 'name']
                subtask_id = sub_task[0] if isinstance(sub_task[0], int) else None
                task_name = str(sub_task[1]).strip() if sub_task[1] else '—'
            elif isinstance(sub_task, (list, tuple)) and len(sub_task) == 1:
                # Tuple with only one element
                if isinstance(sub_task[0], int):
                    subtask_id = sub_task[0]
                    task_name = '—'
                else:
                    task_name = str(sub_task[0]).strip() if sub_task[0] else '—'
            else:
                # It's already a string or single value
                task_name = str(sub_task).strip()
        else:
            task_name = '—'
        
        # Collect subtask IDs for batch fetching
        if subtask_id:
            subtask_ids_to_fetch.add(subtask_id)

        # Parse dates
        start_dt = task.get('start_datetime', '')
        end_dt = task.get('end_datetime', '')

        # Parse date range
        try:
            if start_dt and end_dt:
                start_parsed = datetime.strptime(start_dt, '%Y-%m-%d %H:%M:%S')
                end_parsed = datetime.strptime(end_dt, '%Y-%m-%d %H:%M:%S')
                start_date_only = start_parsed.date()
                end_date_only = end_parsed.date()
            else:
                # Skip tasks without valid dates
                continue
        except Exception:
            # Skip tasks with invalid date formats
            continue

        # Store task data for second pass
        task_data_list.append({
            'task': task,
            'subtask_id': subtask_id,
            'task_name': task_name,
            'start_date': start_date_only,
            'end_date': end_date_only
        })
        
        # Count tasks per (subtask_id, date) combination
        if subtask_id:
            all_days = _get_date_range_days(start_date_only, end_date_only)
            for day in all_days:
                key = (subtask_id, day)
                task_counts_by_subtask_date[key] = task_counts_by_subtask_date.get(key, 0) + 1

    # Fetch subtask details (client and project ID) in batch
    subtask_details = _fetch_subtask_details(odoo_service, list(subtask_ids_to_fetch))

    # Second pass: Build rows, checking timesheet entry counts against task counts
    # Track how many rows we've added for each (subtask_id, date) to limit display
    rows_added_by_subtask_date = {}
    
    for task_data in task_data_list:
        task = task_data['task']
        subtask_id = task_data['subtask_id']
        task_name = task_data['task_name']
        start_date_only = task_data['start_date']
        end_date_only = task_data['end_date']

        # Get project name
        project_id = task.get('project_id')
        if isinstance(project_id, (list, tuple)) and len(project_id) > 1:
            project_name = project_id[1]
        else:
            project_name = 'No Project'

        # Fetch timesheet entry counts for this task's date range, filtered by subtask
        ok_timesheet, timesheet_counts_result = _fetch_timesheet_entry_counts(
            odoo_service, employee_id, start_date_only, end_date_only, subtask_id=subtask_id
        )
        
        if not ok_timesheet:
            # If we can't fetch timesheet entries, assume all days are unlogged
            # This is safer than skipping the task entirely
            timesheet_counts = {}
        else:
            timesheet_counts = timesheet_counts_result if isinstance(timesheet_counts_result, dict) else {}
        
        # Get all days in the task's date range
        all_days = _get_date_range_days(start_date_only, end_date_only)
        
        # Filter days: include a day if:
        # 1. No subtask_id (always show these)
        # 2. OR the number of timesheet entries for (subtask_id, date) is less than the number of tasks with that (subtask_id, date)
        #    AND we haven't already shown enough rows for that (subtask_id, date)
        unlogged_days = []
        for day in all_days:
            if not subtask_id:
                # Tasks without subtask_id are always shown
                unlogged_days.append(day)
            else:
                # Check if we have fewer timesheet entries than tasks for this (subtask_id, date)
                key = (subtask_id, day)
                task_count = task_counts_by_subtask_date.get(key, 0)
                timesheet_count = timesheet_counts.get(day, 0)
                rows_already_added = rows_added_by_subtask_date.get(key, 0)
                
                # Calculate how many rows we should show for this (subtask_id, date)
                rows_to_show = task_count - timesheet_count
                
                # Only include if:
                # 1. There are fewer timesheet entries than tasks (timesheet_count < task_count)
                # 2. We haven't already shown enough rows (rows_already_added < rows_to_show)
                if timesheet_count < task_count and rows_already_added < rows_to_show:
                    unlogged_days.append(day)
        
        # Get client and project ID from subtask details
        client_name = '—'
        project_id_display = '—'
        if subtask_id and subtask_id in subtask_details:
            details = subtask_details[subtask_id]
            client_name = details.get('client', '—')
            project_id_display = details.get('project_id', '—')

        # Create a row for each unlogged day
        for day in unlogged_days:
            # Format the date for display
            day_datetime = datetime.combine(day, datetime.min.time())
            include_year = day.year != current_year
            day_display = _format_date_with_ordinal(day_datetime, include_year=include_year)
            
            rows.append({
                'task_name': task_name,
                'client': client_name,
                'project': project_name,
                'project_id': project_id_display,
                'dates': day_display,
                'log_hours': f'<button class="log-hours-btn h-10 px-4 rounded-full text-sm font-medium btn-gradient text-white" data-subtask-id="{subtask_id or ""}" data-date="{day.strftime("%Y-%m-%d")}" data-task-name="{_escape_html(task_name)}">Log Hours</button>' if subtask_id else '<span class="text-gray-400">—</span>',
            })
            
            # Track that we've added a row for this (subtask_id, date)
            if subtask_id:
                key = (subtask_id, day)
                rows_added_by_subtask_date[key] = rows_added_by_subtask_date.get(key, 0) + 1

    return { 'columns': columns, 'rows': rows }


def _fetch_task_activity_options(odoo_service) -> Tuple[bool, Any]:
    """
    Fetch task activity options from x_task_activity model.
    
    Args:
        odoo_service: Active Odoo service instance
    
    Returns:
        Tuple of (success: bool, data: list of options or error message)
    """
    try:
        ok_session, msg = odoo_service.ensure_active_session()
        if not ok_session:
            return False, msg
        
        params = {
            'args': [[]],
            'kwargs': {
                'fields': ['id', 'x_name'],
                'limit': 1000,
                'order': 'x_name asc'
            }
        }
        
        ok, data = _make_odoo_request(odoo_service, 'x_task_activity', 'search_read', params)
        
        if not ok:
            return False, data
        
        # Format options for dropdown
        options = []
        if isinstance(data, list):
            for item in data:
                activity_id = item.get('id')
                activity_name = item.get('x_name') or f"Activity {activity_id}"
                options.append({
                    'value': activity_id,
                    'label': activity_name
                })
        
        return True, options
        
    except Exception as e:
        return False, f"Error fetching task activity options: {str(e)}"


def start_log_hours_for_task(odoo_service, employee_data: dict, subtask_id: int, task_date: str, task_name: str) -> Dict[str, Any]:
    """
    Start the log hours flow for a specific task.
    
    Args:
        odoo_service: Active Odoo service instance
        employee_data: Employee data dict with 'id' field
        subtask_id: Subtask (project.task) ID
        task_date: Date string in YYYY-MM-DD format
        task_name: Name of the task
    
    Returns:
        Response dict with message and widgets
    """
    try:
        # Fetch task activity options
        ok, activity_options = _fetch_task_activity_options(odoo_service)
        
        if not ok:
            return {
                'message': f'Failed to fetch task activity options: {activity_options}',
                'success': False
            }
        
        # Format date for display
        try:
            date_obj = datetime.strptime(task_date, '%Y-%m-%d')
            date_display = _format_date_with_ordinal(date_obj, include_year=False)
        except Exception:
            date_display = task_date
        
        return {
            'message': f'**Logging hours for {task_name} on {date_display}**\n\nPlease fill in the details below:',
            'success': True,
            'widgets': {
                'log_hours_flow': {
                    'step': 'log_hours_form',
                    'subtask_id': subtask_id,
                    'task_date': task_date,
                    'task_name': task_name,
                    'employee_id': employee_data.get('id'),
                },
                'log_hours_form': True,
                'activity_options': activity_options,
                'context_key': 'log_hours_form'
            }
        }
        
    except Exception as e:
        return {
            'message': f'An error occurred while starting the log hours flow: {str(e)}',
            'success': False
        }


def handle_log_hours_form_step(odoo_service, employee_data: dict, context: dict, form_data: dict, odoo_session_data: dict = None, metrics_service=None) -> Dict[str, Any]:
    """
    Handle the combined log hours form submission (activity, hours, description all at once).
    
    Args:
        odoo_service: Active Odoo service instance
        employee_data: Employee data dict
        context: Flow context containing subtask_id, task_date, task_name, etc.
        form_data: Dict with 'activity_id', 'hours', and optionally 'description' keys
        odoo_session_data: Optional Odoo session data for stateless requests
        metrics_service: Optional metrics service for logging
    
    Returns:
        Response dict with confirmation message or error
    """
    try:
        subtask_id = context.get('subtask_id')
        task_date = context.get('task_date')
        task_name = context.get('task_name')
        employee_id = context.get('employee_id') or employee_data.get('id')
        
        # Extract form data
        task_activity_id = form_data.get('activity_id')
        hours_str = form_data.get('hours', '').strip()
        minutes_str = form_data.get('minutes', '').strip()
        description = form_data.get('description', '').strip()
        
        # Validate activity
        if not task_activity_id:
            # Fetch activity options for error message
            ok, activity_options = _fetch_task_activity_options(odoo_service)
            if not ok:
                activity_options = []
            return {
                'message': 'Please select a task activity.',
                'success': False,
                'widgets': {
                    'log_hours_flow': {
                        'step': 'log_hours_form',
                        **context
                    },
                    'log_hours_form': True,
                    'activity_options': activity_options,
                    'context_key': 'log_hours_form'
                }
            }
        
        # Parse hours and minutes from separate fields
        hours = None
        if hours_str or minutes_str:
            try:
                hours_num = int(hours_str) if hours_str else 0
                minutes_num = int(minutes_str) if minutes_str else 0
                
                # Validate ranges
                if hours_num < 0 or minutes_num < 0 or minutes_num > 59:
                    ok, activity_options = _fetch_task_activity_options(odoo_service)
                    if not ok:
                        activity_options = []
                    return {
                        'message': 'Please enter valid hours (≥0) and minutes (0-59).',
                        'success': False,
                        'widgets': {
                            'log_hours_flow': {
                                'step': 'log_hours_form',
                                **context
                            },
                            'log_hours_form': True,
                            'activity_options': activity_options,
                            'context_key': 'log_hours_form'
                        }
                    }
                
                # Convert to decimal hours
                hours = float(hours_num) + (float(minutes_num) / 60.0)
                
                if hours <= 0:
                    ok, activity_options = _fetch_task_activity_options(odoo_service)
                    if not ok:
                        activity_options = []
                    return {
                        'message': 'Please enter at least some hours or minutes.',
                        'success': False,
                        'widgets': {
                            'log_hours_flow': {
                                'step': 'log_hours_form',
                                **context
                            },
                            'log_hours_form': True,
                            'activity_options': activity_options,
                            'context_key': 'log_hours_form'
                        }
                    }
            except (ValueError, TypeError):
                # Fallback: try parsing as natural language text (for backward compatibility)
                combined_str = f"{hours_str} {minutes_str}".strip()
                if combined_str:
                    hours = _parse_hours_from_text(combined_str)
                    if hours is None:
                        try:
                            hours = float(hours_str) if hours_str else 0.0
                        except (ValueError, TypeError):
                            pass
        
        if hours is None or hours <= 0:
            ok, activity_options = _fetch_task_activity_options(odoo_service)
            if not ok:
                activity_options = []
            return {
                'message': 'Please enter valid hours and minutes.',
                'success': False,
                'widgets': {
                    'log_hours_flow': {
                        'step': 'log_hours_form',
                        **context
                    },
                    'log_hours_form': True,
                    'activity_options': activity_options,
                    'context_key': 'log_hours_form'
                }
            }
        
        # Store all data in context
        context['task_activity_id'] = task_activity_id
        context['hours'] = hours
        context['description'] = description
        
        # Fetch task activity name for display
        activity_name = f"Activity {task_activity_id}"
        ok, activity_options = _fetch_task_activity_options(odoo_service)
        if ok and isinstance(activity_options, list):
            for opt in activity_options:
                if str(opt.get('value')) == str(task_activity_id):
                    activity_name = opt.get('label', activity_name)
                    break
        
        # Format date for display (DD/MM/YYYY format like time off flow)
        try:
            date_obj = datetime.strptime(task_date, '%Y-%m-%d')
            date_display = date_obj.strftime('%d/%m/%Y')
        except Exception:
            date_display = task_date
        
        hours_display = f"{hours:.1f}" if hours else "0"
        
        # Format confirmation message
        confirmation_text = f"Great! Here's a summary of your timesheet entry:\n\n"
        confirmation_text += f"📋 **Task:** {task_name}\n"
        confirmation_text += f"📅 **Date:** {date_display}\n"
        confirmation_text += f"📝 **Activity:** {activity_name}\n"
        confirmation_text += f"⏰ **Hours:** {hours_display}\n"
        confirmation_text += f"💬 **Description:** {description if description else 'None'}\n\n"
        confirmation_text += "Do you want to submit this entry? Reply or click 'yes' to confirm or 'no' to cancel"
        
        return {
            'message': confirmation_text,
            'success': True,
            'widgets': {
                'log_hours_flow': {
                    'step': 'confirmation',
                    **context
                }
            },
            'buttons': [
                {'text': 'Yes', 'value': 'log_hours_confirm', 'type': 'action'},
                {'text': 'No', 'value': 'log_hours_cancel', 'type': 'action'}
            ]
        }
        
    except Exception as e:
        return {
            'message': f'An error occurred while processing the form: {str(e)}',
            'success': False
        }


def handle_log_hours_step(odoo_service, employee_data: dict, step: str, context: dict, user_input: str = None, odoo_session_data: dict = None, metrics_service=None) -> Dict[str, Any]:
    """
    Handle a step in the log hours flow.
    
    Args:
        odoo_service: Active Odoo service instance
        employee_data: Employee data dict
        step: Current step ('task_activity', 'hours', 'description', 'confirmation')
        context: Flow context containing subtask_id, task_date, task_name, etc.
        user_input: User's input for the current step (can be dropdown value or chat text)
    
    Returns:
        Response dict with message and widgets
    """
    try:
        subtask_id = context.get('subtask_id')
        task_date = context.get('task_date')
        task_name = context.get('task_name')
        employee_id = context.get('employee_id') or employee_data.get('id')
        
        if step == 'task_activity':
            # Fetch activity options first
            ok, activity_options = _fetch_task_activity_options(odoo_service)
            if not ok:
                activity_options = []
            
            # Check if input looks like hours instead of activity
            if user_input:
                input_lower = user_input.lower().strip()
                # Check for hour-related keywords
                has_hour_keywords = bool(re.search(r'\b(hours?|hrs?|h|minutes?|mins?|m)\b', input_lower))
                # Check for number words
                number_words_list = ['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 
                                   'eight', 'nine', 'ten', 'eleven', 'twelve', 'thirteen', 'fourteen',
                                   'fifteen', 'sixteen', 'seventeen', 'eighteen', 'nineteen', 'twenty',
                                   'thirty', 'forty', 'fifty', 'sixty']
                has_word_number = any(word in input_lower for word in number_words_list)
                has_number = bool(re.search(r'\d+', user_input))
                has_half = 'half' in input_lower
                
                # If it looks like hours input, treat it as hours step
                if has_hour_keywords and (has_number or has_word_number or has_half):
                    # User is trying to enter hours, but we're in activity step
                    # This shouldn't happen if session is correct, but handle it gracefully
                    return {
                        'message': 'It looks like you\'re entering hours. Please first select the task activity, then enter the hours.',
                        'success': False,
                        'widgets': {
                            'log_hours_flow': {
                                'step': 'task_activity',
                                'subtask_id': subtask_id,
                                'task_date': task_date,
                                'task_name': task_name,
                                'employee_id': employee_id,
                            },
                            'select_dropdown': True,
                            'options': activity_options,
                            'context_key': 'log_hours_task_activity',
                            'placeholder': 'Select task activity'
                        }
                    }
            
            # If user_input is a number (dropdown selection), use it directly
            task_activity_id = None
            try:
                # Try to parse as integer (dropdown value)
                task_activity_id = str(int(user_input))
            except (ValueError, TypeError):
                # Not a number, try to match by name
                if user_input:
                    task_activity_id = _match_activity_name(user_input, activity_options)
            
            if not task_activity_id:
                # No match found, show dropdown again
                return {
                    'message': f'I couldn\'t find "{user_input}" in the activity list. Please select an activity from the dropdown below:',
                    'success': False,
                    'widgets': {
                        'log_hours_flow': {
                            'step': 'task_activity',
                            'subtask_id': subtask_id,
                            'task_date': task_date,
                            'task_name': task_name,
                            'employee_id': employee_id,
                        },
                        'select_dropdown': True,
                        'options': activity_options,
                        'context_key': 'log_hours_task_activity',
                        'placeholder': 'Select task activity'
                    }
                }
            
            context['task_activity_id'] = task_activity_id
            
            return {
                'message': 'How many hours did you spend on this task? (e.g., "five", "five hours", "five hours and 30 minutes", "5.5")',
                'success': True,
                'widgets': {
                    'log_hours_flow': {
                        'step': 'hours',
                        **context
                    },
                    'select_dropdown': True,
                    'options': _generate_hours_options(),
                    'context_key': 'log_hours_hours',
                    'placeholder': 'Select hours'
                }
            }
            
        elif step == 'hours':
            # Parse hours from natural language text
            hours = _parse_hours_from_text(user_input) if user_input else None
            
            if hours is None or hours <= 0:
                return {
                    'message': 'I couldn\'t understand the hours format. Please enter hours like: "five", "five hours", "five hours and 30 minutes", or "5.5"',
                    'success': False,
                    'widgets': {
                        'log_hours_flow': {
                            'step': 'hours',
                            **context
                        },
                        'select_dropdown': True,
                        'options': _generate_hours_options(),
                        'context_key': 'log_hours_hours',
                        'placeholder': 'Select hours'
                    }
                }
            
            context['hours'] = hours
            
            return {
                'message': 'Please add a description in chat or Skip',
                'success': True,
                'widgets': {
                    'log_hours_flow': {
                        'step': 'description',
                        **context
                    }
                },
                'buttons': [
                    {'text': 'Skip', 'value': 'log_hours_skip_description', 'type': 'action'}
                ]
            }
            
        elif step == 'description':
            # Store description and show confirmation
            description = user_input or ''
            context['description'] = description
            
            # Fetch task activity name for display
            task_activity_id = context.get('task_activity_id')
            activity_name = f"Activity {task_activity_id}"
            if task_activity_id:
                ok, activity_options = _fetch_task_activity_options(odoo_service)
                if ok and isinstance(activity_options, list):
                    for opt in activity_options:
                        if str(opt.get('value')) == str(task_activity_id):
                            activity_name = opt.get('label', activity_name)
                            break
            
            # Format date for display (DD/MM/YYYY format like time off flow)
            try:
                date_obj = datetime.strptime(task_date, '%Y-%m-%d')
                date_display = date_obj.strftime('%d/%m/%Y')
            except Exception:
                date_display = task_date
            
            hours = context.get('hours', 0)
            hours_display = f"{hours:.1f}" if hours else "0"
            
            # Format confirmation message similar to time off flow
            confirmation_text = f"Great! Here's a summary of your timesheet entry:\n\n"
            confirmation_text += f"📋 **Task:** {task_name}\n"
            confirmation_text += f"📅 **Date:** {date_display}\n"
            confirmation_text += f"📝 **Activity:** {activity_name}\n"
            confirmation_text += f"⏰ **Hours:** {hours_display}\n"
            confirmation_text += f"💬 **Description:** {description if description else 'None'}\n\n"
            confirmation_text += "Do you want to submit this entry? Reply or click 'yes' to confirm or 'no' to cancel"
            
            return {
                'message': confirmation_text,
                'success': True,
                'widgets': {
                    'log_hours_flow': {
                        'step': 'confirmation',
                        **context
                    }
                },
                'buttons': [
                    {'text': 'Yes', 'value': 'log_hours_confirm', 'type': 'action'},
                    {'text': 'No', 'value': 'log_hours_cancel', 'type': 'action'}
                ]
            }
            
        elif step == 'confirmation':
            # Handle confirmation - accept 'yes', 'confirm', or button click
            confirm_input = (user_input or '').lower().strip()
            if confirm_input in ['log_hours_confirm', 'yes', 'confirm', 'y']:
                # Create the timesheet entry
                return create_timesheet_entry(odoo_service, employee_data, context, odoo_session_data, metrics_service)
            else:
                return {
                    'message': 'Log hours cancelled.',
                    'success': True
                }
                
        else:
            return {
                'message': f'Unknown step: {step}',
                'success': False
            }
            
    except Exception as e:
        return {
            'message': f'An error occurred in the log hours flow: {str(e)}',
            'success': False
        }


def _resolve_identity(employee_data: dict) -> Dict[str, Optional[str]]:
    """Resolve tenant/company identifiers and user id/name from employee context."""
    tenant_id = None
    tenant_name = None
    user_id = None
    user_name = None
    try:
        if isinstance(employee_data, dict):
            eid = employee_data.get('id')
            if eid is not None:
                user_id = str(eid)
            name = employee_data.get('name')
            if name:
                user_name = str(name)
            company_details = employee_data.get('company_id_details')
            if isinstance(company_details, dict):
                if company_details.get('id') is not None:
                    tenant_id = str(company_details.get('id'))
                if company_details.get('name'):
                    tenant_name = company_details.get('name')
            else:
                raw_company = employee_data.get('company_id')
                if isinstance(raw_company, (list, tuple)) and raw_company:
                    tenant_id = str(raw_company[0])
                    if len(raw_company) > 1 and raw_company[1]:
                        tenant_name = raw_company[1]
                elif raw_company:
                    tenant_id = str(raw_company)
    except Exception:
        pass
    return {
        'tenant_id': tenant_id,
        'tenant_name': tenant_name,
        'user_id': user_id,
        'user_name': user_name
    }


def create_timesheet_entry(odoo_service, employee_data: dict, context: dict, odoo_session_data: dict = None, metrics_service=None) -> Dict[str, Any]:
    """
    Create a timesheet entry in account.analytic.line.
    
    Args:
        odoo_service: Active Odoo service instance
        employee_data: Employee data dict
        context: Flow context with all the collected data
        odoo_session_data: Optional Odoo session data for stateless requests
        metrics_service: Optional metrics service for logging
    
    Returns:
        Response dict with success message or error
    """
    try:
        ok_session, msg = odoo_service.ensure_active_session()
        if not ok_session:
            return {
                'message': f'Failed to authenticate with Odoo: {msg}',
                'success': False
            }
        
        subtask_id = context.get('subtask_id')
        task_date = context.get('task_date')
        employee_id = context.get('employee_id') or employee_data.get('id')
        task_activity_id = context.get('task_activity_id')
        hours = context.get('hours', 0)
        description = context.get('description', '')
        
        if not subtask_id or not task_date or not employee_id:
            return {
                'message': 'Missing required information to create timesheet entry.',
                'success': False
            }
        
        # Prepare timesheet entry data
        timesheet_data = {
            'date': task_date,
            'employee_id': employee_id,
            'task_id': subtask_id,
            'unit_amount': hours,
            'name': description or f'Hours logged by Nasma',
        }
        
        # Add task activity if provided
        if task_activity_id:
            timesheet_data['x_studio_task_activity'] = task_activity_id
        
        params = {
            'args': [timesheet_data],
            'kwargs': {}
        }
        
        # Use stateless requests if session data provided
        if odoo_session_data and odoo_session_data.get('session_id') and odoo_session_data.get('user_id'):
            # Use stateless authenticated request with automatic retry
            result_dict = odoo_service.make_authenticated_request(
                model='account.analytic.line',
                method='create',
                args=params.get('args', []),
                kwargs=params.get('kwargs', {}),
                session_id=odoo_session_data['session_id'],
                user_id=odoo_session_data['user_id'],
                username=odoo_session_data.get('username'),
                password=odoo_session_data.get('password')
            )
            
            renewed_session = result_dict.pop('_renewed_session', None) if isinstance(result_dict, dict) else None
            
            # Update Flask session if session was renewed
            if renewed_session:
                try:
                    from flask import session as flask_session
                    flask_session['odoo_session_id'] = renewed_session['session_id']
                    flask_session['user_id'] = renewed_session['user_id']
                    flask_session.modified = True
                except Exception:
                    pass
            
            if 'error' in result_dict:
                return {
                    'message': f'Failed to create timesheet entry: {result_dict.get("error", "Unknown error")}',
                    'success': False
                }
            
            timesheet_id = result_dict.get('result') if isinstance(result_dict.get('result'), int) else None
        else:
            # Fallback to regular request
            ok, result = _make_odoo_request(odoo_service, 'account.analytic.line', 'create', params)
            if not ok:
                return {
                    'message': f'Failed to create timesheet entry: {result}',
                    'success': False
                }
            timesheet_id = result if isinstance(result, int) else None
        
        if not timesheet_id:
            return {
                'message': 'Failed to create timesheet entry: Invalid response',
                'success': False
            }
        
        # Log metrics for successful hours logging
        if metrics_service:
            try:
                identity = _resolve_identity(employee_data)
                task_name = context.get('task_name', '')
                task_activity_id = context.get('task_activity_id')
                
                # Generate thread_id for this log hours action
                import time
                thread_id = f"log_hours_{int(time.time() * 1000)}"
                
                metric_payload = {
                    'timesheet_id': timesheet_id,
                    'subtask_id': subtask_id,
                    'task_name': task_name,
                    'task_date': task_date,
                    'hours': hours,
                    'description': description or '',
                    'task_activity_id': task_activity_id,
                    'timestamp': datetime.now(timezone.utc).isoformat()
                }
                
                if identity.get('tenant_name'):
                    metric_payload.setdefault('context', {})['tenant_name'] = identity['tenant_name']
                
                logged = metrics_service.log_metric(
                    'log_hours',
                    thread_id,
                    user_id=identity.get('user_id'),
                    user_name=identity.get('user_name'),
                    tenant_id=identity.get('tenant_id'),
                    payload=metric_payload
                )
                if not logged:
                    metrics_error = getattr(metrics_service, "last_error", None)
                    debug_log(f"[LogHours] Metrics logging failed: {metrics_error}", "bot_logic")
            except Exception as e:
                # Don't let metrics failure affect the actual timesheet creation
                debug_log(f"[LogHours] Metrics logging error: {e}", "bot_logic")
        
        # Check if there are remaining tasks to log
        employee_name = employee_data.get('name', '')
        employee_id = employee_data.get('id')
        ok, remaining_tasks = _fetch_current_month_tasks(odoo_service, employee_name, employee_id=employee_id)
        
        response_data = {
            'message': f'✅ Successfully logged {hours:.1f} hours for {context.get("task_name", "the task")}!',
            'success': True,
            'timesheet_id': timesheet_id
        }
        
        # If there are remaining tasks, show the task table with a message and cancel button
        if ok and remaining_tasks and len(remaining_tasks) > 0:
            # Build task table widget (this filters out already logged tasks)
            tasks_table = build_tasks_table_widget(odoo_service, employee_data, remaining_tasks)
            
            # Check if there are any unlogged tasks in the table
            if tasks_table.get('rows') and len(tasks_table.get('rows', [])) > 0:
                response_data['message'] += '\n\n**Would you like to log another task?**'
                response_data['widgets'] = {
                    'tasks_table': tasks_table
                }
                response_data['buttons'] = [
                    {'text': 'Cancel', 'value': 'log_hours_cancel', 'type': 'action'}
                ]
        
        return response_data

    except Exception as e:
        return {
            'message': f'An error occurred while creating the timesheet entry: {str(e)}',
            'success': False
        }


def is_log_hours_trigger(message: str) -> bool:
    """
    Check if the user message should trigger the log hours flow.

    Matches variations like:
    - "log my hours"
    - "log my task"
    - "show my tasks"
    - "log my projects"
    - "log hours"
    - "my tasks"

    Args:
        message: User message text

    Returns:
        True if message should trigger log hours flow
    """
    try:
        text = (message or '').strip().lower()
        if not text:
            return False

        # Define trigger patterns
        triggers = [
            'log my hours',
            'log my task',
            'log my tasks',
            'show my tasks',
            'log my projects',
            'log my project',
            'log hours',
            'my tasks',
            'view my tasks',
            'see my tasks',
            'show tasks'
        ]

        # Check for exact or partial matches
        for trigger in triggers:
            if trigger in text:
                return True

        return False

    except Exception:
        return False
