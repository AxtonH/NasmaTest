import requests
import json
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime, timedelta
import re
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

class TimeOffService:
    """Service for managing Odoo time-off/leave requests with fuzzy logic detection"""
    
    def __init__(self, odoo_service, employee_service):
        self.odoo_service = odoo_service
        self.employee_service = employee_service
        
        # Time-off detection patterns (fuzzy logic)
        self.timeoff_patterns = [
            # Direct requests with "want"
            r'(?:i\s+)?(?:want|need|would like)(?:\s+to)?\s+(?:take|have|request|apply for|get)?\s*(?:an?|some)?\s*(?:annual|sick|vacation|holiday|time off|leave|day off|day)',
            # Direct leave type requests
            r'(?:i\s+)?(?:want|need|would like)(?:\s+to)?\s*(?:an?|some)?\s*(?:annual\s+leave|sick\s+leave|sick\s+day|vacation\s+day|holiday)',
            # General time-off patterns
            r'(?:request|apply|take|need|want).{0,20}(?:time off|leave|vacation|holiday|day off)',
            r'(?:sick|ill|unwell).{0,10}(?:day|leave|time)',
            r'(?:annual|vacation|holiday).{0,10}(?:leave|day)',
            r'(?:unpaid|without pay).{0,10}(?:leave|day)',
            # Question forms
            r'(?:can i|may i|could i).{0,20}(?:take|have|get).{0,10}(?:time off|leave|day|day off)',
            r'(?:how do i|how to).{0,20}(?:request|apply).{0,10}(?:leave|time off)',
            # Casual mentions
            r'(?:off work|absent|away).{0,10}(?:tomorrow|next week|monday)',
            r'(?:doctor|appointment|medical).{0,20}(?:day|leave)',
            # More specific patterns
            r'(?:i want to|i need to|i would like to).{0,20}(?:request|apply|take).{0,10}(?:time off|leave|day off)',
            r'(?:book|schedule).{0,10}(?:time off|leave|vacation)',
            r'(?:submit|put in).{0,10}(?:leave request|time off request)',
            # Common casual phrases
            r'(?:take|have).{0,10}(?:a day|some days|few days).{0,10}(?:off)',
            r'(?:day off|days off)',
            # Simple patterns
            r'(?:sick day|annual leave|vacation day|holiday day)',
        ]
        
        # Leave type mapping
        self.leave_types = {
            'annual': ['annual', 'vacation', 'holiday', 'pto', 'paid time off', 'annual leave', 'vacation day', 'holiday day'],
            'sick': ['sick', 'ill', 'medical', 'doctor', 'unwell', 'health', 'sick day', 'sick leave', 'medical day'],
            'unpaid': ['unpaid', 'without pay', 'no pay', 'personal', 'unpaid leave']
        }
        
        # Date patterns for extraction
        self.date_patterns = [
            r'(?:from|starting)\s+(\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?)',
            r'(?:to|until|ending)\s+(\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?)',
            r'(\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?)\s+(?:to|until|-)\s+(\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?)',
            r'(?:tomorrow|next\s+(?:monday|tuesday|wednesday|thursday|friday))',
            r'(?:next\s+week|this\s+week)',
        ]

    def _log(self, message: str, category: str = "general"):
        """Log message based on category and configuration"""
        debug_log(message, category)

    def detect_timeoff_intent(self, message: str) -> Tuple[bool, float, Dict]:
        """
        Use fuzzy logic to detect time-off request intent
        Returns: (is_timeoff_request, confidence_score, extracted_data)
        """
        debug_log(f"TimeOff detection for message: '{message}'", "bot_logic")
        message_lower = message.lower()
        confidence = 0.0
        extracted_data = {}
        
        # Check for time-off patterns with weighted scoring
        pattern_matches = 0
        matched_patterns = []
        high_confidence_patterns = [0, 1, 2]  # First 3 patterns are most reliable

        for i, pattern in enumerate(self.timeoff_patterns):
            if re.search(pattern, message_lower):
                pattern_matches += 1
                # Give higher confidence to more specific patterns
                if i in high_confidence_patterns:
                    confidence += 0.5  # High confidence patterns
                else:
                    confidence += 0.3  # Standard patterns
                matched_patterns.append(f"Pattern {i+1}: {pattern}")

        debug_log(f"Pattern matches: {pattern_matches}, Matched: {matched_patterns}", "bot_logic")

        # Extract leave type if mentioned (gives strong confidence boost)
        leave_type = self._extract_leave_type(message_lower)
        if leave_type:
            extracted_data['leave_type'] = leave_type
            confidence += 0.4  # Higher boost for explicit leave type
            debug_log(f"Leave type detected: {leave_type}", "bot_logic")

        # Extract dates if mentioned
        dates = self._extract_dates(message_lower)
        if dates:
            extracted_data.update(dates)
            confidence += 0.3  # Dates are strong indicators
            debug_log(f"Dates detected: {dates}", "bot_logic")

        # Boost confidence for multiple patterns
        if pattern_matches > 1:
            confidence += 0.2

        # Context-aware adjustments
        # Strong intent keywords boost confidence
        strong_intent_words = ['want', 'need', 'request', 'apply', 'submit']
        for word in strong_intent_words:
            if word in message_lower:
                confidence += 0.1
                break
        
        # Check for negative indicators (reduce confidence)
        negative_patterns = [
            r'(?:not|don\'t|won\'t).{0,10}(?:need|want|take)',
            r'(?:already|have).{0,10}(?:requested|applied)',
        ]
        for neg_pattern in negative_patterns:
            if re.search(neg_pattern, message_lower):
                confidence -= 0.3
        
        # Normalize confidence
        confidence = max(0.0, min(1.0, confidence))
        
        # Threshold for detection - balanced for better accuracy
        is_timeoff_request = confidence >= 0.4
        
        debug_log(f"TimeOff detection result - confidence: {confidence:.2f}, is_timeoff: {is_timeoff_request}, extracted: {extracted_data}", "bot_logic")
        return is_timeoff_request, confidence, extracted_data
    
    def _extract_leave_type(self, message: str) -> Optional[str]:
        """Extract leave type from message"""
        for leave_type, keywords in self.leave_types.items():
            for keyword in keywords:
                if keyword in message:
                    return leave_type
        return None
    
    def _extract_dates(self, message: str) -> Dict:
        """Extract dates from message"""
        dates = {}
        
        # Simple date extraction (can be enhanced)
        date_matches = re.findall(r'\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?', message)
        if len(date_matches) >= 2:
            dates['start_date'] = date_matches[0]
            dates['end_date'] = date_matches[1]
        elif len(date_matches) == 1:
            dates['start_date'] = date_matches[0]
        
        # Handle relative dates (do not force end_date; let range parser resolve)
        if 'tomorrow' in message:
            tomorrow = (datetime.now() + timedelta(days=1)).strftime('%d/%m/%Y')
            # Only set start_date; end_date will be collected/parsed later
            dates['start_date'] = tomorrow
        
        return dates
    
    def get_leave_types(self) -> Tuple[bool, Any]:
        """Get available leave types from Odoo"""
        try:
            if not self.odoo_service.is_authenticated():
                return False, "Not authenticated with Odoo"
            
            # Fetch hr.leave.type records (using correct fields from your Odoo setup)
            params = {
                'args': [[]],
                'kwargs': {
                    'fields': ['id', 'name', 'active', 'requires_allocation', 'has_valid_allocation', 'max_leaves', 'allows_negative'],
                    'limit': 50
                }
            }
            
            success, data = self._make_odoo_request('hr.leave.type', 'search_read', params)
            
            if success:
                # Filter active leave types and add additional info
                leave_types = []
                for lt in data:
                    if isinstance(lt, dict) and lt.get('active', True):
                        # Add readable info about allocation requirements
                        lt['allocation_info'] = self._get_allocation_info(lt)
                        leave_types.append(lt)
                return True, leave_types
            else:
                return False, data
                
        except Exception as e:
            return False, f"Error fetching leave types: {str(e)}"
    
    def _get_allocation_info(self, leave_type: dict) -> str:
        """Generate readable allocation information for a leave type"""
        requires_allocation = leave_type.get('requires_allocation', False)
        has_valid_allocation = leave_type.get('has_valid_allocation', False)
        max_leaves = leave_type.get('max_leaves', 0)
        allows_negative = leave_type.get('allows_negative', False)
        
        if not requires_allocation:
            return "No allocation required"
        elif has_valid_allocation and max_leaves > 0:
            return f"Allocation available ({max_leaves} days)"
        elif allows_negative:
            return "Available (allows negative balance)"
        else:
            return "Allocation required"
    
    def submit_leave_request(self, employee_id: int, leave_type_id: int,
                           start_date: str, end_date: str, description: str = None,
                           extra_fields: Optional[Dict] = None,
                           supporting_attachments: Optional[List[Dict[str, Any]]] = None) -> Tuple[bool, Any]:
        """Submit a leave request to Odoo"""
        try:
            self._log(f"Starting leave request submission for employee {employee_id}", "bot_logic")

            if not self.odoo_service.is_authenticated():
                self._log("Not authenticated with Odoo", "bot_logic")
                return False, "Not authenticated with Odoo"

            # Prepare leave request data
            leave_data = {
                'employee_id': employee_id,
                'holiday_status_id': leave_type_id,
                'request_date_from': start_date,
                'request_date_to': end_date,
                'name': description or "Time off request via Nasma chatbot",
                'state': 'confirm'  # Submit for approval
            }

            # Merge any extra fields (e.g., request_unit_hours for Half Days)
            if extra_fields and isinstance(extra_fields, dict):
                leave_data.update(extra_fields)

            self._log(f"Leave request data: {leave_data}", "bot_logic")

            # Create the leave request
            params = {
                'args': [leave_data],
                'kwargs': {}
            }

            self._log(f"Making Odoo request to create leave", "bot_logic")
            success, data = self._make_odoo_request('hr.leave', 'create', params)

            self._log(f"Odoo request result - Success: {success}, Data: {data}", "bot_logic")

            if success:
                leave_id = data
                self._log(f"Leave request created successfully with ID: {leave_id}", "bot_logic")

                attachment_ids: List[int] = []
                if supporting_attachments:
                    for idx, attachment in enumerate(supporting_attachments):
                        try:
                            if not isinstance(attachment, dict):
                                continue
                            datas = attachment.get('data')
                            if not datas:
                                continue
                            name = attachment.get('filename') or attachment.get('name') or f'supporting-document-{idx + 1}'
                            mimetype = attachment.get('mimetype') or attachment.get('content_type') or 'application/octet-stream'
                            attachment_payload = {
                                'name': name,
                                'datas': datas,
                                'res_model': 'hr.leave',
                                'res_id': leave_id,
                                'type': 'binary',
                                'mimetype': mimetype,
                            }
                            self._log(f"Uploading supporting document '{name}' for leave {leave_id}", "bot_logic")
                            att_success, att_id = self._make_odoo_request('ir.attachment', 'create', {
                                'args': [attachment_payload],
                                'kwargs': {}
                            })
                            if att_success and isinstance(att_id, int):
                                attachment_ids.append(att_id)
                            else:
                                self._log(f"Failed to create attachment for '{name}': {att_id}", "general")
                        except Exception as attachment_error:
                            self._log(f"Exception uploading attachment {idx}: {attachment_error}", "general")
                    if attachment_ids:
                        self._log(f"Linking {len(attachment_ids)} attachments to supported_attachment_ids", "bot_logic")
                        link_args = {
                            'args': [[leave_id], {'supported_attachment_ids': [(6, 0, attachment_ids)]}],
                            'kwargs': {}
                        }
                        link_success, link_resp = self._make_odoo_request('hr.leave', 'write', link_args)
                        if not link_success:
                            self._log(f"Failed to link supporting attachments: {link_resp}", "general")
                return True, {
                    'leave_id': leave_id,
                    'message': f"Leave request #{leave_id} submitted successfully and is pending approval."
                }
            else:
                self._log(f"Leave request creation failed: {data}", "general")
                return False, data

        except Exception as e:
            self._log(f"Exception during leave request submission: {e}", "general")
            import traceback
            traceback.print_exc()
            return False, f"Error submitting leave request: {str(e)}"
    
    def _make_odoo_request(self, model: str, method: str, params: Dict) -> Tuple[bool, Any]:
        """Make authenticated request to Odoo using web session"""
        try:
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
            
            # Use OdooService retry-aware post to auto-renew expired sessions
            post = getattr(self.odoo_service, 'post_with_retry', None)
            if callable(post):
                response = post(url, json=data, cookies=cookies, timeout=15)
            else:
                response = requests.post(
                    url,
                    json=data,
                    headers={'Content-Type': 'application/json'},
                    cookies=cookies,
                    timeout=15
                )
            
            if response.status_code == 200:
                # Log response for debugging
                response_text = response.text
                debug_log(f"Odoo response length: {len(response_text)} characters", "odoo_data")
                debug_log(f"Odoo response preview: {response_text[:500]}...", "odoo_data")
                
                try:
                    result = response.json()
                    if 'result' in result:
                        debug_log(f"Successfully parsed JSON, result type: {type(result['result'])}", "odoo_data")
                        if isinstance(result['result'], list):
                            debug_log(f"Leave types count: {len(result['result'])}", "odoo_data")
                        return True, result['result']
                    else:
                        error_msg = f"Odoo API error: {result.get('error', 'Unknown error')}"
                        debug_log(f"Odoo API error: {error_msg}", "odoo_data")
                        return False, error_msg
                except ValueError as json_error:
                    debug_log(f"JSON parsing error: {json_error}", "general")
                    debug_log(f"Raw response causing JSON error: {response_text}", "general")
                    return False, f"Invalid JSON response from Odoo: {json_error}"
            else:
                debug_log(f"HTTP error {response.status_code}: {response.text}", "general")
                return False, f"HTTP error: {response.status_code}"
                
        except Exception as e:
            return False, f"Request error: {str(e)}"
    
    def format_leave_types_for_user(self, leave_types: List[Dict]) -> str:
        """Format leave types for user selection"""
        if not leave_types:
            return "No leave types available."
        
        formatted = "Available leave types:\n"
        valid_count = 0
        
        for i, lt in enumerate(leave_types, 1):
            try:
                # Validate the leave type data
                if not isinstance(lt, dict):
                    debug_log(f"Invalid leave type at index {i}: not a dict - {type(lt)}", "general")
                    continue
                    
                name = lt.get('name', 'Unknown')
                if not name or name == 'Unknown':
                    debug_log(f"Invalid leave type name at index {i}: {lt}", "general") 
                    continue
                    
                # Only count and display valid leave types
                valid_count += 1
                formatted += f"{valid_count}. {name}\n"
                
            except Exception as format_error:
                debug_log(f"Error formatting leave type {i}: {format_error}, data: {lt}", "general")
                continue
        
        if valid_count == 0:
            return "No valid leave types available."
            
        return formatted
    
    def parse_date_input(self, date_str: str) -> Optional[str]:
        """Parse user date input to YYYY-MM-DD format"""
        try:
            # Normalize
            text = (date_str or "").strip().lower()

            # Handle weekday names like "monday" or "next monday" as upcoming day
            weekday_map = {
                'monday': 0, 'mon': 0,
                'tuesday': 1, 'tue': 1, 'tues': 1,
                'wednesday': 2, 'wed': 2,
                'thursday': 3, 'thu': 3, 'thur': 3, 'thurs': 3,
                'friday': 4, 'fri': 4,
                'saturday': 5, 'sat': 5,
                'sunday': 6, 'sun': 6
            }

            def next_weekday(base_dt: datetime, target_wd: int, include_today: bool) -> datetime:
                days_ahead = (target_wd - base_dt.weekday()) % 7
                if days_ahead == 0 and not include_today:
                    days_ahead = 7
                return base_dt + timedelta(days=days_ahead)

            # Match "next monday" | "this monday" | "monday"
            m = re.search(r'^(?:\b(this|next)\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)\b$', text)
            if m:
                qualifier, wname = m.groups()
                now = datetime.now()
                target_wd = weekday_map[wname]
                if qualifier == 'next':
                    dt = next_weekday(now, target_wd, include_today=False)
                else:
                    # Upcoming same weekday; if today is that weekday, choose next week
                    dt = next_weekday(now, target_wd, include_today=False)
                return dt.strftime('%Y-%m-%d')

            # Handle different date formats
            date_formats = [
                '%d/%m/%Y', '%d-%m-%Y', '%d.%m.%Y',
                '%d/%m/%y', '%d-%m-%y', '%d.%m.%y',
                '%Y-%m-%d', '%Y/%m/%d',
                '%m/%d/%Y', '%m-%d-%Y'
            ]
            
            for fmt in date_formats:
                try:
                    parsed_date = datetime.strptime(text, fmt)
                    return parsed_date.strftime('%Y-%m-%d')
                except ValueError:
                    continue
            
            return None
            
        except Exception:
            return None

    def parse_date_range(self, text: str) -> Optional[Tuple[str, str]]:
        """Parse a date range from free text and return (start_date, end_date) in YYYY-MM-DD.

        Rules:
        - Prefer day-first parsing (DD/MM) by default per user preference
        - If year missing, default to current year
        - If month missing on the second date, inherit from the first date
        - If year missing on the second date, inherit from the first or current year
        - Accept connectors: to, till, until, through, '-', '–', '—'
        - Accept month names and ordinal suffixes (e.g., 23rd of September)
        """
        try:
            if not text or not isinstance(text, str):
                return None

            now = datetime.now()

            month_map = {
                'january': 1, 'jan': 1,
                'february': 2, 'feb': 2,
                'march': 3, 'mar': 3,
                'april': 4, 'apr': 4,
                'may': 5,
                'june': 6, 'jun': 6,
                'july': 7, 'jul': 7,
                'august': 8, 'aug': 8,
                'september': 9, 'sep': 9, 'sept': 9,
                'october': 10, 'oct': 10,
                'november': 11, 'nov': 11,
                'december': 12, 'dec': 12,
            }

            def to_date_ymd(day: int, month: int, year: int) -> str:
                return datetime(year, month, day).strftime('%Y-%m-%d')

            def clean_ordinals(s: str) -> str:
                return re.sub(r'(st|nd|rd|th)', '', s)

            text_norm = text.lower().strip()
            # Normalize connectors and whitespace
            text_norm = re.sub(r'\s*(?:–|—)\s*', '-', text_norm)
            # Only treat connectors as standalone words to avoid matching inside words like 'tomorrow'
            connectors = r'(?:\bto\b|\btill\b|\buntil\b|\bthrough\b|\-\-|\-|\u2013|\u2014)'

            # Weekday ranges: "next monday to wednesday", "this tue - thu", "monday to wednesday"
            weekday_map = {
                'monday': 0, 'mon': 0,
                'tuesday': 1, 'tue': 1, 'tues': 1,
                'wednesday': 2, 'wed': 2,
                'thursday': 3, 'thu': 3, 'thur': 3, 'thurs': 3,
                'friday': 4, 'fri': 4,
                'saturday': 5, 'sat': 5,
                'sunday': 6, 'sun': 6
            }

            def next_weekday(base: datetime, target_wd: int, include_today: bool) -> datetime:
                days_ahead = (target_wd - base.weekday()) % 7
                if days_ahead == 0 and not include_today:
                    days_ahead = 7
                return base + timedelta(days=days_ahead)

            weekday_names = '|'.join(weekday_map.keys())
            m = re.search(
                rf'\b(?:(this|next)\s+)?({weekday_names})\s*{connectors}\s*(?:(this|next)\s+)?({weekday_names})\b',
                text_norm
            )
            if m:
                q1, w1, q2, w2 = m.groups()
                wd1 = weekday_map[w1]
                wd2 = weekday_map[w2]
                # Determine first date
                if q1 == 'next':
                    first_date = next_weekday(now, wd1, include_today=False)
                else:
                    # 'this' or no qualifier => next occurrence including today
                    first_date = next_weekday(now, wd1, include_today=True)
                # Determine end date within same anchored week
                start_of_week = first_date - timedelta(days=first_date.weekday())
                end_candidate = start_of_week + timedelta(days=wd2)
                if end_candidate < first_date:
                    # If the named end day is before the start day, move to next week
                    end_candidate = end_candidate + timedelta(days=7)
                start = first_date.strftime('%Y-%m-%d')
                end = end_candidate.strftime('%Y-%m-%d')
                return start, end

            # 1) Numeric date range: 23/9[/2025] to 24/9[/2025] or with '-' separators
            m = re.search(
                rf'(\b\d{{1,2}})[\./\-](\d{{1,2}})(?:[\./\-](\d{{2,4}}))?\s*{connectors}\s*(\d{{1,2}})[\./\-](\d{{1,2}})(?:[\./\-](\d{{2,4}}))?\b',
                text_norm
            )
            if m:
                d1, m1, y1, d2, m2, y2 = m.groups()
                day1 = int(d1)
                mon1 = int(m1)
                year1 = int(y1) if y1 else now.year
                if year1 < 100:
                    year1 += 2000
                day2 = int(d2)
                mon2 = int(m2)
                year2 = int(y2) if y2 else year1
                if year2 < 100:
                    year2 += 2000
                start = to_date_ymd(day1, mon1, year1)
                end = to_date_ymd(day2, mon2, year2)
                if start <= end:
                    return start, end
                else:
                    # If end before start and user didn't specify year/month explicitly, keep as invalid
                    return None

            # 2) Month-name first then numeric day: 23rd of September till the 24th [year optional]
            month_names = '|'.join(month_map.keys())
            # 2a) Full: 23rd of September 2025 to 24th of September 2025
            m = re.search(
                rf'(\d{{1,2}})(?:st|nd|rd|th)?\s*(?:of\s*)?({month_names})(?:\s*,?\s*(\d{{4}}))?\s*{connectors}\s*'
                rf'(\d{{1,2}})(?:st|nd|rd|th)?\s*(?:of\s*)?({month_names})?(?:\s*,?\s*(\d{{4}}))?'
                , text_norm
            )
            if m:
                d1, mon_name1, y1, d2, mon_name2, y2 = m.groups()
                day1 = int(clean_ordinals(d1))
                mon1 = month_map.get(mon_name1, now.month)
                year1 = int(y1) if y1 else now.year
                day2 = int(clean_ordinals(d2))
                mon2 = month_map.get(mon_name2, mon1)
                year2 = int(y2) if y2 else year1
                start = to_date_ymd(day1, mon1, year1)
                end = to_date_ymd(day2, mon2, year2)
                if start <= end:
                    return start, end
                else:
                    return None

            # 2b) Numeric day to numeric day with trailing month name: 23 to 24 September [year optional]
            m = re.search(
                rf'(\d{{1,2}})(?:st|nd|rd|th)?\s*{connectors}\s*(\d{{1,2}})(?:st|nd|rd|th)?\s*({month_names})(?:\s*,?\s*(\d{{4}}))?',
                text_norm
            )
            if m:
                d1, d2, mon_name, y = m.groups()
                day1 = int(clean_ordinals(d1))
                day2 = int(clean_ordinals(d2))
                mon = month_map.get(mon_name, now.month)
                year = int(y) if y else now.year
                start = to_date_ymd(day1, mon, year)
                end = to_date_ymd(day2, mon, year)
                if start <= end:
                    return start, end
                else:
                    return None

            # 3) Fallback: detect two standalone dates (DD/MM[/YY]) separated by connector
            # Extract two date-like tokens honoring DD/MM default
            tokens = re.split(rf'\s*{connectors}\s*', text_norm)
            if len(tokens) == 2:
                def parse_single(token: str, inherit: Optional[datetime] = None) -> Optional[datetime]:
                    token = token.strip()
                    token = re.sub(r'^the\s+', '', token)  # allow "the 23rd"
                    token = re.sub(r'[\.,]$', '', token)   # strip trailing punctuation
                    # Try numeric DD/MM[/YYYY]
                    m_local = re.search(r'\b(\d{1,2})[\./\-](\d{1,2})(?:[\./\-](\d{2,4}))?\b', token)
                    if m_local:
                        d, mth, yr = m_local.groups()
                        dd = int(d)
                        mm = int(mth)
                        yy = int(yr) if yr else (inherit.year if inherit else now.year)
                        if yy < 100:
                            yy += 2000
                        return datetime(yy, mm, dd)
                    # Try month name + day
                    m_local = re.search(rf'\b(\d{{1,2}})(?:st|nd|rd|th)?\s*(?:of\s*)?({month_names})(?:\s*,?\s*(\d{{4}}))?\b', token)
                    if m_local:
                        dname, mname, yr = m_local.groups()
                        dd = int(clean_ordinals(dname))
                        mm = month_map.get(mname, (inherit.month if inherit else now.month))
                        yy = int(yr) if yr else (inherit.year if inherit else now.year)
                        return datetime(yy, mm, dd)
                    # Today / Tomorrow
                    if token == 'today':
                        base = inherit or now
                        return datetime(base.year, base.month, base.day)
                    if token == 'tomorrow':
                        base = inherit or now
                        nxt = base + timedelta(days=1)
                        return datetime(nxt.year, nxt.month, nxt.day)
                    # Weekday with optional qualifier (this/next)
                    m_local = re.search(rf'^(?:\b(this|next)\s+)?({weekday_names})\b$', token)
                    if m_local:
                        qual, wname = m_local.groups()
                        wd = weekday_map[wname]
                        base = inherit or now
                        if qual == 'next':
                            dt = next_weekday(base, wd, include_today=False)
                        else:
                            dt = next_weekday(base, wd, include_today=True)
                            if dt < base:
                                dt = dt + timedelta(days=7)
                        return datetime(dt.year, dt.month, dt.day)
                    # Ordinal day only (e.g., 23rd) inheriting month/year
                    m_local = re.search(r'^\b(\d{1,2})(?:st|nd|rd|th)?\b$', token)
                    if m_local:
                        dd = int(clean_ordinals(m_local.group(1)))
                        base = inherit or now
                        return datetime(base.year, base.month, dd)
                    return None

                first = parse_single(tokens[0])
                second = parse_single(tokens[1], inherit=first or now)
                if first and second:
                    start = first.strftime('%Y-%m-%d')
                    end = second.strftime('%Y-%m-%d')
                    if start <= end:
                        return start, end
                    else:
                        return None

            # 4) Single date fallback: interpret as same start/end
            # Numeric DD/MM[/YYYY]
            m = re.search(r'\b(\d{1,2})[\./\-](\d{1,2})(?:[\./\-](\d{2,4}))?\b', text_norm)
            if m:
                d, mth, yr = m.groups()
                dd = int(d)
                mm = int(mth)
                yy = int(yr) if yr else now.year
                if yy < 100:
                    yy += 2000
                single = datetime(yy, mm, dd).strftime('%Y-%m-%d')
                return single, single

            # 'today' / 'tomorrow' (only when no explicit connector/range)
            if 'tomorrow' in text_norm and not re.search(connectors, text_norm):
                dt = (now + timedelta(days=1)).strftime('%Y-%m-%d')
                return dt, dt
            if 'today' in text_norm and not re.search(connectors, text_norm):
                dt = now.strftime('%Y-%m-%d')
                return dt, dt

            return None
        except Exception:
            return None
