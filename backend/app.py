from flask import Flask, request, jsonify, render_template, session, redirect, url_for, send_from_directory, g
from flask_cors import CORS
from typing import Optional
import base64
from datetime import datetime, timezone
try:
    # Production import style when running as package 'backend.app'
    from .services.chatgpt_service import ChatGPTService
    from .services.odoo_service import OdooService
    from .services.employee_service import EmployeeService
    from .services.timeoff_service import TimeOffService
    from .services.halfday_service import HalfDayLeaveService
    from .services.session_manager import SessionManager
    from .services.document_service import DocumentService
    from .services.metrics_service import MetricsService
    from .config.settings import Config
    from .services.intent_service import IntentService
    from .services.overtime_service import OvertimeService
    from .services.remember_me_service import RememberMeService
    from .services.leave_balance_service import LeaveBalanceService
    from .services.title_generator import generate_conversation_title, update_title_if_needed
    from .services.manager_helper import (
        get_team_overview,
        format_team_overview_message,
        build_team_overview_table_widget,
        build_overtime_table_widget,
        build_main_overview_table_widget,
    )
    from .services.log_hours_flow import start_log_hours_flow, is_log_hours_trigger, start_log_hours_for_task, handle_log_hours_step
except Exception:
    # Local import style when running as script from backend/ directory
    from services.chatgpt_service import ChatGPTService
    from services.odoo_service import OdooService
    from services.employee_service import EmployeeService
    from services.timeoff_service import TimeOffService
    from services.halfday_service import HalfDayLeaveService
    from services.session_manager import SessionManager
    from services.document_service import DocumentService
    from services.metrics_service import MetricsService
    from config.settings import Config
    from services.intent_service import IntentService
    from services.overtime_service import OvertimeService
    from services.remember_me_service import RememberMeService
    from services.leave_balance_service import LeaveBalanceService
    from services.title_generator import generate_conversation_title, update_title_if_needed
    from services.manager_helper import (
        get_team_overview,
        format_team_overview_message,
        build_team_overview_table_widget,
        build_overtime_table_widget,
        build_main_overview_table_widget,
    )
    from services.log_hours_flow import start_log_hours_flow, is_log_hours_trigger, start_log_hours_for_task, handle_log_hours_step
import os
import sys
import logging
from datetime import date
import time

# Configure Python logging to output to stdout/stderr (for Railway)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ],
    force=True  # Override any existing configuration
)

# Suppress httpx INFO logs (Supabase client) - only show WARNING and above
logging.getLogger('httpx').setLevel(logging.WARNING)

# Get Flask logger
logger = logging.getLogger('flask')
logger.setLevel(logging.INFO)

# Ensure stdout/stderr are unbuffered for immediate log output
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(line_buffering=True)
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(line_buffering=True)

def debug_log(message: str, category: str = "general"):
    """Conditional debug logging based on configuration"""
    # Always log errors and warnings with flush (critical)
    if "ERROR" in message.upper() or "FAILED" in message.upper() or "FAIL" in message.upper():
        print(f"ERROR: {message}", flush=True)
        logger.error(message)
    elif "WARNING" in message.upper() or "WARN" in message.upper():
        print(f"WARNING: {message}", flush=True)
        logger.warning(message)
    elif category == "odoo_data" and Config.DEBUG_ODOO_DATA:
        print(f"DEBUG: {message}")  # No flush for debug logs (performance)
        logger.debug(message)
    elif category == "bot_logic" and Config.DEBUG_BOT_LOGIC:
        print(f"DEBUG: {message}")  # No flush for debug logs (performance)
        logger.debug(message)
    elif category == "general" and Config.VERBOSE_LOGS:
        print(f"DEBUG: {message}")  # No flush for debug logs (performance)
        logger.debug(message)

def _parse_two_dates_from_text(text: str) -> tuple:
    # Restored to a no-op to avoid aggressive parsing side-effects
    return (None, None)

# Common country aliases for embassy flow normalization
COUNTRY_ALIASES = {
    # United States of America
    'us': 'United States', 'usa': 'United States', 'u s': 'United States', 'u s a': 'United States',
    'america': 'United States', 'american': 'United States', 'united states': 'United States',
    # United Arab Emirates
    'uae': 'United Arab Emirates', 'u a e': 'United Arab Emirates', 'emirates': 'United Arab Emirates', 'emirati': 'United Arab Emirates',
    'united arab emirates': 'United Arab Emirates',
    # Saudi Arabia
    'ksa': 'Saudi Arabia', 'k s a': 'Saudi Arabia', 'saudi': 'Saudi Arabia', 'saudi arabia': 'Saudi Arabia', 'saudia': 'Saudi Arabia',
    # United Kingdom
    'uk': 'United Kingdom', 'u k': 'United Kingdom', 'britain': 'United Kingdom', 'great britain': 'United Kingdom',
    'england': 'United Kingdom', 'gb': 'United Kingdom', 'u k g b': 'United Kingdom', 'united kingdom': 'United Kingdom',
    # Other frequent abbreviations
    'jordan': 'Jordan',
    'ua': 'Ukraine',
    'drc': 'Democratic Republic of the Congo', 'dr congo': 'Democratic Republic of the Congo',
    'south korea': 'South Korea', 's korea': 'South Korea', 'republic of korea': 'South Korea',
    'north korea': 'North Korea', 'n korea': 'North Korea',
}

def _normalize_country_name(name: str) -> str:
    """Normalize common country abbreviations/demonyms to canonical names for embassy letters.

    Uses light string normalization and a curated alias map for popular abbreviations.
    """
    try:
        raw = (name or '').strip().lower()
        if not raw:
            return ''
        # Remove dots and extra spaces to unify forms like "u.s.a"/"u.a.e"/"k.s.a"
        raw_clean = raw.replace('.', ' ').replace(',', ' ')
        raw_clean = ' '.join(raw_clean.split())

        return COUNTRY_ALIASES.get(raw_clean, name.strip())
    except Exception:
        return name or ''

def _detect_country_in_text(text: str, country_names: list) -> str:
    """Detect a country name from free text using alias matching and simple contains.

    Returns canonical country name or empty string.
    """
    try:
        t = (text or '').strip().lower()
        if not t:
            return ''
        t = t.replace('.', ' ').replace(',', ' ')
        t = ' '.join(t.split())
        # Check aliases first (so US/USA/UAE/KSA/UK work)
        for alias, canonical in COUNTRY_ALIASES.items():
            if alias in t:
                return canonical
        # Fallback: check full country names
        for name in country_names:
            if name and name.lower() in t:
                return name
        return ''
    except Exception:
        return ''

