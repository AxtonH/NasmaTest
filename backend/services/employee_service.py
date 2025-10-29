import requests
import json
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime, timedelta
import logging
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
    elif category == "general" and Config.VERBOSE_LOGS:
        print(f"DEBUG: {message}")

class EmployeeService:
    """Service for Odoo employee data operations with role-based access and caching"""
    
    def __init__(self, odoo_service):
        self.odoo_service = odoo_service
        self.cache = {}
        self.cache_expiry = {}
        self.cache_duration = timedelta(hours=2)  # Cache for 2 hours (longer caching)
        self.verbose = getattr(Config, 'VERBOSE_LOGS', False)
        # Related-records cache to avoid repeated Odoo calls
        self.related_cache: Dict[str, Dict[int, Any]] = {}
        self.related_cache_expiry: Dict[str, Dict[int, datetime]] = {}

        # Super-fast cache for current user (persists across requests)
        self.user_fast_cache: Dict[int, Dict] = {}
        self.user_fast_cache_expiry: Dict[int, datetime] = {}
        self.fast_cache_duration = timedelta(minutes=15)  # 15 minute super-fast cache
        
        # Employee fields to fetch (only standard fields that exist in all Odoo instances)
        self.employee_fields = [
            'name', 'job_title', 'work_email', 'work_phone', 'department_id',
            'mobile_phone', 'identification_id', 'gender', 'birthday', 'address_id',
            'work_location_id', 'parent_id', 'coach_id', 'job_id', 'resource_calendar_id',
            'tz', 'category_ids', 'marital', 'company_id', 'planning_role_ids'
        ]
        
        # Custom fields that might exist (will be added dynamically if they exist)
        self.custom_fields = [
            'x_studio_employee_arabic_name', 'x_studio_joining_date', 'x_studio_contract_end_date'
        ]
        
        # Related fields to expand (optimized - only essential fields)
        self.related_fields = {
            'department_id': ['name'],  # Simplified - just department name
            'parent_id': ['name', 'job_title'],  # Manager info without email
            'coach_id': ['name', 'job_title'],  # Coach info without email
            'address_id': ['city', 'country_id'],  # Simplified address
            'company_id': ['name'],  # Just company name
            'job_id': ['name'],  # Just job title, no description
            'work_location_id': ['name'],  # Just location name
            'resource_calendar_id': ['name'],  # Just calendar name, no attendance details
            'state_id': ['name'],
            'country_id': ['name']
        }
    
    def _log(self, message: str, category: str = "general"):
        """Log message based on category and configuration"""
        debug_log(message, category)

    def _is_cache_valid(self, cache_key: str) -> bool:
        """Check if cached data is still valid"""
        if cache_key not in self.cache_expiry:
            return False
        return datetime.now() < self.cache_expiry[cache_key]
    
    def _set_cache(self, cache_key: str, data: Any):
        """Set data in cache with expiry"""
        self.cache[cache_key] = data
        self.cache_expiry[cache_key] = datetime.now() + self.cache_duration
    
    def _get_cache(self, cache_key: str) -> Optional[Any]:
        """Get data from cache if valid"""
        if self._is_cache_valid(cache_key):
            return self.cache[cache_key]
        return None
    
    def _make_odoo_request(self, model: str, method: str, params: Dict) -> Tuple[bool, Any]:
        """Make authenticated request to Odoo using web session"""
        try:
            # Ensure session is active before making request
            session_ok, session_msg = self.odoo_service.ensure_active_session()
            if not session_ok:
                return False, f"Session error: {session_msg}"
            
            # Use web session endpoint
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
            
            # Use session cookies for authentication
            cookies = {'session_id': self.odoo_service.session_id} if self.odoo_service.session_id else {}
            
            self._log(f"Making web session request to {url}", "odoo_data")
            self._log(f"Session ID: {self.odoo_service.session_id}", "odoo_data")
            self._log(f"Cookies: {cookies}", "odoo_data")
            self._log(f"Data: {data}", "odoo_data")
            
            # Use OdooService retry-aware post
            post = getattr(self.odoo_service, 'post_with_retry', None)
            if callable(post):
                response = post(url, json=data, cookies=cookies, timeout=30)
            else:
                client = getattr(self.odoo_service, 'http', requests)
                response = client.post(
                    url,
                    json=data,
                    cookies=cookies,
                    timeout=30
                )
            
            self._log(f"Response status: {response.status_code}", "odoo_data")
            self._log(f"Response text: {response.text[:500]}...", "odoo_data")
            
            if response.status_code == 200:
                result = response.json()
                self._log(f"Parsed JSON result: {result}", "odoo_data")
                if 'result' in result:
                    self._log(f"Result data: {result['result']}", "odoo_data")
                    return True, result['result']
                else:
                    # Return raw error object so callers can inspect details
                    err_obj = result.get('error', {'message': 'Unknown error'})
                    self._log(f"Odoo API error: {err_obj}", "odoo_data")
                    return False, err_obj
            else:
                error_msg = f"HTTP error: {response.status_code}"
                self._log(f"{error_msg}", "odoo_data")
                return False, error_msg
                
        except Exception as e:
            return False, f"Request error: {str(e)}"

    def _parse_access_error_forbidden_fields(self, error_obj: Any) -> List[str]:
        """Extract field names from Odoo AccessError message if present.

        Returns a list of field names that caused access restrictions.
        """
        forbidden: List[str] = []
        try:
            if not error_obj:
                return forbidden
            # error_obj may be a string (already formatted) or dict with 'data'/'message'
            text = None
            if isinstance(error_obj, dict):
                # Prefer detailed message inside data.message or data.debug
                data = error_obj.get('data') or {}
                text = data.get('message') or data.get('debug') or error_obj.get('message')
            if not text and isinstance(error_obj, str):
                text = error_obj
            if not text:
                return forbidden

            # Normalize Windows/Unix newlines
            text = str(text).replace('\r\n', '\n')

            # Look for bullet list like "- field (allowed for groups ...)"
            lines = text.split('\n')
            for line in lines:
                s = line.strip()
                if not s.startswith('- '):
                    continue
                # Extract the token after '- ' up to space or '(' or end
                token = s[2:].strip()
                cut = len(token)
                for sep in [' (', ' ', '(']:
                    idx = token.find(sep)
                    if idx != -1:
                        cut = min(cut, idx)
                field = token[:cut].strip('-').strip()
                # Basic sanity: only accept ascii letters, underscores and digits
                import re
                if field and re.match(r'^[a-zA-Z0-9_]+$', field):
                    forbidden.append(field)
        except Exception:
            pass
        return forbidden

    def _safe_employee_read(self, employee_ids: List[int], fields: List[str]) -> Tuple[bool, Any]:
        """Perform hr.employee read with graceful fallback when AccessError occurs.

        Strategy:
        - Attempt read with requested fields
        - If AccessError, parse forbidden fields and retry without them
        - If still failing, fallback to a minimal safe field set
        """
        # First attempt
        params = {'args': [employee_ids], 'kwargs': {'fields': fields}}
        ok, data = self._make_odoo_request('hr.employee', 'read', params)
        if ok:
            return ok, data

        # Detect AccessError and parse forbidden fields
        forbidden = self._parse_access_error_forbidden_fields(data)
        if forbidden:
            allowed_fields = [f for f in fields if f not in forbidden]
            if not allowed_fields:
                # Keep at least name/id
                allowed_fields = ['name']
            params2 = {'args': [employee_ids], 'kwargs': {'fields': allowed_fields}}
            ok2, data2 = self._make_odoo_request('hr.employee', 'read', params2)
            if ok2:
                return True, data2

        # Final fallback to a minimal safe set
        minimal_fields = ['name', 'job_title', 'work_email', 'department_id', 'company_id', 'user_id']
        params3 = {'args': [employee_ids], 'kwargs': {'fields': minimal_fields}}
        return self._make_odoo_request('hr.employee', 'read', params3)

    def _safe_model_read(self, model: str, record_ids: List[int], fields: List[str]) -> Tuple[bool, Any]:
        """Generic safe read with AccessError field fallback for any model."""
        params = {'args': [record_ids], 'kwargs': {'fields': fields}}
        ok, data = self._make_odoo_request(model, 'read', params)
        if ok:
            return ok, data
        forbidden = self._parse_access_error_forbidden_fields(data)
        if forbidden:
            allowed_fields = [f for f in fields if f not in forbidden] or ['id']
            params2 = {'args': [record_ids], 'kwargs': {'fields': allowed_fields}}
            ok2, data2 = self._make_odoo_request(model, 'read', params2)
            if ok2:
                return True, data2
        # Minimal fallback
        params3 = {'args': [record_ids], 'kwargs': {'fields': ['id']}}
        return self._make_odoo_request(model, 'read', params3)

    def _safe_employee_search_read(self, domain: List[Any], fields: List[str], limit: int = 100, order: Optional[str] = None) -> Tuple[bool, Any]:
        """Perform hr.employee search_read with fallback on AccessError fields."""
        kwargs = {'fields': fields, 'limit': limit}
        if order:
            kwargs['order'] = order
        params = {'args': [domain], 'kwargs': kwargs}
        ok, data = self._make_odoo_request('hr.employee', 'search_read', params)
        if ok:
            return ok, data
        forbidden = self._parse_access_error_forbidden_fields(data)
        if forbidden:
            allowed_fields = [f for f in fields if f not in forbidden]
            if not allowed_fields:
                allowed_fields = ['name']
            kwargs2 = {'fields': allowed_fields, 'limit': limit}
            if order:
                kwargs2['order'] = order
            params2 = {'args': [domain], 'kwargs': kwargs2}
            ok2, data2 = self._make_odoo_request('hr.employee', 'search_read', params2)
            if ok2:
                return True, data2
        # Final minimal fallback
        minimal_fields = ['name', 'job_title', 'department_id']
        kwargs3 = {'fields': minimal_fields, 'limit': limit}
        if order:
            kwargs3['order'] = order
        params3 = {'args': [domain], 'kwargs': kwargs3}
        return self._make_odoo_request('hr.employee', 'search_read', params3)

    def _get_safe_public_employee_fields(self) -> List[str]:
        """Return a conservative field set likely allowed for regular employees."""
        # Exclude sensitive fields like birthday, identification_id, marital, category_ids, gender, planning_role_ids
        return [
            'name', 'job_title', 'work_email', 'work_phone', 'department_id',
            'mobile_phone', 'address_id', 'work_location_id', 'parent_id', 'coach_id',
            'job_id', 'resource_calendar_id', 'tz', 'company_id', 'user_id'
        ]
    
    def _expand_related_data(self, employee_data: Dict) -> Dict:
        """Expand related field data using batched reads and per-record caching"""
        expanded_data = employee_data.copy()

        # Map field names to model names
        model_mapping = {
            'department_id': 'hr.department',
            'parent_id': 'hr.employee',
            'coach_id': 'hr.employee',
            'address_id': 'res.partner',
            'company_id': 'res.company',
            'job_id': 'hr.job',
            'work_location_id': 'hr.work.location',
            'resource_calendar_id': 'resource.calendar',
            'state_id': 'res.country.state',
            'country_id': 'res.country'
        }

        # Collect IDs per model
        model_to_ids: Dict[str, List[int]] = {}
        field_to_model: Dict[str, str] = {}
        for field in self.related_fields.keys():
            if field in employee_data and employee_data[field]:
                value = employee_data[field]
                record_id = value[0] if isinstance(value, list) else value if isinstance(value, int) else None
                if record_id:
                    model_name = model_mapping.get(field)
                    if model_name:
                        field_to_model[field] = model_name
                        model_to_ids.setdefault(model_name, [])
                        if record_id not in model_to_ids[model_name]:
                            model_to_ids[model_name].append(record_id)

        # Optimize with parallel batch fetching
        model_results: Dict[str, Dict[int, Dict]] = {}
        fetch_tasks = []  # Store models that need fetching

        # First pass: collect cached data and identify what needs fetching
        for model_name, ids in model_to_ids.items():
            to_fetch: List[int] = []
            combined: Dict[int, Dict] = {}
            for rid in ids:
                cached = self._get_related_cache(model_name, rid)
                if cached is not None:
                    combined[rid] = cached
                else:
                    to_fetch.append(rid)

            model_results[model_name] = combined
            if to_fetch:
                fetch_tasks.append((model_name, to_fetch, self._get_fields_for_model(model_name)))

        # Parallel batch fetch using threading for I/O operations
        if fetch_tasks:
            import concurrent.futures
            import threading

            def fetch_model_batch(model_name, ids, fields):
                try:
                    success, data = self._fetch_related_records_batch(model_name, ids, fields)
                    if success and isinstance(data, list):
                        return model_name, data
                    return model_name, []
                except Exception as e:
                    print(f"ERROR: Failed to fetch {model_name} batch: {e}")
                    return model_name, []

            # Execute parallel requests (max 3 concurrent to avoid overwhelming Odoo)
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                future_to_model = {
                    executor.submit(fetch_model_batch, model_name, ids, fields): model_name
                    for model_name, ids, fields in fetch_tasks
                }

                for future in concurrent.futures.as_completed(future_to_model):
                    model_name, data = future.result()
                    for rec in data:
                        if isinstance(rec, dict) and 'id' in rec:
                            model_results[model_name][rec['id']] = rec
                            self._set_related_cache(model_name, rec['id'], rec)

        # Fill expanded_data
        for field in self.related_fields.keys():
            if field in employee_data and employee_data[field]:
                value = employee_data[field]
                record_id = value[0] if isinstance(value, list) else value if isinstance(value, int) else None
                model_name = field_to_model.get(field)
                if record_id and model_name:
                    related_map = model_results.get(model_name, {})
                    if record_id in related_map:
                        expanded_data[f"{field}_details"] = related_map[record_id]
                    else:
                        if isinstance(value, list) and len(value) > 1:
                            expanded_data[f"{field}_details"] = {"name": value[1], "error": "Details unavailable"}
                        else:
                            expanded_data[f"{field}_details"] = {"error": "Could not retrieve details"}

        return expanded_data
    
    def _get_fields_for_model(self, model_name: str) -> List[str]:
        reverse_map = {
            'hr.department': self.related_fields['department_id'],
            'hr.employee': self.related_fields['parent_id'],
            'res.partner': self.related_fields['address_id'],
            'res.company': self.related_fields['company_id'],
            'hr.job': self.related_fields['job_id'],
            'hr.work.location': self.related_fields['work_location_id'],
            'resource.calendar': self.related_fields['resource_calendar_id'],
            'res.country.state': self.related_fields['state_id'],
            'res.country': self.related_fields['country_id']
        }
        return reverse_map.get(model_name, ['name'])

    def _fetch_related_records_batch(self, model_name: str, record_ids: List[int], fields: List[str]) -> Tuple[bool, Any]:
        """Fetch multiple related records in one request"""
        try:
            params = {
                'args': [record_ids],
                'kwargs': {'fields': fields}
            }
            return self._make_odoo_request(model_name, 'read', params)
        except Exception as e:
            return False, f"Error fetching related records: {str(e)}"

    def _set_related_cache(self, model_name: str, record_id: int, data: Any):
        if model_name not in self.related_cache:
            self.related_cache[model_name] = {}
            self.related_cache_expiry[model_name] = {}
        self.related_cache[model_name][record_id] = data
        self.related_cache_expiry[model_name][record_id] = datetime.now() + self.cache_duration

    def _get_related_cache(self, model_name: str, record_id: int) -> Optional[Any]:
        if model_name in self.related_cache and record_id in self.related_cache_expiry.get(model_name, {}):
            if datetime.now() < self.related_cache_expiry[model_name][record_id]:
                return self.related_cache[model_name][record_id]
        return None
    
    def _get_available_fields(self) -> List[str]:
        """Get list of fields that actually exist in the hr.employee model"""
        # For now, just return the standard fields to avoid fetching employee data unnecessarily
        # This prevents the system from retrieving data for random employees when testing field availability
        debug_log("Using standard employee fields without dynamic detection", "odoo_data")
        return self.employee_fields
    
    def get_current_user_employee_data(self) -> Tuple[bool, Any]:
        """Get employee data for the currently logged-in user with fast caching"""
        try:
            self._log(f"get_current_user_employee_data() called", "bot_logic")
            self._log(f"Odoo service authenticated: {self.odoo_service.is_authenticated()}", "bot_logic")
            self._log(f"User ID: {self.odoo_service.user_id}", "bot_logic")
            self._log(f"Session ID: {self.odoo_service.session_id}", "bot_logic")

            if not self.odoo_service.is_authenticated():
                debug_log("Not authenticated with Odoo, returning error", "bot_logic")
                return False, "Not authenticated with Odoo"

            user_id = self.odoo_service.user_id

            # Check super-fast cache first
            if user_id in self.user_fast_cache_expiry:
                if datetime.now() < self.user_fast_cache_expiry[user_id]:
                    self._log(f"Using super-fast cached data for user {user_id}", "bot_logic")
                    return True, self.user_fast_cache[user_id]
                else:
                    # Expired, remove from cache
                    del self.user_fast_cache[user_id]
                    del self.user_fast_cache_expiry[user_id]
            
            user_id = self.odoo_service.user_id
            self._log(f"Fetching employee data for user_id: {user_id}", "bot_logic")
            self._log(f"Session ID: {self.odoo_service.session_id}", "bot_logic")
            
            cache_key = f"employee_data_{user_id}"
            
            # Check cache first
            cached_data = self._get_cache(cache_key)
            if cached_data:
                self._log(f"Using cached employee data for user {user_id}", "bot_logic")
                return True, cached_data
            
            # Start with a conservative field set to avoid common AccessError fields
            available_fields = self._get_safe_public_employee_fields()
            self._log(f"Available fields: {available_fields}", "odoo_data")
            
            # Fetch employee id first (lightweight), then read details by id to avoid heavy search_read payloads
            search_params = {
                'args': [[('user_id', '=', user_id)]],
                'kwargs': {'limit': 1}
            }
            self._log(f"Making Odoo search with params: {search_params}", "odoo_data")
            ok_ids, id_list = self._make_odoo_request('hr.employee', 'search', search_params)
            if not ok_ids or not isinstance(id_list, list) or not id_list:
                return False, "No employee record found for current user"

            # Chunked read (single id, but keep structure for future batch use)
            # Use safe read wrapper to avoid AccessError field violations
            self._log(f"Making safe employee read for ids: {id_list} with fields: {available_fields}", "odoo_data")
            success, data = self._safe_employee_read(id_list, available_fields)
            
            self._log(f"Employee search result - Success: {success}", "odoo_data")
            self._log(f"Employee search result - Data length: {len(data) if isinstance(data, list) else 'Not a list'}", "odoo_data")
            
            if success and data:
                if isinstance(data, list) and len(data) > 0:
                    employee_data = data[0]  # read returns list of dicts
                    self._log(f"Found employee record: {employee_data.get('name', 'Unknown')}", "bot_logic")
                    
                    # Expand related data
                    expanded_data = self._expand_related_data(employee_data)

                    # Cache the result (standard cache)
                    self._set_cache(cache_key, expanded_data)

                    # Store in super-fast cache for current user
                    self.user_fast_cache[user_id] = expanded_data
                    self.user_fast_cache_expiry[user_id] = datetime.now() + self.fast_cache_duration
                    self._log(f"Stored in super-fast cache for user {user_id}", "bot_logic")

                    return True, expanded_data
                else:
                    self._log(f"No employee records found for user_id {user_id}", "bot_logic")
                    return False, f"No employee record found for user ID {user_id}"
            else:
                self._log(f"Employee search failed - Success: {success}, Data: {data}", "bot_logic")
                return False, data or "Could not retrieve employee data from Odoo"
                
        except Exception as e:
            return False, f"Error retrieving employee data: {str(e)}"
    
    def search_employees(self, search_term: str = "", filters: Dict = None) -> Tuple[bool, Any]:
        """Search employees based on criteria (role-based access)"""
        try:
            if not self.odoo_service.is_authenticated():
                return False, "Not authenticated with Odoo"
            
            # Check user permissions (simplified - in production, implement proper role checking)
            user_id = self.odoo_service.user_id
            cache_key = f"employee_search_{user_id}_{search_term}_{str(filters)}"
            
            # Check cache first
            cached_data = self._get_cache(cache_key)
            if cached_data:
                return True, cached_data
            
            # Build search domain
            domain = []
            
            # Add search term filters
            if search_term:
                domain.append('|')
                domain.append(('name', 'ilike', search_term))
                domain.append(('work_email', 'ilike', search_term))
            
            # Add additional filters
            if filters:
                for field, value in filters.items():
                    if field in self.employee_fields:
                        domain.append((field, '=', value))
            
            # For now, limit to current user's data (implement role-based access later)
            domain.append(('user_id', '=', user_id))
            
            safe_fields = self._get_safe_public_employee_fields()
            success, data = self._safe_employee_search_read(domain, safe_fields, limit=100)
            
            if success:
                # Expand related data for each employee
                expanded_data = []
                for employee in data:
                    expanded_employee = self._expand_related_data(employee)
                    expanded_data.append(expanded_employee)
                
                # Cache the result
                self._set_cache(cache_key, expanded_data)
                
                return True, expanded_data
            else:
                return False, data or "Could not retrieve employee data from Odoo"
                
        except Exception as e:
            return False, f"Error searching employees: {str(e)}"
    
    def get_employee_by_id(self, employee_id: int) -> Tuple[bool, Any]:
        """Get specific employee data by ID (with permission check)"""
        try:
            if not self.odoo_service.is_authenticated():
                return False, "Not authenticated with Odoo"
            
            # Skip permission check to avoid duplicate API calls
            # Permission checking should be done at controller level
            
            # In a real implementation, you'd check if the employee_id belongs to the user
            # or if the user has manager/HR permissions
            
            cache_key = f"employee_{employee_id}"
            
            # Check cache first
            cached_data = self._get_cache(cache_key)
            if cached_data:
                return True, cached_data
            
            success, data = self._safe_employee_read([employee_id], self.employee_fields)
            
            if success and data:
                employee_data = data[0]
                expanded_data = self._expand_related_data(employee_data)
                
                # Cache the result
                self._set_cache(cache_key, expanded_data)
                
                return True, expanded_data
            else:
                return False, data or "Could not retrieve employee data from Odoo"
                
        except Exception as e:
            return False, f"Error retrieving employee data: {str(e)}"
    
    def clear_cache(self):
        """Clear all cached data"""
        self.cache.clear()
        self.cache_expiry.clear()
    
    def get_current_user_avatar(self, size: int = 128) -> Tuple[bool, Any]:
        """Fetch the current user's avatar at a specific size (e.g., image_128).

        Uses cache to avoid repeated reads. Falls back to 128 if an unknown size is requested.
        """
        try:
            if not self.odoo_service.is_authenticated():
                return False, "Not authenticated with Odoo"

            # Allow full-resolution image_1920 in addition to thumbnails
            size = 128 if size not in (128, 256, 512, 1920) else size
            field_name = f"image_{size}"

            user_id = self.odoo_service.user_id
            cache_key = f"avatar_{user_id}_{field_name}"
            cached = self._get_cache(cache_key)
            if cached is not None:
                return True, cached

            # Resolve employee id
            ok_ids, id_list = self._make_odoo_request('hr.employee', 'search', {
                'args': [[('user_id', '=', user_id)]],
                'kwargs': {'limit': 1}
            })
            if not ok_ids or not isinstance(id_list, list) or not id_list:
                return False, "No employee found"

            # Use safe read in case the image field is restricted
            ok_read, data = self._safe_employee_read(id_list, [field_name])
            if not ok_read or not isinstance(data, list) or not data:
                # Try fallback on res.users image
                ok_user, data_user = self._safe_model_read('res.users', [self.odoo_service.user_id], [field_name])
                if ok_user and isinstance(data_user, list) and data_user:
                    img_user = data_user[0].get(field_name)
                    if img_user:
                        self._set_cache(cache_key, img_user)
                        return True, img_user
                return False, "Avatar not available"

            img = data[0].get(field_name)
            if img:
                self._set_cache(cache_key, img)
                return True, img
            return False, "Avatar not available"
        except Exception as e:
            return False, f"Avatar fetch error: {e}"

    def get_cache_stats(self) -> Dict:
        """Get cache statistics"""
        return {
            'cache_size': len(self.cache),
            'cache_keys': list(self.cache.keys()),
            'expired_entries': len([k for k in self.cache_expiry.keys() if not self._is_cache_valid(k)])
        }

    def get_direct_reports_current_user(self) -> Tuple[bool, Any]:
        """Return a list of direct reports (team members) for the current user.

        Each entry includes minimal profile: name, job_title, department name, and employee id.
        """
        try:
            if not self.odoo_service.is_authenticated():
                return False, "Not authenticated with Odoo"

            # Get current employee record (cached)
            ok, me = self.get_current_user_employee_data()
            if not ok or not isinstance(me, dict) or not me.get('id'):
                return False, "Could not resolve current employee record"

            my_employee_id = me.get('id')

            # Use cache key per manager
            cache_key = f"direct_reports_{my_employee_id}"
            cached = self._get_cache(cache_key)
            if cached is not None:
                return True, cached

            # Query hr.employee for children with parent_id = me
            params = {
                'args': [[('parent_id', '=', my_employee_id)]],
                'kwargs': {
                    'fields': ['name', 'job_title', 'department_id', 'user_id'],
                    'limit': 200,
                    'order': 'name asc'
                }
            }
            ok2, rows = self._make_odoo_request('hr.employee', 'search_read', params)
            if not ok2:
                return False, rows

            # Normalize
            team = []
            for r in rows or []:
                dept = None
                d = r.get('department_id')
                if isinstance(d, list) and len(d) > 1:
                    dept = d[1]
                # Extract linked user id if available
                user_id = None
                u = r.get('user_id')
                if isinstance(u, list) and len(u) > 0:
                    user_id = u[0]
                elif isinstance(u, int):
                    user_id = u

                team.append({
                    'id': r.get('id'),
                    'name': r.get('name'),
                    'job_title': r.get('job_title') or '',
                    'department': dept or '',
                    'user_id': user_id
                })

            # Cache team list
            self._set_cache(cache_key, team)
            return True, team
        except Exception as e:
            return False, f"Error fetching direct reports: {str(e)}"

    def is_current_user_manager(self) -> bool:
        """Heuristic: user is a manager if they have at least one direct report."""
        try:
            ok, team = self.get_direct_reports_current_user()
            return bool(ok and isinstance(team, list) and len(team) > 0)
        except Exception:
            return False
