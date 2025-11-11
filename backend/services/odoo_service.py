import requests
import json
import time
from typing import Dict, Optional, Tuple
try:
    from ..config.settings import Config
except Exception:
    from config.settings import Config

class OdooService:
    """Service for Odoo API integration and authentication"""
    
    def __init__(self):
        self.odoo_url = Config.ODOO_URL
        self.odoo_db = Config.ODOO_DB
        self.session_id = None
        self.user_id = None
        self.username = None
        self.password = None  # Store for re-authentication
        self.last_activity = None
        self.session_timeout = 7200  # 2 hours in seconds (Odoo default)
        # Reuse HTTP connections across requests for lower latency
        try:
            self.http = requests.Session()
            adapter = requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=20)
            self.http.mount('http://', adapter)
            self.http.mount('https://', adapter)
            self.http.headers.update({'Content-Type': 'application/json'})
        except Exception:
            self.http = requests
    
    def authenticate(self, username: str, password: str) -> Tuple[bool, str, Optional[Dict]]:
        """
        Authenticate user with Odoo (stateless - returns session data instead of storing)

        Args:
            username: Odoo username
            password: Odoo password

        Returns:
            Tuple of (success: bool, message: str, session_data: dict or None)
            session_data contains: {'session_id', 'user_id', 'username', 'password'}
        """
        try:
            # Odoo authentication endpoint
            auth_url = f"{self.odoo_url}/web/session/authenticate"
            print(f"DEBUG ODOO AUTH: Attempting authentication for {username} against {auth_url} (DB: {self.odoo_db})")

            # Prepare authentication data
            auth_data = {
                "jsonrpc": "2.0",
                "method": "call",
                "params": {
                    "db": self.odoo_db,
                    "login": username,
                    "password": password
                },
                "id": 1
            }

            # Make authentication request
            response = self.http.post(
                auth_url,
                json=auth_data,
                timeout=10
            )

            if response.status_code == 200:
                result = response.json()
                
                # Check for Odoo error response
                if 'error' in result:
                    error_data = result.get('error', {})
                    error_msg = error_data.get('message', 'Unknown error')
                    error_data_details = error_data.get('data', {})
                    print(f"DEBUG ODOO AUTH ERROR: {error_msg} - Details: {error_data_details}")
                    return False, f"Odoo error: {error_msg}", None

                if 'result' in result and result['result']:
                    # Authentication successful - return session data without storing
                    session_id = response.cookies.get('session_id')
                    if not session_id:
                        print(f"DEBUG ODOO AUTH WARNING: No session_id in cookies. Response cookies: {response.cookies}")
                        # Try to get from Set-Cookie header
                        set_cookie = response.headers.get('Set-Cookie', '')
                        if 'session_id=' in set_cookie:
                            session_id = set_cookie.split('session_id=')[1].split(';')[0]
                    
                    session_data = {
                        'session_id': session_id,
                        'user_id': result['result'].get('uid'),
                        'username': username,
                        'password': password,  # For re-authentication
                        'last_activity': time.time()
                    }

                    # Deprecated: Keep for backward compatibility during migration
                    self.username = username
                    self.password = password
                    self.user_id = session_data['user_id']
                    self.session_id = session_data['session_id']
                    self.last_activity = session_data['last_activity']

                    return True, "Authentication successful", session_data
                else:
                    # Authentication failed - log the actual response for debugging
                    print(f"DEBUG ODOO AUTH FAILED: Odoo returned 200 but result is empty. Full response: {result}")
                    return False, "Invalid username or password", None
            else:
                print(f"DEBUG ODOO AUTH HTTP ERROR: Status {response.status_code}, Response: {response.text[:500]}")
                return False, f"Connection error: {response.status_code}", None

        except requests.exceptions.Timeout:
            return False, "Connection timeout. Please check your internet connection.", None
        except requests.exceptions.ConnectionError:
            return False, "Unable to connect to Odoo server. Please check the URL.", None
        except Exception as e:
            return False, f"Authentication error: {str(e)}", None
    
    def is_authenticated(self) -> bool:
        """Check if user is currently authenticated"""
        return self.session_id is not None and self.user_id is not None
    
    def test_session_validity(self) -> Tuple[bool, str]:
        """Test if the current Odoo session is still valid"""
        try:
            if not self.is_authenticated():
                return False, "Not authenticated"
            
            # Try to make a simple request to test session validity
            url = f"{self.odoo_url}/web/dataset/call_kw"
            test_data = {
                "jsonrpc": "2.0",
                "method": "call",
                "params": {
                    "model": "res.users",
                    "method": "read",
                    "args": [[self.user_id]],
                    "kwargs": {"fields": ["name", "login"]}
                },
                "id": 1
            }
            
            cookies = {'session_id': self.session_id} if self.session_id else {}
            
            response = self.http.post(
                url,
                json=test_data,
                cookies=cookies,
                timeout=10
            )
            
            if response.status_code == 200:
                result = response.json()
                if 'result' in result and result['result']:
                    return True, "Session is valid"
                else:
                    return False, "Session expired or invalid"
            else:
                return False, f"HTTP error: {response.status_code}"
                
        except Exception as e:
            return False, f"Session test error: {str(e)}"
    
    def get_user_info(self) -> Optional[Dict]:
        """Get current user information"""
        if not self.is_authenticated():
            return None
            
        return {
            'user_id': self.user_id,
            'username': self.username,
            'database': self.odoo_db,
            'server_url': self.odoo_url
        }
    
    def logout(self):
        """Logout current user"""
        self.session_id = None
        self.user_id = None
        self.username = None
        self.password = None
        self.last_activity = None
    
    def test_connection(self) -> Tuple[bool, str]:
        """
        Test connection to Odoo server without authentication
        
        Returns:
            Tuple of (success: bool, message: str)
        """
        try:
            # Try to access Odoo web interface
            response = requests.get(f"{self.odoo_url}/web", timeout=5)
            
            if response.status_code == 200:
                return True, "Connection to Odoo server successful"
            else:
                return False, f"Server responded with status: {response.status_code}"
                
        except requests.exceptions.Timeout:
            return False, "Connection timeout"
        except requests.exceptions.ConnectionError:
            return False, "Unable to connect to Odoo server"
        except Exception as e:
            return False, f"Connection error: {str(e)}"

    def _should_renew_session(self) -> bool:
        """Check if session should be renewed based on last activity"""
        if not self.last_activity:
            return False

        time_since_activity = time.time() - self.last_activity
        # Renew if session is 50% expired (about 1 hour for 2-hour session) for more proactive renewal
        return time_since_activity > (self.session_timeout * 0.5)

    def _renew_session(self) -> Tuple[bool, str]:
        """Renew the Odoo session"""
        if not self.username or not self.password:
            return False, "Cannot renew session: credentials not stored"

        return self.authenticate(self.username, self.password)

    def ensure_active_session(self) -> Tuple[bool, str]:
        """Ensure session is active, renew if necessary"""
        try:
            # First check if we have basic authentication data
            if not self.is_authenticated():
                return False, "Not authenticated"

            # Update last activity time
            self.last_activity = time.time()

            # Check if session needs renewal based on time
            if self._should_renew_session():
                success, message = self._renew_session()
                if not success:
                    # Don't return error yet, try testing session validity
                    pass
                else:
                    return True, "Session renewed proactively"

            # Test current session validity regardless of time-based renewal
            valid, message = self.test_session_validity()
            if not valid:
                if not self.username or not self.password:
                    return False, "Session invalid and cannot renew (missing credentials)"

                success, renew_message = self._renew_session()
                if not success:
                    return False, f"Session renewal failed: {renew_message}"

                # Test again after renewal
                valid_after_renewal, test_message = self.test_session_validity()
                if not valid_after_renewal:
                    return False, f"Session still invalid after renewal: {test_message}"

                return True, "Session renewed after invalidity"

            return True, "Session is active"

        except Exception as e:
            return False, f"Session check error: {str(e)}"

    def post_with_retry(self, url: str, json: dict, cookies: dict, timeout: int = 20):
        """POST helper that retries once after attempting session renewal if 401/invalid."""
        client = getattr(self, 'http', requests)
        try:
            resp = client.post(url, json=json, cookies=cookies, timeout=timeout)
            # Case 1: HTTP auth errors → renew and retry
            if resp.status_code in (401, 403):
                # try renewal
                ok, _ = self._renew_session()
                if ok:
                    cookies = {'session_id': self.session_id} if self.session_id else {}
                    return client.post(url, json=json, cookies=cookies, timeout=timeout)
            # Case 2: Odoo returns 200 with JSON error payload indicating session expiry
            if resp.status_code == 200:
                try:
                    body = resp.json()
                    err = body.get('error') if isinstance(body, dict) else None
                    if isinstance(err, dict):
                        name = str(err.get('data', {}).get('name') or err.get('name') or '').lower()
                        msg = str(err.get('data', {}).get('message') or err.get('message') or '').lower()
                        code = str(err.get('code') or '')
                        session_expired = (
                            'session expired' in msg or
                            'sessionexpiredexception' in name or
                            code == '100'
                        )
                        if session_expired:
                            ok, _ = self._renew_session()
                            if ok:
                                cookies = {'session_id': self.session_id} if self.session_id else {}
                                return client.post(url, json=json, cookies=cookies, timeout=timeout)
                except Exception:
                    # If parsing fails, fall through and return original response
                    pass
            return resp
        except Exception:
            # best-effort retry after renewal
            ok, _ = self._renew_session()
            cookies = {'session_id': self.session_id} if self.session_id else {}
            return client.post(url, json=json, cookies=cookies, timeout=timeout)

    # ========== NEW STATELESS METHODS ==========

    def test_session_validity_with_session(self, session_id: str, user_id: int) -> Tuple[bool, str]:
        """Test if an Odoo session is valid (stateless version)"""
        try:
            if not session_id or not user_id:
                return False, "Missing session_id or user_id"

            url = f"{self.odoo_url}/web/dataset/call_kw"
            test_data = {
                "jsonrpc": "2.0",
                "method": "call",
                "params": {
                    "model": "res.users",
                    "method": "read",
                    "args": [[user_id]],
                    "kwargs": {"fields": ["name", "login"]}
                },
                "id": 1
            }

            cookies = {'session_id': session_id}

            # Use requests directly to avoid shared session pollution
            response = requests.post(
                url,
                json=test_data,
                cookies=cookies,
                timeout=10
            )

            if response.status_code == 200:
                result = response.json()
                if 'result' in result and result['result']:
                    return True, "Session is valid"
                else:
                    return False, "Session expired or invalid"
            else:
                return False, f"HTTP error: {response.status_code}"

        except Exception as e:
            return False, f"Session test error: {str(e)}"

    def renew_session_with_credentials(self, username: str, password: str) -> Tuple[bool, str, Optional[Dict]]:
        """Renew session by re-authenticating (stateless version)"""
        return self.authenticate(username, password)

    def make_authenticated_request(self, model: str, method: str, args: list, kwargs: dict,
                                   session_id: str, user_id: int,
                                   username: Optional[str] = None, password: Optional[str] = None) -> Dict:
        """
        Make an authenticated Odoo API request (stateless version)

        Args:
            model: Odoo model name (e.g., 'hr.employee')
            method: Method to call (e.g., 'search_read', 'create')
            args: Positional arguments for the method
            kwargs: Keyword arguments for the method
            session_id: Odoo session ID
            user_id: Odoo user ID
            username: Username for session renewal (optional)
            password: Password for session renewal (optional)

        Returns:
            Response dict from Odoo API
            If session was renewed, includes '_renewed_session' key with new session data
        """
        url = f"{self.odoo_url}/web/dataset/call_kw"
        request_data = {
            "jsonrpc": "2.0",
            "method": "call",
            "params": {
                "model": model,
                "method": method,
                "args": args,
                "kwargs": kwargs
            },
            "id": 1
        }

        cookies = {'session_id': session_id}
        renewed_session_data = None
        # Use requests directly (not self.http) to avoid shared session cookie pollution
        # self.http is a shared Session object that can cache cookies from other users

        try:
            response = requests.post(url, json=request_data, cookies=cookies, timeout=20)

            # Check for auth errors and retry with renewal if credentials provided
            if response.status_code in (401, 403) and username and password:
                success, msg, new_session_data = self.renew_session_with_credentials(username, password)
                if success and new_session_data:
                    renewed_session_data = new_session_data
                    cookies = {'session_id': new_session_data['session_id']}
                    response = requests.post(url, json=request_data, cookies=cookies, timeout=20)

            # Check for Odoo session expiry errors
            if response.status_code == 200:
                result = response.json()
                err = result.get('error') if isinstance(result, dict) else None
                if isinstance(err, dict) and username and password:
                    name = str(err.get('data', {}).get('name') or err.get('name') or '').lower()
                    msg = str(err.get('data', {}).get('message') or err.get('message') or '').lower()
                    session_expired = 'session expired' in msg or 'sessionexpiredexception' in name

                    if session_expired:
                        success, msg, new_session_data = self.renew_session_with_credentials(username, password)
                        if success and new_session_data:
                            renewed_session_data = new_session_data
                            cookies = {'session_id': new_session_data['session_id']}
                            response = requests.post(url, json=request_data, cookies=cookies, timeout=20)

            final_result = response.json() if response.status_code == 200 else {'error': f'HTTP {response.status_code}'}

            # CRITICAL: Include renewed session data so caller can update Flask session
            if renewed_session_data:
                final_result['_renewed_session'] = renewed_session_data

            return final_result

        except Exception as e:
            return {'error': str(e)}
