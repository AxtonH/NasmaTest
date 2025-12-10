from typing import Any, Dict, List, Tuple
from datetime import datetime, timedelta, timezone
try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except Exception:
    ZoneInfo = None


def _today_ymd() -> str:
    """Return today's date in YYYY-MM-DD string format."""
    return datetime.now().strftime('%Y-%m-%d')


def _ymd_in_days(days: int) -> str:
    """Return date in YYYY-MM-DD string format N days from now."""
    return (datetime.now() + timedelta(days=days)).strftime('%Y-%m-%d')


def _current_month_range() -> Tuple[str, str]:
    """Return first and last day of the current month as YYYY-MM-DD strings.

    Example: ("2025-09-01", "2025-09-30")
    """
    now = datetime.now()
    start = datetime(now.year, now.month, 1)
    if now.month == 12:
        next_month = datetime(now.year + 1, 1, 1)
    else:
        next_month = datetime(now.year, now.month + 1, 1)
    end = next_month - timedelta(days=1)
    return start.strftime('%Y-%m-%d'), end.strftime('%Y-%m-%d')


def _current_month_datetime_range() -> Tuple[str, str]:
    """Return current month start/end as datetime strings suitable for Odoo domains.

    Example: ("2025-09-01 00:00:00", "2025-09-30 23:59:59")
    """
    start_ymd, end_ymd = _current_month_range()
    return f"{start_ymd} 00:00:00", f"{end_ymd} 23:59:59"


def fetch_team_members(employee_service) -> Tuple[bool, Any]:
    """Fetch direct reports of the current user using the existing employee service.

    Returns (ok, team_list) where team_list is a list of dicts
    with keys: id, name, job_title, department.
    """
    try:
        return employee_service.get_direct_reports_current_user()
    except Exception as e:
        return False, f"Error fetching team members: {e}"


def fetch_upcoming_timeoffs(odoo_service, employee_ids: List[int], days_ahead: int = 60) -> Tuple[bool, Any]:
    """Fetch upcoming time-off (hr.leave) for the given employees within the next N days.

    Returns (ok, leaves) where leaves is a list of hr.leave dictionaries including:
    - id, employee_id, holiday_status_id, request_date_from, request_date_to, state
    """
    try:
        if not employee_ids:
            return True, []

        # Ensure session is active
        ok_session, msg = odoo_service.ensure_active_session()
        if not ok_session:
            return False, msg

        # Domain: employee in team AND state in desired set (To Approve, Second Approval)
        # States included: confirm, validate1. Exclude 'validate' (Approved)
        domain = [
            ('employee_id', 'in', employee_ids),
            ('state', 'in', ['confirm', 'validate1'])
        ]

        params = {
            'args': [domain],
            'kwargs': {
                'fields': ['id', 'employee_id', 'holiday_status_id', 'request_date_from', 'request_date_to', 'number_of_days', 'state', 'duration_display'],
                'limit': 500,
                'order': 'request_date_from desc'
            }
        }

        url_ok, data = _make_odoo_request(odoo_service, 'hr.leave', 'search_read', params)
        return url_ok, data
    except Exception as e:
        return False, f"Error fetching upcoming time off: {e}"


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


def group_leaves_by_employee(leaves: List[Dict]) -> Dict[int, Dict[str, List[Dict]]]:
    """Group leaves by employee into approved and pending buckets.

    Returns mapping: employee_id -> { 'approved': [...], 'pending': [...] }
    """
    by_employee: Dict[int, Dict[str, List[Dict]]] = {}
    for lv in leaves or []:
        emp = None
        e = lv.get('employee_id')
        if isinstance(e, list) and len(e) > 0:
            emp = int(e[0])
        elif isinstance(e, int):
            emp = e
        if emp is None:
            continue

        bucket = 'approved' if lv.get('state') == 'validate' else 'pending'
        by_employee.setdefault(emp, {'approved': [], 'pending': []})
        by_employee[emp][bucket].append(lv)
    return by_employee


