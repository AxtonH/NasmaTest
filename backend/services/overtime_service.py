import re
from datetime import datetime, date, timezone, timedelta
try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except Exception:
    ZoneInfo = None
from typing import Dict, Any, Tuple, Optional, List


def debug_log(message: str, category: str = "general"):
    """Debug logging helper"""
    try:
        print(f"DEBUG: {message}")
    except Exception:
        pass


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

        result = (score >= 0.5), min(1.0, score)
        return result

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
                error_code = error_data.get('code', 'Unknown') if isinstance(error_data, dict) else 'Unknown'
                error_name = error_data.get('name', 'Unknown') if isinstance(error_data, dict) else 'Unknown'
                debug_log(f"Odoo error in {model}.{method}: code={error_code}, name={error_name}, message={error_msg}", "bot_logic")
                debug_log(f"Full error data: {error_data}", "bot_logic")
                return False, error_msg, renewed_session

            return True, result.get('result'), renewed_session

        except Exception as e:
            debug_log(f"Exception in _make_odoo_request_stateless: {str(e)}", "bot_logic")
            import traceback
            debug_log(f"Traceback: {traceback.format_exc()}", "bot_logic")
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

    def _find_overtime_category(self, company_name: str, odoo_session_data: Dict = None) -> Tuple[bool, Any]:
        name = f"Overtime - {company_name}".strip()
        domain = [["name", "=", name]]
        params = {
            'args': [domain],
            'kwargs': {'fields': ['id', 'name'], 'limit': 1}
        }
        # Use stateless if session data provided, otherwise fallback to regular
        if odoo_session_data and odoo_session_data.get('session_id') and odoo_session_data.get('user_id'):
            ok, res, _ = self._make_odoo_request_stateless(
                'approval.category', 'search_read', params,
                session_id=odoo_session_data['session_id'],
                user_id=odoo_session_data['user_id'],
                username=odoo_session_data.get('username'),
                password=odoo_session_data.get('password')
            )
            return ok, res
        return self._make_odoo_request('approval.category', 'search_read', params)

    def _generate_hour_options(self) -> List[Dict[str, str]]:
        """Generate hour options with 15-minute intervals covering 24 hours (0:00 to 23:45)."""
        options = []
        def _push_hour(val: float):
            key = str(int(val)) if abs(val - int(val)) < 1e-9 else f"{val:.2f}".rstrip('0').rstrip('.')
            h = int(val)
            m = int(round((val - h) * 60))  # Calculate minutes from decimal part
            label = f"{h:02d}:{m:02d}"
            options.append({'value': key, 'label': label})
        
        # Generate all 15-minute intervals from 0:00 to 23:45
        v = 0.0
        while v <= 23.75 + 1e-9:  # 23.75 = 23:45
            _push_hour(v)
            v += 0.25  # 15 minutes = 0.25 hours
        
        return options

    def _generate_hour_options_30min(self) -> List[Dict[str, str]]:
        """Generate hour options with 30-minute intervals covering 24 hours (0:00 to 23:30).
        
        Used for time off custom hours which require 30-minute intervals.
        """
        options = []
        def _push_hour(val: float):
            key = str(int(val)) if abs(val - int(val)) < 1e-9 else f"{val:.2f}".rstrip('0').rstrip('.')
            h = int(val)
            m = int(round((val - h) * 60))  # Calculate minutes from decimal part
            label = f"{h:02d}:{m:02d}"
            options.append({'value': key, 'label': label})
        
        # Generate all 30-minute intervals from 0:00 to 23:30
        v = 0.0
        while v <= 23.5 + 1e-9:  # 23.5 = 23:30
            _push_hour(v)
            v += 0.5  # 30 minutes = 0.5 hours
        
        return options

    def _list_projects(self, odoo_session_data: Dict = None) -> Tuple[bool, Any]:
        """Fetch a large list of projects using robust fallbacks.

        Strategy:
        1) Try search_read on project.project (active only). If empty, try without domain.
        2) If still empty, try explicit search -> read on project.project.
        3) If model missing/empty, try alternative model names ('project', 'x_project') using search+read.
        """
        # Helper to make request (stateless or regular)
        def _make_req(model, method, req_params):
            if odoo_session_data and odoo_session_data.get('session_id') and odoo_session_data.get('user_id'):
                ok, res, _ = self._make_odoo_request_stateless(
                    model, method, req_params,
                    session_id=odoo_session_data['session_id'],
                    user_id=odoo_session_data['user_id'],
                    username=odoo_session_data.get('username'),
                    password=odoo_session_data.get('password')
                )
                return ok, res
            return self._make_odoo_request(model, method, req_params)
        
        # 1) search_read active
        sr_args = {'args': [[["active", "=", True]]], 'kwargs': {'fields': ['id', 'name', 'display_name', 'active'], 'order': 'name asc', 'limit': 1000}}
        ok, result = _make_req('project.project', 'search_read', sr_args)
        if ok and isinstance(result, list) and len(result) > 0:
            return True, result
        # 1b) search_read without domain
        sr_args_nd = {'args': [[]], 'kwargs': {'fields': ['id', 'name', 'display_name', 'active'], 'order': 'name asc', 'limit': 1000}}
        ok2, result2 = _make_req('project.project', 'search_read', sr_args_nd)
        if ok2 and isinstance(result2, list) and len(result2) > 0:
            return True, result2
        # 2) search -> read on project.project
        try_models = ['project.project', 'project', 'x_project']
        for model in try_models:
            # search (no domain to be safe)
            ok_s, ids = _make_req(model, 'search', {'args': [[]], 'kwargs': {'limit': 1000}})
            if not ok_s or not isinstance(ids, list) or len(ids) == 0:
                continue
            ok_r, recs = _make_req(model, 'read', {'args': [ids], 'kwargs': {'fields': ['id', 'name', 'display_name', 'active']}})
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
        }
        
        # Only include project if it's a valid integer (not False or None)
        if project_id and isinstance(project_id, int):
            payload['x_studio_project'] = project_id
        
        # Use stateless if session data provided, otherwise fallback to regular
        use_stateless = odoo_session_data and odoo_session_data.get('session_id') and odoo_session_data.get('user_id')
        
        debug_log(f"Creating approval request with payload: {payload}", "bot_logic")
        
        if use_stateless:
            ok_create, rid, renewed_create = self._make_odoo_request_stateless(
                'approval.request', 'create', {'args': [payload], 'kwargs': {}},
                session_id=odoo_session_data['session_id'],
                user_id=odoo_session_data['user_id'],
                username=odoo_session_data.get('username'),
                password=odoo_session_data.get('password')
            )
            if not ok_create:
                debug_log(f"Failed to create approval request: {rid}", "bot_logic")
                return False, rid, renewed_create
            
            debug_log(f"Approval request created successfully with ID: {rid}", "bot_logic")
            
            # Immediately submit the request for approval
            try:
                ok_confirm, confirm_result, renewed_confirm = self._make_odoo_request_stateless(
                    'approval.request', 'action_confirm', {'args': [[rid]], 'kwargs': {}},
                    session_id=odoo_session_data['session_id'],
                    user_id=odoo_session_data['user_id'],
                    username=odoo_session_data.get('username'),
                    password=odoo_session_data.get('password')
                )
                if not ok_confirm:
                    debug_log(f"Failed to confirm approval request: {confirm_result}", "bot_logic")
                    # Request was created but confirmation failed - return error
                    error_msg = str(confirm_result) if confirm_result else "Failed to submit request for approval"
                    return False, error_msg, renewed_create
                # Return latest renewed session
                latest_renewed = renewed_confirm or renewed_create
                debug_log(f"Approval request confirmed successfully", "bot_logic")
                return True, rid, latest_renewed
            except Exception as e:
                debug_log(f"Exception during confirmation: {str(e)}", "bot_logic")
                import traceback
                debug_log(f"Traceback: {traceback.format_exc()}", "bot_logic")
                # Request was created but confirmation had exception - return error
                return False, f"Failed to submit request: {str(e)}", renewed_create
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
                        'message': 'Overtime request cancelled.',
                        'thread_id': thread_id,
                        'session_handled': True
                    }
                # Route form submissions to form handler
                if message.startswith('overtime_form='):
                    return self.handle_overtime_form_step(message, thread_id, active, employee_data, odoo_session_data)
                # Otherwise continue with confirmation step
                return self._continue_overtime(message, thread_id, active, employee_data, odoo_session_data)

            # Detect new intent first
            is_ot, conf = self.detect_intent(message)
            if not is_ot:
                return None

            # CRITICAL: Re-check active session after intent detection to ensure we have the latest state
            # Block if another flow is active AND user is trying to start overtime
            active = self.session_manager.get_session(thread_id)
            if active and active.get('type') not in (None, 'overtime') and active.get('state') in ['started', 'active']:
                other_flow = active.get('type', 'another')
                debug_log(f"Blocking overtime - active {other_flow} flow detected on thread {thread_id}", "bot_logic")
                return {
                    'message': 'Sorry, I cannot start a new request until you finish or cancel the current one. To cancel the request, type ***Cancel***.',
                    'thread_id': thread_id,
                    'session_handled': True
                }

            # Initialize new overtime session
            debug_log(f"Initializing overtime session for company: {employee_data.get('company_id_details', {}).get('name', 'Unknown')}", "bot_logic")
            company_name = self._get_company_name(employee_data) or 'Company'
            debug_log(f"Resolved company name: {company_name}", "bot_logic")
            try:
                ok, cat = self._find_overtime_category(company_name, odoo_session_data)
                debug_log(f"Overtime category lookup result: ok={ok}, cat={cat if ok else 'N/A'}", "bot_logic")
            except Exception as e:
                debug_log(f"Exception in _find_overtime_category: {str(e)}", "bot_logic")
                import traceback
                debug_log(f"Traceback: {traceback.format_exc()}", "bot_logic")
                return {
                    'message': f"Sorry, I encountered an error while looking up the overtime category. Please try again later.",
                    'session_handled': True
                }
            
            if not ok or not cat:
                return {
                    'message': f"Sorry, I couldn't find the overtime category for {company_name}. Please contact HR.",
                    'session_handled': True
                }
            category = cat[0] if isinstance(cat, list) else cat
            category_id = category.get('id')
            
            # Validate category_id exists
            if not category_id:
                debug_log(f"ERROR: category_id is None after lookup! Category object: {category}", "bot_logic")
                return {
                    'message': f"Sorry, I couldn't find a valid overtime category for {company_name}. Please contact HR.",
                    'session_handled': True
                }

            try:
                okp, projects = self._list_projects(odoo_session_data)
            except Exception as e:
                debug_log(f"Exception in _list_projects: {str(e)}", "bot_logic")
                import traceback
                debug_log(f"Traceback: {traceback.format_exc()}", "bot_logic")
                projects = []
                okp = False
            
            project_options = []
            if okp and isinstance(projects, list):
                project_options = [
                    {
                        'label': (p.get('display_name') or p.get('name') or f"Project {p.get('id')}") or f"Project {p.get('id')}",
                        'value': str(p.get('id'))
                    }
                    for p in projects if p.get('id')
                ]

            # Store category_id and other metadata at top level AND in data
            # Note: start_session stores the data dict in session['data'], so we need to store
            # category_id at top level separately for easy access
            session_data = {
                'category_id': category_id,
                'category_name': category.get('name'),
                'projects': project_options,
                'user_tz': (employee_data or {}).get('tz') or 'Asia/Amman'
            }
            # Start session with data dict
            session_obj = self.session_manager.start_session(thread_id, 'overtime', session_data)
            # Also store category_id at top level for easy access
            self.session_manager.update_session(thread_id, {
                'category_id': category_id,
                'category_name': category.get('name')
            })

            # Generate hour options for dropdown with 15-minute intervals covering 24 hours
            hour_options = self._generate_hour_options()

            return {
                'message': '**Request Overtime**\n\nPlease fill in the details below:',
                'thread_id': thread_id,
                'session_handled': True,
                'widgets': {
                    'overtime_flow': {
                        'step': 'overtime_form',
                        'category_id': category_id,
                        'category_name': category.get('name'),
                        'user_tz': session_data['user_tz']
                    },
                    'overtime_form': True,
                    'hour_options': hour_options,
                    'project_options': project_options,
                    'context_key': 'overtime_form'
                }
            }
        except Exception as e:
            debug_log(f"Exception in handle_flow: {str(e)}", "bot_logic")
            import traceback
            debug_log(f"Traceback: {traceback.format_exc()}", "bot_logic")
            return {
                'message': 'Sorry, I encountered an error while processing your overtime request. Please try again.',
                'session_handled': True
            }

    def handle_overtime_form_step(self, message: str, thread_id: str, session: Dict, employee_data: Dict, odoo_session_data: Dict = None) -> Optional[Dict[str, Any]]:
        """Handle single-step overtime form submission.
        
        Args:
            message: Form data in format: overtime_form=date|hour_from|hour_to|project_id
            thread_id: Thread ID for session management
            session: Current session data
            employee_data: Employee data dict
            odoo_session_data: Optional Odoo session data for stateless requests
        
        Returns:
            Response dict with confirmation or error
        """
        try:
            msg = (message or '').strip()
            
            # Early cancellation check
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
                    'message': 'Overtime request cancelled.',
                    'thread_id': thread_id,
                    'session_handled': True
                }
            
            # Parse form data: overtime_form=date|hour_from|hour_to|project_id
            if not msg.startswith('overtime_form='):
                # Invalid format, show form again
                hour_options = self._generate_hour_options()
                project_options = session.get('projects', [])
                return {
                    'message': 'Please fill in all required fields.',
                    'thread_id': thread_id,
                    'session_handled': True,
                    'widgets': {
                        'overtime_flow': session.get('overtime_flow', {}),
                        'overtime_form': True,
                        'hour_options': hour_options,
                        'project_options': project_options,
                        'context_key': 'overtime_form'
                    }
                }
            
            # Extract form fields
            form_data_str = msg.replace('overtime_form=', '')
            parts = form_data_str.split('|')
            
            if len(parts) < 4:
                # Regenerate hour options and get project options
                hour_options = self._generate_hour_options()
                project_options = session.get('projects', [])
                return {
                    'message': 'Please fill in all required fields: date, start time, end time, and project.',
                    'thread_id': thread_id,
                    'session_handled': True,
                    'widgets': {
                        'overtime_flow': session.get('overtime_flow', {}),
                        'overtime_form': True,
                        'hour_options': hour_options,
                        'project_options': project_options,
                        'context_key': 'overtime_form'
                    }
                }
            
            date_str = parts[0].strip()
            hour_from_str = parts[1].strip()
            hour_to_str = parts[2].strip()
            project_id_str = parts[3].strip()
            
            # Validate date
            date_dmy = self._parse_date_input(date_str)
            if not date_dmy:
                # Regenerate hour options and get project options
                hour_options = []
                def _push_hour(val: float):
                    key = str(int(val)) if abs(val - int(val)) < 1e-9 else str(val)
                    h = int(val)
                    m = 30 if abs(val - h - 0.5) < 1e-9 else 0
                    label = f"{h:02d}:{m:02d}"
                    hour_options.append({'value': key, 'label': label})
                
                v = 9.0
                while v <= 23.5 + 1e-9:
                    _push_hour(v)
                    v += 0.5
                v = 0.0
                while v <= 1.0 + 1e-9:
                    _push_hour(v)
                    v += 0.5
                
                project_options = session.get('projects', [])
                return {
                    'message': 'Please enter a valid date (DD/MM/YYYY format).',
                    'thread_id': thread_id,
                    'session_handled': True,
                    'widgets': {
                        'overtime_flow': session.get('overtime_flow', {}),
                        'overtime_form': True,
                        'hour_options': hour_options,
                        'project_options': project_options,
                        'context_key': 'overtime_form'
                    }
                }
            
            # Parse hour range (supports both typed input like "9 to 9:30" and dropdown values)
            debug_log(f"Parsing hour range: from='{hour_from_str}', to='{hour_to_str}'", "bot_logic")
            hour_from, hour_to = self._parse_hour_range_from_form(hour_from_str, hour_to_str)
            debug_log(f"Parsed hour range: from='{hour_from}', to='{hour_to}'", "bot_logic")
            if not hour_from or not hour_to:
                # Regenerate hour options and get project options
                hour_options = self._generate_hour_options()
                project_options = session.get('projects', [])
                return {
                    'message': 'Please enter a valid time range (e.g., "9:15" to "9:30" or select from dropdown). End time must be after start time.',
                    'thread_id': thread_id,
                    'session_handled': True,
                    'widgets': {
                        'overtime_flow': session.get('overtime_flow', {}),
                        'overtime_form': True,
                        'hour_options': hour_options,
                        'project_options': project_options,
                        'context_key': 'overtime_form'
                    }
                }
            
            # Validate project
            try:
                project_id = int(project_id_str) if project_id_str.isdigit() else None
            except Exception:
                project_id = None
            
            if not project_id:
                # Regenerate hour options and get project options
                hour_options = self._generate_hour_options()
                project_options = session.get('projects', [])
                return {
                    'message': 'Please select a project from the dropdown.',
                    'thread_id': thread_id,
                    'session_handled': True,
                    'widgets': {
                        'overtime_flow': session.get('overtime_flow', {}),
                        'overtime_form': True,
                        'hour_options': hour_options,
                        'project_options': project_options,
                        'context_key': 'overtime_form'
                    }
                }
            
            # Store form data in session
            # Ensure hour values are strings (they should already be, but ensure for safety)
            hour_from_str = str(hour_from) if hour_from else '9'
            hour_to_str = str(hour_to) if hour_to else '17'
            
            debug_log(f"Storing form data: hour_from='{hour_from_str}', hour_to='{hour_to_str}'", "bot_logic")
            
            # Get category_id from session - check both top level and data dict
            # (start_session stores initial data in session['data'], but we also store it at top level)
            category_id = session.get('category_id') or session.get('data', {}).get('category_id')
            if not category_id:
                debug_log(f"WARNING: category_id missing from session during form submission! Session keys: {list(session.keys())}, Data keys: {list(session.get('data', {}).keys())}", "bot_logic")
            
            form_data = {
                'date_dmy': date_dmy,
                'hour_from': hour_from_str,
                'hour_to': hour_to_str,
                'project_id': project_id,
                'category_id': category_id,  # Store the category_id explicitly
                'category_name': session.get('category_name'),
                'user_tz': session.get('user_tz') or (employee_data or {}).get('tz') or 'Asia/Amman',
                'projects': session.get('projects', [])
            }
            # Update session: preserve category_id at session level AND store in data
            update_dict = {'data': form_data, 'step': 'confirmation'}
            if category_id:
                update_dict['category_id'] = category_id  # Preserve at session level
            self.session_manager.update_session(thread_id, update_dict)
            
            # Show confirmation
            return self._confirmation_response(thread_id, form_data)
            
        except Exception as e:
            debug_log(f"Error in handle_overtime_form_step: {str(e)}", "bot_logic")
            import traceback
            debug_log(f"Traceback: {traceback.format_exc()}", "bot_logic")
            return {
                'message': 'Sorry, something went wrong processing your overtime form. Please try again.',
                'thread_id': thread_id,
                'session_handled': True
            }

    def _continue_overtime(self, message: str, thread_id: str, session: Dict, employee_data: Dict, odoo_session_data: Dict = None) -> Optional[Dict[str, Any]]:
        """Continue overtime flow - handles confirmation step."""
        try:
            step = session.get('step', 'confirmation')
            data = session.get('data', {})
            msg = (message or '').strip().lower()

            # Early cancellation check
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
                    'message': 'Overtime request cancelled.',
                    'thread_id': thread_id,
                    'session_handled': True
                }

            # Confirmation step only
            if step == 'confirmation':
                if msg in {'yes', 'y', 'confirm', 'submit', 'overtime_confirm'}:
                    # Build datetimes
                    hour_from_val = data.get('hour_from', '9')
                    hour_to_val = data.get('hour_to', '17')
                    debug_log(f"Submitting overtime: hour_from='{hour_from_val}', hour_to='{hour_to_val}'", "bot_logic")
                    
                    start_iso = self._parse_dmy(data.get('date_dmy'))
                    end_iso = self._parse_dmy(data.get('date_dmy'))  # Same date for overtime
                    tzname = data.get('user_tz') or (employee_data or {}).get('tz') or 'Asia/Amman'
                    start_dt = self._local_to_utc_datetime_str(start_iso, hour_from_val, tzname)
                    end_dt = self._local_to_utc_datetime_str(end_iso, hour_to_val, tzname)
                    debug_log(f"Converted to UTC: start_dt='{start_dt}', end_dt='{end_dt}'", "bot_logic")
                    
                    # Validate that end_dt is after start_dt
                    try:
                        start_dt_obj = datetime.strptime(start_dt, '%Y-%m-%d %H:%M:%S')
                        end_dt_obj = datetime.strptime(end_dt, '%Y-%m-%d %H:%M:%S')
                        if end_dt_obj <= start_dt_obj:
                            return {
                                'message': '❌ Invalid time range: End time must be after start time.',
                                'thread_id': thread_id,
                                'session_handled': True
                            }
                    except Exception as e:
                        debug_log(f"Error validating datetime range: {str(e)}", "bot_logic")
                    
                    # Get category_id from data first (it's stored in form_data), then fallback to session level
                    category_id_from_data = data.get('category_id') if data else None
                    category_id_from_session = session.get('category_id')
                    category_id = category_id_from_data or category_id_from_session
                    
                    debug_log(f"Category ID check: data.get('category_id')={category_id_from_data}, session.get('category_id')={category_id_from_session}, final={category_id}", "bot_logic")
                    
                    if not category_id:
                        debug_log(f"Category ID missing! Session keys: {list(session.keys())}, Data keys: {list(data.keys()) if data else 'No data'}, Data values: {data if data else 'No data'}", "bot_logic")
                        return {
                            'message': '❌ Error: Category ID is missing. Please try starting the overtime request again.',
                            'thread_id': thread_id,
                            'session_handled': True
                        }
                    
                    debug_log(f"Using category_id: {category_id} (type: {type(category_id)}, from: {'data' if category_id_from_data else 'session'})", "bot_logic")
                    
                    ok, result, renewed_session = self._create_approval_request(
                        category_id=int(category_id) if category_id else None,
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
                                'start': data.get('date_dmy'),
                                'end': data.get('date_dmy'),
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
                    
                    # Format error message more clearly
                    error_msg = str(result) if result else "Unknown error"
                    if "Odoo Server Error" in error_msg or not error_msg:
                        error_msg = "Odoo Server Error - Please check that the time range is valid and try again."
                    
                    return {
                        'message': f"❌ Failed to submit overtime request: {error_msg}",
                        'thread_id': thread_id,
                        'session_handled': True
                    }
                elif msg in {'no', 'n', 'cancel', 'stop', 'exit', 'quit', 'abort', 'end', 'undo', 'overtime_cancel'}:
                    try:
                        self.session_manager.cancel_session(thread_id, 'User cancelled overtime flow')
                    finally:
                        # Clear immediately
                        try:
                            self.session_manager.clear_session(thread_id)
                        except Exception:
                            pass
                    return {
                        'message': 'Overtime request cancelled.',
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
        
        hour_key can be:
        - "9" -> 9:00
        - "9.25" -> 9:15
        - "9.5" -> 9:30
        - "9.75" -> 9:45
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
        # Calculate minutes from decimal hours (e.g., 9.25 -> 15 minutes, 9.5 -> 30 minutes)
        minutes = int((hfloat - h) * 60)
        # Round minutes to nearest 5-minute increment (0, 5, 10, 15, ..., 55) as per Odoo requirements
        minutes = round(minutes / 5) * 5
        # Ensure minutes are within valid range (0-55)
        minutes = max(0, min(55, minutes))
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
        """Parse hour value from string, preserving minutes.
        
        Supports formats like:
        - "9" -> 9.0
        - "9:15" -> 9.25
        - "9:30" -> 9.5
        - "17:45" -> 17.75
        - "9am" -> 9.0
        - "5pm" -> 17.0
        """
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
            # Preserve actual minutes instead of rounding to 30 or 0
            # Convert minutes to decimal hours
            return h + (mins / 60.0)
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
        """Convert hour key (like '14', '14.5', '9.25') to 12-hour format (like '2:00 PM', '2:30 PM', '9:15 AM').
        
        Supports decimal hours:
        - "9" -> 9:00 AM
        - "9.25" -> 9:15 AM (15 minutes)
        - "9.5" -> 9:30 AM (30 minutes)
        - "9.75" -> 9:45 AM (45 minutes)
        """
        try:
            if not hour_key:
                return "N/A"

            hour_float = float(hour_key)
            hours = int(hour_float)
            # Calculate minutes from decimal hours (e.g., 9.25 -> 15 minutes, 9.5 -> 30 minutes)
            minutes = int((hour_float - hours) * 60)

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

    def _parse_date_input(self, date_str: str) -> Optional[str]:
        """Parse date input from various formats to DD/MM/YYYY."""
        if not date_str:
            return None
        try:
            # Try parsing DD/MM/YYYY or DD/MM/YY
            date_str = date_str.strip().replace('.', '/').replace('-', '/')
            for fmt in ['%d/%m/%Y', '%d/%m/%y', '%d/%m']:
                try:
                    dt = datetime.strptime(date_str, fmt)
                    year = dt.year if '%Y' in fmt or '%y' in fmt else date.today().year
                    return datetime(year, dt.month, dt.day).strftime('%d/%m/%Y')
                except Exception:
                    continue
            # Try YYYY-MM-DD format
            try:
                dt = datetime.strptime(date_str, '%Y-%m-%d')
                return dt.strftime('%d/%m/%Y')
            except Exception:
                pass
        except Exception:
            pass
        return None

    def _parse_hour_range_from_form(self, hour_from_str: str, hour_to_str: str) -> Tuple[Optional[str], Optional[str]]:
        """Parse hour range from form inputs.
        
        Supports:
        - Dropdown values: "9", "9.5", "17", "17.25" (decimal hours)
        - Typed input: "9", "9:15", "9:30", "17:00", "5pm"
        - Preserves actual minutes (e.g., "9:15" -> 9.25, "9:30" -> 9.5)
        """
        if not hour_from_str or not hour_to_str:
            return None, None
        
        # First, try parsing as decimal numbers (dropdown values like "17.25")
        # This must come BEFORE _parse_hour_value because _parse_hour_value converts "." to ":"
        # which would incorrectly parse "17.25" as "17:25" (17 hours 25 minutes) instead of 17.25 hours (17:15)
        try:
            from_val = float(hour_from_str)
            to_val = float(hour_to_str)
            # Check if these look like decimal hours (not just whole numbers that could be time strings)
            # If they're valid floats and to > from, use them directly
            if to_val > from_val:
                def _to_key(v: float) -> str:
                    # If it's a whole number, return as integer string
                    if abs(v - int(v)) < 1e-9:
                        return str(int(v))
                    # Otherwise return with up to 2 decimal places
                    return f"{v:.2f}".rstrip('0').rstrip('.')
                return _to_key(from_val), _to_key(to_val)
        except (ValueError, TypeError):
            pass
        
        # Fallback: Try parsing as typed time strings (handles "9:15" format)
        from_parsed = self._parse_hour_value(hour_from_str)
        to_parsed = self._parse_hour_value(hour_to_str)
        
        # If both parsed successfully as time strings, use those
        if from_parsed == from_parsed and to_parsed == to_parsed:  # Check not NaN
            if to_parsed > from_parsed:
                # Convert to string preserving decimal precision
                def _to_key(v: float) -> str:
                    # If it's a whole number, return as integer string
                    if abs(v - int(v)) < 1e-9:
                        return str(int(v))
                    # Otherwise return with up to 2 decimal places
                    return f"{v:.2f}".rstrip('0').rstrip('.')
                return _to_key(from_parsed), _to_key(to_parsed)
        
        return None, None

    def _confirmation_response(self, thread_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Generate confirmation message for overtime request."""
        date_dmy = data.get('date_dmy', '')
        def fmt(d: str) -> str:
            try:
                # If already in DD/MM/YYYY format, return as is
                if '/' in d:
                    return d
                # Otherwise parse and format
                return datetime.strptime(d, '%Y-%m-%d').strftime('%d/%m/%Y')
            except Exception:
                return d

        # Format hours to 12-hour format
        hour_from_12 = self._format_hour_12(data.get('hour_from'))
        hour_to_12 = self._format_hour_12(data.get('hour_to'))

        # Calculate total time requested
        total_time = self._calculate_time_duration(data.get('hour_from'), data.get('hour_to'))
        
        # Get project name
        project_id = data.get('project_id')
        project_name = f"Project {project_id}" if project_id else "N/A"
        projects = data.get('projects', [])
        if projects:
            for p in projects:
                if str(p.get('value')) == str(project_id):
                    project_name = p.get('label', project_name)
                    break

        msg = (
            "Here are the details for your overtime request:\n\n"
            f"📂 **Category:** {data.get('category_name', 'Overtime')}\n"
            f"📅 **Date:** {fmt(date_dmy)}\n"
            f"⏰ **Hours:** {hour_from_12} → {hour_to_12}\n"
            f"🕒 **Time Requested:** {total_time}\n"
            f"📁 **Project:** {project_name}\n\n"
            "Do you want to submit this request? Reply or click 'yes' to confirm or 'no' to cancel"
        )
        buttons = [
            {'text': 'Yes', 'value': 'overtime_confirm', 'type': 'action'},
            {'text': 'No', 'value': 'overtime_cancel', 'type': 'action'}
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
