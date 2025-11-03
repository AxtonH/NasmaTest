import re
from datetime import datetime, date, timezone, timedelta
try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except Exception:
    ZoneInfo = None
from typing import Dict, Any, Tuple, Optional, List


def _to_datetime_str(dmy: str, hour_key: str) -> str:
    """Combine a date string (YYYY-MM-DD) and an hour selection key ('16' or '16.5')
    into an ISO datetime string suitable for Odoo: 'YYYY-MM-DD HH:MM:SS'."""
    try:
        h = float(hour_key)
        hours = int(h)
        minutes = 30 if abs(h - hours - 0.5) < 1e-9 else 0
        return f"{dmy} {hours:02d}:{minutes:02d}:00"
    except Exception:
        # Fallback to midnight
        return f"{dmy} 00:00:00"


class OvertimeService:
    """Service to handle Odoo Approvals overtime requests via conversational flow.

    Flow steps:
      1) Initialize: resolve approval.category for company ("Overtime - {Company}")
      2) Ask for overtime date (widget: single_date_picker, context_key='overtime_date_range')
      3) Ask for hour range (widget: hour_range_picker)
      4) Show project dropdown (widget: select_dropdown, context_key='overtime_project_id')
      5) Confirmation -> Submit approval.request
    """

    INTENT_PHRASES = [
        'overtime', 'over time', 'ot', 'extra hours', 'extra time', 'work overtime'
    ]

    def __init__(self, odoo_service, employee_service, session_manager, metrics_service=None):
        self.odoo_service = odoo_service
        self.employee_service = employee_service
        self.session_manager = session_manager
        self.metrics_service = metrics_service

    def _resolve_identity(self, employee_data: Dict) -> Dict[str, Optional[str]]:
        tenant_id = None
        tenant_name = None
        user_id = None
        user_name = None
        try:
            if isinstance(employee_data, dict):
                emp_id = employee_data.get('id')
                if emp_id is not None:
                    user_id = str(emp_id)
                name = employee_data.get('name')
                if name:
                    user_name = str(name)
                company_details = employee_data.get('company_id_details')
                if isinstance(company_details, dict):
                    if company_details.get('id') is not None:
                        tenant_id = str(company_details.get('id'))
                    tenant_name = company_details.get('name') or tenant_name
                else:
                    comp_val = employee_data.get('company_id')
                    if isinstance(comp_val, (list, tuple)) and comp_val:
                        tenant_id = str(comp_val[0])
                        if len(comp_val) > 1 and comp_val[1]:
                            tenant_name = comp_val[1]
                    elif comp_val:
                        tenant_id = str(comp_val)
        except Exception:
            pass
        return {
            'tenant_id': tenant_id,
            'tenant_name': tenant_name,
            'user_name': user_name,
            'user_id': user_id,
        }

    def _log_metric(self, metric_type: str, thread_id: str, payload: Dict[str, Any], employee_data: Dict):
        if not self.metrics_service:
            return
        try:
            identity = self._resolve_identity(employee_data or {})
            metrics_payload = dict(payload or {})
            if identity.get('tenant_name'):
                metrics_payload.setdefault('context', {})['tenant_name'] = identity['tenant_name']
            logged = self.metrics_service.log_metric(
                metric_type,
                thread_id,
                user_id=identity.get('user_id'),
                user_name=identity.get('user_name'),
                tenant_id=identity.get('tenant_id'),
                payload=metrics_payload,
            )
            if not logged:
                last_err = getattr(self.metrics_service, "last_error", None)
                print(f"[MetricsService] Overtime log failed: {last_err}")
        except Exception:
            pass

    # -------------------------- Intent detection --------------------------
    def detect_intent(self, text: str) -> Tuple[bool, float]:
        """Light fuzzy intent detection for overtime requests.
        Avoids triggering on policy/info questions (e.g., "overtime policy").
        """
        if not text:
            return False, 0.0
        s = (text or '').lower()

        # If the user is asking about policy/rules/information, don't start the flow
        policy_keywords = [
            'policy', 'policies', 'rule', 'rules', 'guideline', 'guidelines',
            'what is', 'how does', 'how do', 'tell me about', 'explain', 'information about', 'details about'
        ]
        if any(k in s for k in policy_keywords):
            return False, 0.0

        # Require action-oriented phrasing if "overtime" is present
        action_markers = [
            'request', 'apply', 'submit', 'book', 'file', 'log', 'record', 'claim', 'enter', 'register', 'start',
            'ask', 'need', 'want'
        ]

        score = 0.0
        contains_anchor = any(p in s for p in self.INTENT_PHRASES)
        if contains_anchor:
            if any(a in s for a in action_markers):
                score = 0.7
            else:
                # Ambiguous mention of overtime without an action -> do not trigger
                score = 0.0
        else:
            for p in self.INTENT_PHRASES:
                if p in s:
                    score = max(score, 0.7 if p == 'overtime' else 0.5)

        return (score >= 0.5), min(1.0, score)

    # -------------------------- Odoo utilities ---------------------------
    def _make_odoo_request_stateless(self, model: str, method: str, params: Dict,
                                     session_id: str = None, user_id: int = None,
                                     username: str = None, password: str = None) -> Tuple[bool, Any, Optional[Dict]]:
        """Make authenticated request to Odoo using stateless session (with automatic retry)"""
        try:
            if not session_id or not user_id:
                return False, "Session data missing", None

            result = self.odoo_service.make_authenticated_request(
                model=model,
                method=method,
                args=params.get('args', []),
                kwargs=params.get('kwargs', {}),
                session_id=session_id,
                user_id=user_id,
                username=username,
                password=password
            )

            renewed_session = result.pop('_renewed_session', None) if isinstance(result, dict) else None

            if 'error' in result:
                error_data = result.get('error', {})
                error_msg = error_data.get('message', 'Unknown error') if isinstance(error_data, dict) else str(error_data)
                return False, error_msg, renewed_session

            return True, result.get('result'), renewed_session

        except Exception as e:
            return False, str(e), None

    def _make_odoo_request(self, model: str, method: str, params: Dict) -> Tuple[bool, Any]:
        """Make authenticated request to Odoo using web session (legacy - use stateless version when possible)"""
        try:
            session_ok, session_msg = self.odoo_service.ensure_active_session()
            if not session_ok:
                return False, f"Session error: {session_msg}"
            url = f"{self.odoo_service.odoo_url}/web/dataset/call_kw"
            import requests
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
            resp = requests.post(url, json=data, headers={'Content-Type': 'application/json'}, cookies=cookies, timeout=20)
            if resp.status_code != 200:
                return False, f"HTTP {resp.status_code}"
            try:
                j = resp.json()
                if 'result' in j:
                    return True, j['result']
                return False, j.get('error', 'Unknown error')
            except Exception as je:
                return False, f"Invalid JSON: {je}"
        except Exception as e:
            return False, str(e)

    def _get_company_name(self, employee_data: Dict) -> Optional[str]:
        try:
            comp = employee_data.get('company_id_details')
            if isinstance(comp, dict):
                return comp.get('name')
            # Fallback: fetch via employee_service
            emp_id = employee_data.get('id')
            ok, emp_read = self.employee_service._make_odoo_request('hr.employee', 'read', {
                'args': [[emp_id]],
                'kwargs': {'fields': ['company_id']}
            })
            if ok and isinstance(emp_read, list) and emp_read:
                comp_val = emp_read[0].get('company_id')
                comp_id = comp_val[0] if isinstance(comp_val, list) else comp_val
                ok2, comp_read = self.employee_service._make_odoo_request('res.company', 'read', {
                    'args': [[comp_id]],
                    'kwargs': {'fields': ['name']}
                })
                if ok2:
                    comp_obj = comp_read[0] if isinstance(comp_read, list) else comp_read
                    return comp_obj.get('name')
        except Exception:
            return None
        return None

    def _find_overtime_category(self, company_name: str) -> Tuple[bool, Any]:
        name = f"Overtime - {company_name}".strip()
        domain = [["name", "=", name]]
        params = {
            'args': [domain],
            'kwargs': {'fields': ['id', 'name'], 'limit': 1}
        }
        return self._make_odoo_request('approval.category', 'search_read', params)

    def _list_projects(self) -> Tuple[bool, Any]:
        """Fetch a large list of projects using robust fallbacks.

        Strategy:
        1) Try search_read on project.project (active only). If empty, try without domain.
        2) If still empty, try explicit search -> read on project.project.
        3) If model missing/empty, try alternative model names ('project', 'x_project') using search+read.
        """
        # 1) search_read active
        sr_args = {'args': [[]], 'kwargs': {'fields': ['id', 'name', 'display_name', 'active'], 'domain': [["active", "=", True]], 'order': 'name asc', 'limit': 1000}}
        ok, result = self._make_odoo_request('project.project', 'search_read', sr_args)
        if ok and isinstance(result, list) and len(result) > 0:
            return True, result
        # 1b) search_read without domain
        sr_args_nd = {'args': [[]], 'kwargs': {'fields': ['id', 'name', 'display_name', 'active'], 'order': 'name asc', 'limit': 1000}}
        ok2, result2 = self._make_odoo_request('project.project', 'search_read', sr_args_nd)
        if ok2 and isinstance(result2, list) and len(result2) > 0:
            return True, result2
        # 2) search -> read on project.project
        try_models = ['project.project', 'project', 'x_project']
        for model in try_models:
            # search (no domain to be safe)
            ok_s, ids = self._make_odoo_request(model, 'search', {'args': [[]], 'kwargs': {'limit': 1000}})
            if not ok_s or not isinstance(ids, list) or len(ids) == 0:
                continue
            ok_r, recs = self._make_odoo_request(model, 'read', {'args': [ids], 'kwargs': {'fields': ['id', 'name', 'display_name', 'active']}})
            if ok_r and isinstance(recs, list) and len(recs) > 0:
                # Normalize field names for consistency
                for r in recs:
                    if 'display_name' not in r and 'name' in r:
                        r['display_name'] = r.get('name')
                return True, recs
        # Nothing found
        return False, "No projects found or access denied"

    def _create_approval_request(self, category_id: int, date_start: str, date_end: str, project_id: int,
                                 odoo_session_data: Dict = None) -> Tuple[bool, Any, Optional[Dict]]:
        """Create approval request (returns success, result, renewed_session)"""
        payload = {
            'category_id': category_id,
            'request_owner_id': self.odoo_service.user_id if not odoo_session_data else odoo_session_data.get('user_id'),
            'name': 'Overtime request via Nasma chatbot',
            'date_start': date_start,
            'date_end': date_end,
            'x_studio_project': project_id,
        }
        
        # Use stateless if session data provided, otherwise fallback to regular
        use_stateless = odoo_session_data and odoo_session_data.get('session_id') and odoo_session_data.get('user_id')
        
        if use_stateless:
            ok_create, rid, renewed_create = self._make_odoo_request_stateless(
                'approval.request', 'create', {'args': [payload], 'kwargs': {}},
                session_id=odoo_session_data['session_id'],
                user_id=odoo_session_data['user_id'],
                username=odoo_session_data.get('username'),
                password=odoo_session_data.get('password')
            )
            if not ok_create:
                return False, rid, renewed_create
            
            # Immediately submit the request for approval
            try:
                ok_confirm, _, renewed_confirm = self._make_odoo_request_stateless(
                    'approval.request', 'action_confirm', {'args': [[rid]], 'kwargs': {}},
                    session_id=odoo_session_data['session_id'],
                    user_id=odoo_session_data['user_id'],
                    username=odoo_session_data.get('username'),
                    password=odoo_session_data.get('password')
                )
                # Return latest renewed session
                latest_renewed = renewed_confirm or renewed_create
                return True, rid, latest_renewed
            except Exception:
                return True, rid, renewed_create
        else:
            # Legacy fallback
            ok_create, rid = self._make_odoo_request('approval.request', 'create', {'args': [payload], 'kwargs': {}})
            if not ok_create:
                return False, rid, None
            
            try:
                _ = self._make_odoo_request('approval.request', 'action_confirm', {'args': [[rid]], 'kwargs': {}})
            except Exception:
                pass
            return True, rid, None

    # -------------------------- Flow handling -----------------------------
    def handle_flow(self, message: str, thread_id: str, employee_data: Dict, odoo_session_data: Dict = None) -> Optional[Dict[str, Any]]:
        """Main entry to manage overtime flow. Returns response dict or None."""
        try:
            # Normalize message early and catch cancellation regardless of step/widgets
            norm = (message or '').strip().lower()
            def _is_cancel_intent(txt: str) -> bool:
                try:
                    import difflib
                    txt = (txt or '').strip().lower()
                    hard = {'cancel','stop','exit','quit','abort','end','undo','nevermind','no thanks','no','n'}
                    if txt in hard:
                        return True
                    # tolerate small typos like 'canel'
                    for token in ['cancel','stop','exit','quit','abort','end','undo']:
                        if difflib.SequenceMatcher(a=txt, b=token).ratio() >= 0.8:
                            return True
                    return False
                except Exception:
                    return txt in {'cancel','stop','exit','quit','abort','end','undo','nevermind','no thanks','no','n'}

            # Ensure a working thread_id for session continuity
            if not thread_id:
                import time
                thread_id = f"overtime_{int(time.time())}"

            # Check active session first (overtime)
            active = self.session_manager.get_session(thread_id)
            if active and active.get('type') == 'overtime':
                # If session is not active anymore, clear and do not intercept other flows
                state = active.get('state', 'started')
                if state in ['cancelled', 'completed']:
                    self.session_manager.clear_session(thread_id)
                    return None
                # Global cancel while a session is active
                if _is_cancel_intent(norm):
                    try:
                        self.session_manager.cancel_session(thread_id, 'User cancelled overtime flow')
                    finally:
                        self.session_manager.clear_session(thread_id)
                    return {
                        'message': 'request cancelled, can i help you with anything else',
                        'thread_id': thread_id,
                        'session_handled': True
                    }
                return self._continue_overtime(message, thread_id, active, employee_data, odoo_session_data)

            # CRITICAL: Block if another flow is active (BEFORE intent detection)
            if active and active.get('type') not in (None, 'overtime') and active.get('state') in ['started', 'active']:
                other_flow = active.get('type', 'another')
                self._log(f"Blocking overtime - active {other_flow} flow detected on thread {thread_id}")
                return None  # Let the active flow handle the message

            # Detect new intent
            is_ot, conf = self.detect_intent(message)
            if not is_ot:
                return None

            # Initialize new overtime session
            company_name = self._get_company_name(employee_data) or 'Company'
            ok, cat = self._find_overtime_category(company_name)
            if not ok or not cat:
                return {
                    'message': f"Sorry, I couldn't find the overtime category for {company_name}. Please contact HR.",
                    'session_handled': True
                }
            category = cat[0] if isinstance(cat, list) else cat
            category_id = category.get('id')

            okp, projects = self._list_projects()
            project_options = []
            if okp and isinstance(projects, list):
                project_options = [
                    {
                        'label': (p.get('display_name') or p.get('name') or f"Project {p.get('id')}") or f"Project {p.get('id')}",
                        'value': str(p.get('id'))
                    }
                    for p in projects if p.get('id')
                ]

            session_data = {
                'category_id': category_id,
                'category_name': category.get('name'),
                'projects': project_options,
                'user_tz': (employee_data or {}).get('tz') or 'Asia/Amman'
            }
            self.session_manager.start_session(thread_id, 'overtime', session_data)

            return {
                'message': 'Please select the overtime date.',
                'thread_id': thread_id,
                'session_handled': True,
                'widgets': {
                    'single_date_picker': True,
                    'context_key': 'overtime_date_range'
                }
            }
        except Exception:
            return None

    def _continue_overtime(self, message: str, thread_id: str, session: Dict, employee_data: Dict, odoo_session_data: Dict = None) -> Optional[Dict[str, Any]]:
        try:
            step = session.get('step', 1)
            data = session.get('data', {})
            msg = (message or '').strip()

            # Early cancellation check before any parsing/validation
            def _is_cancel_intent(txt: str) -> bool:
                try:
                    import difflib
                    txt = (txt or '').strip().lower()
                    hard = {'cancel','stop','exit','quit','abort','end','undo','nevermind','no thanks','no','n'}
                    if txt in hard:
                        return True
                    for token in ['cancel','stop','exit','quit','abort','end','undo']:
                        if difflib.SequenceMatcher(a=txt, b=token).ratio() >= 0.8:
                            return True
                    return False
                except Exception:
                    return txt in {'cancel','stop','exit','quit','abort','end','undo','nevermind','no thanks','no','n'}
            if _is_cancel_intent(msg):
                try:
                    self.session_manager.cancel_session(thread_id, 'User cancelled overtime flow')
                finally:
                    self.session_manager.clear_session(thread_id)
                return {
                    'message': 'request cancelled, can i help you with anything else',
                    'thread_id': thread_id,
                    'session_handled': True
                }

            # Step 1: Expect date range reply
            if step == 1:
                value = None
                if msg.lower().startswith('overtime_date_range='):
                    value = msg.split('=', 1)[1].strip()
                else:
                    # Accept natural input like "24/09/2025 to 24/09/2025" or with '-'
                    value = msg
                if value:
                    import re
                    parts = [p.strip() for p in re.split(r"\s*(?:to|until|till|[-–—])\s*", value, flags=re.IGNORECASE) if p.strip()]
                    def _norm(token: str) -> Optional[str]:
                        from datetime import datetime
                        token = token.replace('.', '/').replace('-', '/').strip()
                        fmts = ['%d/%m/%Y', '%d/%m/%y', '%d/%m']
                        for f in fmts:
                            try:
                                dt = datetime.strptime(token, f)
                                year = dt.year if '%Y' in f or '%y' in f else date.today().year
                                return datetime(year, dt.month, dt.day).strftime('%d/%m/%Y')
                            except Exception:
                                continue
                        return None
                    if len(parts) == 1:
                        single = _norm(parts[0])
                        if single:
                            data['date_start_dmy'] = single
                            data['date_end_dmy'] = single
                            self.session_manager.update_session(thread_id, {'data': data, 'step': 2})
                            # Ask for hour range next
                            return self._hour_picker_response('Please choose your overtime hours (from/to).', thread_id)
                    elif len(parts) == 2:
                        s = _norm(parts[0])
                        e = _norm(parts[1])
                        if s and e:
                            if s != e:
                                return {
                                    'message': 'Overtime must be submitted for a single day. Please pick one date.',
                                    'thread_id': thread_id,
                                    'session_handled': True,
                                    'widgets': { 'single_date_picker': True, 'context_key': 'overtime_date_range' }
                                }
                            data['date_start_dmy'] = s
                            data['date_end_dmy'] = e
                            self.session_manager.update_session(thread_id, {'data': data, 'step': 2})
                            # Ask for hour range next
                            return self._hour_picker_response('Please choose your overtime hours (from/to).', thread_id)
                # Re-ask with widget if not understood
                return {
                    'message': 'Please pick a single overtime date.',
                    'thread_id': thread_id,
                    'session_handled': True,
                    'widgets': { 'single_date_picker': True, 'context_key': 'overtime_date_range' }
                }

            # Step 2: Capture hour range (hour_from/hour_to pairs)
            if step == 2:
                if 'hour_from=' in msg and 'hour_to=' in msg:
                    def _get_param(k: str, s: str) -> str:
                        try:
                            parts = {p.split('=')[0]: p.split('=')[1] for p in s.split('&') if '=' in p}
                            return parts.get(k, '')
                        except Exception:
                            return ''
                    hf = _get_param('hour_from', msg)
                    ht = _get_param('hour_to', msg)
                    if hf and ht:
                        data['hour_from'] = hf
                        data['hour_to'] = ht
                        self.session_manager.update_session(thread_id, {'data': data, 'step': 3})
                        # Ask for project
                        options = data.get('projects') or []
                        if not options:
                            okp2, projects2 = self._list_projects()
                            if okp2 and isinstance(projects2, list):
                                options = [{'label': p.get('name', f"Project {p.get('id')}") or f"Project {p.get('id')}", 'value': str(p.get('id'))} for p in projects2 if p.get('id')]
                                data['projects'] = options
                                self.session_manager.update_session(thread_id, {'data': data})
                        return {
                            'message': 'Select the related project.',
                            'thread_id': thread_id,
                            'session_handled': True,
                            'widgets': {
                                'select_dropdown': True,
                                'options': options,
                                'context_key': 'overtime_project_id',
                                'placeholder': 'Select a project'
                            }
                        }
                else:
                    # Accept natural language hour ranges like "17:00 to 18:00" or "5pm - 7pm"
                    frm, to = self._parse_hour_range_text(msg)
                    if frm and to:
                        data['hour_from'] = frm
                        data['hour_to'] = to
                        self.session_manager.update_session(thread_id, {'data': data, 'step': 3})
                        options = data.get('projects') or []
                        return {
                            'message': 'Select the related project.',
                            'thread_id': thread_id,
                            'session_handled': True,
                            'widgets': {
                                'select_dropdown': True,
                                'options': options,
                                'context_key': 'overtime_project_id',
                                'placeholder': 'Select a project'
                            }
                        }
                # Re-open picker
                return self._hour_picker_response('Please choose a valid hours range (end must be after start).', thread_id)

            # Step 3: Capture project selection
            if step == 3:
                if msg.lower().startswith('overtime_project_id='):
                    proj_id = msg.split('=', 1)[1].strip()
                    data['project_id'] = int(proj_id) if proj_id.isdigit() else proj_id
                    self.session_manager.update_session(thread_id, {'data': data, 'step': 4})
                    # Show confirmation
                    return self._confirmation_response(thread_id, data)
                # Re-ask with dropdown
                options = data.get('projects') or []
                if not options:
                    okp3, projects3 = self._list_projects()
                    if okp3 and isinstance(projects3, list):
                        options = [{'label': p.get('name', f"Project {p.get('id')}") or f"Project {p.get('id')}", 'value': str(p.get('id'))} for p in projects3 if p.get('id')]
                        data['projects'] = options
                        self.session_manager.update_session(thread_id, {'data': data})
                return {
                    'message': 'Please select a project from the list.',
                    'thread_id': thread_id,
                    'session_handled': True,
                    'widgets': {
                        'select_dropdown': True,
                        'options': options,
                        'context_key': 'overtime_project_id',
                        'placeholder': 'Select a project'
                    }
                }

            # Step 4: Confirm/cancel
            if step == 4:
                low = msg.lower()
                if low in {'yes', 'y', 'confirm', 'submit'}:
                    # Build datetimes
                    start_iso = self._parse_dmy(data.get('date_start_dmy'))
                    end_iso = self._parse_dmy(data.get('date_end_dmy'))
                    tzname = data.get('user_tz') or (employee_data or {}).get('tz') or 'Asia/Amman'
                    start_dt = self._local_to_utc_datetime_str(start_iso, data.get('hour_from', '9'), tzname)
                    end_dt = self._local_to_utc_datetime_str(end_iso, data.get('hour_to', '17'), tzname)
                    ok, result, renewed_session = self._create_approval_request(
                        category_id=data.get('category_id'),
                        date_start=start_dt,
                        date_end=end_dt,
                        project_id=int(data.get('project_id')) if data.get('project_id') else False,
                        odoo_session_data=odoo_session_data
                    )
                    
                    # Update Flask session if session was renewed
                    if renewed_session:
                        try:
                            from flask import session as flask_session
                            flask_session['odoo_session_id'] = renewed_session['session_id']
                            flask_session['user_id'] = renewed_session['user_id']
                            flask_session.modified = True
                        except Exception:
                            pass
                    
                    self.session_manager.complete_session(thread_id, {'submitted': ok, 'result': result})
                    if ok:
                        rid = result
                        metric_payload = {
                            'request_id': rid,
                            'category_id': data.get('category_id'),
                            'project_id': data.get('project_id'),
                            'date_local': {
                                'start': data.get('date_start_dmy'),
                                'end': data.get('date_end_dmy'),
                                'hour_from': data.get('hour_from'),
                                'hour_to': data.get('hour_to'),
                                'timezone': data.get('user_tz') or (employee_data or {}).get('tz'),
                            },
                            'date_utc': {
                                'start': start_dt,
                                'end': end_dt,
                            },
                        }
                        self._log_metric('overtime', thread_id, metric_payload, employee_data)
                        # Clear the session after success
                        try:
                            self.session_manager.clear_session(thread_id)
                        except Exception:
                            pass
                        return {
                            'message': f"✅ Overtime request #{rid} submitted for approval.",
                            'thread_id': thread_id,
                            'session_handled': True
                        }
                    # Also clear session on failure to avoid sticky flows
                    try:
                        self.session_manager.clear_session(thread_id)
                    except Exception:
                        pass
                    return {
                        'message': f"❌ Failed to submit overtime request: {result}",
                        'thread_id': thread_id,
                        'session_handled': True
                    }
                elif low in {'no', 'n', 'cancel', 'stop', 'exit', 'quit', 'abort', 'end', 'undo'}:
                    try:
                        self.session_manager.cancel_session(thread_id, 'User cancelled overtime flow')
                    finally:
                        # Clear immediately
                        try:
                            self.session_manager.clear_session(thread_id)
                        except Exception:
                            pass
                    return {
                        'message': 'request cancelled, can i help you with anything else',
                        'thread_id': thread_id,
                        'session_handled': True
                    }
                # Re-show confirmation if unclear
                return self._confirmation_response(thread_id, data)

            return None
        except Exception:
            # In any error, clear session to avoid stuck flows
            if thread_id:
                self.session_manager.clear_session(thread_id)
            return {
                'message': 'Sorry, something went wrong handling your overtime request. Please try again.',
                'thread_id': thread_id,
                'session_handled': True
            }

    # -------------------------- Helpers ----------------------------------
    def _local_to_utc_datetime_str(self, ymd: str, hour_key: str, tzname: str) -> str:
        """Convert a local date + hour selection to UTC timestamp string for Odoo.

        Odoo stores datetimes in UTC; to display the same local time in the UI,
        we must convert the provided local time to UTC before submission.
        """
        # Build local datetime
        try:
            y, m, d = [int(p) for p in ymd.split('-')]
        except Exception:
            # Accept DD/MM/YYYY
            try:
                dd, mm, yy = [int(p) for p in ymd.split('/')]
                y, m, d = yy, mm, dd
            except Exception:
                y, m, d = date.today().year, date.today().month, date.today().day
        try:
            hfloat = float(hour_key)
        except Exception:
            hfloat = 0.0
        h = int(hfloat)
        minutes = 30 if abs(hfloat - h - 0.5) < 1e-9 else 0
        naive = datetime(y, m, d, h, minutes)
        # Attach timezone
        try:
            if ZoneInfo and tzname:
                local_dt = naive.replace(tzinfo=ZoneInfo(tzname))
            else:
                # Fallback to fixed +03:00 (Jordan standard) if tz unavailable
                local_dt = naive.replace(tzinfo=timezone(timedelta(hours=3)))
        except Exception:
            local_dt = naive.replace(tzinfo=timezone(timedelta(hours=3)))
        utc_dt = local_dt.astimezone(timezone.utc)
        return utc_dt.strftime('%Y-%m-%d %H:%M:%S')

    def _hour_picker_response(self, message: str, thread_id: str) -> Dict[str, Any]:
        options = []
        def _push(val: float):
            key = str(int(val)) if abs(val - int(val)) < 1e-9 else str(val)
            h = int(val)
            m = 30 if abs(val - h - 0.5) < 1e-9 else 0
            label = f"{h:02d}:{m:02d}"
            options.append({'value': key, 'label': label})
        v = 9.0
        while v <= 23.5 + 1e-9:
            _push(v)
            v += 0.5
        v = 0.0
        while v <= 1.0 + 1e-9:
            _push(v)
            v += 0.5
        return {
            'message': message,
            'thread_id': thread_id,
            'session_handled': True,
            'widgets': {
                'hour_range_picker': True,
                'hour_options': options
            }
        }

    def _parse_hour_value(self, token: str) -> float:
        try:
            import re
            s = token.strip().lower()
            s = s.replace('.', ':')
            m = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", s)
            if not m:
                return float('nan')
            h = int(m.group(1))
            mins = int(m.group(2)) if m.group(2) else 0
            ap = m.group(3)
            if ap == 'am':
                if h == 12:
                    h = 0
            elif ap == 'pm':
                if h != 12:
                    h += 12
            h = max(0, min(23, h))
            mins = 30 if 15 <= mins < 45 else 0
            return h + (0.5 if mins == 30 else 0.0)
        except Exception:
            return float('nan')

    def _parse_hour_range_text(self, text: str):
        try:
            import re
            if not text or not isinstance(text, str):
                return None, None
            s = text.strip().lower()
            s = re.sub(r"\s*(?:–|—)\s*", '-', s)
            parts = re.split(r"\s*(?:to|till|until|-)\s*", s)
            if len(parts) != 2:
                return None, None
            v1 = self._parse_hour_value(parts[0])
            v2 = self._parse_hour_value(parts[1])
            if v1 != v1 or v2 != v2:
                return None, None
            if v2 <= v1:
                return None, None
            def _to_key(v: float) -> str:
                return str(int(v)) if abs(v - int(v)) < 1e-9 else str(v)
            return _to_key(v1), _to_key(v2)
        except Exception:
            return None, None

    def _format_hour_12(self, hour_key: str) -> str:
        """Convert hour key (like '14' or '14.5') to 12-hour format (like '2:00 PM' or '2:30 PM')."""
        try:
            if not hour_key:
                return "N/A"

            hour_float = float(hour_key)
            hours = int(hour_float)
            minutes = 30 if abs(hour_float - hours - 0.5) < 1e-9 else 0

            # Convert to 12-hour format
            if hours == 0:
                hour_12 = 12
                period = "AM"
            elif hours < 12:
                hour_12 = hours
                period = "AM"
            elif hours == 12:
                hour_12 = 12
                period = "PM"
            else:
                hour_12 = hours - 12
                period = "PM"

            return f"{hour_12}:{minutes:02d} {period}"
        except Exception:
            return str(hour_key) if hour_key else "N/A"

    def _calculate_time_duration(self, hour_from: str, hour_to: str) -> str:
        """Calculate duration between two hour keys and return as formatted string."""
        try:
            if not hour_from or not hour_to:
                return "N/A"

            from_float = float(hour_from)
            to_float = float(hour_to)

            if to_float <= from_float:
                return "N/A"

            duration_hours = to_float - from_float
            hours = int(duration_hours)
            minutes = int((duration_hours - hours) * 60)

            if hours == 0:
                return f"{minutes} minutes"
            elif minutes == 0:
                return f"{hours} hour{'s' if hours != 1 else ''}"
            else:
                return f"{hours} hour{'s' if hours != 1 else ''} {minutes} minutes"
        except Exception:
            return "N/A"

    def _confirmation_response(self, thread_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        start_iso = self._parse_dmy(data.get('date_start_dmy'))
        end_iso = self._parse_dmy(data.get('date_end_dmy'))
        def fmt(d: str) -> str:
            try:
                return datetime.strptime(d, '%Y-%m-%d').strftime('%d/%m/%Y')
            except Exception:
                return d

        # Format hours to 12-hour format
        hour_from_12 = self._format_hour_12(data.get('hour_from'))
        hour_to_12 = self._format_hour_12(data.get('hour_to'))

        # Calculate total time requested
        total_time = self._calculate_time_duration(data.get('hour_from'), data.get('hour_to'))

        msg = (
            "Here are the details for your overtime request:\n\n"
            f"📂 **Category:** {data.get('category_name', 'Overtime')}\n"
            f"📅 **Period:** {fmt(start_iso)} → {fmt(end_iso)}\n"
            f"⏰ **Hours:** {hour_from_12} → {hour_to_12}\n"
            f"🕒 **Time Requested:** {total_time}\n"
            f"📁 **Project:** {data.get('project_id')}\n\n"
            "Do you want to submit this request? Reply or click 'yes' to confirm or 'no' to cancel"
        )
        buttons = [
            {'text': 'Yes', 'value': 'yes', 'type': 'confirmation_choice'},
            {'text': 'No', 'value': 'no', 'type': 'confirmation_choice'}
        ]
        return {
            'message': msg,
            'thread_id': thread_id,
            'session_handled': True,
            'buttons': buttons
        }

    def _parse_dmy(self, s: Optional[str]) -> str:
        try:
            s = (s or '').strip()
            # Accept DD/MM/YYYY or DD/MM/YY
            for fmt in ('%d/%m/%Y', '%d/%m/%y'):
                try:
                    return datetime.strptime(s, fmt).strftime('%Y-%m-%d')
                except Exception:
                    continue
            # If already ISO
            datetime.strptime(s, '%Y-%m-%d')
            return s
        except Exception:
            # Fallback to today
            return date.today().strftime('%Y-%m-%d')