def build_team_overview(team: List[Dict], leaves_by_employee: Dict[int, Dict[str, List[Dict]]]) -> List[Dict]:
    """Build a normalized overview list per team member with upcoming and approved time off."""
    overview: List[Dict] = []
    for member in team:
        emp_id = member.get('id')
        grouped = leaves_by_employee.get(emp_id, {'approved': [], 'pending': []})
        overview.append({
            'id': emp_id,
            'name': member.get('name') or 'Unknown',
            'job_title': member.get('job_title') or '',
            'department': member.get('department') or '',
            # Keep linked Odoo user for downstream widgets (e.g., overtime requests)
            'user_id': member.get('user_id'),
            'approved': grouped.get('approved', []),
            'upcoming': (grouped.get('pending', []) + grouped.get('approved', [])),
        })
    return overview


def _format_date(d: str) -> str:
    try:
        # Expect YYYY-MM-DD
        dt = datetime.strptime(d, '%Y-%m-%d')
        return dt.strftime('%d/%m/%Y')
    except Exception:
        return d or ''


def format_team_overview_message(overview: List[Dict]) -> str:
    """Format a concise team list message used above the table."""
    if not overview:
        return "Here is your team overview:\n\n(no direct reports found)"

    # Replace per-member bullets with a concise header; details shown in tables
    return "Here is your team overview:"


def _label(raw) -> str:
    """Resolve many2one 'holiday_status_id' label safely."""
    if isinstance(raw, list) and len(raw) > 1:
        return str(raw[1])
    return 'Time off'


def get_team_overview(odoo_service, employee_service, days_ahead: int = 60) -> Tuple[bool, Any]:
    """High-level helper to assemble team overview with leave data."""
    ok_team, team = fetch_team_members(employee_service)
    if not ok_team:
        return False, team
    if not isinstance(team, list) or not team:
        return True, []

    employee_ids = [m.get('id') for m in team if isinstance(m, dict) and m.get('id')]
    ok_leaves, leaves = fetch_upcoming_timeoffs(odoo_service, employee_ids, days_ahead=days_ahead)
    if not ok_leaves:
        return False, leaves

    grouped = group_leaves_by_employee(leaves if isinstance(leaves, list) else [])
    overview = build_team_overview(team, grouped)
    return True, overview


def _format_date_label(d: str) -> str:
    try:
        dt = datetime.strptime(d, '%Y-%m-%d')
        return dt.strftime('%d/%m/%Y')
    except Exception:
        return d or ''


def _utc_day_bounds_for_tz(tzname: str) -> Tuple[str, str]:
    """Return today's start/end in UTC strings based on user's timezone.

    Output format: 'YYYY-MM-DD HH:MM:SS'
    """
    now = datetime.now()
    try:
        if ZoneInfo and tzname:
            local_tz = ZoneInfo(tzname)
        else:
            local_tz = timezone(timedelta(hours=0))
    except Exception:
        local_tz = timezone(timedelta(hours=0))

    local_start = datetime(now.year, now.month, now.day, 0, 0, 0, tzinfo=local_tz)
    local_end = datetime(now.year, now.month, now.day, 23, 59, 59, tzinfo=local_tz)
    utc_start = local_start.astimezone(timezone.utc)
    utc_end = local_end.astimezone(timezone.utc)
    return utc_start.strftime('%Y-%m-%d %H:%M:%S'), utc_end.strftime('%Y-%m-%d %H:%M:%S')


def _utc_to_local_hhmm(utc_str: str, tzname: str) -> str:
    """Convert an Odoo UTC datetime string to user's local HH:MM."""
    if not utc_str:
        return ''
    try:
        dt = datetime.strptime(utc_str[:19], '%Y-%m-%d %H:%M:%S')
        dt = dt.replace(tzinfo=timezone.utc)
        if ZoneInfo and tzname:
            local_dt = dt.astimezone(ZoneInfo(tzname))
        else:
            local_dt = dt
        return local_dt.strftime('%H:%M')
    except Exception:
        try:
            # Fallback parse without seconds
            dt = datetime.strptime(utc_str[:16], '%Y-%m-%d %H:%M')
            dt = dt.replace(tzinfo=timezone.utc)
            if ZoneInfo and tzname:
                local_dt = dt.astimezone(ZoneInfo(tzname))
            else:
                local_dt = dt
            return local_dt.strftime('%H:%M')
        except Exception:
            return ''