def create_app():
    app = Flask(__name__, 
                template_folder='../frontend/templates',
                static_folder='../frontend/static')
    
    # Configure Flask logging to stdout/stderr (for Railway)
    app.logger.setLevel(logging.INFO)
    app.logger.handlers = [logging.StreamHandler(sys.stdout)]
    
    # Enable CORS for all routes
    CORS(app)
    
    # Load configuration
    app.config.from_object(Config)
    
    # Log startup info
    app.logger.info("=" * 50)
    app.logger.info("Nasma Flask Application Starting")
    app.logger.info(f"Environment: {'DEVELOPMENT' if Config.DEBUG else 'PRODUCTION'}")
    app.logger.info(f"Debug flags - ODOO_DATA: {Config.DEBUG_ODOO_DATA}, BOT_LOGIC: {Config.DEBUG_BOT_LOGIC}, VERBOSE: {Config.VERBOSE_LOGS}")
    app.logger.info("=" * 50)
    
    # Initialize services
    chatgpt_service = ChatGPTService()
    odoo_service = OdooService()
    employee_service = EmployeeService(odoo_service)
    timeoff_service = TimeOffService(odoo_service, employee_service)
    halfday_service = HalfDayLeaveService()
    session_manager = SessionManager()
    metrics_service = MetricsService()
    document_service = DocumentService(odoo_service, employee_service)
    document_service.metrics_service = metrics_service
    intent_service = IntentService()
    overtime_service = OvertimeService(odoo_service, employee_service, session_manager, metrics_service=metrics_service)
    remember_me_service = RememberMeService(
        supabase_url=Config.SUPABASE_URL,
        supabase_key=Config.SUPABASE_SERVICE_ROLE,
        table_name=Config.SUPABASE_REMEMBER_ME_TABLE
    )
    
    # Import and initialize reimbursement service (support package/local)
    try:
        from .services.reimbursement_service import ReimbursementService
    except Exception:
        from services.reimbursement_service import ReimbursementService
    reimbursement_service = ReimbursementService(odoo_service, employee_service, metrics_service=metrics_service)
    reimbursement_service.session_manager = session_manager
    
    # Initialize leave balance service
    leave_balance_service = LeaveBalanceService(odoo_service)
    
    # Wire services together
    chatgpt_service.set_services(timeoff_service, session_manager, halfday_service, reimbursement_service, metrics_service)
    chatgpt_service.leave_balance_service = leave_balance_service

    PEOPLE_CULTURE_DENIED = "sorry this flow is restricted to members of the People & Culture Department"

    def get_odoo_session_data():
        """
        Get Odoo session data from Flask session (per-user, thread-safe)

        Returns:
            Dict with session_id, user_id, username, password or None if not authenticated
        """
        if not session.get('authenticated'):
            return None

        return {
            'session_id': session.get('odoo_session_id'),
            'user_id': session.get('user_id'),
            'username': session.get('username'),
            'password': session.get('password')  # May be None if not stored
        }

    def _is_people_culture_member(data) -> bool:
        """Return True if the provided employee data belongs to People & Culture."""
        try:
            if not isinstance(data, dict):
                return False
            dept_name = ''
            dept_details = data.get('department_id_details')
            if isinstance(dept_details, dict):
                dept_name = dept_details.get('name') or ''
            elif isinstance(dept_details, (list, tuple)) and len(dept_details) > 1:
                dept_name = dept_details[1] or ''
            else:
                raw = data.get('department_id')
                if isinstance(raw, (list, tuple)) and len(raw) > 1:
                    dept_name = raw[1] or ''
                elif isinstance(raw, str):
                    dept_name = raw
            return dept_name.strip().lower() == 'people & culture'
        except Exception:
            return False
    
    def _extract_identity(employee: dict = None):
        """Resolve tenant/company identifiers and user id/name from employee context."""
        tenant_id = None
        tenant_name = None
        user_id = None
        user_name = None
        try:
            if isinstance(employee, dict):
                eid = employee.get('id')
                if eid is not None:
                    user_id = str(eid)
                name = employee.get('name')
                if name:
                    user_name = str(name)
                company_details = employee.get('company_id_details')
                if isinstance(company_details, dict):
                    if company_details.get('id') is not None:
                        tenant_id = str(company_details.get('id'))
                    if company_details.get('name'):
                        tenant_name = company_details.get('name')
                else:
                    raw_company = employee.get('company_id')
                    if isinstance(raw_company, (list, tuple)) and raw_company:
                        tenant_id = str(raw_company[0])
                        if len(raw_company) > 1 and raw_company[1]:
                            tenant_name = raw_company[1]
                    elif raw_company:
                        tenant_id = str(raw_company)
        except Exception:
            tenant_id = tenant_id or None
        if not user_id:
            try:
                session_user = session.get('user_id')
                if session_user is not None:
                    user_id = str(session_user)
            except Exception:
                pass
        if not user_name:
            try:
                session_name = session.get('username')
                if session_name:
                    user_name = str(session_name)
            except Exception:
                pass
        return tenant_id, tenant_name, user_id, user_name

    def _log_usage_metric(metric_type: str, thread_id: str, payload: dict, employee: dict = None, skip_if_exists: bool = False):
        """Send usage metrics to Supabase without impacting user flows."""
        if not metrics_service:
            return
        try:
            resolved_thread_id = thread_id or f"{metric_type}:{int(time.time() * 1000)}"
            tenant_id, tenant_name, user_id, user_name = _extract_identity(employee or {})
            metric_payload = dict(payload or {})
            if tenant_name:
                metric_payload.setdefault('context', {})['tenant_name'] = tenant_name
            metrics_service.log_metric(
                metric_type,
                resolved_thread_id,
                user_id=user_id,
                user_name=user_name,
                tenant_id=tenant_id,
                payload=metric_payload,
                skip_if_exists=skip_if_exists
            )
        except Exception:
            pass

    def _log_document_metric(thread_id: str, document_type: str, *, language: str = None, extra: dict = None, employee: dict = None):
        payload = {'document_type': document_type}
        if language:
            payload['language'] = language
        if extra:
            payload.update(extra)
        _log_usage_metric('document', thread_id, payload, employee)

    def _fetch_employee_profile() -> Optional[dict]:
        if not session.get('authenticated'):
            return None
        if not odoo_service.is_authenticated():
            return None
        try:
            session_valid, _ = odoo_service.ensure_active_session()
        except Exception:
            session_valid = False
        if not session_valid:
            return None
        try:
            success, employee = employee_service.get_current_user_employee_data()
            if success:
                return employee
        except Exception:
            pass
        return None

    def _truncate_preview(text: str, limit: int = 180) -> str:
        txt = (text or '').strip()
        if len(txt) <= limit:
            return txt
        return txt[: limit - 3].rstrip() + "..."

    def _log_chat_message_event(thread_id: str, role: str, content: str, employee: dict = None, metadata: dict = None):
        """Record chat messages (user/assistant) into Supabase."""
        if not metrics_service or not thread_id:
            print(f"[DEBUG] _log_chat_message_event skipped: metrics_service={metrics_service is not None}, thread_id={thread_id}")
            return
        text = (content or '').strip()
        if not text:
            print(f"[DEBUG] _log_chat_message_event skipped: empty content")
            return
        tenant_id, tenant_name, user_id, user_name = _extract_identity(employee or {})
        print(f"[DEBUG] _log_chat_message_event: role={role}, user_id={user_id}, thread_id={thread_id}, content_length={len(text)}")

        # IMPORTANT: Upsert thread FIRST before storing message
        # The chat_messages table has a foreign key constraint on thread_id
        preview = _truncate_preview(text)
        thread_kwargs = {
            "user_id": user_id,
            "tenant_id": tenant_id,
            "last_message_preview": preview,
            "last_sender": role,
        }
        if role == 'user':
            # Generate a descriptive title for the conversation
            existing_thread = metrics_service.fetch_thread(thread_id)
            existing_title = existing_thread.get('title') if existing_thread else None
            thread_kwargs["title"] = update_title_if_needed(existing_title, text)
        thread_kwargs["last_message_at"] = datetime.now(timezone.utc).isoformat()
        print(f"[DEBUG] Upserting thread with title: {thread_kwargs.get('title', 'N/A')}")
        metrics_service.upsert_thread(thread_id, **thread_kwargs)

        # Now store the message (thread must exist first due to FK constraint)
        meta = dict(metadata or {})
        if tenant_name:
            meta.setdefault('tenant_name', tenant_name)
        if user_name:
            meta.setdefault('user_name', user_name)
        logged = metrics_service.store_message(thread_id, role=role, content=text, metadata=meta)
        if not logged:
            last_err = getattr(metrics_service, "last_error", None)
            print(f"[MetricsService] Chat message store failed ({role}): {last_err}")
    
    @app.before_request
    def _rehydrate_odoo_service_from_session():
        """Rehydrate OdooService after any Flask auto-reload or process restart.

        If the Flask session indicates the user is authenticated and we have a stored
        Odoo session id, ensure the in-memory service carries those values so
        downstream handlers can call Odoo without forcing a fresh login.
        """
        try:
            if not session.get('authenticated'):
                if odoo_service.is_authenticated():
                    odoo_service.logout()
                return

            sid = session.get('odoo_session_id')
            uid = session.get('user_id')
            uname = session.get('username')
            if not sid or not uid:
                return

            if (
                odoo_service.session_id != sid
                or odoo_service.user_id != uid
                or odoo_service.username != uname
            ):
                odoo_service.session_id = sid
                odoo_service.user_id = uid
                odoo_service.username = uname

            # no password stored for security; best-effort activity timestamp scoped to this session
            odoo_service.last_activity = time.time()
        except Exception:
            # Best-effort only; never block requests here
            pass
    
    @app.route('/')
    def index():
        # Check if user is authenticated
        debug_log(f"Session data: {dict(session)}", "bot_logic")
        if not session.get('authenticated'):
            debug_log("User not authenticated, redirecting to login", "bot_logic")
            return redirect(url_for('login'))
        
        # Verify Odoo authentication is still valid
        if not odoo_service.is_authenticated():
            debug_log("Flask session exists but Odoo service not authenticated, clearing session and redirecting to login", "bot_logic")
            session.clear()
            odoo_service.logout()
            return redirect(url_for('login'))
        else:
            # Test if the Odoo session is still valid
            debug_log("Testing Odoo session validity...", "bot_logic")
            session_valid, session_message = odoo_service.test_session_validity()
            if not session_valid:
                debug_log(f"Odoo session is invalid: {session_message}, clearing session and redirecting to login", "bot_logic")
                session.clear()
                odoo_service.logout()
                return redirect(url_for('login'))
            else:
                debug_log("Odoo session is valid", "bot_logic")
        
        debug_log("User authenticated and Odoo service connected, showing chat interface", "bot_logic")
        # Determine brand and manager status
        brand_name = 'NasmaPL'
        is_manager = False
        try:
            is_manager = employee_service.is_current_user_manager()
            if is_manager:
                brand_name = 'NasmaManager'
        except Exception:
            pass
        return render_template('chat_smooth.html', brand_name=brand_name, is_manager=is_manager)
    
    @app.route('/chat')
    def chat_page():
        # Check if user is authenticated
        debug_log(f"Chat route - Session data: {dict(session)}", "bot_logic")
        if not session.get('authenticated'):
            debug_log("User not authenticated, redirecting to login", "bot_logic")
            return redirect(url_for('login'))
        
        # Verify Odoo authentication is still valid
        if not odoo_service.is_authenticated():
            debug_log("Flask session exists but Odoo service not authenticated, clearing session and redirecting to login", "bot_logic")
            session.clear()
            odoo_service.logout()
            return redirect(url_for('login'))
        else:
            # Test if the Odoo session is still valid
            debug_log("Testing Odoo session validity...", "bot_logic")
            session_valid, session_message = odoo_service.test_session_validity()
            if not session_valid:
                debug_log(f"Odoo session is invalid: {session_message}, clearing session and redirecting to login", "bot_logic")
                session.clear()
                odoo_service.logout()
                return redirect(url_for('login'))
            else:
                debug_log("Odoo session is valid", "bot_logic")
        
        debug_log("User authenticated and Odoo service connected, showing conversation chat state", "bot_logic")
        # Determine brand and manager status
        brand_name = 'NasmaPL'
        is_manager = False
        try:
            is_manager = employee_service.is_current_user_manager()
            if is_manager:
                brand_name = 'NasmaManager'
        except Exception:
            pass
        return render_template('chat_conversation.html', brand_name=brand_name, is_manager=is_manager)
    
    @app.route('/login')
    def login():
        # If already authenticated and Odoo service is connected, redirect to main page
        debug_log(f"Login route - Session data: {dict(session)}", "bot_logic")
        if session.get('authenticated') and odoo_service.is_authenticated():
            debug_log("User already authenticated and Odoo service connected, redirecting to index", "bot_logic")
            return redirect(url_for('index'))
        elif session.get('authenticated') and not odoo_service.is_authenticated():
            debug_log("Flask session exists but Odoo service not authenticated, clearing session", "bot_logic")
            session.clear()
            odoo_service.logout()
        debug_log("User not authenticated, showing login page", "bot_logic")
        return render_template('login.html')
    
    @app.route('/api/auth/login', methods=['POST'])
    def auth_login():
        try:
            data = request.get_json()
            username = data.get('username', '')
            password = data.get('password', '')
            remember_me = data.get('remember_me', False)
            device_fingerprint = data.get('device_fingerprint', '')

            if not username or not password:
                return jsonify({
                    'success': False,
                    'message': 'Username and password are required'
                }), 400

            # Authenticate with Odoo (stateless - returns session data)
            success, message, session_data = odoo_service.authenticate(username, password)

            if success and session_data:
                # Store authentication in Flask session (per-user, isolated)
                # Only set permanent session if remember_me is checked
                session.permanent = remember_me  # Permanent session only with remember_me
                session['authenticated'] = True
                session['username'] = username
                session['user_id'] = session_data['user_id']
                # Persist Odoo web session id for rehydration across reloads
                session['odoo_session_id'] = session_data['session_id']
                # Store password encrypted for session renewal (only if remember_me)
                if remember_me:
                    session['password'] = password  # Flask session is encrypted by default

                debug_log(f"Authentication successful for {username} (permanent session: {remember_me})", "bot_logic")
                debug_log(f"Session stored with session_id: {session_data['session_id'][:20]}...", "bot_logic")

                response_data = {
                    'success': True,
                    'message': message,
                    'user_info': {
                        'user_id': session_data['user_id'],
                        'username': username,
                        'database': Config.ODOO_DB,
                        'server_url': Config.ODOO_URL
                    }
                }

                # Handle remember me functionality
                if remember_me and device_fingerprint:
                    try:
                        token = remember_me_service.create_token(
                            username=username,
                            password=password,
                            device_fingerprint=device_fingerprint
                        )
                        response_data['remember_me_token'] = token
                        debug_log(f"Remember me token created for {username} on device {device_fingerprint[:8]}...", "bot_logic")
                    except Exception as e:
                        debug_log(f"Failed to create remember me token: {str(e)}", "bot_logic")

                return jsonify(response_data)
            else:
                return jsonify({
                    'success': False,
                    'message': message
                }), 401

        except Exception as e:
            return jsonify({
                'success': False,
                'message': f'Authentication error: {str(e)}'
            }), 500
    
    @app.route('/api/auth/verify-remember-me', methods=['POST'])
    def verify_remember_me():
        """Verify remember me token and auto-login if valid"""
        failure_reason = None
        step = "initialization"
        
        try:
            step = "parsing_request"
            data = request.get_json()
            token = data.get('token', '')
            device_fingerprint = data.get('device_fingerprint', '')
            
            debug_log(f"[STEP: {step}] Received auto-login request (token length: {len(token)}, fingerprint length: {len(device_fingerprint)})", "bot_logic")

            if not token or not device_fingerprint:
                failure_reason = "missing_token_or_fingerprint"
                debug_log(f"FAILED [{failure_reason}]: Missing token or device_fingerprint", "bot_logic")
                return jsonify({
                    'success': False,
                    'message': 'Token and device fingerprint are required',
                    'failure_reason': failure_reason
                }), 400

            step = "token_verification"
            # Verify token and get credentials
            credentials = remember_me_service.verify_token(token, device_fingerprint)

            if not credentials:
                failure_reason = "token_verification_failed"
                debug_log(f"FAILED [{failure_reason}]: Token verification returned None (invalid token or device mismatch)", "bot_logic")
                return jsonify({
                    'success': False,
                    'message': 'Invalid or expired token',
                    'failure_reason': failure_reason
                }), 401

            username, password, token_failure_reason = credentials
            if token_failure_reason:
                failure_reason = f"token_error_{token_failure_reason}"
                debug_log(f"FAILED [{failure_reason}]: {token_failure_reason}", "bot_logic")
                return jsonify({
                    'success': False,
                    'message': 'Token verification failed',
                    'failure_reason': failure_reason
                }), 401

            step = "odoo_authentication"
            debug_log(f"[STEP: {step}] Token verified for {username}, attempting Odoo authentication", "bot_logic")

            # Authenticate with Odoo (stateless - returns session data)
            success, message, session_data = odoo_service.authenticate(username, password)

            if not success or not session_data:
                failure_reason = "odoo_authentication_failed"
                debug_log(f"FAILED [{failure_reason}]: Odoo authentication failed for {username}. Message: {message}", "bot_logic")
                # Token is valid but Odoo authentication failed - clear the token as it may be stale
                try:
                    remember_me_service.remove_token(username, device_fingerprint)
                    debug_log(f"Removed stale remember me token for {username} after Odoo auth failure", "bot_logic")
                except Exception as e:
                    debug_log(f"Failed to remove stale token: {str(e)}", "bot_logic")
                
                return jsonify({
                    'success': False,
                    'message': 'Authentication failed. Please log in again.',
                    'failure_reason': failure_reason,
                    'odoo_error': message
                }), 401

            step = "session_setup"
            debug_log(f"[STEP: {step}] Odoo authentication successful, setting up Flask session", "bot_logic")

            # Store authentication in Flask session (per-user, isolated)
            try:
                session.permanent = True
                session['authenticated'] = True
                session['username'] = username
                session['user_id'] = session_data['user_id']
                session['odoo_session_id'] = session_data['session_id']
                session['password'] = password  # For session renewal
                debug_log(f"Flask session configured successfully. Session ID: {session.get('odoo_session_id', 'N/A')[:20]}...", "bot_logic")
            except Exception as e:
                failure_reason = "session_setup_failed"
                debug_log(f"FAILED [{failure_reason}]: Error setting up Flask session: {str(e)}", "bot_logic")
                return jsonify({
                    'success': False,
                    'message': 'Session setup failed',
                    'failure_reason': failure_reason
                }), 500

            step = "success"
            debug_log(f"SUCCESS [{step}]: Auto-login completed successfully for {username} via remember me token", "bot_logic")

            return jsonify({
                'success': True,
                'message': 'Auto-login successful',
                'user_info': {
                    'user_id': session_data['user_id'],
                    'username': username,
                    'database': Config.ODOO_DB,
                    'server_url': Config.ODOO_URL
                }
            })

        except Exception as e:
            failure_reason = f"exception_at_{step}"
            debug_log(f"FAILED [{failure_reason}]: Exception occurred during auto-login at step '{step}': {str(e)}", "bot_logic")
            import traceback
            debug_log(f"Traceback: {traceback.format_exc()}", "bot_logic")
            return jsonify({
                'success': False,
                'message': 'Verification error',
                'failure_reason': failure_reason,
                'error': str(e)
            }), 500

    @app.route('/api/auth/logout', methods=['POST'])
    def auth_logout():
        try:
            # Get device fingerprint to clear remember me token
            # Handle both JSON and non-JSON requests
            device_fingerprint = ''
            try:
                data = request.get_json(silent=True) or {}
                device_fingerprint = data.get('device_fingerprint', '')
            except Exception:
                # If JSON parsing fails, that's okay - logout should still work
                pass

            username = session.get('username', '')

            # Clear remember me token if device fingerprint provided
            if username and device_fingerprint:
                try:
                    remember_me_service.remove_token(username, device_fingerprint)
                    debug_log(f"Removed remember me token for {username} on device {device_fingerprint[:8]}...", "bot_logic")
                except Exception as e:
                    debug_log(f"Failed to remove remember me token: {str(e)}", "bot_logic")

            # Clear session
            # Clear session data
            session.pop('authenticated', None)
            session.pop('username', None)
            session.pop('user_id', None)
            session.pop('odoo_session_id', None)
            # Note: No password to clear since we don't store it anymore
            odoo_service.logout()

            return jsonify({
                'success': True,
                'message': 'Logged out successfully'
            })

        except Exception as e:
            debug_log(f"Logout error: {str(e)}", "bot_logic")
            return jsonify({
                'success': False,
                'message': f'Logout error: {str(e)}'
            }), 500
    
    @app.route('/api/auth/status', methods=['GET'])
    def auth_status():
        try:
            if session.get('authenticated') and odoo_service.is_authenticated():
                return jsonify({
                    'authenticated': True,
                    'user_info': odoo_service.get_user_info()
                })
            else:
                return jsonify({
                    'authenticated': False,
                    'message': 'Not authenticated'
                })
                
        except Exception as e:
            return jsonify({
                'authenticated': False,
                'message': f'Status check error: {str(e)}'
            }), 500
    
    @app.route('/api/odoo/employee/current', methods=['GET'])
    def get_current_employee():
        """Get current user's employee data"""
        try:
            if not session.get('authenticated'):
                return jsonify({'error': 'Authentication required'}), 401
            
            success, data = employee_service.get_current_user_employee_data()
            
            if success:
                return jsonify({
                    'success': True,
                    'data': data
                })
            else:
                return jsonify({
                    'success': False,
                    'message': data
                }), 500
                
        except Exception as e:
            return jsonify({
                'success': False,
                'message': f'Error retrieving employee data: {str(e)}'
            }), 500
    
    @app.route('/api/odoo/employee/<int:employee_id>', methods=['GET'])
    def get_employee_by_id(employee_id):
        """Get specific employee data by ID"""
        try:
            if not session.get('authenticated'):
                return jsonify({'error': 'Authentication required'}), 401
            
            success, data = employee_service.get_employee_by_id(employee_id)
            
            if success:
                return jsonify({
                    'success': True,
                    'data': data
                })
            else:
                return jsonify({
                    'success': False,
                    'message': data
                }), 500
                
        except Exception as e:
            return jsonify({
                'success': False,
                'message': f'Error retrieving employee data: {str(e)}'
            }), 500
    
    @app.route('/api/odoo/employees/search', methods=['POST'])
    def search_employees():
        """Search employees with filters"""
        try:
            if not session.get('authenticated'):
                return jsonify({'error': 'Authentication required'}), 401
            
            data = request.get_json()
            search_term = data.get('search_term', '')
            filters = data.get('filters', {})
            
            success, result = employee_service.search_employees(search_term, filters)
            
            if success:
                return jsonify({
                    'success': True,
                    'data': result,
                    'count': len(result)
                })
            else:
                return jsonify({
                    'success': False,
                    'message': result
                }), 500
                
        except Exception as e:
            return jsonify({
                'success': False,
                'message': f'Error searching employees: {str(e)}'
            }), 500
    
    @app.route('/api/odoo/cache/clear', methods=['POST'])
    def clear_employee_cache():
        """Clear employee data cache"""
        try:
            if not session.get('authenticated'):
                return jsonify({'error': 'Authentication required'}), 401
            
            employee_service.clear_cache()
            
            return jsonify({
                'success': True,
                'message': 'Cache cleared successfully'
            })
            
        except Exception as e:
            return jsonify({
                'success': False,
                'message': f'Error clearing cache: {str(e)}'
            }), 500
    
    @app.route('/api/odoo/cache/stats', methods=['GET'])
    def get_cache_stats():
        """Get cache statistics"""
        try:
            if not session.get('authenticated'):
                return jsonify({'error': 'Authentication required'}), 401
            
            stats = employee_service.get_cache_stats()
            
            return jsonify({
                'success': True,
                'stats': stats
            })
            
        except Exception as e:
            return jsonify({
                'success': False,
                'message': f'Error getting cache stats: {str(e)}'
            }), 500
    
    @app.route('/api/odoo/debug/user-info', methods=['GET'])
    def debug_user_info():
        """Debug endpoint to check user authentication and employee data"""
        try:
            if not session.get('authenticated'):
                return jsonify({'error': 'Authentication required'}), 401
            
            # Get Odoo user info
            odoo_user_info = odoo_service.get_user_info()
            
            # Try to get employee data
            employee_success, employee_data = employee_service.get_current_user_employee_data()
            
            return jsonify({
                'success': True,
                'odoo_user_info': odoo_user_info,
                'employee_data_success': employee_success,
                'employee_data': employee_data if employee_success else None,
                'employee_error': employee_data if not employee_success else None
            })
            
        except Exception as e:
            return jsonify({
                'success': False,
                'message': f'Debug error: {str(e)}'
            }), 500
    
    @app.route('/api/odoo/test-employee', methods=['GET'])
    def test_employee_data():
        """Test endpoint to fetch and display employee data"""
        try:
            debug_log(f"Test employee endpoint called - Session: {dict(session)}", "odoo_data")
            debug_log(f"Request headers: {dict(request.headers)}", "odoo_data")
            debug_log(f"Request remote address: {request.remote_addr}", "odoo_data")
            if not session.get('authenticated'):
                debug_log("Test employee endpoint - User not authenticated, returning 401", "bot_logic")
                return jsonify({'error': 'Authentication required'}), 401
            
            # Check if Odoo service is authenticated
            if not odoo_service.is_authenticated():
                debug_log("Odoo service not authenticated for test", "bot_logic")
                return jsonify({'error': 'Odoo service not authenticated'}), 401
            
            # Try to get employee data
            debug_log("Attempting to fetch employee data...", "odoo_data")
            employee_success, employee_data = employee_service.get_current_user_employee_data()
            
            if employee_success:
                return jsonify({
                    'success': True,
                    'message': 'Employee data fetched successfully',
                    'data': employee_data
                })
            else:
                return jsonify({
                    'success': False,
                    'message': f'Failed to fetch employee data: {employee_data}'
                }), 500
            
        except Exception as e:
            debug_log(f"Test error: {e}", "general")
            import traceback
            traceback.print_exc()
            return jsonify({
                'success': False,
                'message': f'Test error: {str(e)}'
            }), 500
    
    @app.route('/api/chat', methods=['POST'])
    def chat():
        try:
            # Check authentication
            if not session.get('authenticated'):
                return jsonify({'error': 'Authentication required'}), 401
            
            data = request.get_json()
            message = data.get('message', '')
            thread_id = data.get('thread_id')
            
            if not message:
                return jsonify({'error': 'Message is required'}), 400
            
            # Initialize employee data as None
            employee_data = None
            employee_success = False
            
            # Only try to fetch employee data if user is properly authenticated
            if session.get('authenticated') and odoo_service.is_authenticated():
                debug_log("User is authenticated, ensuring active Odoo session...", "bot_logic")

                # Ensure session is active (with automatic renewal if needed)
                session_valid, session_message = odoo_service.ensure_active_session()
                if not session_valid:
                    debug_log(f"Could not ensure active Odoo session: {session_message}", "bot_logic")
                    employee_data = None
                else:
                    # Now try to fetch employee data
                    debug_log("Odoo session is active, fetching employee data...", "bot_logic")
                    employee_success, employee_data = employee_service.get_current_user_employee_data()

                    if not employee_success:
                        # If we can't get employee data, still proceed but without context
                        print(f"ERROR: Could not fetch employee data: {employee_data}")
                        employee_data = None
                    else:
                        print(f"SUCCESS: Fetched employee data for user: {employee_data.get('name', 'Unknown')}")
                        debug_log(f"Employee data keys: {list(employee_data.keys()) if employee_data else 'None'}", "odoo_data")
            else:
                debug_log("User not authenticated - skipping employee data fetch", "bot_logic")
            
            # Normalize message and handle commands/intents
            normalized_msg = (message or '').strip().lower()

            if thread_id:
                _log_chat_message_event(
                    thread_id,
                    'user',
                    message,
                    employee_data,
                    {
                        'source': 'user_input',
                        'normalized': normalized_msg
                    }
                )

            # Global cancel intent handling (before any validation/parsing or ChatGPT call)
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

            if _is_cancel_intent(normalized_msg):
                # Clear any known flows/sessions
                try:
                    session.pop('embassy_letter_flow', None)
                except Exception:
                    pass
                try:
                    # Cancel and clear multi-step flows keyed by thread_id
                    if thread_id:
                        session_manager.cancel_session(thread_id, 'User cancel (global)')
                        session_manager.clear_session(thread_id)
                except Exception:
                    pass
                assistant_text = 'request cancelled, can i help you with anything else'
                _log_chat_message_event(
                    thread_id,
                    'assistant',
                    assistant_text,
                    employee_data,
                    {'source': 'system', 'event': 'user_cancel'}
                )
                return jsonify({
                    'response': assistant_text,
                    'status': 'success',
                    'has_employee_context': employee_data is not None,
                    'thread_id': thread_id
                })

            # Leave balance query: handle before time-off flow
            def _is_leave_balance_query(text: str) -> bool:
                """Detect if user is asking about remaining leave balance"""
                import re
                text_lower = text.lower()
                balance_patterns = [
                    # Patterns with "what" + balance/leave
                    r'(?:what|what\'s|whats).{0,15}(?:is|my|me)?.{0,10}(?:remaining|balance|left|available).{0,20}(?:annual|sick|leave|vacation)',
                    r'(?:what|what\'s|whats).{0,15}(?:is|my|me)?.{0,10}(?:leave|annual|sick).{0,10}(?:balance|remaining|left)',
                    # Patterns with balance/remaining + leave types
                    r'(?:remaining|balance|left|available).{0,20}(?:annual|sick|leave|vacation)',
                    # Patterns with "my/me" + balance
                    r'(?:my|me).{0,10}(?:remaining|balance|left|available).{0,20}(?:annual|sick|leave|vacation)',
                    r'(?:my|me).{0,10}(?:leave|annual|sick).{0,10}(?:balance|remaining|left)',
                    # Simple patterns
                    r'leave.{0,10}balance',
                    r'balance.{0,10}leave',
                    # Patterns with "how many days"
                    r'(?:how many).{0,20}(?:days|hours).{0,20}(?:remaining|left|available).{0,20}(?:annual|sick|leave)',
                    # Patterns with action verbs
                    r'(?:show|check|tell|see|how much).{0,20}(?:remaining|balance|left|available).{0,20}(?:annual|sick|leave|vacation)',
                    r'(?:show|check|tell|see).{0,20}(?:my|me).{0,10}(?:leave|annual|sick).{0,10}(?:balance|remaining)',
                ]
                for pattern in balance_patterns:
                    if re.search(pattern, text_lower):
                        return True
                return False
            
            if _is_leave_balance_query(normalized_msg):
                if not employee_data or not employee_data.get('id'):
                    assistant_text = 'Unable to retrieve your leave balance. Please ensure you are logged in properly.'
                    _log_chat_message_event(
                        thread_id,
                        'assistant',
                        assistant_text,
                        employee_data,
                        {'source': 'leave_balance', 'error': 'no_employee_data'}
                    )
                    return jsonify({
                        'response': assistant_text,
                        'status': 'error',
                        'has_employee_context': False,
                        'thread_id': thread_id
                    })
                
                try:
                    # Extract leave type from message if mentioned
                    leave_type_name = None
                    message_lower = normalized_msg.lower()
                    if 'annual' in message_lower or 'vacation' in message_lower:
                        leave_type_name = 'Annual Leave'
                    elif 'sick' in message_lower:
                        leave_type_name = 'Sick Leave'
                    
                    # Calculate remaining leave
                    remaining = leave_balance_service.calculate_remaining_leave(
                        employee_data.get('id'),
                        leave_type_name
                    )
                    
                    if remaining:
                        # Format message with each leave type on a separate line
                        lines = []
                        for leave_type, days in sorted(remaining.items()):
                            # Format days with 1 decimal place, but show as integer if whole number
                            if days == int(days):
                                days_str = str(int(days))
                            else:
                                days_str = f"{days:.1f}"
                            lines.append(f"\tAvailable {leave_type}: {days_str} days")
                        
                        formatted_message = "\n".join(lines)
                        assistant_text = f"Here's your leave balance:\n\n{formatted_message}"
                    else:
                        assistant_text = "I couldn't retrieve your leave balance at the moment. Please try again later."
                    
                    _log_chat_message_event(
                        thread_id,
                        'assistant',
                        assistant_text,
                        employee_data,
                        {'source': 'leave_balance'}
                    )
                    
                    return jsonify({
                        'response': assistant_text,
                        'status': 'success',
                        'has_employee_context': True,
                        'thread_id': thread_id
                    })
                except Exception as e:
                    debug_log(f"Error handling leave balance query: {str(e)}", "bot_logic")
                    assistant_text = "I encountered an error while retrieving your leave balance. Please try again later."
                    _log_chat_message_event(
                        thread_id,
                        'assistant',
                        assistant_text,
                        employee_data,
                        {'source': 'leave_balance', 'error': str(e)}
                    )
                    return jsonify({
                        'response': assistant_text,
                        'status': 'error',
                    'has_employee_context': employee_data is not None,
                    'thread_id': thread_id
                })

            # Log hours flow: handle task viewing for normal users
            try:
                if is_log_hours_trigger(message):
                    if not employee_data:
                        assistant_text = 'Unable to retrieve your tasks. Please ensure you are logged in properly.'
                        _log_chat_message_event(
                            thread_id,
                            'assistant',
                            assistant_text,
                            employee_data,
                            {'source': 'log_hours', 'error': 'no_employee_data'}
                        )
                        return jsonify({
                            'response': assistant_text,
                            'status': 'error',
                            'has_employee_context': False,
                            'thread_id': thread_id
                        })

                    log_hours_resp = start_log_hours_flow(odoo_service, employee_data)
                    assistant_text = log_hours_resp.get('message', '')

                    if assistant_text:
                        _log_chat_message_event(
                            thread_id,
                            'assistant',
                            assistant_text,
                            employee_data,
                            {'source': 'log_hours'}
                        )

                    # Log usage metric
                    _log_usage_metric(
                        'log_hours',
                        thread_id,
                        {
                            'user_message': message[:200] if message else '',
                            'task_count': len(log_hours_resp.get('tasks', [])),
                            'success': log_hours_resp.get('success', False)
                        },
                        employee_data
                    )

                    response_data = {
                        'response': assistant_text,
                        'status': 'success' if log_hours_resp.get('success') else 'error',
                        'has_employee_context': employee_data is not None,
                        'thread_id': thread_id
                    }
                    # Include widgets if present
                    if 'widgets' in log_hours_resp:
                        response_data['widgets'] = log_hours_resp['widgets']
                    return jsonify(response_data)
            except Exception as e:
                debug_log(f"Error in log hours flow: {str(e)}", "bot_logic")
                pass

            # Log hours flow: handle button clicks and flow steps
            try:
                # Check for log hours button click format: log_hours:subtask_id:date:task_name
                if message.startswith('log_hours:'):
                    parts = message.split(':', 3)
                    if len(parts) >= 4:
                        subtask_id = int(parts[1])
                        task_date = parts[2]
                        task_name = parts[3] if len(parts) > 3 else 'Task'
                        
                        log_hours_resp = start_log_hours_for_task(odoo_service, employee_data, subtask_id, task_date, task_name)
                        
                        # Store flow context in session
                        session['log_hours_flow'] = {
                            'context': log_hours_resp.get('widgets', {}).get('log_hours_flow', {}),
                            'started': True
                        }
                        
                        assistant_text = log_hours_resp.get('message', '')
                        if assistant_text:
                            _log_chat_message_event(
                                thread_id,
                                'assistant',
                                assistant_text,
                                employee_data,
                                {'source': 'log_hours'}
                            )
                        
                        response_data = {
                            'response': assistant_text,
                            'status': 'success' if log_hours_resp.get('success') else 'error',
                            'has_employee_context': employee_data is not None,
                            'thread_id': thread_id
                        }
                        if 'widgets' in log_hours_resp:
                            response_data['widgets'] = log_hours_resp['widgets']
                        if 'buttons' in log_hours_resp:
                            response_data['buttons'] = log_hours_resp['buttons']
                        return jsonify(response_data)
                
                # Check for log hours flow step inputs
                log_hours_session = session.get('log_hours_flow', {})
                if log_hours_session.get('started'):
                    context = log_hours_session.get('context', {})
                    current_step = context.get('step', '')
                    
                    # Handle dropdown selection format: context_key=value
                    if '=' in message:
                        parts = message.split('=', 1)
                        context_key = parts[0]
                        value = parts[1] if len(parts) > 1 else ''
                        
                        if context_key == 'log_hours_task_activity':
                            step_resp = handle_log_hours_step(odoo_service, employee_data, 'task_activity', context, value)
                        elif context_key == 'log_hours_hours':
                            step_resp = handle_log_hours_step(odoo_service, employee_data, 'hours', context, value)
                        elif context_key == 'log_hours_description':
                            step_resp = handle_log_hours_step(odoo_service, employee_data, 'description', context, value)
                        else:
                            step_resp = None
                    # Handle button actions
                    elif message.startswith('log_hours_task_activity:'):
                        activity_id = message.split(':', 1)[1] if ':' in message else message.replace('log_hours_task_activity:', '')
                        step_resp = handle_log_hours_step(odoo_service, employee_data, 'task_activity', context, activity_id)
                    elif message.startswith('log_hours_hours:'):
                        hours = message.split(':', 1)[1] if ':' in message else message.replace('log_hours_hours:', '')
                        step_resp = handle_log_hours_step(odoo_service, employee_data, 'hours', context, hours)
                    elif message.startswith('log_hours_description:'):
                        desc = message.split(':', 1)[1] if ':' in message else message.replace('log_hours_description:', '')
                        step_resp = handle_log_hours_step(odoo_service, employee_data, 'description', context, desc)
                    elif message == 'log_hours_skip_description':
                        step_resp = handle_log_hours_step(odoo_service, employee_data, 'description', context, '')
                    elif message == 'log_hours_confirm':
                        step_resp = handle_log_hours_step(odoo_service, employee_data, 'confirmation', context, 'log_hours_confirm')
                        # Clear session after confirmation
                        session.pop('log_hours_flow', None)
                    elif message == 'log_hours_cancel':
                        session.pop('log_hours_flow', None)
                        step_resp = {
                            'message': 'Log hours cancelled.',
                            'success': True
                        }
                    else:
                        # Check if this is a direct input for the current step (chat input)
                        # Safeguard: if message looks like hours (contains hour-related words),
                        # and we have task_activity_id in context, treat it as hours input
                        looks_like_hours = False
                        if message:
                            import re
                            msg_lower = message.lower().strip()
                            # Check for hour-related keywords
                            has_hour_keywords = bool(re.search(r'\b(hours?|hrs?|h|minutes?|mins?|m)\b', msg_lower))
                            # Check for numbers (digits)
                            has_number = bool(re.search(r'\d+', message))
                            # Check for number words (zero, one, two, ..., ten, etc.)
                            number_words = ['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 
                                         'eight', 'nine', 'ten', 'eleven', 'twelve', 'thirteen', 'fourteen',
                                         'fifteen', 'sixteen', 'seventeen', 'eighteen', 'nineteen', 'twenty',
                                         'thirty', 'forty', 'fifty', 'sixty']
                            has_word_number = any(word in msg_lower for word in number_words)
                            # Check for "half" (as in "half an hour")
                            has_half = 'half' in msg_lower
                            
                            # It looks like hours if it has hour keywords AND (numbers OR word numbers OR half)
                            looks_like_hours = has_hour_keywords and (has_number or has_word_number or has_half)
                        
                        # Prevent going back to previous steps - check if we've already completed them
                        has_activity_id = bool(context.get('task_activity_id'))
                        has_hours = bool(context.get('hours'))
                        has_description = 'description' in context  # Even if empty string, it's been set
                        
                        # If it looks like hours and we have activity_id, prioritize hours step
                        # This handles cases where session step wasn't updated correctly after activity selection
                        if looks_like_hours and has_activity_id:
                            # If we have task_activity_id but step is still task_activity (or empty),
                            # it means activity was selected but session wasn't updated - treat as hours
                            if current_step in ['task_activity', ''] or not current_step:
                                step_resp = handle_log_hours_step(odoo_service, employee_data, 'hours', context, message)
                            else:
                                # Step is already correct, use current step
                                step_resp = handle_log_hours_step(odoo_service, employee_data, current_step, context, message)
                        # Prevent going back: if we have activity_id, don't allow task_activity step
                        elif current_step == 'task_activity' and has_activity_id:
                            # Activity already selected, treat as hours if it looks like hours, otherwise show error
                            if looks_like_hours:
                                step_resp = handle_log_hours_step(odoo_service, employee_data, 'hours', context, message)
                            else:
                                step_resp = {
                                    'message': 'Activity has already been selected. Please enter the hours spent (e.g., "five hours", "5.5").',
                                    'success': False,
                                    'widgets': {
                                        'log_hours_flow': {
                                            'step': 'hours',
                                            **context
                                        }
                                    }
                                }
                        # Prevent going back: if we have hours, don't allow hours step again
                        elif current_step == 'hours' and has_hours:
                            # Hours already entered, move to description
                            step_resp = {
                                'message': 'Hours have already been entered. Please add a description or click Skip.',
                                'success': False,
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
                        elif current_step == 'task_activity':
                            step_resp = handle_log_hours_step(odoo_service, employee_data, 'task_activity', context, message)
                        elif current_step == 'hours':
                            step_resp = handle_log_hours_step(odoo_service, employee_data, 'hours', context, message)
                        elif current_step == 'description':
                            step_resp = handle_log_hours_step(odoo_service, employee_data, 'description', context, message)
                        elif current_step == 'confirmation':
                            step_resp = handle_log_hours_step(odoo_service, employee_data, 'confirmation', context, message)
                            # Clear session after confirmation or cancellation
                            if step_resp and step_resp.get('success'):
                                if message.lower() in ['yes', 'confirm', 'y']:
                                    session.pop('log_hours_flow', None)
                                elif message.lower() in ['no', 'cancel', 'n']:
                                    session.pop('log_hours_flow', None)
                        else:
                            step_resp = None
                    
                    if step_resp:
                        # Update session context - always update if we have widgets with log_hours_flow
                        # OR if we have a successful response (to preserve context even if no widgets)
                        if 'widgets' in step_resp and 'log_hours_flow' in step_resp['widgets']:
                            session['log_hours_flow'] = {
                                'context': step_resp['widgets']['log_hours_flow'],
                                'started': True
                            }
                        elif step_resp.get('success'):
                            # Even if no widgets, preserve context for next step
                            # Only update if we're not clearing the session
                            if 'timesheet_id' not in step_resp:
                                # Update context with current step from existing context
                                existing_context = session.get('log_hours_flow', {}).get('context', {})
                                if existing_context:
                                    session['log_hours_flow'] = {
                                        'context': existing_context,
                                        'started': True
                                    }
                        # Clear session if timesheet entry was successfully created
                        if step_resp.get('success') and 'timesheet_id' in step_resp:
                            session.pop('log_hours_flow', None)
                        
                        assistant_text = step_resp.get('message', '')
                        if assistant_text:
                            _log_chat_message_event(
                                thread_id,
                                'assistant',
                                assistant_text,
                                employee_data,
                                {'source': 'log_hours'}
                            )
                        
                        response_data = {
                            'response': assistant_text,
                            'status': 'success' if step_resp.get('success') else 'error',
                            'has_employee_context': employee_data is not None,
                            'thread_id': thread_id
                        }
                        if 'widgets' in step_resp:
                            response_data['widgets'] = step_resp['widgets']
                        if 'buttons' in step_resp:
                            response_data['buttons'] = step_resp['buttons']
                        return jsonify(response_data)
                        
            except Exception as e:
                debug_log(f"Error in log hours flow step: {str(e)}", "bot_logic")
                pass

            # Overtime flow: handle before document intents
            try:
                ot_resp = overtime_service.handle_flow(message, thread_id, employee_data or {})
                if ot_resp:
                    resp_thread = ot_resp.get('thread_id') or thread_id
                    assistant_text = ot_resp.get('message', '')
                    if assistant_text:
                        _log_chat_message_event(
                            resp_thread,
                            'assistant',
                            assistant_text,
                            employee_data,
                            {'source': 'overtime'}
                        )
                    # Log overtime metric
                    _log_usage_metric(
                        'overtime',
                        resp_thread,
                        {
                            'user_message': message[:200] if message else '',
                            'status': ot_resp.get('status', 'active')
                        },
                        employee_data
                    )
                    return jsonify({
                        'response': ot_resp.get('message', ''),
                        'status': 'success',
                        'has_employee_context': employee_data is not None,
                        'thread_id': resp_thread,
                        'widgets': ot_resp.get('widgets'),
                        'buttons': ot_resp.get('buttons')
                    })
            except Exception:
                pass

            # Quick entry: generic "generate letters" should open the document picker
            # Guard: do NOT trigger for internal action values used by buttons
            internal_doc_commands = {
                'generate_experience_letter',
                'generate_employment_letter_en',
                'generate_employment_letter_ar',
                'employment_letter_options',
                'embassy_letter'
            }
            if (
                normalized_msg not in internal_doc_commands and (
                    normalized_msg in {
                        'generate letters', 'generate letter',
                        'create letters', 'create letter',
                        'make letters', 'make letter',
                        'prepare letters', 'prepare letter'
                    }
                    or (
                        any(k in normalized_msg for k in ['generate', 'create', 'make', 'prepare'])
                        and any(w in normalized_msg for w in ['letter', 'letters'])
                    )
                )
            ):
                response = {
                    'message': 'Which document would you like to generate?',
                    'buttons': [
                        { 'text': 'Employment letter', 'value': 'employment_letter_options', 'type': 'action_document' },
                        { 'text': 'Embassy employment letter', 'value': 'embassy_letter', 'type': 'action_document' },
                        { 'text': 'Experience letter', 'value': 'generate_experience_letter', 'type': 'action_document' }
                    ]
                }
            elif normalized_msg in {'generate_employment_letter', 'generate employment letter', 'employment letter', 'create employment letter'}:
                # Fast-path: explicit generation command
                success, att = document_service.generate_employment_letter()
                if success:
                    extra_meta = {'attachment_name': att.get('filename') if isinstance(att, dict) else None}
                    _log_document_metric(thread_id, 'employment_letter', extra=extra_meta, employee=employee_data)
                    response = {
                        'message': 'Your Employment Letter is ready.\n\nPlease double-check the document, I\'m fast, but not always perfect.',
                        'attachments': [att]
                    }
                else:
                    response = {
                        'message': f"Error generating Employment Letter: {att}",
                        'error': True
                    }
            elif normalized_msg in {'employment_letter_options'}:
                response = {
                    'message': 'Which version of the Employment Letter would you like?',
                    'buttons': [
                        { 'text': 'Employment letter (English)', 'value': 'generate_employment_letter_en', 'type': 'action_document' },
                        { 'text': 'Employment letter (Arabic)', 'value': 'generate_employment_letter_ar', 'type': 'action_document' }
                    ]
                }
            elif normalized_msg in {'embassy_letter', 'embassy employment letter', 'employment letter to embassy', 'employment letter for embassy', 'embassy letter'} or (
                'embassy' in normalized_msg and ('letter' in normalized_msg or 'document' in normalized_msg)
            ):
                # Start embassy flow: ask for country with a dropdown widget
                countries = [
                    {'label': n, 'value': n} for n in [
                        'Afghanistan','Albania','Algeria','Andorra','Angola','Antigua and Barbuda','Argentina','Armenia','Australia','Austria','Azerbaijan',
                        'Bahamas','Bahrain','Bangladesh','Barbados','Belarus','Belgium','Belize','Benin','Bhutan','Bolivia','Bosnia and Herzegovina','Botswana','Brazil','Brunei','Bulgaria','Burkina Faso','Burundi',
                        'Cabo Verde','Cambodia','Cameroon','Canada','Central African Republic','Chad','Chile','China','Colombia','Comoros','Congo','Democratic Republic of the Congo','Costa Rica','Cote d\'Ivoire','Croatia','Cuba','Cyprus','Czechia',
                        'Denmark','Djibouti','Dominica','Dominican Republic',
                        'Ecuador','Egypt','El Salvador','Equatorial Guinea','Eritrea','Estonia','Eswatini','Ethiopia',
                        'Fiji','Finland','France',
                        'Gabon','Gambia','Georgia','Germany','Ghana','Greece','Grenada','Guatemala','Guinea','Guinea-Bissau','Guyana',
                        'Haiti','Honduras','Hungary',
                        'Iceland','India','Indonesia','Iran','Iraq','Ireland','Israel','Italy',
                        'Jamaica','Japan','Jordan',
                        'Kazakhstan','Kenya','Kiribati','North Korea','South Korea','Kuwait','Kyrgyzstan',
                        'Laos','Latvia','Lebanon','Lesotho','Liberia','Libya','Liechtenstein','Lithuania','Luxembourg',
                        'Madagascar','Malawi','Malaysia','Maldives','Mali','Malta','Marshall Islands','Mauritania','Mauritius','Mexico','Micronesia','Moldova','Monaco','Mongolia','Montenegro','Morocco','Mozambique','Myanmar',
                        'Namibia','Nauru','Nepal','Netherlands','New Zealand','Nicaragua','Niger','Nigeria','North Macedonia','Norway',
                        'Oman',
                        'Pakistan','Palau','Panama','Papua New Guinea','Paraguay','Peru','Philippines','Poland','Portugal',
                        'Qatar',
                        'Romania','Russia','Rwanda',
                        'Saint Kitts and Nevis','Saint Lucia','Saint Vincent and the Grenadines','Samoa','San Marino','Sao Tome and Principe','Saudi Arabia','Senegal','Serbia','Seychelles','Sierra Leone','Singapore','Slovakia','Slovenia','Solomon Islands','Somalia','South Africa','South Sudan','Spain','Sri Lanka','Sudan','Suriname','Sweden','Switzerland','Syria',
                        'Taiwan','Tajikistan','Tanzania','Thailand','Timor-Leste','Togo','Tonga','Trinidad and Tobago','Tunisia','Turkey','Turkmenistan','Tuvalu',
                        'Uganda','Ukraine','United Arab Emirates','United Kingdom','United States','Uruguay','Uzbekistan',
                        'Vanuatu','Vatican City','Venezuela','Vietnam',
                        'Yemen',
                        'Zambia','Zimbabwe'
                    ]
                ]
                # Try to auto-extract country and dates from the user's message
                auto_country = None
                auto_start = None
                auto_end = None
                try:
                    # Country heuristic: detect via aliases first, then full names
                    auto_country = _detect_country_in_text(normalized_msg, [c['value'] for c in countries])
                    # Date heuristic: original simple extraction
                    import re as _re
                    date_patterns = [r"(\d{1,2}/\d{1,2}/\d{2,4})", r"(\d{1,2}-\d{1,2}-\d{2,4})"]
                    found = []
                    for pat in date_patterns:
                        found += _re.findall(pat, message)
                    found = [f.replace('-', '/') for f in found]
                    if len(found) >= 2:
                        auto_start, auto_end = found[0], found[1]
                    if not (auto_start and auto_end):
                        m = _re.search(r"\b(?:from\s*)?(?:the\s*)?(\d{1,2})(?:st|nd|rd|th)?\b.*?\b(?:to|until|till|-|through)\b.*?(?:the\s*)?(\d{1,2})(?:st|nd|rd|th)?\b", normalized_msg)
                        if m:
                            d1, d2 = m.group(1), m.group(2)
                            today = date.today()
                            auto_start = f"{int(d1):02d}/{today.month:02d}/{today.year}"
                            auto_end = f"{int(d2):02d}/{today.month:02d}/{today.year}"
                except Exception:
                    pass

                if auto_country and auto_start and auto_end:
                    # Generate immediately
                    success, att = document_service.generate_embassy_letter(country=auto_country, start_date=auto_start, end_date=auto_end)
                    session.pop('embassy_letter_flow', None)
                    if success:
                        response = {
                            'message': 'Your embassy employment letter is ready.\n\nPlease double-check the document, I\'m fast, but not always perfect.',
                            'attachments': [att]
                        }
                    else:
                        response = {
                            'message': f"Error generating Embassy Letter: {att}",
                            'error': True
                        }
                else:
                    # Initialize flow with any partial info
                    flow = {'step': 'country'}
                    if auto_country:
                        flow['country'] = auto_country
                        flow['step'] = 'dates'
                    session['embassy_letter_flow'] = flow
                    if flow['step'] == 'dates':
                        response = {
                            'message': f"Please select your travel dates.",
                            'widgets': {
                                'date_range_picker': True,
                                'context_key': 'embassy_date_range'
                            }
                        }
                    else:
                        response = {
                            'message': 'Which country will you be visiting?',
                            'widgets': {
                                'select_dropdown': True,
                                'options': countries,
                                'context_key': 'embassy_country',
                                'placeholder': 'Select a country'
                            }
                        }
            elif normalized_msg.startswith('embassy_country='):
                # Save selected country then ask for dates (preserve original casing from raw message)
                raw_msg = (message or '').strip()
                if raw_msg.lower().startswith('embassy_country='):
                    country_raw = raw_msg.split('=', 1)[1].strip()
                else:
                    country_raw = (normalized_msg.split('=', 1)[1] or '').strip()
                debug_log(f"Embassy flow - raw country from user: '{country_raw}'", "bot_logic")
                country = _normalize_country_name(country_raw)
                debug_log(f"Embassy flow - normalized country: '{country}'", "bot_logic")
                flow = session.get('embassy_letter_flow', {})
                flow['country'] = country
                flow['step'] = 'dates'
                session['embassy_letter_flow'] = flow
                debug_log(f"Embassy flow saved country in session: '{country}'", "bot_logic")
                response = {
                    'message': 'Please select your travel dates.',
                    'widgets': {
                        'date_range_picker': True,
                        'context_key': 'embassy_date_range'
                    }
                }
            elif session.get('embassy_letter_flow', {}).get('step') == 'country':
                # User typed a country name directly; accept it and move to dates step
                raw = (message or '').strip()
                if raw.lower().startswith('embassy_country='):
                    raw = raw.split('=', 1)[1].strip()
                # Early cancel intent check before treating input as a country
                _raw_low = raw.lower()
                if _raw_low in {'cancel','stop','exit','quit','abort','end','undo','nevermind','no thanks','no','n'}:
                    session.pop('embassy_letter_flow', None)
                    response = { 'message': 'request cancelled, can i help you with anything else' }
                else:
                    country = _normalize_country_name(raw)
                    flow = session.get('embassy_letter_flow', {})
                    flow['country'] = country
                    flow['step'] = 'dates'
                    session['embassy_letter_flow'] = flow
                    response = {
                        'message': 'Please select your travel dates.',
                        'widgets': {
                            'date_range_picker': True,
                            'context_key': 'embassy_date_range'
                        }
                    }
            elif normalized_msg.startswith('embassy_date_range='):
                # Parse date range and generate letter
                value = normalized_msg.split('=', 1)[1].strip()
                # Expect "DD/MM/YYYY to DD/MM/YYYY"
                parts = [p.strip() for p in value.split(' to ') if p.strip()]
                if len(parts) == 2:
                    start_date, end_date = parts
                    flow = session.get('embassy_letter_flow', {})
                    country = flow.get('country')
                    if not country:
                        response = {
                            'message': 'Which country will you be visiting?',
                            'widgets': {
                                'select_dropdown': True,
                                'options': [
                                    {'label': 'Jordan', 'value': 'Jordan'},
                                    {'label': 'United Arab Emirates', 'value': 'United Arab Emirates'},
                                    {'label': 'Saudi Arabia', 'value': 'Saudi Arabia'}
                                ],
                                'context_key': 'embassy_country',
                                'placeholder': 'Select a country'
                            }
                        }
                    else:
                        debug_log(f"Calling generate_embassy_letter with country='{country}', start='{start_date}', end='{end_date}'", "bot_logic")
                        success, att = document_service.generate_embassy_letter(country=country, start_date=start_date, end_date=end_date)
                        # Clear flow
                        session.pop('embassy_letter_flow', None)
                        if success:
                            extra_meta = {
                                'country': country,
                                'start_date': start_date,
                                'end_date': end_date,
                                'attachment_name': att.get('filename') if isinstance(att, dict) else None
                            }
                            _log_document_metric(thread_id, 'embassy_letter', extra=extra_meta, employee=employee_data)
                            response = {
                                'message': 'Your embassy employment letter is ready.\n\nPlease double-check the document, I\'m fast, but not always perfect.',
                                'attachments': [att]
                            }
                        else:
                            response = {
                                'message': f"Error generating Embassy Letter: {att}",
                                'error': True
                            }
                else:
                    response = {
                        'message': 'Please provide a full date range in the format DD/MM/YYYY to DD/MM/YYYY.',
                        'widgets': {
                            'date_range_picker': True,
                            'context_key': 'embassy_date_range'
                        }
                    }
            elif normalized_msg in {'generate_experience_letter', 'experience letter', 'experience certificate', 'work experience letter'}:
                success, att = document_service.generate_experience_letter()
                if success:
                    extra_meta = {'attachment_name': att.get('filename') if isinstance(att, dict) else None}
                    _log_document_metric(thread_id, 'experience_letter', extra=extra_meta, employee=employee_data)
                    response = {
                        'message': 'Your Experience Letter is ready.\n\nPlease double-check the document, I\'m fast, but not always perfect.',
                        'attachments': [att]
                    }
                else:
                    response = {
                        'message': f"Error generating Experience Letter: {att}",
                        'error': True
                    }
            elif (
                normalized_msg in {
                    'who are my team members', 'who is on my team', 'list my team',
                    'team members', 'my team', 'show my team'
                }
                or (
                    'team' in normalized_msg and (
                        'member' in normalized_msg or 'members' in normalized_msg or 'report' in normalized_msg or 'reports' in normalized_msg
                    )
                )
            ):
                # Manager query: team overview with time off
                try:
                    # Use functions imported at module load time; avoid runtime absolute imports that break in package mode
                    ok_overview, overview = get_team_overview(odoo_service, employee_service, days_ahead=60)
                    if ok_overview:
                        if isinstance(overview, list):
                            # Provide both text and a structured table widget for UI rendering
                            msg = format_team_overview_message(overview)
                            table = build_team_overview_table_widget(overview)
                            # Build main overview table with planning slots for today (user timezone)
                            user_tz = None
                            try:
                                user_tz = (employee_data or {}).get('tz') if isinstance(employee_data, dict) else None
                            except Exception:
                                user_tz = None
                            ok_main, main_table = build_main_overview_table_widget(odoo_service, overview, user_tz or '')
                            # Build separate overtime table widget (include manager's own overtime)
                            try:
                                ot_team = list(overview) if isinstance(overview, list) else []
                                try:
                                    me_name = (employee_data or {}).get('name') if isinstance(employee_data, dict) else None
                                    me_job = (employee_data or {}).get('job_title') if isinstance(employee_data, dict) else ''
                                    me_dept = ''
                                    if isinstance(employee_data, dict):
                                        dept_det = employee_data.get('department_id_details')
                                        if isinstance(dept_det, dict):
                                            me_dept = dept_det.get('name') or ''
                                    me_uid = getattr(odoo_service, 'user_id', None)
                                    # Append current user to overtime mapping if not already present
                                    if me_uid and not any(isinstance(m, dict) and m.get('user_id') == me_uid for m in ot_team):
                                        ot_team.append({
                                            'id': (employee_data or {}).get('id') if isinstance(employee_data, dict) else None,
                                            'name': me_name or 'Me',
                                            'job_title': me_job or '',
                                            'department': me_dept or '',
                                            'user_id': me_uid,
                                        })
                                except Exception:
                                    pass
                                ok_ot, ot_table = build_overtime_table_widget(odoo_service, ot_team, days_ahead=60)
                            except Exception:
                                ok_ot, ot_table = False, None
                            response = {
                                'message': msg,
                                'widgets': {
                                    'main_overview': main_table if ok_main and main_table else None,
                                    'team_overview': table,
                                    'overtime_overview': ot_table if ok_ot and ot_table else None
                                }
                            }
                        else:
                            response = { 'message': str(overview) }
                    else:
                        response = { 'message': f"I couldn't retrieve your team overview right now: {overview}" }
                except Exception as e:
                    response = { 'message': f"An error occurred preparing the team overview: {e}" }
            elif normalized_msg in {
                'set up new users','setup new users','create new users','new users',
                'set up new user','setup new user','set up a new user','setup a new user',
                'create a new user','create new user','new user','create employee','add employee','new joiner'
            }:
                if not _is_people_culture_member(employee_data):
                    response = { 'message': PEOPLE_CULTURE_DENIED }
                else:
                    try:
                        from services.new_user_flow import start_new_user_flow, handle_new_user_action
                        response = start_new_user_flow()
                    except Exception as e:
                        response = { 'message': f"Couldn't start the new user flow: {e}" }
            elif normalized_msg in {'new_user_manual', 'new_user_upload'}:
                if not _is_people_culture_member(employee_data):
                    response = { 'message': PEOPLE_CULTURE_DENIED }
                else:
                    try:
                        from services.new_user_flow import handle_new_user_action
                        resp = handle_new_user_action(normalized_msg)
                        # Early return for upload widget so frontend doesn't treat empty message as error
                        if normalized_msg == 'new_user_upload':
                            widgets = resp.get('widgets') if isinstance(resp, dict) else None
                            assistant_text = resp.get('message', '') if isinstance(resp, dict) else ''
                            if assistant_text:
                                _log_chat_message_event(
                                    thread_id,
                                    'assistant',
                                    assistant_text,
                                    employee_data,
                                    {'source': 'new_user_flow'}
                                )
                            return jsonify({
                                'response': assistant_text,
                                'status': 'success',
                                'has_employee_context': employee_data is not None,
                                'thread_id': thread_id,
                                'widgets': widgets
                            })
                        response = resp
                    except Exception as e:
                        response = { 'message': f"Couldn't proceed: {e}" }
            elif normalized_msg in {'upload file','upload users','upload new users file','upload user file'}:
                # Allow typing "upload file" to open the upload widget bubble directly
                if not _is_people_culture_member(employee_data):
                    if thread_id:
                        _log_chat_message_event(
                            thread_id,
                            'assistant',
                            PEOPLE_CULTURE_DENIED,
                            employee_data,
                            {'source': 'new_user_flow', 'reason': 'access_denied'}
                        )
                    return jsonify({
                        'response': PEOPLE_CULTURE_DENIED,
                        'status': 'success',
                        'has_employee_context': employee_data is not None,
                        'thread_id': thread_id
                    })
                return jsonify({
                    'response': ' ',
                    'status': 'success',
                    'has_employee_context': employee_data is not None,
                    'thread_id': thread_id,
                    'widgets': { 'new_user_upload': True }
                })
            elif normalized_msg == 'new_user_upload_confirm':
                if not _is_people_culture_member(employee_data):
                    response = { 'message': PEOPLE_CULTURE_DENIED }
                else:
                    try:
                        from services.new_user_flow import create_employees_batch
                        response = create_employees_batch(odoo_service)
                    except Exception as e:
                        response = { 'message': f"Couldn't confirm: {e}" }
            elif normalized_msg.startswith('assign_company:'):
                if not _is_people_culture_member(employee_data):
                    response = { 'message': PEOPLE_CULTURE_DENIED }
                else:
                    try:
                        # Format: assign_company:{index}:{company_label}
                        parts = normalized_msg.split(':', 2)
                        if len(parts) < 3:
                            response = { 'message': 'Invalid assign company command' }
                        else:
                            idx = int(parts[1])
                            label = parts[2]
                            from services.new_user_flow import assign_company_to_record, confirmation_message
                            result = assign_company_to_record(idx, label, odoo_service)
                            if result.get('success'):
                                rows = result.get('rows') or []
                                response = {
                                    'message': 'updated',
                                    'buttons': { 'widgets': { 'new_user_confirm_rows': rows } }
                                }
                            else:
                                response = { 'message': result.get('message') or 'Failed to assign company' }
                    except Exception as e:
                        response = { 'message': f"Assign company error: {e}" }
            elif normalized_msg == 'new_user_assign_hardware_no':
                if not _is_people_culture_member(employee_data):
                    response = { 'message': PEOPLE_CULTURE_DENIED }
                else:
                    session.pop('new_user_recent_employees', None)
                    response = { 'message': 'Alright, no hardware will be assigned right now. Let me know if you need anything else.' }
            elif normalized_msg.startswith('assign_hardware:'):
                if not _is_people_culture_member(employee_data):
                    response = { 'message': PEOPLE_CULTURE_DENIED }
                else:
                    try:
                        _, emp_id_str = normalized_msg.split(':', 1)
                        emp_id = int(emp_id_str)
                    except Exception:
                        response = { 'message': 'Invalid hardware assignment command.' }
                    else:
                        recent = session.get('new_user_recent_employees') or []
                        match = next((item for item in recent if int(item.get('employee_id', 0)) == emp_id), None)
                        if not match:
                            response = { 'message': 'I could not find that teammate in the recently created list.' }
                        else:
                            from services.new_user_flow import list_available_hardware
                            hardware_items = list_available_hardware(odoo_service)
                            if not hardware_items:
                                response = { 'message': 'I could not find any available hardware right now. Please check again later.' }
                            else:
                                options = [{
                                    'label': item.get('name', ''),
                                    'value': str(item.get('id'))
                                } for item in hardware_items if item.get('id') and item.get('name')]
                                hardware_candidates = session.get('hardware_candidates') or {}
                                hardware_candidates[str(emp_id)] = {
                                    'employee_name': match.get('name', ''),
                                    'options': options
                                }
                                session['hardware_candidates'] = hardware_candidates
                                first_name = (match.get('first_name') or match.get('name', '') or 'the employee').split(' ')[0]
                                response = {
                                    'message': f"Select hardware for {first_name}:",
                                    'widgets': {
                                        'hardware_assign': {
                                            'employee_id': emp_id,
                                            'employee_name': match.get('name', ''),
                                            'options': options
                                        }
                                    }
                                }
            elif normalized_msg.startswith('hardware_assign_confirm:'):
                if not _is_people_culture_member(employee_data):
                    response = { 'message': PEOPLE_CULTURE_DENIED }
                else:
                    try:
                        _, emp_id_str, hw_id_str = normalized_msg.split(':', 2)
                        emp_id = int(emp_id_str)
                        hardware_id = int(hw_id_str)
                    except Exception:
                        response = { 'message': 'That hardware confirmation looked malformed. Could you try again?' }
                    else:
                        candidates = session.get('hardware_candidates') or {}
                        info = candidates.get(str(emp_id), {})
                        employee_name = info.get('employee_name', 'the employee')
                        first_name = employee_name.split(' ')[0] if employee_name else 'the employee'
                        options = info.get('options') or []
                        hardware_name = ''
                        for opt in options:
                            if str(opt.get('value')) == str(hardware_id):
                                hardware_name = opt.get('label', 'the selected hardware')
                                break
                        if not hardware_name:
                            hardware_name = 'the selected hardware'
                        from services.new_user_flow import assign_hardware_to_employee, list_available_hardware
                        ok_assign, error_msg = assign_hardware_to_employee(odoo_service, hardware_id, emp_id)
                        if ok_assign:
                            refreshed = list_available_hardware(odoo_service)
                            refreshed_options = [{
                                'label': item.get('name', ''),
                                'value': str(item.get('id'))
                            } for item in refreshed if item.get('id') and item.get('name')]
                            unit = candidates.get(str(emp_id)) or {}
                            unit['employee_name'] = employee_name
                            unit['options'] = refreshed_options
                            candidates[str(emp_id)] = unit
                            session['hardware_candidates'] = candidates
                            recent = session.get('new_user_recent_employees') or []
                            if not isinstance(recent, list):
                                recent = []
                            if not any(int(item.get('employee_id', 0)) == emp_id for item in recent):
                                recent.append({
                                    'employee_id': emp_id,
                                    'name': employee_name,
                                    'first_name': first_name
                                })
                            session['new_user_recent_employees'] = recent
                            msg = f"Great choice! I've assigned {hardware_name} to {first_name}."
                            response = {
                                'message': msg,
                                'hardware_options': recent,
                                'hardware_message': "Would you like to assign another new Prezlaber hardware?"
                            }
                        else:
                            response = { 'message': f"I couldn't assign the hardware: {error_msg}" }
            elif normalized_msg == 'hardware_assign_cancel':
                if not _is_people_culture_member(employee_data):
                    response = { 'message': PEOPLE_CULTURE_DENIED }
                else:
                    response = { 'message': "No problem, I'll skip that hardware assignment for now." }
            elif normalized_msg == 'new_user_upload_cancel':
                if not _is_people_culture_member(employee_data):
                    if thread_id:
                        _log_chat_message_event(
                            thread_id,
                            'assistant',
                            PEOPLE_CULTURE_DENIED,
                            employee_data,
                            {'source': 'new_user_flow', 'reason': 'access_denied'}
                        )
                    return jsonify({
                        'response': PEOPLE_CULTURE_DENIED,
                        'status': 'success',
                        'has_employee_context': employee_data is not None,
                        'thread_id': thread_id
                    })
                try:
                    # Clear any pending batch and inform the user
                    session.pop('new_user_batch', None)
                except Exception:
                    pass
                assistant_text = 'Request cancelled.'
                if thread_id:
                    _log_chat_message_event(
                        thread_id,
                        'assistant',
                        assistant_text,
                        employee_data,
                        {'source': 'new_user_flow', 'event': 'upload_cancel'}
                    )
                return jsonify({
                    'response': assistant_text,
                    'status': 'success',
                    'has_employee_context': employee_data is not None,
                    'thread_id': thread_id
                })
            elif request.path == '/api/new-users/preview-service' and request.method == 'POST':
                try:
                    if not session.get('authenticated'):
                        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

                    payload = request.get_json(silent=True) or {}
                    full_name = (payload.get('name') or '').strip()
                    company_name = (payload.get('company_name') or '').strip()
                    allowed = {
                        'Prezlab FZ LLC - Regional Office',
                        'ALOROD AL TAQADAMIAH LEL TASMEM CO',
                        'Prezlab Advanced Design Company',
                        'Prezlab FZ LLC',
                        'Prezlab Digital Design Firm L.L.C. - O.P.C'
                    }
                    if not full_name:
                        return jsonify({'success': False, 'message': 'Name is required'}), 400
                    if company_name not in allowed:
                        return jsonify({'success': False, 'message': 'Preview only available for selected companies'}), 400

                    from services.employee_service import EmployeeService
                    from services.document_service import DocumentService
                    emp_service = EmployeeService(odoo_service)
                    doc_service = DocumentService(odoo_service, emp_service)
                    doc_service.metrics_service = metrics_service
                    ok_doc, doc_meta = doc_service.generate_service_agreement(full_name, company_name=company_name)
                    if ok_doc:
                        extra_meta = {
                            'company_name': company_name,
                            'attachment_name': doc_meta.get('filename') if isinstance(doc_meta, dict) else None,
                            'source': 'rest_api'
                        }
                        _log_document_metric(data.get('thread_id'), 'service_agreement', extra=extra_meta)
                        return jsonify({'success': True, 'attachment': doc_meta})
                    return jsonify({'success': False, 'message': str(doc_meta)}), 500
                except Exception as e:
                    return jsonify({'success': False, 'message': f'Preview error: {e}'}), 500
            elif session.get('embassy_letter_flow', {}).get('step') == 'dates':
                # Try to interpret free-typed date range
                value = (message or '').trim() if hasattr(message, 'trim') else (message or '').strip()
                # Early cancel intent check before date parsing/validation
                _vlow = value.lower()
                if _vlow in {'cancel','stop','exit','quit','abort','end','undo','nevermind','no thanks','no','n'}:
                    session.pop('embassy_letter_flow', None)
                    response = { 'message': 'request cancelled, can i help you with anything else' }
                else:
                    parts = [p.strip() for p in value.split(' to ') if p.strip()]
                    if len(parts) != 2:
                        import re as _re
                        m = _re.split(r"\s*(?:-|to|until|till|through|\u2013|\u2014)\s*", value)
                        parts = [p.strip() for p in m if p.strip()]
                    if len(parts) != 2:
                        import re as _re
                        dm = _re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\b.*?\b(?:to|until|till|-|through)\b.*?(\d{1,2})(?:st|nd|rd|th)?\b", value.lower())
                        if dm:
                            today = date.today()
                            parts = [f"{int(dm.group(1)):02d}/{today.month:02d}/{today.year}", f"{int(dm.group(2)):02d}/{today.month:02d}/{today.year}"]
                    if len(parts) == 2:
                        start_date, end_date = parts
                        flow = session.get('embassy_letter_flow', {})
                        country = flow.get('country')
                        session.pop('embassy_letter_flow', None)
                        success, att = document_service.generate_embassy_letter(country=country, start_date=start_date, end_date=end_date)
                        if success:
                            extra_meta = {
                                'country': country,
                                'start_date': start_date,
                                'end_date': end_date,
                                'attachment_name': att.get('filename') if isinstance(att, dict) else None
                            }
                            _log_document_metric(thread_id, 'embassy_letter', extra=extra_meta, employee=employee_data)
                            response = {
                                'message': 'Your embassy employment letter is ready.\n\nPlease double-check the document, I\'m fast, but not always perfect.',
                                'attachments': [att]
                            }
                        else:
                            response = {
                                'message': f"Error generating Embassy Letter: {att}",
                                'error': True
                            }
                    else:
                        response = {
                            'message': 'Please provide a full date range in the format DD/MM/YYYY to DD/MM/YYYY.',
                            'widgets': {
                                'date_range_picker': True,
                                'context_key': 'embassy_date_range'
                            }
                        }
            elif normalized_msg in {'generate_employment_letter_en', 'employment letter en'}:
                success, att = document_service.generate_employment_letter(lang='en')
                if success:
                    extra_meta = {'attachment_name': att.get('filename') if isinstance(att, dict) else None}
                    _log_document_metric(thread_id, 'employment_letter', language='en', extra=extra_meta, employee=employee_data)
                    response = {
                        'message': 'Your Employment Letter is ready.\n\nPlease double-check the document, I\'m fast, but not always perfect.',
                        'attachments': [att]
                    }
                else:
                    response = {
                        'message': f"Error generating Employment Letter: {att}",
                        'error': True
                    }
            elif normalized_msg in {'generate_employment_letter_ar', 'employment letter ar', 'employment letter arabic'}:
                success, att = document_service.generate_employment_letter(lang='ar')
                if success:
                    extra_meta = {'attachment_name': att.get('filename') if isinstance(att, dict) else None}
                    _log_document_metric(thread_id, 'employment_letter', language='ar', extra=extra_meta, employee=employee_data)
                    response = {
                        'message': 'Your Employment Letter is ready.\n\nPlease double-check the document, I\'m fast, but not always perfect.',
                        'attachments': [att]
                    }
                else:
                    response = {
                        'message': f"Error generating Employment Letter: {att}",
                        'error': True
                    }
            else:
                # Detect document intent
                intent, confidence, meta = intent_service.detect(message)
                if intent == 'timeoff_request' and confidence >= 0.5:
                    # Handle time-off request through ChatGPT service
                    debug_log(f"Time-off intent detected with confidence {confidence:.2f}", "bot_logic")
                    # Store Odoo session data in Flask's request-scoped 'g' object (isolated per request)
                    g.odoo_session_data = get_odoo_session_data()
                    response = chatgpt_service.get_response(message, thread_id, employee_data)
                    if response:
                        if isinstance(response, dict):
                            message_text = response.get('message', str(response))
                            response_data = {
                                'response': message_text,
                                'status': 'success',
                                'has_employee_context': employee_data is not None,
                                'thread_id': response.get('thread_id', thread_id)
                            }
                            if 'buttons' in response:
                                response_data['buttons'] = response['buttons']
                            if 'widgets' in response:
                                response_data['widgets'] = response['widgets']
                            resp_thread_id = response.get('thread_id') or thread_id
                            if resp_thread_id:
                                _log_chat_message_event(
                                    resp_thread_id,
                                    'assistant',
                                    message_text,
                                    employee_data,
                                    {'source': 'timeoff'}
                                )
                            return jsonify(response_data)
                        else:
                            if thread_id:
                                _log_chat_message_event(
                                    thread_id,
                                    'assistant',
                                    response,
                                    employee_data,
                                    {'source': 'timeoff'}
                                )
                            return jsonify({
                                'response': response,
                                'status': 'success',
                                'has_employee_context': employee_data is not None,
                                'thread_id': thread_id
                            })
                elif intent == 'reimbursement_request' and confidence >= 0.5:
                    # Handle reimbursement request through the reimbursement service
                    reimb_resp = reimbursement_service.handle_flow(message, thread_id, employee_data or {})
                    if reimb_resp:
                        resp_thread = reimb_resp.get('thread_id') or thread_id
                        assistant_text = reimb_resp.get('message', '')
                        if assistant_text:
                            _log_chat_message_event(
                                resp_thread,
                                'assistant',
                                assistant_text,
                                employee_data,
                                {'source': 'reimbursement'}
                            )
                        # Log reimbursement metric
                        _log_usage_metric(
                            'reimbursement',
                            resp_thread,
                            {
                                'user_message': message[:200] if message else '',
                                'status': reimb_resp.get('status', 'active')
                            },
                            employee_data
                        )
                        return jsonify({
                            'response': reimb_resp.get('message', ''),
                            'status': 'success',
                            'has_employee_context': employee_data is not None,
                            'thread_id': resp_thread,
                            'widgets': reimb_resp.get('widgets'),
                            'buttons': reimb_resp.get('buttons')
                        })
                elif intent == 'experience_letter' and confidence >= 0.5:
                    success, att = document_service.generate_experience_letter()
                    if success:
                        extra_meta = {'attachment_name': att.get('filename') if isinstance(att, dict) else None}
                        _log_document_metric(thread_id, 'experience_letter', extra=extra_meta, employee=employee_data)
                        response = {
                            'message': 'Your Experience Letter is ready.\n\nPlease double-check the document, I\'m fast, but not always perfect.',
                            'attachments': [att]
                        }
                    else:
                        response = {
                            'message': f"Error generating Experience Letter: {att}",
                            'error': True
                        }
                elif intent == 'employment_letter' and confidence >= 0.5:
                    # If the user mentioned embassy anywhere, route to embassy flow instead of employment letter
                    if 'embassy' in normalized_msg:
                        countries = [
                            {'label': n, 'value': n} for n in [
                                'Afghanistan','Albania','Algeria','Andorra','Angola','Antigua and Barbuda','Argentina','Armenia','Australia','Austria','Azerbaijan',
                                'Bahamas','Bahrain','Bangladesh','Barbados','Belarus','Belgium','Belize','Benin','Bhutan','Bolivia','Bosnia and Herzegovina','Botswana','Brazil','Brunei','Bulgaria','Burkina Faso','Burundi',
                                'Cabo Verde','Cambodia','Cameroon','Canada','Central African Republic','Chad','Chile','China','Colombia','Comoros','Congo','Democratic Republic of the Congo','Costa Rica','Cote d\'Ivoire','Croatia','Cuba','Cyprus','Czechia',
                                'Denmark','Djibouti','Dominica','Dominican Republic',
                                'Ecuador','Egypt','El Salvador','Equatorial Guinea','Eritrea','Estonia','Eswatini','Ethiopia',
                                'Fiji','Finland','France',
                                'Gabon','Gambia','Georgia','Germany','Ghana','Greece','Grenada','Guatemala','Guinea','Guinea-Bissau','Guyana',
                                'Haiti','Honduras','Hungary',
                                'Iceland','India','Indonesia','Iran','Iraq','Ireland','Israel','Italy',
                                'Jamaica','Japan','Jordan',
                                'Kazakhstan','Kenya','Kiribati','North Korea','South Korea','Kuwait','Kyrgyzstan',
                                'Laos','Latvia','Lebanon','Lesotho','Liberia','Libya','Liechtenstein','Lithuania','Luxembourg',
                                'Madagascar','Malawi','Malaysia','Maldives','Mali','Malta','Marshall Islands','Mauritania','Mauritius','Mexico','Micronesia','Moldova','Monaco','Mongolia','Montenegro','Morocco','Mozambique','Myanmar',
                                'Namibia','Nauru','Nepal','Netherlands','New Zealand','Nicaragua','Niger','Nigeria','North Macedonia','Norway',
                                'Oman',
                                'Pakistan','Palau','Panama','Papua New Guinea','Paraguay','Peru','Philippines','Poland','Portugal',
                                'Qatar',
                                'Romania','Russia','Rwanda',
                                'Saint Kitts and Nevis','Saint Lucia','Saint Vincent and the Grenadines','Samoa','San Marino','Sao Tome and Principe','Saudi Arabia','Senegal','Serbia','Seychelles','Sierra Leone','Singapore','Slovakia','Slovenia','Solomon Islands','Somalia','South Africa','South Sudan','Spain','Sri Lanka','Sudan','Suriname','Sweden','Switzerland','Syria',
                                'Taiwan','Tajikistan','Tanzania','Thailand','Timor-Leste','Togo','Tonga','Trinidad and Tobago','Tunisia','Turkey','Turkmenistan','Tuvalu',
                                'Uganda','Ukraine','United Arab Emirates','United Kingdom','United States','Uruguay','Uzbekistan',
                                'Vanuatu','Vatican City','Venezuela','Vietnam',
                                'Yemen',
                                'Zambia','Zimbabwe'
                            ]
                        ]
                        response = {
                            'message': 'Which country will you be visiting?',
                            'widgets': {
                                'select_dropdown': True,
                                'options': countries,
                                'context_key': 'embassy_country',
                                'placeholder': 'Select a country'
                            }
                        }
                    else:
                        response = {
                            'message': 'Which version of the Employment Letter would you like?',
                            'buttons': [
                                { 'text': 'Employment letter (English)', 'value': 'generate_employment_letter_en', 'type': 'action_document' },
                                { 'text': 'Employment letter (Arabic)', 'value': 'generate_employment_letter_ar', 'type': 'action_document' }
                            ]
                        }
                elif intent == 'document_request' and confidence >= 0.5:
                    response = {
                        'message': 'Which document would you like to generate?',
                        'buttons': [
                            { 'text': 'Employment letter', 'value': 'employment_letter_options', 'type': 'action_document' },
                            { 'text': 'Embassy employment letter', 'value': 'embassy_letter', 'type': 'action_document' },
                            { 'text': 'Experience letter', 'value': 'generate_experience_letter', 'type': 'action_document' }
                        ]
                    }
                else:
                    # Delegate to ChatGPT
                    debug_log(f"Calling ChatGPT with employee_data: {employee_data is not None}", "bot_logic")
                    # Store Odoo session data in Flask's request-scoped 'g' object (isolated per request)
                    g.odoo_session_data = get_odoo_session_data()
                    response = chatgpt_service.get_response(message, thread_id, employee_data)
                    debug_log(f"ChatGPT response received", "bot_logic")

            # Reimbursement flow: handle after ChatGPT service (time-off requests)
            if not isinstance(response, dict) or not response.get('message'):
                try:
                    debug_log(f"Calling reimbursement service with message: '{message[:50]}...'", "bot_logic")
                    reimb_resp = reimbursement_service.handle_flow(message, thread_id, employee_data or {})
                    debug_log(f"Reimbursement service response: {reimb_resp is not None}", "bot_logic")
                    if reimb_resp:
                        resp_thread = reimb_resp.get('thread_id') or thread_id
                        assistant_text = reimb_resp.get('message', '')
                        if assistant_text:
                            _log_chat_message_event(
                                resp_thread,
                                'assistant',
                                assistant_text,
                                employee_data,
                                {'source': 'reimbursement'}
                            )
                        # Log reimbursement metric
                        _log_usage_metric(
                            'reimbursement',
                            resp_thread,
                            {
                                'user_message': message[:200] if message else '',
                                'status': reimb_resp.get('status', 'active')
                            },
                            employee_data
                        )
                        return jsonify({
                            'response': reimb_resp.get('message', ''),
                            'status': 'success',
                            'has_employee_context': employee_data is not None,
                            'thread_id': resp_thread,
                            'widgets': reimb_resp.get('widgets'),
                            'buttons': reimb_resp.get('buttons')
                        })
                except Exception as e:
                    debug_log(f"Error in reimbursement flow: {e}", "general")
                    import traceback
                    traceback.print_exc()

            # Handle both string and dict responses from ChatGPT service
            if isinstance(response, dict):
                message_text = response.get('message', str(response))
                response_data = {
                    'response': message_text,
                    'status': 'success',
                    'has_employee_context': employee_data is not None,
                    'thread_id': response.get('thread_id', thread_id)  # Ensure thread_id is always returned
                }
                # Include any additional data from the response for UI rendering
                if 'buttons' in response:
                    response_data['buttons'] = response['buttons']
                if 'widgets' in response:
                    response_data['widgets'] = response['widgets']
                if 'attachments' in response:
                    response_data['attachments'] = response['attachments']
                if 'hardware_options' in response:
                    response_data['hardware_options'] = response['hardware_options']

                try:
                    widgets = response_data.get('widgets') or {}
                    if isinstance(widgets, dict) and widgets.get('new_user_upload'):
                        print("[DEBUG] new_user_flow response:", response_data)
                except Exception:
                    pass

                resp_thread_id = response.get('thread_id') or thread_id
                if resp_thread_id:
                    _log_chat_message_event(
                        resp_thread_id,
                        'assistant',
                        message_text,
                        employee_data,
                        {'source': 'assistant'}
                    )
                return jsonify(response_data)
            else:
                # Legacy string response
                if thread_id and response:
                    _log_chat_message_event(
                        thread_id,
                        'assistant',
                        response,
                        employee_data,
                        {'source': 'assistant'}
                    )
                return jsonify({
                    'response': response,
                    'status': 'success',
                    'has_employee_context': employee_data is not None,
                    'thread_id': thread_id  # Ensure thread_id is always returned
                })
            
        except Exception as e:
            return jsonify({
                'error': str(e),
                'status': 'error'
            }), 500

    @app.route('/api/conversations', methods=['GET'])
    def list_user_conversations():
        if not session.get('authenticated'):
            return jsonify({'success': False, 'message': 'Authentication required'}), 401
        if not metrics_service:
            return jsonify({'success': True, 'threads': []})

        employee_profile = _fetch_employee_profile()
        tenant_id, tenant_name, user_id, user_name = _extract_identity(employee_profile or {})
        if not user_id:
            return jsonify({'success': True, 'threads': []})

        try:
            limit = request.args.get('limit', default=50, type=int) or 50
            threads = metrics_service.fetch_threads(user_id=user_id, tenant_id=tenant_id, limit=limit)
            return jsonify({'success': True, 'threads': threads})
        except Exception as exc:
            return jsonify({'success': False, 'message': str(exc)}), 500

    @app.route('/api/conversations/<thread_id>/messages', methods=['GET'])
    def list_conversation_messages(thread_id):
        if not session.get('authenticated'):
            return jsonify({'success': False, 'message': 'Authentication required'}), 401
        if not metrics_service:
            return jsonify({'success': False, 'message': 'Conversation history unavailable'}), 503

        employee_profile = _fetch_employee_profile()
        tenant_id, tenant_name, user_id, user_name = _extract_identity(employee_profile or {})
        if not user_id:
            return jsonify({'success': False, 'message': 'User identity unavailable'}), 400

        thread = metrics_service.fetch_thread(thread_id)
        if not thread:
            return jsonify({'success': False, 'message': 'Conversation not found'}), 404
        if str(thread.get('user_id')) != str(user_id):
            return jsonify({'success': False, 'message': 'Forbidden'}), 403
        if tenant_id and thread.get('tenant_id') and str(thread.get('tenant_id')) != str(tenant_id):
            return jsonify({'success': False, 'message': 'Forbidden'}), 403

        try:
            limit = request.args.get('limit', default=200, type=int) or 200
            messages = metrics_service.fetch_messages(thread_id, limit=limit)
            return jsonify({'success': True, 'thread': thread, 'messages': messages})
        except Exception as exc:
            return jsonify({'success': False, 'message': str(exc)}), 500

    @app.route('/api/documents/employment-letter', methods=['POST'])
    def generate_employment_letter():
        """Generate an Employment Letter for the current user and return attachment metadata."""
        try:
            if not session.get('authenticated'):
                return jsonify({'success': False, 'message': 'Authentication required'}), 401

            data = request.get_json(silent=True) or {}
            lang = (data.get('lang') or 'en').lower()
            success, result = document_service.generate_employment_letter(lang=lang)
            if success:
                extra_meta = {
                    'attachment_name': result.get('filename') if isinstance(result, dict) else None,
                    'source': 'rest_api'
                }
                _log_document_metric(data.get('thread_id'), 'employment_letter', language=lang, extra=extra_meta)
                return jsonify({
                    'success': True,
                    'attachment': result
                })
            else:
                return jsonify({
                    'success': False,
                    'message': result
                }), 500
        except Exception as e:
            return jsonify({
                'success': False,
                'message': f'Error generating document: {str(e)}'
            }), 500

    @app.route('/api/documents/embassy-letter', methods=['POST'])
    def generate_embassy_letter_api():
        """Generate an Embassy Employment Letter for the current user with provided country and dates."""
        try:
            if not session.get('authenticated'):
                return jsonify({'success': False, 'message': 'Authentication required'}), 401

            data = request.get_json(silent=True) or {}
            country = (data.get('country') or '').strip()
            start_date = (data.get('start_date') or '').strip()
            end_date = (data.get('end_date') or '').strip()
            if not country or not start_date or not end_date:
                return jsonify({'success': False, 'message': 'country, start_date and end_date are required'}), 400

            success, result = document_service.generate_embassy_letter(country=country, start_date=start_date, end_date=end_date)
            if success:
                extra_meta = {
                    'country': country,
                    'start_date': start_date,
                    'end_date': end_date,
                    'attachment_name': result.get('filename') if isinstance(result, dict) else None,
                    'source': 'rest_api'
                }
                _log_document_metric(data.get('thread_id'), 'embassy_letter', extra=extra_meta)
                return jsonify({'success': True, 'attachment': result})
            else:
                return jsonify({'success': False, 'message': result}), 500
        except Exception as e:
            return jsonify({'success': False, 'message': f'Error generating embassy letter: {str(e)}'}), 500

    @app.route('/api/documents/experience-letter', methods=['POST'])
    def generate_experience_letter_api():
        """Generate an Experience Letter for the current user and return attachment metadata."""
        try:
            if not session.get('authenticated'):
                return jsonify({'success': False, 'message': 'Authentication required'}), 401

            payload = request.get_json(silent=True) or {}
            success, result = document_service.generate_experience_letter()
            if success:
                extra_meta = {
                    'attachment_name': result.get('filename') if isinstance(result, dict) else None,
                    'source': 'rest_api'
                }
                _log_document_metric(payload.get('thread_id'), 'experience_letter', extra=extra_meta)
                return jsonify({'success': True, 'attachment': result})
            else:
                return jsonify({'success': False, 'message': result}), 500
        except Exception as e:
            return jsonify({'success': False, 'message': f'Error generating experience letter: {str(e)}'}), 500

    @app.route('/api/new-users/preview-service', methods=['POST'])
    def preview_service_agreement_api():
        """Generate a Service Agreement preview for a provided name and allowed companies.

        Request JSON: { name: string, company_name: string }
        Returns: { success: bool, attachment?: { file_name, file_url, mime_type }, message?: str }
        """
        try:
            if not session.get('authenticated'):
                return jsonify({'success': False, 'message': 'Not authenticated'}), 401

            data = request.get_json(silent=True) or {}
            full_name = (data.get('name') or '').strip()
            company_name = (data.get('company_name') or '').strip()
            private_street = (data.get('private_street') or '').strip()
            if not full_name:
                return jsonify({'success': False, 'message': 'Name is required'}), 400

            allowed_companies = {
                'Prezlab FZ LLC - Regional Office',
                'ALOROD AL TAQADAMIAH LEL TASMEM CO',
                'Prezlab Advanced Design Company',
                'Prezlab FZ LLC',
                'Prezlab Digital Design Firm L.L.C. - O.P.C',
            }
            if company_name not in allowed_companies:
                return jsonify({'success': False, 'message': 'Preview available only for selected companies'}), 400

            from services.employee_service import EmployeeService
            from services.document_service import DocumentService
            emp_service = EmployeeService(odoo_service)
            doc_service = DocumentService(odoo_service, emp_service)
            doc_service.metrics_service = metrics_service
            ok_doc, result = doc_service.generate_service_agreement(full_name, private_street=private_street, company_name=company_name)
            if ok_doc:
                extra_meta = {
                    'company_name': company_name,
                    'attachment_name': result.get('filename') if isinstance(result, dict) else None,
                    'source': 'rest_api'
                }
                _log_document_metric(data.get('thread_id'), 'service_agreement', extra=extra_meta)
                return jsonify({'success': True, 'attachment': result})
            else:
                return jsonify({'success': False, 'message': result}), 500
        except Exception as e:
            return jsonify({'success': False, 'message': f'Preview error: {str(e)}'}), 500

    @app.route('/api/odoo/approve', methods=['POST'])
    def odoo_approve_action():
        """Generic endpoint to trigger Odoo workflow methods (approve/refuse) for hr.leave and approval.request."""
        try:
            if not session.get('authenticated'):
                return jsonify({'success': False, 'message': 'Authentication required'}), 401

            data = request.get_json(silent=True) or {}
            model = (data.get('model') or '').strip()
            record_id = data.get('id')
            action = (data.get('action') or '').strip()  # 'approve' | 'refuse'

            if model not in {'hr.leave', 'approval.request'} or not isinstance(record_id, int) or action not in {'approve', 'refuse'}:
                return jsonify({'success': False, 'message': 'Invalid model/id/action'}), 400

            log_message = f"[ManagerAction] Button clicked: action={action} model={model} record_id={record_id}"
            debug_log(log_message, "bot_logic")
            try:
                app.logger.info(log_message)
            except Exception:
                pass

            if not odoo_service.is_authenticated():
                return jsonify({'success': False, 'message': 'Odoo session not authenticated'}), 401

            ok_session, msg = odoo_service.ensure_active_session()
            if not ok_session:
                return jsonify({'success': False, 'message': f'Odoo session error: {msg}'}), 500

            method_map = {
                ('hr.leave', 'approve'): 'action_approve',
                ('hr.leave', 'refuse'): 'action_refuse',
                ('approval.request', 'approve'): 'action_approve',
                ('approval.request', 'refuse'): 'action_refuse',
            }
            method = method_map.get((model, action))
            if not method:
                return jsonify({'success': False, 'message': 'Unsupported action'}), 400

            import requests
            url = f"{odoo_service.odoo_url}/web/dataset/call_kw"
            payload = {
                "jsonrpc": "2.0",
                "method": "call",
                "params": {
                    "model": model,
                    "method": method,
                    "args": [[record_id]],
                    "kwargs": {}
                },
                "id": 1
            }
            cookies = {'session_id': odoo_service.session_id} if odoo_service.session_id else {}
            resp = requests.post(url, json=payload, headers={'Content-Type': 'application/json'}, cookies=cookies, timeout=20)
            ok = (resp.status_code == 200)
            result = resp.json() if ok else { 'error': f'HTTP {resp.status_code}' }
            if ok and 'error' not in result:
                success_message = f"[ManagerAction] Success: action={action} model={model} record_id={record_id}"
                debug_log(success_message, "bot_logic")
                try:
                    app.logger.info(success_message)
                except Exception:
                    pass
                return jsonify({'success': True})
            failure_message = f"[ManagerAction] Failure: action={action} model={model} record_id={record_id} error={result.get('error')}"
            debug_log(failure_message, "bot_logic")
            try:
                app.logger.warning(failure_message)
            except Exception:
                pass
            return jsonify({'success': False, 'message': str(result.get('error', 'Unknown error'))}), 500
        except Exception as e:
            exception_message = f"[ManagerAction] Exception during {action} on {model} id={record_id}: {e}"
            debug_log(exception_message, "bot_logic")
            try:
                app.logger.exception(exception_message)
            except Exception:
                pass
            return jsonify({'success': False, 'message': f'Error performing approval action: {str(e)}'}), 500
    
    @app.route('/api/health', methods=['GET'])
    def health():
        return jsonify({'status': 'healthy', 'service': 'chatbot-api'})

    @app.route('/api/new-users/upload', methods=['POST'])
    def upload_new_users():
        try:
            if not session.get('authenticated'):
                return jsonify({'success': False, 'message': 'Not authenticated'}), 401
            if 'file' not in request.files:
                return jsonify({'success': False, 'message': 'No file uploaded'}), 400
            emp_ok, emp_data = employee_service.get_current_user_employee_data()
            if not emp_ok or not _is_people_culture_member(emp_data):
                return jsonify({'success': False, 'message': PEOPLE_CULTURE_DENIED}), 403
            file = request.files['file']
            content = file.read()
            from services.new_user_flow import parse_new_user_excel, confirmation_message
            # Pass odoo_service to enable duplicate checking
            parsed = parse_new_user_excel(content, odoo_service=odoo_service)
            if not parsed.get('success'):
                return jsonify({'success': False, 'message': parsed.get('message') or 'Parse error'}), 400
            rows = parsed.get('rows') or []
            return jsonify({
                'success': True,
                'rows': rows,
                'confirmation_text': confirmation_message(rows)
            })
        except Exception as e:
            return jsonify({'success': False, 'message': str(e)}), 500

    @app.route('/api/timeoff/supporting-document', methods=['POST'])
    def upload_timeoff_supporting_document():
        try:
            if not session.get('authenticated'):
                return jsonify({'success': False, 'message': 'Not authenticated'}), 401

            if 'file' not in request.files:
                return jsonify({'success': False, 'message': 'No file uploaded'}), 400

            thread_id = request.form.get('thread_id') or request.args.get('thread_id')
            if not thread_id:
                return jsonify({'success': False, 'message': 'Missing thread_id'}), 400

            active_session = session_manager.get_session(thread_id)
            if not active_session or active_session.get('type') != 'timeoff':
                return jsonify({'success': False, 'message': 'No active time-off session found'}), 404

            file_storage = request.files['file']
            content = file_storage.read()
            if not content:
                return jsonify({'success': False, 'message': 'Uploaded file is empty'}), 400

            encoded = base64.b64encode(content).decode('utf-8')
            doc_entry = {
                'filename': file_storage.filename,
                'mimetype': file_storage.mimetype or 'application/octet-stream',
                'data': encoded,
                'uploaded_at': datetime.now(timezone.utc).isoformat()
            }

            data_block = dict(active_session.get('data', {}) or {})
            existing_docs = list(data_block.get('supporting_documents') or [])
            existing_docs.append(doc_entry)
            data_block['supporting_documents'] = existing_docs

            context_block = dict(data_block.get('timeoff_context', {}) or {})
            sanitized_docs = []
            for doc in existing_docs:
                sanitized_docs.append({
                    'filename': doc.get('filename'),
                    'mimetype': doc.get('mimetype'),
                    'uploaded_at': doc.get('uploaded_at')
                })
            context_block['supporting_documents'] = sanitized_docs
            context_block['supporting_doc_required'] = True
            context_block['supporting_doc_uploaded'] = False
            data_block['timeoff_context'] = context_block
            data_block['supporting_doc_required'] = True
            data_block['supporting_doc_uploaded'] = False

            session_manager.update_session(thread_id, {
                'data': data_block,
                'supporting_documents': existing_docs,
                'supporting_doc_required': True,
                'supporting_doc_uploaded': False
            })

            return jsonify({
                'success': True,
                'filename': file_storage.filename,
                'mimetype': file_storage.mimetype,
                'uploaded_at': doc_entry['uploaded_at']
            })
        except Exception as e:
            return jsonify({'success': False, 'message': str(e)}), 500

    # Serve Nasma logo from project root so templates can reference /nasma_logo.png
    @app.route('/nasma_logo.png')
    def nasma_logo_asset():
        try:
            root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
            return send_from_directory(root_dir, 'nasma_logo.png')
        except Exception as e:
            return jsonify({'error': f'Logo not found: {str(e)}'}), 404


    # Serve Nasma-1 logo from project root so templates can reference /Nasma-1.png
    @app.route('/Nasma-1.png')
    def nasma_1_asset():
        try:
            root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
            return send_from_directory(root_dir, 'Nasma-1.png')
        except Exception as e:
            return jsonify({'error': f'Nasma-1 logo not found: {str(e)}'}), 404

    # Serve Nasma main background from project root so templates can reference /Nasma-main.png
    @app.route('/Nasma-main.png')
    def nasma_main_asset():
        try:
            root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
            return send_from_directory(root_dir, 'Nasma-main.png')
        except Exception as e:
            return jsonify({'error': f'Nasma-main image not found: {str(e)}'}), 404

    # Serve Nasma logo (new) from project root so templates can reference /Nasma-logo.png
    @app.route('/Nasma-logo.png')
    def nasma_logo_new_asset():
        try:
            root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
            return send_from_directory(root_dir, 'Nasma-logo.png')
        except Exception as e:
            return jsonify({'error': f'Nasma-logo not found: {str(e)}'}), 404

    # Serve Nasma Avatar SVG from project root
    @app.route('/Nasma-Avatar.svg')
    def nasma_avatar_svg():
        try:
            root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
            return send_from_directory(root_dir, 'Nasma-Avatar.svg', mimetype='image/svg+xml')
        except Exception as e:
            return jsonify({'error': f'Nasma-Avatar.svg not found: {str(e)}'}), 404

    # Serve simple avatar.svg from project root (backup)
    @app.route('/avatar.svg')
    def avatar_svg():
        try:
            root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
            return send_from_directory(root_dir, 'Nasma-Avatar.svg', mimetype='image/svg+xml')
        except Exception as e:
            return jsonify({'error': f'avatar.svg not found: {str(e)}'}), 404
    
    @app.route('/api/debug/clear-session', methods=['POST', 'GET'])
    def clear_session():
        """Debug endpoint to clear session and force fresh login"""
        try:
            debug_log("Clearing session...", "bot_logic")
            debug_log(f"Session before clear: {dict(session)}", "bot_logic")
            session.clear()
            odoo_service.logout()
            debug_log("Session cleared successfully", "bot_logic")
            return jsonify({
                'success': True,
                'message': 'Session cleared successfully'
            })
        except Exception as e:
            debug_log(f"Error clearing session: {e}", "general")
            import traceback
            traceback.print_exc()
            return jsonify({
                'success': False,
                'message': f'Error clearing session: {str(e)}'
            }), 500
    
    @app.route('/api/chat/clear', methods=['POST'])
    def clear_chat():
        """Clear chat history for the current user"""
        try:
            thread_id = request.json.get('thread_id')
            if thread_id:
                from services.chatgpt_service import ChatGPTService
                chatgpt_service = ChatGPTService()
                # Clear local conversation history
                chatgpt_service.clear_conversation_history(thread_id)
                debug_log(f"Cleared conversation history for thread: {thread_id}", "bot_logic")
            
            return jsonify({
                'status': 'success',
                'message': 'Chat history cleared successfully'
            })
            
        except Exception as e:
            return jsonify({
                'status': 'error',
                'error': str(e)
            }), 500
    
    @app.route('/api/chat/init-context', methods=['POST'])
    def init_chat_context():
        """Initialize chat context by returning employee data for client-side system prompt."""
        try:
            if not session.get('authenticated'):
                return jsonify({'error': 'Authentication required'}), 401

            data = request.get_json()
            thread_id = data.get('thread_id')
            if not thread_id:
                return jsonify({'error': 'Thread ID is required'}), 400

            # Ensure Odoo session is active before fetching employee data
            if odoo_service.is_authenticated():
                session_valid, session_msg = odoo_service.ensure_active_session()
                if not session_valid:
                    return jsonify({
                        'status': 'error',
                        'error': f'Odoo session error: {session_msg}'
                    }), 500

            employee_success, employee_data = employee_service.get_current_user_employee_data()
            if employee_success and employee_data:
                return jsonify({
                    'status': 'success',
                    'employee_context': {
                        'name': employee_data.get('name'),
                        'job_title': employee_data.get('job_title'),
                        'department': employee_data.get('department_id_details', {}).get('name') if isinstance(employee_data.get('department_id_details'), dict) else None,
                        'manager': employee_data.get('parent_id_details', {}).get('name') if isinstance(employee_data.get('parent_id_details'), dict) else None,
                        'work_location': employee_data.get('address_id_details', {}).get('city') if isinstance(employee_data.get('address_id_details'), dict) else None,
                        'employee_id': employee_data.get('id')
                    }
                })
            else:
                return jsonify({'status': 'error', 'error': 'Could not fetch employee data'}), 500

        except Exception as e:
            return jsonify({'status': 'error', 'error': str(e)}), 500
    
    @app.route('/api/user/avatar', methods=['GET'])
    def get_user_avatar():
        """Get current user's avatar image"""
        try:
            if not session.get('authenticated'):
                return jsonify({'error': 'Authentication required'}), 401

            # Fetch lightweight avatar (cached), do not load full employee payload
            ok_img, img = employee_service.get_current_user_avatar(size=128)
            if ok_img and img:
                # Detect mime type from payload to avoid mismatched data URLs (e.g., SVG placeholder)
                mime = 'image/jpeg'
                try:
                    import base64
                    data_bytes = base64.b64decode(img, validate=True)
                    head = data_bytes[:16]
                    # JPEG
                    if head.startswith(b'\xFF\xD8\xFF'):
                        mime = 'image/jpeg'
                    # PNG
                    elif head.startswith(b'\x89PNG\r\n\x1a\n'):
                        mime = 'image/png'
                    # WEBP (RIFF....WEBP)
                    elif data_bytes[:4] == b'RIFF' and data_bytes[8:12] == b'WEBP':
                        mime = 'image/webp'
                    else:
                        # SVG if XML text
                        trimmed = data_bytes.lstrip()
                        if trimmed.startswith(b'<?xml') or trimmed.startswith(b'<svg'):
                            mime = 'image/svg+xml'
                except Exception:
                    pass
                return jsonify({
                    'success': True,
                    'image_data': img,
                    'content_type': mime
                })
            return jsonify({ 'success': False, 'message': 'No avatar image available' }), 404
        except Exception as e:
            return jsonify({
                'success': False,
                'message': f'Error retrieving avatar: {str(e)}'
            }), 500

    @app.route('/api/ping', methods=['GET'])
    def ping_session():
        """Lightweight keepalive to keep Odoo session fresh while user is active."""
        try:
            if not session.get('authenticated'):
                return jsonify({'success': False, 'message': 'Not authenticated'}), 401
            ok, msg = odoo_service.ensure_active_session()
            if not ok:
                return jsonify({'success': False, 'message': msg}), 500
            # Perform an extremely lightweight call to refresh server-side session TTL
            try:
                url = f"{odoo_service.odoo_url}/web/dataset/call_kw"
                payload = {
                    "jsonrpc": "2.0",
                    "method": "call",
                    "params": {
                        "model": "res.users",
                        "method": "read",
                        "args": [[odoo_service.user_id]],
                        "kwargs": {"fields": ["id"]}
                    },
                    "id": 1
                }
                cookies = {'session_id': odoo_service.session_id} if odoo_service.session_id else {}
                http = getattr(odoo_service, 'http', None)
                (http or __import__('requests')).post(url, json=payload, cookies=cookies, timeout=6)
            except Exception:
                pass
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'success': False, 'message': str(e)}), 500

    @app.route('/api/debug/user-data', methods=['GET'])
    def debug_user_data():
        """Debug endpoint to show current user data and Odoo connection status"""
        try:
            debug_info = {
                'session_data': dict(session),
                'odoo_authenticated': odoo_service.is_authenticated(),
                'odoo_user_info': odoo_service.get_user_info(),
                'employee_data': None,
                'employee_data_success': False,
                'employee_data_error': None,
                'raw_odoo_response': None
            }
            
            # Only try to fetch employee data if user is properly authenticated
            if session.get('authenticated') and odoo_service.is_authenticated():
                debug_log("User is authenticated, fetching employee data for debug...", "bot_logic")
                
                # Test if Odoo session is still valid
                session_valid, session_message = odoo_service.test_session_validity()
                if not session_valid:
                    debug_info['employee_data_error'] = f"Odoo session invalid: {session_message}"
                    debug_log(f"Odoo session is invalid: {session_message}", "bot_logic")
                else:
                    # First, let's try a simple test to see if we can make any Odoo request
                    try:
                        import requests
                        url = f"{odoo_service.odoo_url}/web/dataset/call_kw"
                        test_data = {
                            "jsonrpc": "2.0",
                            "method": "call",
                            "params": {
                                "model": "res.users",
                                "method": "read",
                                "args": [[odoo_service.user_id]],
                                "kwargs": {"fields": ["name", "login", "email"]}
                            },
                            "id": 1
                        }
                        cookies = {'session_id': odoo_service.session_id} if odoo_service.session_id else {}
                        
                        debug_log(f"Testing simple Odoo request...", "odoo_data")
                        debug_log(f"URL: {url}", "odoo_data")
                        debug_log(f"Cookies: {cookies}", "odoo_data")
                        
                        test_response = requests.post(
                            url,
                            json=test_data,
                            headers={'Content-Type': 'application/json'},
                            cookies=cookies,
                            timeout=10
                        )
                        
                        debug_log(f"Test response status: {test_response.status_code}", "odoo_data")
                        debug_log(f"Test response text: {test_response.text[:200]}...", "odoo_data")
                        
                        debug_info['raw_odoo_response'] = {
                            'status_code': test_response.status_code,
                            'response_text': test_response.text[:500]
                        }
                        
                    except Exception as test_e:
                        debug_log(f"Test request failed: {test_e}", "odoo_data")
                        debug_info['raw_odoo_response'] = {'error': str(test_e)}
                    
                    # Use cached employee data to avoid duplicate API calls
                    employee_success, employee_data = employee_service.get_current_user_employee_data()
                    debug_info['employee_data_success'] = employee_success

                    if employee_success:
                        debug_info['employee_data'] = employee_data
                        debug_log(f"Employee data fetched successfully: {employee_data.get('name', 'Unknown')}", "bot_logic")
                    else:
                        debug_info['employee_data_error'] = employee_data
                        debug_log(f"Employee data fetch failed: {employee_data}", "bot_logic")
            else:
                debug_info['employee_data_error'] = "User not authenticated - skipping employee data fetch"
                debug_log("User not authenticated - skipping employee data fetch", "bot_logic")
            
            return jsonify({
                'success': True,
                'debug_info': debug_info
            })
            
        except Exception as e:
            debug_log(f"Error in debug endpoint: {e}", "general")
            import traceback
            traceback.print_exc()
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500

    @app.route('/api/auth/auto-login-diagnostics', methods=['GET'])
    def auto_login_diagnostics():
        """Diagnostic endpoint to view auto-login token statistics"""
        try:
            if not session.get('authenticated'):
                return jsonify({
                    'success': False,
                    'message': 'Authentication required'
                }), 401

            username = session.get('username')
            if not username:
                return jsonify({
                    'success': False,
                    'message': 'Username not found in session'
                }), 400

            # Get all tokens for this user
            user_tokens = remember_me_service.get_user_tokens(username)
            
            # Calculate statistics
            total_tokens = len(user_tokens)
            tokens_with_last_used = len([t for t in user_tokens if t.get('last_used_at')])
            tokens_without_last_used = total_tokens - tokens_with_last_used
            
            # Find tokens that are old but never used
            from datetime import datetime, timedelta
            cutoff_date = datetime.utcnow() - timedelta(days=30)
            old_unused_tokens = []
            for token in user_tokens:
                if token.get('last_used_at') is None:
                    created_at_str = token.get('created_at')
                    if created_at_str:
                        try:
                            created_at = datetime.fromisoformat(created_at_str.replace('Z', '+00:00'))
                            if created_at < cutoff_date:
                                old_unused_tokens.append({
                                    'id': token.get('id'),
                                    'created_at': created_at_str,
                                    'device_fingerprint': token.get('device_fingerprint', '')[:16] + '...'
                                })
                        except Exception:
                            pass

            return jsonify({
                'success': True,
                'username': username,
                'statistics': {
                    'total_tokens': total_tokens,
                    'tokens_with_last_used': tokens_with_last_used,
                    'tokens_without_last_used': tokens_without_last_used,
                    'old_unused_tokens_count': len(old_unused_tokens)
                },
                'tokens': [
                    {
                        'id': t.get('id'),
                        'device_fingerprint': t.get('device_fingerprint', '')[:16] + '...',
                        'created_at': t.get('created_at'),
                        'last_used_at': t.get('last_used_at'),
                        'is_old_unused': t.get('id') in [ot['id'] for ot in old_unused_tokens]
                    }
                    for t in user_tokens
                ],
                'old_unused_tokens': old_unused_tokens
            })
        except Exception as e:
            debug_log(f"Error in auto-login diagnostics: {str(e)}", "bot_logic")
            import traceback
            debug_log(f"Traceback: {traceback.format_exc()}", "bot_logic")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500
    
    return app

if __name__ == '__main__':
    app = create_app()
    # Disable the dev auto-reloader to prevent in-memory Odoo session loss
    app.run(debug=False, host='0.0.0.0', port=5000, use_reloader=False)


