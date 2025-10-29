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
    
    def authenticate(self, username: str, password: str) -> Tuple[bool, str]:
        """
        Authenticate user with Odoo
        
        Args:
            username: Odoo username
            password: Odoo password
            
        Returns:
            Tuple of (success: bool, message: str)
        """
        try:
            # Odoo authentication endpoint
            auth_url = f"{self.odoo_url}/web/session/authenticate"
            
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
                
                if 'result' in result and result['result']:
                    # Authentication successful
                    self.username = username
                    self.password = password  # Store for re-authentication
                    self.user_id = result['result'].get('uid')
                    self.session_id = response.cookies.get('session_id')
                    self.last_activity = time.time()

                    return True, "Authentication successful"
                else:
                    # Authentication failed
                    return False, "Invalid username or password"
            else:
                return False, f"Connection error: {response.status_code}"
                
        except requests.exceptions.Timeout:
            return False, "Connection timeout. Please check your internet connection."
        except requests.exceptions.ConnectionError:
            return False, "Unable to connect to Odoo server. Please check the URL."
        except Exception as e:
            return False, f"Authentication error: {str(e)}"
    
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
        # Renew if session is 80% expired (about 1.6 hours for 2-hour session)
        return time_since_activity > (self.session_timeout * 0.8)

    def _renew_session(self) -> Tuple[bool, str]:
        """Renew the Odoo session"""
        if not self.username or not self.password:
            return False, "Cannot renew session: credentials not stored"

        print(f"DEBUG: Renewing Odoo session for user {self.username}")
        return self.authenticate(self.username, self.password)

    def ensure_active_session(self) -> Tuple[bool, str]:
        """Ensure session is active, renew if necessary"""
        try:
            # First check if we have basic authentication data
            if not self.is_authenticated():
                print("DEBUG: Not authenticated - cannot ensure session")
                return False, "Not authenticated"

            # Update last activity time
            self.last_activity = time.time()

            # Check if session needs renewal based on time
            if self._should_renew_session():
                print("DEBUG: Session approaching expiry, attempting proactive renewal...")
                success, message = self._renew_session()
                if not success:
                    print(f"DEBUG: Proactive session renewal failed: {message}")
                    # Don't return error yet, try testing session validity
                else:
                    print("DEBUG: Proactive session renewal successful")
                    return True, "Session renewed proactively"

            # Test current session validity regardless of time-based renewal
            print("DEBUG: Testing current session validity...")
            valid, message = self.test_session_validity()
            if not valid:
                print(f"DEBUG: Session invalid ({message}), attempting reactive renewal...")
                if not self.username or not self.password:
                    print("DEBUG: Cannot renew - missing credentials")
                    return False, "Session invalid and cannot renew (missing credentials)"

                success, renew_message = self._renew_session()
                if not success:
                    print(f"DEBUG: Reactive session renewal failed: {renew_message}")
                    return False, f"Session renewal failed: {renew_message}"
                print("DEBUG: Reactive session renewal successful")

                # Test again after renewal
                valid_after_renewal, test_message = self.test_session_validity()
                if not valid_after_renewal:
                    print(f"DEBUG: Session still invalid after renewal: {test_message}")
                    return False, f"Session still invalid after renewal: {test_message}"

                return True, "Session renewed after invalidity"

            print("DEBUG: Session is valid and active")
            return True, "Session is active"

        except Exception as e:
            print(f"DEBUG: Error in ensure_active_session: {e}")
            import traceback
            traceback.print_exc()
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