def build_main_overview_table_widget(odoo_service, overview: List[Dict], user_tz: str) -> Tuple[bool, Any]:
    """Build a main overview table including today's planning slots per team member.

    Columns: Name, Title, Shift Today, Tasks
    - Shift Today: Yes/No
    - Tasks: for today, show one or more entries formatted as "HH:MM-HH:MM Project" separated by " | "
    Matching strategy: prefer planning.slot.employee_id -> hr.employee; fallback to partial match of
    planning.slot.resource_id display name containing the employee's name (case-insensitive).
    """
    try:
        if not overview:
            return True, { 'columns': [
                { 'key': 'member', 'label': 'Member' },
                { 'key': 'title', 'label': 'Title' },
                { 'key': 'tasks', 'label': 'Tasks' },
            ], 'rows': [] }

        ok_session, msg = odoo_service.ensure_active_session()
        if not ok_session:
            return False, msg

        day_start_utc, day_end_utc = _utc_day_bounds_for_tz(user_tz or '')

        # Employee IDs and names for matching
        employee_ids: List[int] = []
        name_by_emp: Dict[int, str] = {}
        title_by_emp: Dict[int, str] = {}
        for m in overview:
            emp_id = m.get('id')
            if isinstance(emp_id, int):
                employee_ids.append(emp_id)
                name_by_emp[emp_id] = m.get('name') or 'Unknown'
                title_by_emp[emp_id] = m.get('job_title') or ''

        # Fetch planning slots overlapping today; include fields for mapping
        domain = [
            '&', ('start_datetime', '<=', day_end_utc), ('end_datetime', '>=', day_start_utc),
        ]
        params = {
            'args': [domain],
            'kwargs': {
                'fields': ['id', 'name', 'start_datetime', 'end_datetime', 'employee_id', 'resource_id', 'project_id'],
                'limit': 2000,
                'order': 'start_datetime asc'
            }
        }
        ok, slots = _make_odoo_request(odoo_service, 'planning.slot', 'search_read', params)
        if not ok:
            return False, slots
        slots = slots if isinstance(slots, list) else []

        # Index slots by employee_id and also prepare a list for name-matching
        by_emp: Dict[int, List[Dict]] = {}
        fallback_slots: List[Dict] = []
        for s in slots:
            emp_val = s.get('employee_id')
            if isinstance(emp_val, list) and emp_val:
                emp_id = emp_val[0]
                if isinstance(emp_id, int):
                    by_emp.setdefault(emp_id, []).append(s)
                    continue
            fallback_slots.append(s)

        def _matches_name(slot: Dict, member_name: str) -> bool:
            if not member_name:
                return False
            # Check resource display name if available
            res = slot.get('resource_id')
            disp = ''
            if isinstance(res, list) and len(res) > 1:
                disp = str(res[1] or '')
            if not disp:
                disp = str(slot.get('name') or '')
            return member_name.lower() in disp.lower()

        # Build rows
        columns = [
            { 'key': 'member', 'label': 'Member' },
            { 'key': 'title', 'label': 'Title' },
            { 'key': 'tasks', 'label': 'Tasks' },
        ]
        rows_out: List[Dict[str, str]] = []

        for m in overview:
            emp_id = m.get('id')
            member_name = m.get('name') or 'Unknown'
            title = m.get('job_title') or ''
            matched: List[Dict] = []
            if isinstance(emp_id, int) and emp_id in by_emp:
                matched.extend(by_emp.get(emp_id, []))
            # Fallback name match
            if not matched and member_name:
                for s in fallback_slots:
                    if _matches_name(s, member_name):
                        matched.append(s)

            # Format tasks for today
            if matched:
                parts: List[str] = []
                for s in matched:
                    st = _utc_to_local_hhmm(s.get('start_datetime') or '', user_tz or '')
                    en = _utc_to_local_hhmm(s.get('end_datetime') or '', user_tz or '')
                    proj = s.get('project_id')
                    proj_name = ''
                    if isinstance(proj, list) and len(proj) > 1:
                        proj_name = str(proj[1])
                    label = f"{st}-{en}" if st or en else "Today"
                    if proj_name:
                        label += f" {proj_name}"
                    parts.append(label)
                tasks_cell = " | ".join(parts)
            else:
                tasks_cell = '—'

            rows_out.append({
                'member': member_name,
                'title': title,
                'tasks': tasks_cell,
            })

        return True, { 'columns': columns, 'rows': rows_out }
    except Exception as e:
        return False, f"Error building main overview table: {e}"


