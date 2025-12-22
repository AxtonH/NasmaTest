from typing import Any, Dict, List, Tuple, Optional
from datetime import datetime, timezone, timedelta
try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except Exception:
    ZoneInfo = None

# Import debug_log for logging
try:
    from .config.settings import debug_log
except Exception:
    try:
        from config.settings import debug_log
    except Exception:
        def debug_log(msg, cat):
            pass


def _parse_datetime(dt_str: str, user_tz: Optional[str] = None) -> Tuple[str, str]:
    """Parse datetime string (DD/MM/YYYY HH:MM:SS or YYYY-MM-DD HH:MM:SS) and return (date, hour).
    
    Assumes the datetime string from Odoo is in UTC and converts it to user's local timezone.
    
    Returns (date_str, hour_key) where:
    - date_str is in YYYY-MM-DD format (local date)
    - hour_key is like "9" or "9.5" for the hour dropdown (local time)
    
    Args:
        dt_str: Datetime string from Odoo (assumed UTC)
        user_tz: User's timezone (e.g., "Asia/Amman" for GMT+3)
    """
    if not dt_str:
        return '', ''
    
    try:
        # Parse the datetime string (assume UTC from Odoo)
        dt_utc = None
        try:
            # Try DD/MM/YYYY HH:MM:SS format first
            dt_utc = datetime.strptime(dt_str[:19], '%d/%m/%Y %H:%M:%S')
        except ValueError:
            try:
                # Try YYYY-MM-DD HH:MM:SS format
                dt_utc = datetime.strptime(dt_str[:19], '%Y-%m-%d %H:%M:%S')
            except ValueError:
                # Try just date part
                try:
                    dt_utc = datetime.strptime(dt_str[:10], '%Y-%m-%d')
                    return dt_utc.strftime('%Y-%m-%d'), ''
                except ValueError:
                    return '', ''
        
        # Mark as UTC
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
        
        # Convert to user's local timezone
        if user_tz and ZoneInfo:
            try:
                local_tz = ZoneInfo(user_tz)
                dt_local = dt_utc.astimezone(local_tz)
            except Exception:
                # Invalid timezone, use UTC
                dt_local = dt_utc
        else:
            # No timezone info, assume UTC
            dt_local = dt_utc
        
        date_str = dt_local.strftime('%Y-%m-%d')
        hour = dt_local.hour
        minute = dt_local.minute
        
        # Convert to hour key format (e.g., "9" or "9.5")
        if minute == 0:
            hour_key = str(hour)
        elif minute == 30:
            hour_key = f"{hour}.5"
        elif minute == 15:
            hour_key = f"{hour}.25"
        elif minute == 45:
            hour_key = f"{hour}.75"
        else:
            # Round to nearest 15 minutes
            quarter = round(minute / 15) * 15
            if quarter == 60:
                hour += 1
                hour_key = str(hour)
            elif quarter == 0:
                hour_key = str(hour)
            elif quarter == 15:
                hour_key = f"{hour}.25"
            elif quarter == 30:
                hour_key = f"{hour}.5"
            else:  # 45
                hour_key = f"{hour}.75"
        
        return date_str, hour_key
    except Exception:
        return '', ''


def _datetime_to_hour_key(dt_str: str) -> str:
    """Extract hour key from datetime string."""
    _, hour_key = _parse_datetime(dt_str)
    return hour_key


def _format_date(d: str) -> str:
    """Format date from YYYY-MM-DD to DD/MM/YYYY."""
    try:
        dt = datetime.strptime(d, '%Y-%m-%d')
        return dt.strftime('%d/%m/%Y')
    except Exception:
        return d or ''


def _format_date_label(d: str) -> str:
    """Format date from YYYY-MM-DD to DD/MM/YYYY."""
    try:
        dt = datetime.strptime(d, '%Y-%m-%d')
        return dt.strftime('%d/%m/%Y')
    except Exception:
        return d or ''