def build_overtime_table_widget(odoo_service, team: List[Dict], days_ahead: int = 60) -> Tuple[bool, Any]:
    """Build an overtime table widget (using approval.request) for direct reports.

    Columns: Member, Start, End, Duration (Hours), Project, Status
    We approximate duration in hours from date_start/date_end when both exist.
    Uses request_status filter and create_date for display; compatible with instances
    that don't have custom date fields on approval.request.
    """
    try:
        # Map employee -> related user id (if available)
        emp_to_user: Dict[int, int] = {}
        for m in team:
            if isinstance(m, dict) and m.get('id') and m.get('user_id'):
                emp_to_user[m['id']] = m['user_id']

        user_ids = [uid for uid in emp_to_user.values() if uid]
        if not user_ids:
            return True, { 'columns': [], 'rows': [] }

        ok_session, msg = odoo_service.ensure_active_session()
        if not ok_session:
            return False, msg

        # Domain that matches your working implementation: owners + pending-like statuses
        # Only include pending requests (exclude 'new')
        domain: List = [
            ('request_owner_id', 'in', user_ids),
            ('request_status', '=', 'pending')
        ]

        # Use two-step fetch like your code: search => read
        search_params = { 'args': [domain], 'kwargs': {} }
        ok, request_ids = _make_odoo_request(odoo_service, 'approval.request', 'search', search_params)
        if not ok:
            return False, request_ids
        if not isinstance(request_ids, list) or len(request_ids) == 0:
            rows = []
        else:
            read_params = {
                'args': [request_ids],
                'kwargs': {
                    'fields': ['id', 'name', 'request_owner_id', 'category_id', 'request_status', 'create_date', 'x_studio_hours', 'date_start', 'date_end', 'x_studio_project']
                }
            }
            ok, rows = _make_odoo_request(odoo_service, 'approval.request', 'read', read_params)
            if not ok:
                return False, rows

        # Build table
        columns = [
            { 'key': 'member', 'label': 'Member' },
            { 'key': 'dates', 'label': 'Dates' },
            { 'key': 'duration', 'label': 'Duration<br/>(Hours)' },
            { 'key': 'project', 'label': 'Project' },
            { 'key': 'status', 'label': 'Status' },
            { 'key': 'approval', 'label': 'Approval' },
        ]
        table_rows: List[Dict[str, str]] = []

        # Reverse map for member names
        user_to_member: Dict[int, str] = {}
        for m in team:
            uid = m.get('user_id')
            if uid:
                # Only the name for overtime member column
                user_to_member[uid] = (m.get('name') or 'Unknown')

        for r in rows or []:
            owner = r.get('request_owner_id')
            uid = owner[0] if isinstance(owner, list) and owner else owner if isinstance(owner, int) else None
            member_name = user_to_member.get(uid, 'Unknown')

            # Prefer explicit date_start/date_end; fallback to create_date if missing
            ds = (r.get('date_start') or r.get('create_date') or '')[:10]
            de = (r.get('date_end') or r.get('create_date') or '')[:10]
            start = _format_date_label(ds)
            end = _format_date_label(de)
            
            # Combine dates into single column with from/to format
            dates = f"from<br/>{start}<br/>to<br/>{end}" if start and end else "—"

            # Duration from x_studio_hours field
            dur = '—'
            try:
                hours = r.get('x_studio_hours')
                if hours is not None and hours != 0:
                    hours_float = float(hours)
                    if hours_float == int(hours_float):
                        dur = f"{int(hours_float)} hours"
                    else:
                        dur = f"{hours_float:.1f} hours"
            except Exception:
                pass

            # Project from x_studio_project field (Many2one format: [id, 'name'])
            project_name = '—'
            try:
                project_val = r.get('x_studio_project')
                if project_val:
                    if isinstance(project_val, (list, tuple)) and len(project_val) > 1:
                        project_name = str(project_val[1])
                    elif isinstance(project_val, (list, tuple)) and len(project_val) == 1:
                        project_name = str(project_val[0])
                    else:
                        project_name = str(project_val)
            except Exception:
                pass

            status_raw = r.get('request_status') or r.get('state') or ''
            status_map = {
                'new': 'New', 'pending': 'Pending', 'to_approve': 'To Approve', 'approved': 'Approved', 'refused': 'Refused',
            }
            state_txt = status_map.get(status_raw, status_raw.title() if isinstance(status_raw, str) else '—')

            # Build approval buttons (respect visibility rules)
            attachment_number = r.get('attachment_number') or 0
            user_status = r.get('user_status') or ''
            can_act = (status_raw not in ['approved', 'refused', 'cancel']) and (user_status == 'pending' or not user_status) and (attachment_number < 1)
            approval_html = ''
            if can_act and isinstance(r.get('id'), int):
                rid = r.get('id')
                approval_html = (
                    f"<div class=\"flex flex-col items-center\">"
                    f"<button class=\"approval-button bg-green-600 text-white hover:bg-green-700\" data-action=\"approve\" data-model=\"approval.request\" data-id=\"{rid}\">Approve</button>"
                    f"<button class=\"approval-button bg-red-600 text-white hover:bg-red-700 mt-1\" data-action=\"refuse\" data-model=\"approval.request\" data-id=\"{rid}\">Deny</button>"
                    f"</div>"
                )

            table_rows.append({
                'member': member_name,
                'dates': dates,
                'duration': dur,
                'project': project_name,
                'status': state_txt,
                'approval': approval_html,
            })

        return True, { 'columns': columns, 'rows': table_rows }
    except Exception as e:
        return False, f"Error building overtime table: {e}"


def build_team_overview_table_widget(overview: List[Dict]) -> Dict[str, Any]:
    """Build a simple widget payload to render a table in the frontend.

    Columns: Member, Role/Dept, Start, End, Type, Status
    Rows: one per upcoming leave. Members with no upcoming leave will have a single
    row with dashes for leave columns.
    """
    columns = [
        { 'key': 'member', 'label': 'Member' },
        { 'key': 'dates', 'label': 'Dates' },
        { 'key': 'duration', 'label': 'Duration<br/>(Hours)' },
        { 'key': 'type', 'label': 'Type' },
        { 'key': 'status', 'label': 'Status' },
        { 'key': 'approval', 'label': 'Approval' },
    ]

    rows: List[Dict[str, str]] = []
    for m in overview or []:
        member_name = m.get('name') or 'Unknown'
        upcoming = m.get('upcoming') or []
        if not upcoming:
            # Skip members with no upcoming time off
            continue

        for lv in upcoming:
            lt = _label(lv.get('holiday_status_id'))
            start = _format_date(lv.get('request_date_from') or '')
            end = _format_date(lv.get('request_date_to') or '')
            
            # Use duration_display field for duration
            duration = lv.get('duration_display') or '—'
            
            state = lv.get('state')
            state_txt = 'Second Approval' if state == 'validate1' else ('To Approve' if state == 'confirm' else 'Pending')
            
            # Combine dates into single column with from/to format
            dates = f"from<br/>{start}<br/>to<br/>{end}" if start and end else "—"
            
            # Build approval buttons for hr.leave
            approval_html = ''
            try:
                if isinstance(lv.get('id'), int):
                    lid = lv.get('id')
                    if state == 'validate1':
                        # Second approval: show single orange button that logs message
                        approval_html = (
                            "<div class=\"flex flex-col items-center\">"
                            "<button class=\"approval-button bg-orange-500 text-white hover:bg-orange-600\" data-action=\"note\" data-model=\"hr.leave\" data-id=\"{lid}\">Second approval</button>"
                            "</div>"
                        )
                    elif state == 'confirm':
                        approval_html = (
                            f"<div class=\"flex flex-col items-center\">"
                            f"<button class=\"approval-button bg-green-600 text-white hover:bg-green-700\" data-action=\"approve\" data-model=\"hr.leave\" data-id=\"{lid}\">Approve</button>"
                            f"<button class=\"approval-button bg-red-600 text-white hover:bg-red-700 mt-1\" data-action=\"refuse\" data-model=\"hr.leave\" data-id=\"{lid}\">Deny</button>"
                            f"</div>"
                        )
            except Exception:
                pass

            rows.append({
                'member': member_name,
                'dates': dates,
                'duration': duration,
                'type': lt,
                'status': state_txt,
                'approval': approval_html,
            })

    return { 'columns': columns, 'rows': rows }