def _make_odoo_request(odoo_service, model: str, method: str, params: Dict) -> Tuple[bool, Any]:
    """Lightweight wrapper to call Odoo via the existing session using the same endpoint as services."""
    import requests
    try:
        url = f"{odoo_service.odoo_url}/web/dataset/call_kw"
        payload = {
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
        cookies = {'session_id': odoo_service.session_id} if odoo_service.session_id else {}
        # Use retry-aware post to handle session expiry automatically
        post = getattr(odoo_service, 'post_with_retry', None)
        if callable(post):
            resp = post(url, json=payload, cookies=cookies, timeout=20)
        else:
            resp = requests.post(url, json=payload, headers={'Content-Type': 'application/json'}, cookies=cookies, timeout=20)
        if resp.status_code != 200:
            return False, f"HTTP error: {resp.status_code}"
        try:
            result = resp.json()
            if 'result' in result:
                return True, result['result']
            return False, f"Odoo API error: {result.get('error', 'Unknown error')}"
        except Exception as je:
            return False, f"Invalid JSON from Odoo: {je}"
    except Exception as e:
        return False, f"Request error: {e}"


def fetch_user_overtime_requests(odoo_service, user_id: int) -> Tuple[bool, Any]:
    """Fetch overtime requests (approval.request) for the current user with request_status = 'pending' (Submitted).
    
    Returns (ok, requests) where requests is a list of approval.request dictionaries.
    """
    try:
        if not user_id:
            return False, "User ID not provided"

        # Ensure session is active
        ok_session, msg = odoo_service.ensure_active_session()
        if not ok_session:
            return False, msg

        # Domain: request_owner_id = current user AND request_status = 'pending' (Submitted)
        domain = [
            ('request_owner_id', '=', user_id),
            ('request_status', '=', 'pending')
        ]

        # Use two-step fetch: search => read
        search_params = {'args': [domain], 'kwargs': {}}
        ok, request_ids = _make_odoo_request(odoo_service, 'approval.request', 'search', search_params)
        if not ok:
            return False, request_ids
        if not isinstance(request_ids, list) or len(request_ids) == 0:
            return True, []

        # Try to read with hour fields first, fallback if they don't exist
        read_params = {
            'args': [request_ids],
            'kwargs': {
                'fields': ['id', 'name', 'request_owner_id', 'category_id', 'request_status', 'create_date', 
                          'x_studio_hours', 'date_start', 'date_end', 'x_studio_project']
            }
        }
        ok, requests = _make_odoo_request(odoo_service, 'approval.request', 'read', read_params)
        if not ok:
            return False, requests
        
        # Try to read hour fields separately if they exist (optional fields)
        # We'll check if the fields exist by trying to read them for one record
        if isinstance(requests, list) and len(requests) > 0:
            try:
                test_params = {
                    'args': [[requests[0]['id']]],
                    'kwargs': {'fields': ['x_studio_hour_from', 'x_studio_hour_to']}
                }
                ok_test, test_result = _make_odoo_request(odoo_service, 'approval.request', 'read', test_params)
                # Check if error is about invalid fields
                if ok_test and isinstance(test_result, list) and len(test_result) > 0:
                    # Fields exist, read them for all records
                    read_params_hours = {
                        'args': [request_ids],
                        'kwargs': {'fields': ['id', 'x_studio_hour_from', 'x_studio_hour_to']}
                    }
                    ok_hours, hours_data = _make_odoo_request(odoo_service, 'approval.request', 'read', read_params_hours)
                    if ok_hours and isinstance(hours_data, list):
                        # Merge hour data into requests
                        hours_by_id = {h.get('id'): h for h in hours_data}
                        for req in requests:
                            req_id = req.get('id')
                            if req_id in hours_by_id:
                                req['x_studio_hour_from'] = hours_by_id[req_id].get('x_studio_hour_from')
                                req['x_studio_hour_to'] = hours_by_id[req_id].get('x_studio_hour_to')
            except Exception:
                # Fields don't exist, continue without them
                pass

        return True, requests if isinstance(requests, list) else []
    except Exception as e:
        return False, f"Error fetching overtime requests: {e}"


def fetch_user_timeoff_requests(odoo_service, employee_id: int) -> Tuple[bool, Any]:
    """Fetch time off requests (hr.leave) for the current user with state = 'confirm' (To Approve).
    
    Returns (ok, leaves) where leaves is a list of hr.leave dictionaries.
    """
    try:
        if not employee_id:
            return False, "Employee ID not provided"

        # Ensure session is active
        ok_session, msg = odoo_service.ensure_active_session()
        if not ok_session:
            return False, msg

        # Domain: employee_id = current user AND state = 'confirm' (To Approve)
        domain = [
            ('employee_id', '=', employee_id),
            ('state', '=', 'confirm')
        ]

        params = {
            'args': [domain],
            'kwargs': {
                'fields': ['id', 'employee_id', 'holiday_status_id', 'request_date_from', 'request_date_to', 
                          'number_of_days', 'state', 'duration_display'],
                'limit': 500,
                'order': 'request_date_from desc'
            }
        }

        ok, leaves = _make_odoo_request(odoo_service, 'hr.leave', 'search_read', params)
        if not ok:
            return False, leaves

        return True, leaves if isinstance(leaves, list) else []
    except Exception as e:
        return False, f"Error fetching time off requests: {e}"


def fetch_actioned_timeoff_requests(odoo_service, employee_id: int) -> Tuple[bool, Any]:
    """Fetch actioned time off requests (hr.leave) for the current user for the current year.
    
    Includes requests with state in ['refuse', 'validate1', 'validate'] (Refused, Second Approval, Approved).
    
    Returns (ok, leaves) where leaves is a list of hr.leave dictionaries.
    """
    try:
        if not employee_id:
            return False, "Employee ID not provided"

        # Ensure session is active
        ok_session, msg = odoo_service.ensure_active_session()
        if not ok_session:
            return False, msg

        # Get current year start and end dates
        current_year = datetime.now().year
        year_start = f"{current_year}-01-01"
        year_end = f"{current_year}-12-31"

        # Domain: employee_id = current user AND state in ['refuse', 'validate1', 'validate'] 
        # AND request_date_from within current year
        domain = [
            ('employee_id', '=', employee_id),
            ('state', 'in', ['refuse', 'validate1', 'validate']),
            ('request_date_from', '>=', year_start),
            ('request_date_from', '<=', year_end)
        ]

        params = {
            'args': [domain],
            'kwargs': {
                'fields': ['id', 'employee_id', 'holiday_status_id', 'request_date_from', 'request_date_to', 
                          'number_of_days', 'state', 'duration_display'],
                'limit': 500,
                'order': 'request_date_from desc'
            }
        }

        ok, leaves = _make_odoo_request(odoo_service, 'hr.leave', 'search_read', params)
        if not ok:
            return False, leaves

        return True, leaves if isinstance(leaves, list) else []
    except Exception as e:
        return False, f"Error fetching actioned time off requests: {e}"


def fetch_actioned_overtime_requests(odoo_service, user_id: int) -> Tuple[bool, Any]:
    """Fetch actioned overtime requests (approval.request) for the current user for the current year.
    
    Includes requests with request_status in ['approved', 'refused'].
    
    Returns (ok, requests) where requests is a list of approval.request dictionaries.
    """
    try:
        if not user_id:
            return False, "User ID not provided"

        # Ensure session is active
        ok_session, msg = odoo_service.ensure_active_session()
        if not ok_session:
            return False, msg

        # Get current year start and end dates
        current_year = datetime.now().year
        year_start = f"{current_year}-01-01 00:00:00"
        year_end = f"{current_year}-12-31 23:59:59"

        # Domain: request_owner_id = current user AND request_status in ['approved', 'refused']
        # AND date_start within current year
        domain = [
            ('request_owner_id', '=', user_id),
            ('request_status', 'in', ['approved', 'refused']),
            ('date_start', '>=', year_start),
            ('date_start', '<=', year_end)
        ]

        # Use two-step fetch: search => read
        search_params = {'args': [domain], 'kwargs': {}}
        ok, request_ids = _make_odoo_request(odoo_service, 'approval.request', 'search', search_params)
        if not ok:
            return False, request_ids
        if not isinstance(request_ids, list) or len(request_ids) == 0:
            return True, []

        read_params = {
            'args': [request_ids],
            'kwargs': {
                'fields': ['id', 'name', 'request_owner_id', 'category_id', 'request_status', 'create_date', 
                          'x_studio_hours', 'date_start', 'date_end', 'x_studio_project']
            }
        }
        ok, requests = _make_odoo_request(odoo_service, 'approval.request', 'read', read_params)
        if not ok:
            return False, requests

        return True, requests if isinstance(requests, list) else []
    except Exception as e:
        return False, f"Error fetching actioned overtime requests: {e}"


def build_overtime_requests_table_widget(overtime_requests: List[Dict], user_tz: Optional[str] = None) -> Dict[str, Any]:
    """Build a table widget for overtime requests.
    
    Columns: Project, Dates, Duration, Status, Action
    """
    columns = [
        { 'key': 'project', 'label': 'Project' },
        { 'key': 'dates', 'label': 'Dates' },
        { 'key': 'duration', 'label': 'Duration' },
        { 'key': 'status', 'label': 'Status' },
        { 'key': 'edit', 'label': 'Action', 'align': 'center' },
    ]

    rows: List[Dict[str, str]] = []

    # Process overtime requests
    for req in overtime_requests or []:
        req_id = req.get('id')
        if not req_id:
            continue
            
        # Parse datetime strings from date_start and date_end (convert from UTC to local timezone)
        date_start_str = req.get('date_start') or ''
        date_end_str = req.get('date_end') or ''
        
        # Extract date and hour from datetime strings (converting UTC to local)
        start_date, start_hour = _parse_datetime(date_start_str, user_tz)
        end_date, end_hour = _parse_datetime(date_end_str, user_tz)
        
        # Format dates for display
        start = _format_date_label(start_date) if start_date else "—"
        end = _format_date_label(end_date) if end_date else "—"
        
        # Combine dates into single column with from/to format
        dates = f"from<br/>{start}<br/>to<br/>{end}" if start and end else "—"

        # Calculate duration from datetime difference (using UTC times directly)
        dur = '—'
        try:
            if date_start_str and date_end_str:
                # Parse both datetime strings (assume UTC from Odoo)
                dt_start_utc = None
                dt_end_utc = None
                
                try:
                    dt_start_utc = datetime.strptime(date_start_str[:19], '%d/%m/%Y %H:%M:%S')
                except ValueError:
                    try:
                        dt_start_utc = datetime.strptime(date_start_str[:19], '%Y-%m-%d %H:%M:%S')
                    except ValueError:
                        dt_start_utc = None
                
                try:
                    dt_end_utc = datetime.strptime(date_end_str[:19], '%d/%m/%Y %H:%M:%S')
                except ValueError:
                    try:
                        dt_end_utc = datetime.strptime(date_end_str[:19], '%Y-%m-%d %H:%M:%S')
                    except ValueError:
                        dt_end_utc = None
                
                if dt_start_utc and dt_end_utc:
                    # Mark as UTC for proper timezone-aware calculation
                    dt_start_utc = dt_start_utc.replace(tzinfo=timezone.utc)
                    dt_end_utc = dt_end_utc.replace(tzinfo=timezone.utc)
                    diff = dt_end_utc - dt_start_utc
                    hours = diff.total_seconds() / 3600.0
                    if hours == int(hours):
                        dur = f"{int(hours)} hours"
                    else:
                        dur = f"{hours:.1f} hours"
            else:
                # Fallback to x_studio_hours field if datetime parsing fails
                hours = req.get('x_studio_hours')
                if hours is not None and hours != 0:
                    hours_float = float(hours)
                    if hours_float == int(hours_float):
                        dur = f"{int(hours_float)} hours"
                    else:
                        dur = f"{hours_float:.1f} hours"
        except Exception:
            pass

        # Project from x_studio_project field
        project_name = '—'
        project_id = None
        try:
            project_val = req.get('x_studio_project')
            if project_val:
                if isinstance(project_val, (list, tuple)) and len(project_val) > 0:
                    project_id = project_val[0] if isinstance(project_val[0], int) else None
                    if len(project_val) > 1:
                        project_name = str(project_val[1])
                    elif project_id:
                        project_name = str(project_id)
                else:
                    project_name = str(project_val)
        except Exception:
            pass

        status_raw = req.get('request_status') or req.get('state') or ''
        status_map = {
            'new': 'To Submit',
            'pending': 'Submitted',
            'to_approve': 'To Approve',
            'approved': 'Approved',
            'refused': 'Refused',
            'cancel': 'Cancel'
        }
        state_txt = status_map.get(status_raw, status_raw.title() if isinstance(status_raw, str) else '—')
        
        # Build edit and cancel buttons HTML (matching approval button style from show my team flow)
        # Note: Don't use data-action/data-model for cancel - it has its own handler
        # Edit button uses btn-gradient (purple) to match time off edit button style
        edit_button_html = (
            f"<button class=\"approval-button btn-gradient text-white border-2 border-purple-700 edit-overtime-btn\" "
            f"data-request-id=\"{req_id}\">Edit</button>"
        )
        cancel_button_html = (
            f"<button class=\"approval-button bg-red-600 text-white hover:bg-red-700 border-2 border-red-700 mt-1 cancel-overtime-btn\" "
            f"data-request-id=\"{req_id}\">Cancel</button>"
        )
        action_html = f"<div class=\"flex flex-col items-center\">{edit_button_html}{cancel_button_html}</div>"

        rows.append({
            'project': project_name,
            'dates': dates,
            'duration': dur,
            'status': state_txt,
            'edit': action_html,
        })

    return { 'columns': columns, 'rows': rows }


def build_timeoff_requests_table_widget(timeoff_requests: List[Dict]) -> Dict[str, Any]:
    """Build a table widget for time off requests.
    
    Columns: Type, Dates, Duration, Status, Action
    """
    columns = [
        { 'key': 'type', 'label': 'Type' },
        { 'key': 'dates', 'label': 'Dates' },
        { 'key': 'duration', 'label': 'Duration' },
        { 'key': 'status', 'label': 'Status' },
        { 'key': 'edit', 'label': 'Action', 'align': 'center' },
    ]

    rows: List[Dict[str, str]] = []

    # Process time off requests
    for lv in timeoff_requests or []:
        # Get leave type label
        leave_type = 'Time off'
        try:
            holiday_status = lv.get('holiday_status_id')
            if isinstance(holiday_status, list) and len(holiday_status) > 1:
                leave_type = str(holiday_status[1])
        except Exception:
            pass

        start = _format_date(lv.get('request_date_from') or '')
        end = _format_date(lv.get('request_date_to') or '')
        
        # Combine dates into single column with from/to format
        dates = f"from<br/>{start}<br/>to<br/>{end}" if start and end else "—"
        
        # Use duration_display field for duration
        duration = lv.get('duration_display') or '—'
        
        state = lv.get('state')
        state_map = {
            'draft': 'To Submit',
            'confirm': 'To Approve',
            'refuse': 'Refused',
            'validate1': 'Second Approval',
            'validate': 'Approved'
        }
        state_txt = state_map.get(state, 'Pending' if state else '—')
        
        # Build edit and cancel buttons HTML (matching overtime requests style)
        # Note: Don't use data-action/data-model for cancel - it has its own handler
        # Edit button uses btn-gradient (purple) to match overtime edit button style
        # Only show edit button if the leave hasn't started yet (editing started leaves requires manager permissions)
        leave_id = lv.get('id')
        if leave_id:
            # Check if leave has already started (including today)
            leave_started = False
            try:
                from datetime import date
                request_date_from = lv.get('request_date_from')
                if request_date_from:
                    # Parse the date string (format: YYYY-MM-DD)
                    if isinstance(request_date_from, str):
                        leave_start_date = date.fromisoformat(request_date_from[:10])
                    else:
                        leave_start_date = request_date_from
                    # Compare with today's date (if start date is today or before, leave has started)
                    today = date.today()
                    if leave_start_date <= today:
                        leave_started = True
            except Exception:
                # If we can't parse the date, err on the side of caution and allow editing
                pass
            
            cancel_button_html = (
                f"<button class=\"approval-button bg-red-600 text-white hover:bg-red-700 border-2 border-red-700 cancel-timeoff-btn\" "
                f"data-request-id=\"{leave_id}\">Cancel</button>"
            )
            
            if leave_started:
                # Don't show any buttons if leave has already started (cannot edit or cancel)
                action_html = '<span class="text-gray-400">—</span>'
            else:
                # Show both edit and cancel buttons if leave hasn't started yet
                edit_button_html = (
                    f"<button class=\"approval-button btn-gradient text-white border-2 border-purple-700 edit-timeoff-btn\" "
                    f"data-request-id=\"{leave_id}\">Edit</button>"
                )
                action_html = f"<div class=\"flex flex-col items-center\">{edit_button_html}{cancel_button_html}</div>"
        else:
            action_html = '<span class="text-gray-400">—</span>'

        rows.append({
            'dates': dates,
            'duration': duration,
            'type': leave_type,
            'status': state_txt,
            'edit': action_html,
        })

    return { 'columns': columns, 'rows': rows }


def build_actioned_requests_table_widget(actioned_overtime: List[Dict], actioned_timeoff: List[Dict], user_tz: Optional[str] = None, limit: Optional[int] = None) -> Dict[str, Any]:
    """Build a table widget for actioned requests (overtime and time-off combined).
    
    Columns: Type, Project, Dates, Duration, Status
    Status pills: Green for Approved, Red for Refused, Orange for Second Approval
    
    Args:
        actioned_overtime: List of actioned overtime request dicts
        actioned_timeoff: List of actioned time off request dicts
        user_tz: User's timezone (e.g., "Asia/Amman")
        limit: Optional limit on number of rows to return (for main table view)
    """
    columns = [
        { 'key': 'type', 'label': 'Type' },
        { 'key': 'project', 'label': 'Project' },
        { 'key': 'dates', 'label': 'Dates' },
        { 'key': 'duration', 'label': 'Duration' },
        { 'key': 'status', 'label': 'Status', 'align': 'center' },
    ]

    rows: List[Dict[str, Any]] = []

    # Process actioned overtime requests
    for req in actioned_overtime or []:
        req_id = req.get('id')
        if not req_id:
            continue
            
        # Parse datetime strings from date_start and date_end (convert from UTC to local timezone)
        date_start_str = req.get('date_start') or ''
        date_end_str = req.get('date_end') or ''
        
        # Extract date and hour from datetime strings (converting UTC to local)
        start_date, start_hour = _parse_datetime(date_start_str, user_tz)
        end_date, end_hour = _parse_datetime(date_end_str, user_tz)
        
        # Format dates for display
        start = _format_date_label(start_date) if start_date else "—"
        end = _format_date_label(end_date) if end_date else "—"
        
        # Combine dates into single column with from/to format
        dates = f"from<br/>{start}<br/>to<br/>{end}" if start and end else "—"
        
        # Calculate duration
        dur = "—"
        try:
            if date_start_str and date_end_str:
                start_dt = datetime.strptime(date_start_str[:19], '%Y-%m-%d %H:%M:%S')
                end_dt = datetime.strptime(date_end_str[:19], '%Y-%m-%d %H:%M:%S')
                delta = end_dt - start_dt
                total_hours = delta.total_seconds() / 3600
                hours = int(total_hours)
                minutes = int((total_hours - hours) * 60)
                if minutes == 0:
                    dur = f"{hours}h"
                else:
                    dur = f"{hours}h {minutes}m"
        except Exception:
            pass
        
        # Get project name
        project_field = req.get('x_studio_project')
        project_name = '—'
        if isinstance(project_field, list) and len(project_field) > 1:
            project_name = str(project_field[1])
        elif isinstance(project_field, str):
            project_name = project_field
        
        # Get status and create colored pill
        status_raw = req.get('request_status') or ''
        status_map = {
            'approved': 'Approved',
            'refused': 'Refused'
        }
        status_txt = status_map.get(status_raw, status_raw.title() if isinstance(status_raw, str) else '—')
        
        # Create status pill with color (matching show my team flow - solid colors with white text)
        if status_raw == 'approved':
            status_html = '<span class="inline-flex items-center justify-center px-3 py-1 rounded-full text-xs font-semibold text-white bg-green-600" style="min-width: 70px;">Approved</span>'
        elif status_raw == 'refused':
            status_html = '<span class="inline-flex items-center justify-center px-3 py-1 rounded-full text-xs font-semibold text-gray-700 bg-gray-300" style="min-width: 70px;">Refused</span>'
        else:
            status_html = f'<span class="inline-flex items-center justify-center px-3 py-1 rounded-full text-xs font-semibold text-white bg-gray-600" style="min-width: 70px;">{status_txt}</span>'

        # Extract date for sorting (use date_start)
        sort_date = ''
        try:
            if date_start_str:
                sort_date = date_start_str[:10]  # YYYY-MM-DD
        except Exception:
            pass
        
        rows.append({
            'type': 'Overtime',
            'project': project_name,
            'dates': dates,
            'duration': dur,
            'status': status_html,
            '_sort_date': sort_date,  # Internal field for sorting
        })

    # Process actioned time off requests
    for lv in actioned_timeoff or []:
        # Get leave type label
        leave_type = 'Time off'
        try:
            holiday_status = lv.get('holiday_status_id')
            if isinstance(holiday_status, list) and len(holiday_status) > 1:
                leave_type = str(holiday_status[1])
        except Exception:
            pass

        start = _format_date(lv.get('request_date_from') or '')
        end = _format_date(lv.get('request_date_to') or '')
        
        # Combine dates into single column with from/to format
        dates = f"from<br/>{start}<br/>to<br/>{end}" if start and end else "—"
        
        # Use duration_display field for duration
        duration = lv.get('duration_display') or '—'
        
        # Get status and create colored pill
        state = lv.get('state')
        state_map = {
            'refuse': 'Refused',
            'validate1': 'Second Approval',
            'validate': 'Approved'
        }
        state_txt = state_map.get(state, 'Pending' if state else '—')
        
        # Create status pill with color (matching show my team flow - solid colors with white text)
        # Second Approval uses orange background with white text, split across two lines
        if state == 'validate':
            status_html = '<span class="inline-flex items-center justify-center px-3 py-1 rounded-full text-xs font-semibold text-white bg-green-600" style="min-width: 70px;">Approved</span>'
        elif state == 'refuse':
            status_html = '<span class="inline-flex items-center justify-center px-3 py-1 rounded-full text-xs font-semibold text-gray-700 bg-gray-300" style="min-width: 70px;">Refused</span>'
        elif state == 'validate1':
            status_html = '<span class="inline-flex flex-col items-center justify-center px-3 py-1 rounded-full text-xs font-semibold text-white bg-orange-500" style="min-width: 70px; line-height: 1.2;"><span>Second</span><span>approval</span></span>'
        else:
            status_html = f'<span class="inline-flex items-center justify-center px-3 py-1 rounded-full text-xs font-semibold text-white bg-gray-600" style="min-width: 70px;">{state_txt}</span>'

        # Extract date for sorting (use request_date_from)
        sort_date = ''
        try:
            date_from = lv.get('request_date_from')
            if date_from:
                sort_date = str(date_from)[:10]  # YYYY-MM-DD
        except Exception:
            pass
        
        rows.append({
            'type': 'Time Off',
            'project': leave_type,  # For time-off, project is the leave type (Annual Leave, Sick Leave, etc.)
            'dates': dates,
            'duration': duration,
            'status': status_html,
            '_sort_date': sort_date,  # Internal field for sorting
        })

    # Sort by date descending (most recent first)
    rows.sort(key=lambda x: x.get('_sort_date', ''), reverse=True)
    
    # Remove internal sort field before returning
    for row in rows:
        row.pop('_sort_date', None)
    
    # Apply limit if specified
    if limit is not None and limit > 0:
        rows = rows[:limit]

    return { 'columns': columns, 'rows': rows }


def build_my_requests_table_widget(overtime_requests: List[Dict], timeoff_requests: List[Dict], 
                                   actioned_overtime: List[Dict] = None, actioned_timeoff: List[Dict] = None,
                                   user_tz: Optional[str] = None) -> Dict[str, Any]:
    """Build separate table widgets for overtime, time off, and actioned requests.
    
    Returns a dict with 'overtime', 'timeoff', and 'actioned' keys, each containing a table widget.
    
    Args:
        overtime_requests: List of overtime request dicts
        timeoff_requests: List of time off request dicts
        actioned_overtime: List of actioned overtime request dicts (optional)
        actioned_timeoff: List of actioned time off request dicts (optional)
        user_tz: User's timezone (e.g., "Asia/Amman")
    """
    result = {
        'overtime': build_overtime_requests_table_widget(overtime_requests, user_tz),
        'timeoff': build_timeoff_requests_table_widget(timeoff_requests)
    }
    
    # Add actioned requests table if data is provided
    # Main table shows top 5, full table shows all
    if actioned_overtime is not None and actioned_timeoff is not None:
        result['actioned'] = build_actioned_requests_table_widget(actioned_overtime, actioned_timeoff, user_tz, limit=5)
        result['actioned_full'] = build_actioned_requests_table_widget(actioned_overtime, actioned_timeoff, user_tz, limit=None)
    
    return result


def get_my_requests(odoo_service, employee_data: Dict) -> Tuple[bool, Any]:
    """High-level helper to fetch and assemble user's overtime and time off requests.
    
    Returns (ok, data) where data contains overtime_requests and timeoff_requests lists.
    """
    try:
        # Get user_id (Odoo res.users) and employee_id from employee_data
        user_id = None
        employee_id = None
        
        if isinstance(employee_data, dict):
            employee_id = employee_data.get('id')
            # Get user_id from employee_data.user_id field (Many2one: [id, 'name'])
            user_id_field = employee_data.get('user_id')
            if isinstance(user_id_field, list) and len(user_id_field) > 0:
                user_id = user_id_field[0]
            elif isinstance(user_id_field, int):
                user_id = user_id_field
            
            # Fallback: try to get from odoo_service if not in employee_data
            if not user_id:
                user_id = getattr(odoo_service, 'user_id', None)

        if not user_id:
            return False, "User ID not found"
        if not employee_id:
            return False, "Employee ID not found"

        # Fetch overtime requests
        ok_ot, ot_requests = fetch_user_overtime_requests(odoo_service, user_id)
        if not ok_ot:
            return False, f"Error fetching overtime requests: {ot_requests}"

        # Fetch time off requests
        ok_to, to_requests = fetch_user_timeoff_requests(odoo_service, employee_id)
        if not ok_to:
            return False, f"Error fetching time off requests: {to_requests}"

        # Fetch actioned requests for current year
        ok_actioned_ot, actioned_ot_requests = fetch_actioned_overtime_requests(odoo_service, user_id)
        if not ok_actioned_ot:
            # Don't fail if actioned requests can't be fetched, just log and continue
            actioned_ot_requests = []
        
        ok_actioned_to, actioned_to_requests = fetch_actioned_timeoff_requests(odoo_service, employee_id)
        if not ok_actioned_to:
            # Don't fail if actioned requests can't be fetched, just log and continue
            actioned_to_requests = []

        return True, {
            'overtime_requests': ot_requests if isinstance(ot_requests, list) else [],
            'timeoff_requests': to_requests if isinstance(to_requests, list) else [],
            'actioned_overtime_requests': actioned_ot_requests if isinstance(actioned_ot_requests, list) else [],
            'actioned_timeoff_requests': actioned_to_requests if isinstance(actioned_to_requests, list) else []
        }
    except Exception as e:
        return False, f"Error getting my requests: {e}"


def format_my_requests_message(overtime_count: int, timeoff_count: int) -> str:
    """Format a message describing the user's requests."""
    if overtime_count == 0 and timeoff_count == 0:
        return "You don't have any pending requests at the moment.\n\n*What would you like to do next?*"
    
    parts = []
    if overtime_count > 0:
        parts.append(f"{overtime_count} overtime request{'s' if overtime_count > 1 else ''}")
    if timeoff_count > 0:
        parts.append(f"{timeoff_count} time off request{'s' if timeoff_count > 1 else ''}")
    
    message = f"Here are your pending requests:\n\n"
    if parts:
        message += f"You have {', and '.join(parts)} waiting for approval.\n\n"
    message += "*What would you like to do next?*"
    
    return message


def get_overtime_request_for_edit(odoo_service, request_id: int, user_tz: Optional[str] = None) -> Tuple[bool, Any]:
    """Fetch overtime request details for editing.
    
    Returns (ok, request_data) where request_data contains fields needed for editing.
    """
    try:
        if not request_id:
            return False, "Request ID not provided"

        # Ensure session is active
        ok_session, msg = odoo_service.ensure_active_session()
        if not ok_session:
            return False, msg

        # Read the request with all needed fields
        # Read basic fields first
        read_params = {
            'args': [[request_id]],
            'kwargs': {
                'fields': ['id', 'date_start', 'date_end', 'x_studio_hours', 'x_studio_project', 
                          'request_status', 'create_date', 'reason']
            }
        }
        ok, requests = _make_odoo_request(odoo_service, 'approval.request', 'read', read_params)
        if not ok:
            return False, requests

        if not isinstance(requests, list) or len(requests) == 0:
            return False, "Request not found"

        request_data = requests[0]
        
        # Parse datetime strings to extract date and hour (convert from UTC to local timezone)
        date_start_str = request_data.get('date_start', '')
        date_end_str = request_data.get('date_end', '')
        
        start_date, start_hour = _parse_datetime(date_start_str, user_tz)
        end_date, end_hour = _parse_datetime(date_end_str, user_tz)
        
        # Format date for frontend (DD/MM/YYYY)
        date_start_formatted = ''
        if start_date:
            try:
                dt = datetime.strptime(start_date, '%Y-%m-%d')
                date_start_formatted = dt.strftime('%d/%m/%Y')
            except Exception:
                pass
        
        return True, {
            'id': request_data.get('id'),
            'date_start': date_start_formatted,
            'date_end': end_date,  # Not used in edit form (same day)
            'hours': request_data.get('x_studio_hours'),
            'hour_from': start_hour,
            'hour_to': end_hour,
            'project_id': request_data.get('x_studio_project')[0] if isinstance(request_data.get('x_studio_project'), list) else request_data.get('x_studio_project'),
            'reason': request_data.get('reason', ''),  # Description/reason field
            'status': request_data.get('request_status')
        }
    except Exception as e:
        return False, f"Error fetching request for edit: {e}"


def update_overtime_request(odoo_service, request_id: int, date_start: str, date_end: str, 
                            hour_from: str, hour_to: str, project_id: int, user_tz: Optional[str] = None,
                            description: Optional[str] = None) -> Tuple[bool, Any]:
    """Update an overtime request.
    
    Workflow:
    1. Change status to 'new' (To Submit)
    2. Update the request fields (combining date and hour into datetime strings)
    3. Change status back to 'pending' (Submitted)
    
    Args:
        date_start: DD/MM/YYYY format (from frontend)
        date_end: DD/MM/YYYY format (same as date_start for overtime)
        hour_from: Hour key (e.g., "9" or "9.5")
        hour_to: Hour key (e.g., "17" or "17.5")
        project_id: Project ID
        description: Optional description/reason field
    """
    try:
        if not request_id:
            return False, "Request ID not provided"

        # Ensure session is active
        ok_session, msg = odoo_service.ensure_active_session()
        if not ok_session:
            return False, msg

        # Step 1: Change status to 'new' (To Submit)
        write_params = {
            'args': [[request_id], {'request_status': 'new'}],
            'kwargs': {}
        }
        ok, result = _make_odoo_request(odoo_service, 'approval.request', 'write', write_params)
        if not ok:
            return False, f"Failed to change status to 'new': {result}"

        # Step 2: Convert hour keys to HH:MM format and create local datetime
        def hour_key_to_time(hour_key: str) -> str:
            """Convert hour key (e.g., "9" or "9.5") to HH:MM format."""
            try:
                hour_float = float(hour_key)
                hour = int(hour_float)
                minute = int((hour_float - hour) * 60)
                return f"{hour:02d}:{minute:02d}"
            except Exception:
                return "00:00"
        
        hour_from_time = hour_key_to_time(hour_from) if hour_from else "00:00"
        hour_to_time = hour_key_to_time(hour_to) if hour_to else "00:00"
        
        # Step 3: Parse date (DD/MM/YYYY) and create local datetime objects
        try:
            # Parse date from DD/MM/YYYY
            dt_date = datetime.strptime(date_start, '%d/%m/%Y')
            
            # Parse times
            hour_from_parts = hour_from_time.split(':')
            hour_to_parts = hour_to_time.split(':')
            
            dt_local_start = dt_date.replace(
                hour=int(hour_from_parts[0]),
                minute=int(hour_from_parts[1]),
                second=0
            )
            dt_local_end = dt_date.replace(
                hour=int(hour_to_parts[0]),
                minute=int(hour_to_parts[1]),
                second=0
            )
            
            # Convert local datetime to UTC
            if user_tz and ZoneInfo:
                try:
                    local_tz = ZoneInfo(user_tz)
                    dt_local_start = dt_local_start.replace(tzinfo=local_tz)
                    dt_local_end = dt_local_end.replace(tzinfo=local_tz)
                    dt_utc_start = dt_local_start.astimezone(timezone.utc)
                    dt_utc_end = dt_local_end.astimezone(timezone.utc)
                except Exception:
                    # Invalid timezone, assume UTC
                    dt_utc_start = dt_local_start.replace(tzinfo=timezone.utc)
                    dt_utc_end = dt_local_end.replace(tzinfo=timezone.utc)
            else:
                # No timezone, assume already UTC
                dt_utc_start = dt_local_start.replace(tzinfo=timezone.utc)
                dt_utc_end = dt_local_end.replace(tzinfo=timezone.utc)
            
            # Format as YYYY-MM-DD HH:MM:SS (Odoo format - Odoo expects YYYY-MM-DD, not DD/MM/YYYY)
            date_start_datetime = dt_utc_start.strftime('%Y-%m-%d %H:%M:%S')
            date_end_datetime = dt_utc_end.strftime('%Y-%m-%d %H:%M:%S')
        except Exception as e:
            # Fallback: combine date and time without timezone conversion
            # Convert date from DD/MM/YYYY to YYYY-MM-DD format
            try:
                dt_fallback = datetime.strptime(date_start, '%d/%m/%Y')
                date_ymd = dt_fallback.strftime('%Y-%m-%d')
                date_start_datetime = f"{date_ymd} {hour_from_time}:00"
                date_end_datetime = f"{date_ymd} {hour_to_time}:00"
            except Exception:
                # Last resort fallback
                date_start_datetime = f"{date_start} {hour_from_time}:00"
                date_end_datetime = f"{date_start} {hour_to_time}:00"
        
        # Calculate hours for x_studio_hours field (if it exists)
        try:
            from_hour = float(hour_from) if hour_from else 0.0
            to_hour = float(hour_to) if hour_to else 0.0
            # Handle wrap-around (e.g., 23:00 to 01:00)
            if to_hour < from_hour:
                to_hour += 24.0
            hours = to_hour - from_hour
        except Exception:
            hours = None

        # Step 4: Update request fields
        update_fields = {
            'date_start': date_start_datetime,
            'date_end': date_end_datetime,
            'x_studio_project': project_id,
        }
        
        # Add hours field if it exists (optional)
        if hours is not None:
            update_fields['x_studio_hours'] = hours
        
        # Add description (reason field) if provided
        if description and description.strip():
            update_fields['reason'] = description.strip()

        write_params = {
            'args': [[request_id], update_fields],
            'kwargs': {}
        }
        ok, result = _make_odoo_request(odoo_service, 'approval.request', 'write', write_params)
        if not ok:
            return False, f"Failed to update request: {result}"

        # Step 4: Change status back to 'pending' (Submitted)
        write_params = {
            'args': [[request_id], {'request_status': 'pending'}],
            'kwargs': {}
        }
        ok, result = _make_odoo_request(odoo_service, 'approval.request', 'write', write_params)
        if not ok:
            return False, f"Failed to change status to 'pending': {result}"

        return True, "Request updated successfully"
    except Exception as e:
        return False, f"Error updating request: {e}"


def cancel_overtime_request(odoo_service, request_id: int) -> Tuple[bool, Any]:
    """Cancel an overtime request by setting request_status to 'cancel'.
    
    Args:
        request_id: The ID of the approval.request to cancel
    """
    try:
        if not request_id:
            return False, "Request ID not provided"

        # Ensure session is active
        ok_session, msg = odoo_service.ensure_active_session()
        if not ok_session:
            return False, msg

        # Update request_status to 'cancel'
        write_params = {
            'args': [[request_id], {'request_status': 'cancel'}],
            'kwargs': {}
        }
        ok, result = _make_odoo_request(odoo_service, 'approval.request', 'write', write_params)
        if not ok:
            return False, f"Failed to cancel request: {result}"

        return True, "Request cancelled successfully"
    except Exception as e:
        return False, f"Error cancelling request: {e}"


def cancel_timeoff_request(odoo_service, leave_id: int, employee_data: Dict = None) -> Tuple[bool, Any]:
    """Cancel or delete a time-off request.
    
    First attempts to delete the request. If deletion is not allowed (e.g., already approved),
    sets the state to 'draft' (To Submit) instead.
    
    Args:
        leave_id: The ID of the hr.leave to cancel/delete
        employee_data: Optional employee data dict for logging context
    """
    try:
        # Import debug_log at the top level
        try:
            from .config.settings import debug_log
        except Exception:
            try:
                from config.settings import debug_log
            except Exception:
                def debug_log(msg, cat):
                    print(f"DEBUG [{cat}]: {msg}")
        
        # Log entry point with context
        employee_id = employee_data.get('id') if employee_data else None
        employee_name = employee_data.get('name') if employee_data else 'Unknown'
        debug_log(f"[CANCEL_TIMEOFF] Starting cancel for leave_id={leave_id}, employee_id={employee_id}, employee_name={employee_name}", "bot_logic")
        
        if not leave_id:
            debug_log(f"[CANCEL_TIMEOFF] ERROR: Leave ID not provided", "bot_logic")
            return False, "Leave ID not provided"

        # Ensure session is active
        ok_session, msg = odoo_service.ensure_active_session()
        if not ok_session:
            debug_log(f"[CANCEL_TIMEOFF] ERROR: Session not active - {msg}", "bot_logic")
            return False, msg

        # First, read the current state of the request to understand what we're working with
        ok_read = False
        leave_data = None
        leave_started = False
        request_date_from = None
        
        try:
            read_params = {
                'args': [[leave_id]],
                'kwargs': {'fields': ['id', 'employee_id', 'state', 'request_date_from', 'request_date_to']}
            }
            ok_read, leave_data = _make_odoo_request(odoo_service, 'hr.leave', 'read', read_params)
            if ok_read and isinstance(leave_data, list) and len(leave_data) > 0:
                current_state = leave_data[0].get('state', 'unknown')
                request_employee_id = None
                emp_data = leave_data[0].get('employee_id')
                if isinstance(emp_data, (list, tuple)) and len(emp_data) > 0:
                    request_employee_id = emp_data[0]
                elif isinstance(emp_data, int):
                    request_employee_id = emp_data
                request_date_from = leave_data[0].get('request_date_from', '')
                debug_log(f"[CANCEL_TIMEOFF] Request details - state={current_state}, request_employee_id={request_employee_id}, current_employee_id={employee_id}, request_date_from={request_date_from}", "bot_logic")
                
                # Check if employee matches (security check)
                if employee_id and request_employee_id and employee_id != request_employee_id:
                    debug_log(f"[CANCEL_TIMEOFF] ERROR: Employee mismatch - request belongs to {request_employee_id}, current user is {employee_id}", "bot_logic")
                    return False, "You can only cancel your own requests"
                
                # Check if leave has already started
                if request_date_from:
                    from datetime import date
                    try:
                        # Parse the date string (format: YYYY-MM-DD)
                        if isinstance(request_date_from, str):
                            leave_start_date = date.fromisoformat(request_date_from[:10])
                        else:
                            leave_start_date = request_date_from
                        # Compare with today's date (if start date is today or before, leave has started)
                        today = date.today()
                        if leave_start_date <= today:
                            leave_started = True
                            debug_log(f"[CANCEL_TIMEOFF] Leave has already started (start_date={request_date_from}, today={today})", "bot_logic")
                    except Exception as date_parse_error:
                        debug_log(f"[CANCEL_TIMEOFF] WARNING: Error parsing date to check if started: {str(date_parse_error)}", "bot_logic")
            else:
                debug_log(f"[CANCEL_TIMEOFF] WARNING: Could not read request details - {leave_data}", "bot_logic")
        except Exception as read_error:
            debug_log(f"[CANCEL_TIMEOFF] WARNING: Error reading request details: {str(read_error)}", "bot_logic")

        # First, try to delete the request
        debug_log(f"[CANCEL_TIMEOFF] Attempting to delete leave_id={leave_id} (leave_started={leave_started})", "bot_logic")
        try:
            unlink_params = {
                'args': [[leave_id]],
                'kwargs': {}
            }
            ok_delete, result_delete = _make_odoo_request(odoo_service, 'hr.leave', 'unlink', unlink_params)
            debug_log(f"[CANCEL_TIMEOFF] Delete attempt result - ok={ok_delete}, result={result_delete}", "bot_logic")
            if ok_delete:
                debug_log(f"[CANCEL_TIMEOFF] SUCCESS: Request deleted successfully", "bot_logic")
                return True, "Request deleted successfully"
            else:
                debug_log(f"[CANCEL_TIMEOFF] Delete failed: {result_delete}", "bot_logic")
                # Check if deletion failed because leave has started
                error_msg = str(result_delete) if result_delete else ''
                if leave_started or 'started' in error_msg.lower() or 'manager' in error_msg.lower():
                    debug_log(f"[CANCEL_TIMEOFF] Delete failed for started leave - returning permission error", "bot_logic")
                    return False, "Cannot cancel a time off request that has already started. Please contact a Time Off Manager to cancel this request."
        except Exception as delete_error:
            debug_log(f"[CANCEL_TIMEOFF] Delete exception: {str(delete_error)}", "bot_logic")
            import traceback
            debug_log(f"[CANCEL_TIMEOFF] Delete traceback: {traceback.format_exc()}", "bot_logic")
            # If deletion fails and leave has started, don't try state change
            if leave_started:
                debug_log(f"[CANCEL_TIMEOFF] Delete exception for started leave - returning permission error", "bot_logic")
                return False, "Cannot cancel a time off request that has already started. Please contact a Time Off Manager to cancel this request."

        # If deletion didn't work and leave hasn't started, try to set state to 'draft' (To Submit)
        # Only do this for leaves that haven't started yet
        if leave_started:
            debug_log(f"[CANCEL_TIMEOFF] Leave has started - skipping state change attempt", "bot_logic")
            return False, "Cannot cancel a time off request that has already started. Please contact a Time Off Manager to cancel this request."
        
        debug_log(f"[CANCEL_TIMEOFF] Delete failed for non-started leave, attempting to set state to 'draft' for leave_id={leave_id}", "bot_logic")
        write_params = {
            'args': [[leave_id], {'state': 'draft'}],
            'kwargs': {}
        }
        ok, result = _make_odoo_request(odoo_service, 'hr.leave', 'write', write_params)
        debug_log(f"[CANCEL_TIMEOFF] Write state to 'draft' result - ok={ok}, result={result}", "bot_logic")
        if not ok:
            debug_log(f"[CANCEL_TIMEOFF] ERROR: Failed to cancel request - {result}", "bot_logic")
            # Check if the error is about started leave
            error_msg = str(result) if result else ''
            if 'started' in error_msg.lower() or 'manager' in error_msg.lower():
                return False, "Cannot cancel a time off request that has already started. Please contact a Time Off Manager to cancel this request."
            return False, f"Failed to cancel request: {result}"

        debug_log(f"[CANCEL_TIMEOFF] SUCCESS: Request cancelled (state set to draft)", "bot_logic")
        return True, "Request cancelled successfully"
    except Exception as e:
        import traceback
        try:
            from .config.settings import debug_log
        except Exception:
            try:
                from config.settings import debug_log
            except Exception:
                def debug_log(msg, cat):
                    print(f"DEBUG [{cat}]: {msg}")
        debug_log(f"[CANCEL_TIMEOFF] ERROR: Exception cancelling timeoff request: {str(e)}", "bot_logic")
        debug_log(f"[CANCEL_TIMEOFF] Traceback: {traceback.format_exc()}", "bot_logic")
        return False, f"Error cancelling request: {e}"


def get_timeoff_request_for_edit(odoo_service, leave_id: int, user_tz: Optional[str] = None) -> Tuple[bool, Any]:
    """Fetch time off request details for editing.
    
    Returns (ok, request_data) where request_data contains fields needed for editing.
    """
    try:
        if not leave_id:
            return False, "Leave ID not provided"

        # Ensure session is active
        ok_session, msg = odoo_service.ensure_active_session()
        if not ok_session:
            return False, msg

        # Read the leave request with all needed fields
        read_params = {
            'args': [[leave_id]],
            'kwargs': {
                'fields': ['id', 'holiday_status_id', 'request_date_from', 'request_date_to', 
                          'request_unit_hours', 'request_hour_from', 'request_hour_to', 
                          'state', 'number_of_days', 'duration_display', 'supported_attachment_ids', 'x_studio_relation']
            }
        }
        ok, leaves = _make_odoo_request(odoo_service, 'hr.leave', 'read', read_params)
        if not ok:
            return False, leaves

        if not isinstance(leaves, list) or len(leaves) == 0:
            return False, "Leave request not found"

        leave_data = leaves[0]
        
        # Extract leave type
        holiday_status = leave_data.get('holiday_status_id')
        leave_type_id = None
        leave_type_name = None
        if isinstance(holiday_status, list) and len(holiday_status) > 0:
            leave_type_id = holiday_status[0] if isinstance(holiday_status[0], int) else None
            leave_type_name = holiday_status[1] if len(holiday_status) > 1 else None
        
        # Determine if custom hours mode
        is_custom_hours = leave_data.get('request_unit_hours', False)
        
        # Parse dates
        date_from_str = leave_data.get('request_date_from', '')
        date_to_str = leave_data.get('request_date_to', '')
        
        # Format dates for frontend (DD/MM/YYYY)
        date_from_formatted = ''
        date_to_formatted = ''
        if date_from_str:
            try:
                dt = datetime.strptime(date_from_str[:10], '%Y-%m-%d')
                date_from_formatted = dt.strftime('%d/%m/%Y')
            except Exception:
                date_from_formatted = date_from_str[:10]
        
        if date_to_str:
            try:
                dt = datetime.strptime(date_to_str[:10], '%Y-%m-%d')
                date_to_formatted = dt.strftime('%d/%m/%Y')
            except Exception:
                date_to_formatted = date_to_str[:10]
        
        # Extract hours if custom hours mode - convert to hour keys for dropdown
        hour_from = ''
        hour_to = ''
        if is_custom_hours:
            hour_from_val = leave_data.get('request_hour_from')
            hour_to_val = leave_data.get('request_hour_to')
            if hour_from_val:
                try:
                    # Convert float hour to hour key format (e.g., "9" or "9.5")
                    hour_float = float(hour_from_val)
                    hour = int(hour_float)
                    minute = int((hour_float - hour) * 60)
                    if minute == 0:
                        hour_from = str(hour)
                    elif minute == 30:
                        hour_from = f"{hour}.5"
                    elif minute == 15:
                        hour_from = f"{hour}.25"
                    elif minute == 45:
                        hour_from = f"{hour}.75"
                    else:
                        # Round to nearest quarter hour
                        quarter = round(minute / 15) * 15
                        if quarter == 60:
                            hour_from = str(hour + 1)
                        elif quarter == 0:
                            hour_from = str(hour)
                        elif quarter == 15:
                            hour_from = f"{hour}.25"
                        elif quarter == 30:
                            hour_from = f"{hour}.5"
                        else:  # 45
                            hour_from = f"{hour}.75"
                except Exception:
                    hour_from = str(hour_from_val)
            if hour_to_val:
                try:
                    hour_float = float(hour_to_val)
                    hour = int(hour_float)
                    minute = int((hour_float - hour) * 60)
                    if minute == 0:
                        hour_to = str(hour)
                    elif minute == 30:
                        hour_to = f"{hour}.5"
                    elif minute == 15:
                        hour_to = f"{hour}.25"
                    elif minute == 45:
                        hour_to = f"{hour}.75"
                    else:
                        # Round to nearest quarter hour
                        quarter = round(minute / 15) * 15
                        if quarter == 60:
                            hour_to = str(hour + 1)
                        elif quarter == 0:
                            hour_to = str(hour)
                        elif quarter == 15:
                            hour_to = f"{hour}.25"
                        elif quarter == 30:
                            hour_to = f"{hour}.5"
                        else:  # 45
                            hour_to = f"{hour}.75"
                except Exception:
                    hour_to = str(hour_to_val)
        
        # Extract existing attachment IDs
        attachment_ids = leave_data.get('supported_attachment_ids', [])
        existing_attachment_ids = []
        if isinstance(attachment_ids, list):
            # Odoo returns Many2one as list of IDs
            existing_attachment_ids = [aid for aid in attachment_ids if isinstance(aid, int)]
        
        return True, {
            'id': leave_data.get('id'),
            'leave_type_id': leave_type_id,
            'leave_type_name': leave_type_name,
            'date_from': date_from_formatted,
            'date_to': date_to_formatted,
            'is_custom_hours': is_custom_hours,
            'hour_from': hour_from,
            'hour_to': hour_to,
            'state': leave_data.get('state'),
            'existing_attachment_ids': existing_attachment_ids,
            'x_studio_relation': leave_data.get('x_studio_relation', '')  # For Compassionate Leave
        }
    except Exception as e:
        return False, f"Error fetching leave request for edit: {e}"


def update_timeoff_request(odoo_service, leave_id: int, leave_type_id: int, 
                           date_from: str, date_to: str, is_custom_hours: bool,
                           hour_from: str = '', hour_to: str = '', 
                           existing_attachment_ids: Optional[List[int]] = None,
                           new_attachment_data: Optional[Dict[str, Any]] = None,
                           user_tz: Optional[str] = None,
                           relation: str = '') -> Tuple[bool, Any]:
    """Update a time off request by deleting the old one and creating a new one.
    
    This approach works for all users since all creatives have permissions to delete
    and create time off requests, but not all have permissions to edit existing requests.
    
    Workflow:
    1. Read the existing request to get employee_id
    2. Read existing attachment data (before deletion, as attachments may be deleted with the request)
    3. Delete the existing request (frees up allocation and prevents double-booking)
    4. Create a new request with updated data
    5. Recreate attachments for the new request
    6. Link attachments to the new request
    
    Args:
        leave_id: Leave request ID to replace
        leave_type_id: Leave type ID (holiday_status_id)
        date_from: Start date in DD/MM/YYYY format
        date_to: End date in DD/MM/YYYY format (same as date_from for custom hours)
        is_custom_hours: Whether this is custom hours mode
        hour_from: Start hour in HH:MM format (for custom hours)
        hour_to: End hour in HH:MM format (for custom hours)
        existing_attachment_ids: List of attachment IDs to preserve
        new_attachment_data: New attachment data dict to add
        user_tz: User timezone (optional)
    """
    try:
        if not leave_id:
            return False, "Leave ID not provided"

        # Ensure session is active
        ok_session, msg = odoo_service.ensure_active_session()
        if not ok_session:
            return False, msg

        # Step 1: Read existing request to get employee_id and check state
        read_params = {
            'args': [[leave_id]],
            'kwargs': {'fields': ['employee_id', 'state']}
        }
        ok_read, leave_data = _make_odoo_request(odoo_service, 'hr.leave', 'read', read_params)
        if not ok_read or not leave_data or not isinstance(leave_data, list) or len(leave_data) == 0:
            return False, f"Failed to read leave request: {leave_data}"
        
        existing_leave = leave_data[0]
        current_state = existing_leave.get('state')
        
        # Cannot edit requests that are already approved, refused, or in second approval
        if current_state in ['validate', 'validate1', 'refuse']:
            return False, f"Cannot edit a request that is already {current_state}. Please cancel and create a new request."
        
        # Extract employee_id
        employee_id_data = existing_leave.get('employee_id')
        if not employee_id_data:
            return False, "Could not determine employee ID from existing request"
        
        # Handle Odoo's tuple format: (id, name) or just id
        if isinstance(employee_id_data, (list, tuple)) and len(employee_id_data) > 0:
            employee_id = employee_id_data[0]
        elif isinstance(employee_id_data, int):
            employee_id = employee_id_data
        else:
            return False, f"Invalid employee_id format: {employee_id_data}"
        
        # Validation: Check if changing to Unpaid Leave - need to verify original balance
        try:
            # Get the new leave type name
            leave_type_read_params = {
                'args': [[leave_type_id]],
                'kwargs': {'fields': ['name']}
            }
            ok_lt, leave_type_data = _make_odoo_request(odoo_service, 'hr.leave.type', 'read', leave_type_read_params)
            
            if ok_lt and isinstance(leave_type_data, list) and len(leave_type_data) > 0:
                new_leave_type_name = leave_type_data[0].get('name', '')
                
                if new_leave_type_name == 'Unpaid Leave':
                    # Read existing request to get its leave type and days
                    existing_read_params = {
                        'args': [[leave_id]],
                        'kwargs': {'fields': ['holiday_status_id', 'number_of_days']}
                    }
                    ok_existing, existing_leave_data = _make_odoo_request(odoo_service, 'hr.leave', 'read', existing_read_params)
                    
                    if ok_existing and isinstance(existing_leave_data, list) and len(existing_leave_data) > 0:
                        existing_leave_record = existing_leave_data[0]
                        existing_holiday_status = existing_leave_record.get('holiday_status_id')
                        existing_number_of_days = existing_leave_record.get('number_of_days', 0.0)
                        
                        # Extract existing leave type name
                        existing_leave_type_name = None
                        if isinstance(existing_holiday_status, (list, tuple)) and len(existing_holiday_status) > 1:
                            existing_leave_type_name = existing_holiday_status[1]
                        elif isinstance(existing_holiday_status, dict):
                            existing_leave_type_name = existing_holiday_status.get('name')
                        
                        # Get current Annual Leave balance
                        try:
                            from .leave_balance_service import LeaveBalanceService
                        except Exception:
                            from leave_balance_service import LeaveBalanceService
                        
                        leave_balance_service = LeaveBalanceService(odoo_service)
                        remaining, error = leave_balance_service.calculate_remaining_leave(
                            employee_id,
                            'Annual Leave',
                            None  # Will use odoo_service session
                        )
                        
                        if not error and remaining:
                            current_annual_balance = remaining.get('Annual Leave', 0.0)
                            
                            # If existing request was Annual Leave, add back those days to get original balance
                            original_annual_balance = current_annual_balance
                            if existing_leave_type_name == 'Annual Leave':
                                try:
                                    existing_days = float(existing_number_of_days)
                                    original_annual_balance = current_annual_balance + existing_days
                                    debug_log(f"Changing from Annual Leave to Unpaid Leave - current balance: {current_annual_balance}, existing request days: {existing_days}, original balance: {original_annual_balance}", "bot_logic")
                                except Exception:
                                    pass
                            
                            # Check if original balance was > 30 minutes (0.0625 days)
                            # 30 minutes = 0.5 hours = 0.5/8 = 0.0625 days
                            if original_annual_balance > 0.0625:
                                error_msg = "According to P&C policy Prezlabers cannot request unpaid time off while having unused Annual Leave time"
                                debug_log(f"Blocked change to Unpaid Leave - original Annual Leave balance was {original_annual_balance} days (threshold: 0.0625 days)", "bot_logic")
                                return False, error_msg
        except Exception as e:
            debug_log(f"Error validating unpaid leave change: {str(e)}", "bot_logic")
            # On error, allow the change to proceed (fail open)
        
        # Step 2: Convert dates from DD/MM/YYYY to YYYY-MM-DD
        try:
            dt_from = datetime.strptime(date_from, '%d/%m/%Y')
            date_from_ymd = dt_from.strftime('%Y-%m-%d')
            
            if is_custom_hours:
                # For custom hours, end date is same as start date
                date_to_ymd = date_from_ymd
            else:
                dt_to = datetime.strptime(date_to, '%d/%m/%Y')
                date_to_ymd = dt_to.strftime('%Y-%m-%d')
        except Exception as e:
            return False, f"Invalid date format: {e}"

        # Step 3: Prepare new leave request data
        leave_data = {
            'employee_id': employee_id,
            'holiday_status_id': leave_type_id,
            'request_date_from': date_from_ymd,
            'request_date_to': date_to_ymd,
            'name': "Time off request via Nasma chatbot",
            'state': 'confirm'  # Submit for approval
        }
        
        # Add relation field for Compassionate Leave
        if relation:
            # Odoo selection fields are case-sensitive
            # Capitalize the relation value to match Odoo's expected format
            relation_capitalized = relation.capitalize()
            debug_log(f"Adding relation field (update): '{relation}' -> '{relation_capitalized}'", "bot_logic")
            leave_data['x_studio_relation'] = relation_capitalized
        
        # Add custom hours fields if needed
        if is_custom_hours:
            leave_data['request_unit_hours'] = True
            # Convert HH:MM to Odoo format (string like '9' or '9.5', rounded to nearest half-hour)
            def _to_odoo_hour_format(hhmm_str: str) -> str:
                """Convert HH:MM to Odoo hour format string (e.g., '9' or '9.5').
                
                Odoo only accepts whole hours or half hours, so we round to nearest half-hour.
                """
                if not hhmm_str:
                    return ''
                try:
                    parts = hhmm_str.split(':')
                    hour = int(parts[0])
                    minute = int(parts[1]) if len(parts) > 1 else 0
                    # Convert to float hours
                    hour_float = hour + (minute / 60.0)
                    # Round to nearest half-hour (Odoo only accepts whole or half hours)
                    hour_float = round(hour_float * 2) / 2.0
                    # Convert to string format
                    if abs(hour_float - round(hour_float)) < 1e-9:
                        return str(int(round(hour_float)))
                    return f"{hour_float:.1f}".rstrip('0').rstrip('.')
                except Exception:
                    return ''
            
            if hour_from:
                hour_from_str = _to_odoo_hour_format(hour_from)
                if hour_from_str:
                    leave_data['request_hour_from'] = hour_from_str
            if hour_to:
                hour_to_str = _to_odoo_hour_format(hour_to)
                if hour_to_str:
                    leave_data['request_hour_to'] = hour_to_str
        else:
            leave_data['request_unit_hours'] = False

        # Step 4: Read existing attachment data BEFORE deletion (attachments may be deleted with the request)
        existing_attachment_data = []
        if existing_attachment_ids:
            for att_id in existing_attachment_ids:
                try:
                    # Read attachment data
                    att_read_params = {
                        'args': [[att_id]],
                        'kwargs': {'fields': ['name', 'datas', 'mimetype', 'type']}
                    }
                    att_ok, att_data = _make_odoo_request(odoo_service, 'ir.attachment', 'read', att_read_params)
                    if att_ok and isinstance(att_data, list) and len(att_data) > 0:
                        att_info = att_data[0]
                        # Store attachment data for recreation
                        existing_attachment_data.append({
                            'name': att_info.get('name', 'supporting-document'),
                            'datas': att_info.get('datas'),  # Base64 encoded file data
                            'mimetype': att_info.get('mimetype', 'application/octet-stream'),
                            'type': att_info.get('type', 'binary')
                        })
                except Exception:
                    # If we can't read an attachment, skip it but continue
                    pass

        # Step 5: Delete the old request first to free up allocation and avoid double-booking
        # This prevents errors when dates overlap or when allocation is insufficient
        unlink_params = {
            'args': [[leave_id]],
            'kwargs': {}
        }
        ok_delete, delete_result = _make_odoo_request(odoo_service, 'hr.leave', 'unlink', unlink_params)
        if not ok_delete:
            return False, f"Failed to delete existing request: {delete_result}. Cannot proceed with update."

        # Step 6: Create new request with updated data
        create_params = {
            'args': [leave_data],
            'kwargs': {}
        }
        ok_create, new_leave_id = _make_odoo_request(odoo_service, 'hr.leave', 'create', create_params)
        if not ok_create:
            return False, f"Failed to create new request: {new_leave_id}. The old request has been deleted."
        
        if not isinstance(new_leave_id, int):
            return False, f"Invalid leave ID returned: {new_leave_id}"

        # Step 7: Recreate attachments for the new request
        attachment_ids = []
        
        # Recreate existing attachments with their data
        for att_data in existing_attachment_data:
            try:
                attachment_payload = {
                    'name': att_data.get('name', 'supporting-document'),
                    'datas': att_data.get('datas'),
                    'res_model': 'hr.leave',
                    'res_id': new_leave_id,
                    'type': att_data.get('type', 'binary'),
                    'mimetype': att_data.get('mimetype', 'application/octet-stream'),
                }
                att_ok, att_id = _make_odoo_request(odoo_service, 'ir.attachment', 'create', {
                    'args': [attachment_payload],
                    'kwargs': {}
                })
                if att_ok and isinstance(att_id, int):
                    attachment_ids.append(att_id)
            except Exception:
                # Log but don't fail if attachment recreation fails
                pass
        
        # Add new attachment if provided
        if new_attachment_data and new_attachment_data.get('data'):
            try:
                attachment_payload = {
                    'name': new_attachment_data.get('filename') or new_attachment_data.get('name') or 'supporting-document',
                    'datas': new_attachment_data.get('data'),
                    'res_model': 'hr.leave',
                    'res_id': new_leave_id,
                    'type': 'binary',
                    'mimetype': new_attachment_data.get('mimetype') or new_attachment_data.get('content_type') or 'application/octet-stream',
                }
                att_ok, att_id = _make_odoo_request(odoo_service, 'ir.attachment', 'create', {
                    'args': [attachment_payload],
                    'kwargs': {}
                })
                if att_ok and isinstance(att_id, int):
                    attachment_ids.append(att_id)
            except Exception:
                # Log but don't fail if attachment fails
                pass
        
        # Link attachments to the new request if we have any
        if attachment_ids:
            link_params = {
                'args': [[new_leave_id], {'supported_attachment_ids': [(6, 0, attachment_ids)]}],
                'kwargs': {}
            }
            _make_odoo_request(odoo_service, 'hr.leave', 'write', link_params)
            # Don't fail if linking fails - the request was created successfully

        return True, f"Leave request updated successfully (new ID: {new_leave_id})"
    except Exception as e:
        return False, f"Error updating leave request: {e}"
