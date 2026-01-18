from typing import Optional, Dict, Any, Tuple, List
import re
from datetime import datetime, date
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
    elif category == "knowledge_base" and Config.DEBUG_KNOWLEDGE_BASE:
        print(f"DEBUG: {message}")
    elif category == "general" and Config.VERBOSE_LOGS:
        print(f"DEBUG: {message}")

class ReimbursementService:
    """Service for managing expense reimbursement requests with fuzzy logic detection"""

    def __init__(self, odoo_service, employee_service, metrics_service=None, auth_token_service=None):
        self.odoo_service = odoo_service
        self.employee_service = employee_service
        self.metrics_service = metrics_service
        self.auth_token_service = auth_token_service

        # Reimbursement detection patterns (fuzzy logic)
        self.reimbursement_patterns = [
            # Direct requests
            r'(?:i\s+)?(?:want|need|would like)(?:\s+to)?\s+(?:request|submit|file|create|make)\s+(?:a\s*|an\s*)?(?:reimbursement|expense|expense report)',
            r'(?:i\s+)?(?:want|need|would like)(?:\s+to)?\s+(?:get|claim)\s+(?:reimbursed|reimbursement)',
            # Allow simple desire + noun
            r'(?:i\s+)?(?:want|need|would like)\s+(?:a\s*|an\s*)?reimbursement',
            # General expense patterns
            r'(?:request|submit|file|create).{0,20}(?:expense|reimbursement)',
            r'(?:expense|reimbursement).{0,10}(?:request|report|claim)',
            # Question forms
            r'(?:can i|may i|could i).{0,20}(?:request|submit|file).{0,10}(?:expense|reimbursement)',
            r'(?:can i|may i|could i).{0,20}(?:get|have).{0,10}(?:a\s*|an\s*)?reimbursement',
            r'(?:how do i|how to).{0,20}(?:request|submit|file).{0,10}(?:expense|reimbursement)',
            # Casual mentions
            r'(?:i spent|i paid|i bought).{0,20}(?:for work|for company|on business)',
            r'(?:business expense|work expense|company expense)',
            # More specific patterns
            r'(?:i want to|i need to|i would like to).{0,20}(?:request|submit|file).{0,10}(?:expense|reimbursement)',
            r'(?:submit|file).{0,10}(?:expense report|reimbursement request)',
            # Simple patterns
            r'(?:expense report|reimbursement request|\breimbursement\b)',
        ]

        # Expense categories mapping
        self.expense_categories = {
            'miscellaneous': '[EXP_GEN] Miscellaneous',
            'per_diem': '[PER_DIEM] Per Diem',
            'travel_accommodation': '[TRANS & ACC] Travel & Accommodation'
        }

        # Map internal category keys to Odoo Internal Reference (default_code)
        self.category_default_codes = {
            'miscellaneous': 'EXP_GEN',
            'per_diem': 'PER_DIEM',
            'travel_accommodation': 'TRANS & ACC'
        }

        # Enable all categories for selection buttons
        self.supported_categories = ['miscellaneous', 'per_diem', 'travel_accommodation']

    def _log(self, message: str, category: str = "general"):
        """Log message with category"""
        debug_log(message, category)

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
                    company_val = employee_data.get('company_id')
                    if isinstance(company_val, (list, tuple)) and company_val:
                        tenant_id = str(company_val[0])
                        if len(company_val) > 1 and company_val[1]:
                            tenant_name = company_val[1]
                    elif company_val:
                        tenant_id = str(company_val)
        except Exception:
            pass
        return {
            'tenant_id': tenant_id,
            'tenant_name': tenant_name,
            'user_name': user_name,
            'user_id': user_id,
        }

    def _log_metric(self, thread_id: str, expense_data: Dict, result: Dict, employee_data: Dict, metric_type: str = 'reimbursement'):
        if not self.metrics_service:
            return
        try:
            identity = self._resolve_identity(employee_data or {})
            payload = {
                'expense_id': result.get('id'),
                'category': expense_data.get('category'),
                'amount': expense_data.get('amount'),
                'currency': expense_data.get('currency'),
                'date': expense_data.get('date'),
                'additional': {
                    'has_attachment': bool(expense_data.get('attachments')),
                    'attached_link': expense_data.get('attached_link'),
                    'per_diem_from': expense_data.get('per_diem_from'),
                    'per_diem_to': expense_data.get('per_diem_to'),
                    'travel_destination': expense_data.get('travel_destination'),
                },
            }
            tenant_name = identity.get('tenant_name')
            if tenant_name:
                payload['additional']['tenant_name'] = tenant_name
            logged = self.metrics_service.log_metric(
                metric_type,
                thread_id,
                user_id=identity.get('user_id'),
                user_name=identity.get('user_name'),
                tenant_id=identity.get('tenant_id'),
                payload=payload,
            )
            if not logged:
                last_err = getattr(self.metrics_service, "last_error", None)
                self._log(f"Supabase metric logging failed ({metric_type}): {last_err}", "general")
        except Exception:
            pass

    def detect_reimbursement_intent(self, message: str) -> Tuple[bool, float, Dict]:
        """
        Use fuzzy logic to detect reimbursement request intent
        Returns: (is_reimbursement_request, confidence_score, extracted_data)
        """
        self._log(f"Reimbursement detection for message: '{message}'", "bot_logic")
        message_lower = message.lower()
        confidence = 0.0
        extracted_data = {}

        # Check for reimbursement patterns with weighted scoring
        pattern_matches = 0
        matched_patterns = []
        high_confidence_patterns = [0, 1, 2]  # First patterns are most reliable

        for i, pattern in enumerate(self.reimbursement_patterns):
            if re.search(pattern, message_lower):
                pattern_matches += 1
                # Give higher confidence to more specific patterns
                if i in high_confidence_patterns:
                    confidence += 0.5  # High confidence patterns
                else:
                    confidence += 0.3  # Standard patterns
                matched_patterns.append(f"Pattern {i+1}: {pattern}")

        # Heuristic boost when any reimbursement stem appears
        if 'reimburs' in message_lower:
            confidence += 0.25

        self._log(f"Pattern matches: {pattern_matches}, Matched: {matched_patterns}", "bot_logic")

        # Extract category if mentioned (gives strong confidence boost)
        category = self._extract_category(message_lower)
        if category:
            extracted_data['category'] = category
            confidence += 0.4  # Higher boost for explicit category
            self._log(f"Category detected: {category}", "bot_logic")

        # Extract amount if mentioned
        amount = self._extract_amount(message_lower)
        if amount:
            extracted_data['amount'] = amount
            confidence += 0.3  # Amount is a strong indicator
            self._log(f"Amount detected: {amount}", "bot_logic")

        # Extract date if mentioned
        # CRITICAL: Only add date confidence if there are other indicators
        # This prevents dates from timeoff/overtime flows from triggering reimbursement
        expense_date = self._extract_date(message_lower)
        if expense_date:
            extracted_data['date'] = expense_date
            # Only boost confidence if we already have some reimbursement context
            if confidence > 0 or pattern_matches > 0:
                confidence += 0.2  # Date is a moderate indicator when combined with other signals
                self._log(f"Date detected with context: {expense_date}", "bot_logic")
            else:
                self._log(f"Date detected but no reimbursement context, skipping confidence boost: {expense_date}", "bot_logic")

        # Boost confidence for multiple patterns
        if pattern_matches > 1:
            confidence += 0.2

        # Context-aware adjustments
        # Strong intent keywords boost confidence
        strong_intent_words = ['want', 'need', 'request', 'submit', 'file', 'create', 'get', 'claim']
        if any(word in message_lower for word in strong_intent_words):
            confidence += 0.1

        # Business context keywords
        business_context = ['work', 'company', 'business', 'office', 'meeting', 'client']
        if any(word in message_lower for word in business_context):
            confidence += 0.1

        # Final confidence adjustment
        confidence = min(confidence, 1.0)

        is_reimbursement = confidence >= 0.3

        self._log(f"Reimbursement intent result: {is_reimbursement}, confidence: {confidence:.2f}", "bot_logic")

        return is_reimbursement, confidence, extracted_data

    def _extract_category(self, message: str) -> Optional[str]:
        """Extract expense category from message"""
        message_lower = message.lower()

        # Check for category keywords
        if any(word in message_lower for word in ['miscellaneous', 'general', 'other', 'misc']):
            return 'miscellaneous'
        elif any(word in message_lower for word in ['per diem', 'perdiem', 'daily allowance']):
            return 'per_diem'
        elif any(word in message_lower for word in ['travel', 'accommodation', 'hotel', 'flight', 'transport']):
            return 'travel_accommodation'

        return None

    def _extract_amount(self, message: str) -> Optional[float]:
        """Extract monetary amount from message"""
        # Look for currency patterns
        amount_patterns = [
            r'\$(\d+(?:\.\d{2})?)',  # $123.45
            r'(\d+(?:\.\d{2})?)\s*dollars?',  # 123.45 dollars
            r'(\d+(?:\.\d{2})?)\s*usd',  # 123.45 USD
            r'(\d+(?:\.\d{2})?)\s*jod',  # 123.45 JOD
            r'(\d+(?:\.\d{2})?)',  # Just numbers
        ]

        for pattern in amount_patterns:
            match = re.search(pattern, message)
            if match:
                try:
                    return float(match.group(1))
                except ValueError:
                    continue

        return None

    def _extract_date(self, message: str) -> Optional[str]:
        """Extract date from message (DD/MM format preferred)"""
        # Date patterns
        date_patterns = [
            r'(\d{1,2})/(\d{1,2})/(\d{2,4})',  # DD/MM/YYYY or DD/MM/YY
            r'(\d{1,2})-(\d{1,2})-(\d{2,4})',  # DD-MM-YYYY
            r'(\d{1,2})/(\d{1,2})',  # DD/MM (current year)
        ]

        for pattern in date_patterns:
            match = re.search(pattern, message)
            if match:
                try:
                    if len(match.groups()) == 3:  # Full date
                        day, month, year = match.groups()
                        if len(year) == 2:
                            year = '20' + year
                        return f"{int(day):02d}/{int(month):02d}/{year}"
                    elif len(match.groups()) == 2:  # DD/MM (current year)
                        day, month = match.groups()
                        current_year = datetime.now().year
                        return f"{int(day):02d}/{int(month):02d}/{current_year}"
                except ValueError:
                    continue

        return None

    def get_expense_categories(self) -> List[Dict[str, str]]:
        """Get available expense categories"""
        categories = []
        for key, value in self.expense_categories.items():
            if key in self.supported_categories:
                categories.append({
                    'value': key,
                    'text': value,
                    'type': 'action_reimbursement'
                })
        return categories

    def create_expense_record(self, employee_data: Dict, expense_data: Dict, 
                              odoo_session_data: Dict = None) -> Tuple[bool, Any, Optional[Dict]]:
        """
        Create expense record in Odoo
        Returns: (success, result_data, renewed_session_data)
        """
        try:
            # Validate employee data early to avoid NoneType errors when sessions expire
            if not employee_data or not isinstance(employee_data, dict) or not employee_data.get('id'):
                self._log("Missing or invalid employee_data while creating expense record", "bot_logic")
                return False, (
                    "I couldn't verify your employee profile (session may have expired). "
                    "Please log in again and resubmit your reimbursement."
                ), None
            
            # Validate that session user_id matches employee's user_id (if available)
            if odoo_session_data and odoo_session_data.get('user_id'):
                session_user_id = odoo_session_data.get('user_id')
                # Extract employee user_id from employee_data (could be direct field or nested in user_id_details)
                employee_user_id = None
                if 'user_id' in employee_data:
                    user_id_val = employee_data.get('user_id')
                    if isinstance(user_id_val, (list, tuple)) and len(user_id_val) > 0:
                        employee_user_id = user_id_val[0]
                    elif isinstance(user_id_val, int):
                        employee_user_id = user_id_val
                elif 'user_id_details' in employee_data:
                    user_id_details = employee_data.get('user_id_details')
                    if isinstance(user_id_details, dict) and user_id_details.get('id'):
                        employee_user_id = user_id_details.get('id')
                
                # If we have both user_ids, validate they match
                if employee_user_id is not None and session_user_id != employee_user_id:
                    self._log(
                        f"Session user_id mismatch: session has user_id={session_user_id}, "
                        f"but employee has user_id={employee_user_id}. This may cause access errors.",
                        "bot_logic"
                    )
                    return False, (
                        "Session user mismatch detected. Please refresh the page and try again. "
                        "If the issue persists, please log out and log back in."
                    ), None
            
            # Use stateless requests if session data provided
            use_stateless = odoo_session_data and odoo_session_data.get('session_id') and odoo_session_data.get('user_id')
            
            # Helper method to make requests (stateless or regular)
            def _make_request(model, method, params):
                if use_stateless:
                    ok, res, renewed = self._make_odoo_request_stateless(
                        model, method, params,
                        session_id=odoo_session_data['session_id'],
                        user_id=odoo_session_data['user_id'],
                        username=odoo_session_data.get('username'),
                        password=odoo_session_data.get('password')
                    )
                    return ok, res, renewed
                else:
                    ok, res = self._make_odoo_request(model, method, params)
                    return ok, res, None
            
            self._log(f"Creating expense record with data: {expense_data}", "bot_logic")

            # Get product ID first
            product_id = self._get_product_id_for_category(expense_data.get('category', 'miscellaneous'))

            # Log employee data for debugging
            self._log(f"Employee data: {employee_data}", "bot_logic")
            self._log(f"Employee company_id: {employee_data.get('company_id')}", "bot_logic")

            # Prepare expense data - let Odoo inherit company from employee
            # Resolve commonly referenced custom field names dynamically by label where possible
            def _resolve_field_by_label(model: str, label: str) -> Optional[str]:
                try:
                    params = {
                        'args': [[]],
                        'kwargs': {'attributes': ['string']}
                    }
                    success, fields = self._make_odoo_request(model, 'fields_get', params, odoo_session_data)
                    if success and isinstance(fields, dict):
                        for tech, meta in fields.items():
                            if isinstance(meta, dict) and meta.get('string') and str(meta.get('string')).strip().lower() == label.strip().lower():
                                return tech
                except Exception:
                    return None
                return None
            

            # Default description per category if not provided
            default_desc = expense_data.get('description', '')
            cat_key = (expense_data.get('category') or '').lower()
            if not default_desc and cat_key == 'miscellaneous':
                default_desc = '[EXP_GEN] Miscellaneous'
            if not default_desc and cat_key == 'per_diem':
                default_desc = '[PER_DIEM] Per Diem'
            if not default_desc and cat_key == 'travel_accommodation':
                default_desc = '[TRANS & ACC] Travel & Accommodation'

            expense_values = {
                'name': default_desc,
                'total_amount_currency': expense_data.get('amount', 0.0),
                'date': self._convert_date_format(expense_data.get('date', datetime.now().strftime('%d/%m/%Y'))),
                'employee_id': employee_data.get('id'),
                'state': 'draft'  # Start as draft
            }
            
            # Add currency_id if provided
            currency_id = expense_data.get('currency_id')
            if currency_id:
                expense_values['currency_id'] = currency_id

            # Ensure hr.expense.company_id matches the employee's company
            try:
                emp_company_val = employee_data.get('company_id')
                emp_company_id = None
                if isinstance(emp_company_val, (list, tuple)) and len(emp_company_val) > 0:
                    emp_company_id = emp_company_val[0]
                elif isinstance(emp_company_val, int):
                    emp_company_id = emp_company_val
                if emp_company_id:
                    expense_values['company_id'] = emp_company_id
            except Exception:
                # If resolution fails, let Odoo enforce constraints
                pass

            # Require a valid product_id to avoid creating with wrong category
            if not product_id:
                return False, (
                    "I couldn't find the configured expense category product in Odoo. "
                    "Please ensure the product exists with the correct Internal Reference and is allowed to be expensed."
                )

            expense_values['product_id'] = product_id

            # Only add custom fields if they have values
            attached_link = expense_data.get('attached_link', '')
            if attached_link:
                expense_values['x_studio_attached_link'] = attached_link
            
            # Add analytic distribution if provided
            analytic_distribution = expense_data.get('analytic_distribution')
            if analytic_distribution and isinstance(analytic_distribution, list):
                # Try to resolve the actual field name dynamically
                analytic_field_name = _resolve_field_by_label('hr.expense', 'Analytic Distribution')
                if not analytic_field_name:
                    analytic_field_name = 'analytic_distribution'  # Fallback to default
                
                self._log(f"Using analytic distribution field name: {analytic_field_name}", "bot_logic")
                
                try:
                    if len(analytic_distribution) > 0:
                        first_line = analytic_distribution[0]
                        if isinstance(first_line, dict):
                            project_id = first_line.get('project_id')
                            market_id = first_line.get('market_id')
                            pool_id = first_line.get('pool_id')
                            
                            account_ids = []
                            if project_id:
                                account_ids.append(int(project_id))
                            if market_id:
                                account_ids.append(int(market_id))
                            if pool_id:
                                account_ids.append(int(pool_id))
                            
                            if account_ids:
                                # Odoo stores ONE line with all columns using a comma-separated string key
                                # Format: {'153,265,269': 100.0} creates one row with Project, Market, Pool columns all filled
                                account_ids_str = ','.join(str(int(acc_id)) for acc_id in account_ids)
                                analytic_dict = {account_ids_str: 100.0}
                                
                                expense_values[analytic_field_name] = analytic_dict
                        else:
                            self._log(f"Invalid line format in analytic distribution", "bot_logic")
                    else:
                        self._log(f"Empty analytic distribution list", "bot_logic")
                except Exception as e:
                    self._log(f"Error converting analytic distribution to dict format: {e}", "bot_logic")
                    import traceback
                    traceback.print_exc()
            # PER_DIEM specific custom fields (dates)
            if (expense_data.get('category') or '').lower() == 'per_diem':
                pd_from = expense_data.get('per_diem_from')
                pd_to = expense_data.get('per_diem_to')
                # Guard against same/invalid dates which trigger server automation errors
                try:
                    from datetime import datetime as _dt, timedelta as _td
                    if pd_from and pd_to:
                        d1 = _dt.strptime(pd_from, '%d/%m/%Y')
                        d2 = _dt.strptime(pd_to, '%d/%m/%Y')
                        if d2 <= d1:
                            # Enforce minimum one-day span to avoid division-by-zero in automation
                            d2 = d1 + _td(days=1)
                            expense_data['per_diem_to'] = d2.strftime('%d/%m/%Y')
                        # Compute INCLUSIVE days abroad and pass it proactively (e.g., 28–30 => 3)
                        days_abroad = max(1, (d2 - d1).days + 1)
                        # Some implementations expect inclusive days; if ui shows inclusive, bump by +1
                        # We choose max of computed and 1 to avoid zero
                        try:
                            expense_values['x_studio_days_abroad'] = int(days_abroad)
                        except Exception:
                            pass
                        # Also set built-in hr.expense quantity to days to avoid any /quantity logic
                        try:
                            expense_values['quantity'] = int(days_abroad)
                        except Exception:
                            pass
                        # If the 'Days Abroad' field has a different technical name, resolve by label and set it too
                        field_by_label = _resolve_field_by_label('hr.expense', 'Days Abroad')
                        if field_by_label and field_by_label not in expense_values:
                            try:
                                expense_values[field_by_label] = int(days_abroad)
                            except Exception:
                                pass
                    if expense_data.get('per_diem_from'):
                        expense_values['x_studio_from'] = self._convert_date_format(expense_data.get('per_diem_from'))
                    if expense_data.get('per_diem_to'):
                        expense_values['x_studio_to'] = self._convert_date_format(expense_data.get('per_diem_to'))
                except Exception:
                    # If parsing fails, skip setting to prevent bad values
                    pass
                # If destination id missing but a name was provided, try to resolve it
                if not expense_data.get('per_diem_destination_id') and expense_data.get('per_diem_destination_name'):
                    try:
                        name = str(expense_data.get('per_diem_destination_name')).strip()
                        params = {
                            'args': [[['name', '=', name]]],
                            'kwargs': {'limit': 1}
                        }
                        ok, res = self._make_odoo_request('res.country.state', 'search', params)
                        if ok and res:
                            expense_values['x_studio_destination'] = res[0]
                    except Exception:
                        pass
            if expense_data.get('per_diem_destination_id'):
                # Many2one to res.country.state expects the ID
                expense_values['x_studio_destination'] = expense_data.get('per_diem_destination_id')

            self._log(f"Expense values: {expense_values}", "bot_logic")

            # Helper function to submit expense to "Submitted" status
            def _submit_expense(expense_id: int) -> Tuple[bool, str, Optional[Dict]]:
                """Submit expense through the two-step process to reach 'Submitted' status."""
                try:
                    # DEBUG: Check initial expense state before submission
                    self._log(f"[REIMBURSEMENT DEBUG] Starting submission for expense #{expense_id}", "bot_logic")
                    params_check_initial = {
                        'args': [[expense_id]],
                        'kwargs': {'fields': ['state', 'name', 'employee_id', 'sheet_id', 'company_id']}
                    }
                    ok_check_initial, res_check_initial, renewed_check_initial = _make_request('hr.expense', 'read', params_check_initial)
                    if renewed_check_initial:
                        odoo_session_data.update(renewed_check_initial)
                    expense_company_id = None
                    if ok_check_initial and res_check_initial:
                        expense_initial = res_check_initial[0] if isinstance(res_check_initial, list) else res_check_initial
                        initial_state = expense_initial.get('state', 'unknown')
                        initial_sheet_id = expense_initial.get('sheet_id')
                        company_val = expense_initial.get('company_id')
                        if isinstance(company_val, (list, tuple)) and company_val:
                            expense_company_id = company_val[0]
                        elif isinstance(company_val, int):
                            expense_company_id = company_val
                        self._log(f"[REIMBURSEMENT DEBUG] Initial expense state: '{initial_state}', sheet_id: {initial_sheet_id}", "bot_logic")
                    else:
                        self._log(f"[REIMBURSEMENT DEBUG] Failed to read initial expense state: {res_check_initial}", "bot_logic")
                    if not expense_company_id:
                        try:
                            emp_company_val = employee_data.get('company_id') if isinstance(employee_data, dict) else None
                            if isinstance(emp_company_val, (list, tuple)) and emp_company_val:
                                expense_company_id = emp_company_val[0]
                            elif isinstance(emp_company_val, int):
                                expense_company_id = emp_company_val
                        except Exception:
                            pass
                    submit_context = {}
                    if expense_company_id:
                        submit_context = {
                            'allowed_company_ids': [expense_company_id],
                            'force_company': expense_company_id,
                            'company_id': expense_company_id
                        }
                    
                    # Step 1: Call action_submit_expenses on hr.expense to create report/sheet
                    self._log(f"[REIMBURSEMENT DEBUG] Step 1: Calling action_submit_expenses on expense #{expense_id}", "bot_logic")
                    params_submit_expenses = {
                        'args': [[expense_id]],
                        'kwargs': {'context': submit_context} if submit_context else {}
                    }
                    ok_submit_expenses, res_submit_expenses, renewed_submit_expenses = _make_request(
                        'hr.expense', 'action_submit_expenses', params_submit_expenses
                    )
                    if renewed_submit_expenses:
                        odoo_session_data.update(renewed_submit_expenses)
                    
                    if not ok_submit_expenses:
                        self._log(f"[REIMBURSEMENT DEBUG] ❌ action_submit_expenses FAILED for expense #{expense_id}: {res_submit_expenses}", "bot_logic")
                        # Check expense state after failure
                        params_check_fail = {
                            'args': [[expense_id]],
                            'kwargs': {'fields': ['state', 'sheet_id']}
                        }
                        ok_check_fail, res_check_fail, _ = _make_request('hr.expense', 'read', params_check_fail)
                        if ok_check_fail and res_check_fail:
                            expense_fail = res_check_fail[0] if isinstance(res_check_fail, list) else res_check_fail
                            fail_state = expense_fail.get('state', 'unknown')
                            self._log(f"[REIMBURSEMENT DEBUG] Expense state after action_submit_expenses failure: '{fail_state}'", "bot_logic")
                        return False, f"Failed to submit expenses: {res_submit_expenses}", renewed_submit_expenses
                    
                    self._log(f"[REIMBURSEMENT DEBUG] ✅ action_submit_expenses succeeded for expense #{expense_id}", "bot_logic")
                    
                    # Step 2: Read expense to get sheet_id and check state
                    self._log(f"[REIMBURSEMENT DEBUG] Step 2: Reading expense #{expense_id} to get sheet_id and state", "bot_logic")
                    params_read = {
                        'args': [[expense_id]],
                        'kwargs': {'fields': ['sheet_id', 'state', 'name']}
                    }
                    ok_read, res_read, renewed_read = _make_request('hr.expense', 'read', params_read)
                    if renewed_read:
                        odoo_session_data.update(renewed_read)
                    
                    if not ok_read or not res_read:
                        self._log(f"[REIMBURSEMENT DEBUG] ❌ Failed to read expense #{expense_id} after action_submit_expenses: {res_read}", "bot_logic")
                        return False, f"Failed to read expense: {res_read}", renewed_read
                    
                    # Extract sheet_id and state from read result
                    expense_record = res_read[0] if isinstance(res_read, list) else res_read
                    sheet_id_val = expense_record.get('sheet_id')
                    expense_state_after_submit = expense_record.get('state', 'unknown')
                    
                    self._log(f"[REIMBURSEMENT DEBUG] Expense state after action_submit_expenses: '{expense_state_after_submit}', sheet_id: {sheet_id_val}", "bot_logic")
                    
                    if not sheet_id_val:
                        self._log(f"[REIMBURSEMENT DEBUG] ❌ No sheet_id found for expense #{expense_id} after action_submit_expenses. State: '{expense_state_after_submit}'", "bot_logic")
                        return False, f"No expense sheet found after submission. Expense state: '{expense_state_after_submit}'", renewed_read
                    
                    # sheet_id_val might be a list [id, name] or just an id
                    sheet_id = sheet_id_val[0] if isinstance(sheet_id_val, (list, tuple)) else sheet_id_val
                    self._log(f"[REIMBURSEMENT DEBUG] Extracted sheet_id: {sheet_id}", "bot_logic")
                    
                    # DEBUG: Check sheet state before submitting
                    params_check_sheet = {
                        'args': [[sheet_id]],
                        'kwargs': {'fields': ['state', 'name', 'expense_line_ids']}
                    }
                    ok_check_sheet, res_check_sheet, renewed_check_sheet = _make_request('hr.expense.sheet', 'read', params_check_sheet)
                    if renewed_check_sheet:
                        odoo_session_data.update(renewed_check_sheet)
                    if ok_check_sheet and res_check_sheet:
                        sheet_before = res_check_sheet[0] if isinstance(res_check_sheet, list) else res_check_sheet
                        sheet_state_before = sheet_before.get('state', 'unknown')
                        self._log(f"[REIMBURSEMENT DEBUG] Sheet #{sheet_id} state before action_submit_sheet: '{sheet_state_before}'", "bot_logic")
                    else:
                        self._log(f"[REIMBURSEMENT DEBUG] Failed to read sheet state before submission: {res_check_sheet}", "bot_logic")
                    
                    # Step 3: Call action_submit_sheet on hr.expense.sheet
                    self._log(f"[REIMBURSEMENT DEBUG] Step 3: Calling action_submit_sheet on sheet #{sheet_id}", "bot_logic")
                    params_submit_sheet = {
                        'args': [[sheet_id]],
                        'kwargs': {'context': submit_context} if submit_context else {}
                    }
                    ok_submit_sheet, res_submit_sheet, renewed_submit_sheet = _make_request(
                        'hr.expense.sheet', 'action_submit_sheet', params_submit_sheet
                    )
                    if renewed_submit_sheet:
                        odoo_session_data.update(renewed_submit_sheet)
                    
                    if not ok_submit_sheet:
                        self._log(f"[REIMBURSEMENT DEBUG] ❌ action_submit_sheet FAILED for sheet #{sheet_id}: {res_submit_sheet}", "bot_logic")
                        # Check both sheet and expense states after failure
                        params_check_sheet_fail = {
                            'args': [[sheet_id]],
                            'kwargs': {'fields': ['state']}
                        }
                        ok_sheet_fail, res_sheet_fail, _ = _make_request('hr.expense.sheet', 'read', params_check_sheet_fail)
                        if ok_sheet_fail and res_sheet_fail:
                            sheet_fail = res_sheet_fail[0] if isinstance(res_sheet_fail, list) else res_sheet_fail
                            sheet_state_fail = sheet_fail.get('state', 'unknown')
                            self._log(f"[REIMBURSEMENT DEBUG] Sheet state after action_submit_sheet failure: '{sheet_state_fail}'", "bot_logic")
                        
                        params_check_expense_fail = {
                            'args': [[expense_id]],
                            'kwargs': {'fields': ['state']}
                        }
                        ok_expense_fail, res_expense_fail, _ = _make_request('hr.expense', 'read', params_check_expense_fail)
                        if ok_expense_fail and res_expense_fail:
                            expense_fail = res_expense_fail[0] if isinstance(res_expense_fail, list) else res_expense_fail
                            expense_state_fail = expense_fail.get('state', 'unknown')
                            self._log(f"[REIMBURSEMENT DEBUG] Expense state after action_submit_sheet failure: '{expense_state_fail}'", "bot_logic")
                        
                        return False, f"Failed to submit sheet: {res_submit_sheet}", renewed_submit_sheet
                    
                    self._log(f"[REIMBURSEMENT DEBUG] ✅ action_submit_sheet succeeded for sheet #{sheet_id}", "bot_logic")
                    
                    # DEBUG: Verify final states after submission
                    params_check_final_expense = {
                        'args': [[expense_id]],
                        'kwargs': {'fields': ['state', 'sheet_id']}
                    }
                    ok_final_expense, res_final_expense, renewed_final_expense = _make_request('hr.expense', 'read', params_check_final_expense)
                    if renewed_final_expense:
                        odoo_session_data.update(renewed_final_expense)
                    if ok_final_expense and res_final_expense:
                        expense_final = res_final_expense[0] if isinstance(res_final_expense, list) else res_final_expense
                        final_expense_state = expense_final.get('state', 'unknown')
                        self._log(f"[REIMBURSEMENT DEBUG] Final expense #{expense_id} state: '{final_expense_state}'", "bot_logic")
                        
                        if final_expense_state not in ['submitted', 'approve']:
                            self._log(f"[REIMBURSEMENT DEBUG] ⚠️ WARNING: Expense #{expense_id} is NOT in 'submitted' state. Current state: '{final_expense_state}'", "bot_logic")
                    else:
                        self._log(f"[REIMBURSEMENT DEBUG] Failed to verify final expense state: {res_final_expense}", "bot_logic")
                    
                    params_check_final_sheet = {
                        'args': [[sheet_id]],
                        'kwargs': {'fields': ['state']}
                    }
                    ok_final_sheet, res_final_sheet, renewed_final_sheet = _make_request('hr.expense.sheet', 'read', params_check_final_sheet)
                    if renewed_final_sheet:
                        odoo_session_data.update(renewed_final_sheet)
                    if ok_final_sheet and res_final_sheet:
                        sheet_final = res_final_sheet[0] if isinstance(res_final_sheet, list) else res_final_sheet
                        final_sheet_state = sheet_final.get('state', 'unknown')
                        self._log(f"[REIMBURSEMENT DEBUG] Final sheet #{sheet_id} state: '{final_sheet_state}'", "bot_logic")
                        
                        if final_sheet_state not in ['submit', 'approve']:
                            self._log(f"[REIMBURSEMENT DEBUG] ⚠️ WARNING: Sheet #{sheet_id} is NOT in 'submit' state. Current state: '{final_sheet_state}'", "bot_logic")
                    else:
                        self._log(f"[REIMBURSEMENT DEBUG] Failed to verify final sheet state: {res_final_sheet}", "bot_logic")
                    
                    # Determine success based on final state
                    if ok_final_expense and res_final_expense:
                        expense_final = res_final_expense[0] if isinstance(res_final_expense, list) else res_final_expense
                        final_expense_state = expense_final.get('state', 'unknown')
                        if final_expense_state in ['submitted', 'approve']:
                            self._log(f"[REIMBURSEMENT DEBUG] ✅ SUCCESS: Expense #{expense_id} successfully moved to '{final_expense_state}' state", "bot_logic")
                            return True, f"Expense submitted successfully (state: {final_expense_state})", renewed_submit_sheet
                        else:
                            self._log(f"[REIMBURSEMENT DEBUG] ⚠️ PARTIAL SUCCESS: Expense #{expense_id} created but state is '{final_expense_state}' (expected 'submitted')", "bot_logic")
                            return True, f"Expense created but may require manual submission. Current state: {final_expense_state}", renewed_submit_sheet
                    else:
                        # If we can't verify state, assume success if action_submit_sheet succeeded
                        self._log(f"[REIMBURSEMENT DEBUG] ⚠️ Could not verify final state, but action_submit_sheet succeeded", "bot_logic")
                        return True, "Expense submitted successfully", renewed_submit_sheet
                    
                except Exception as e:
                    self._log(f"[REIMBURSEMENT DEBUG] ❌ EXCEPTION submitting expense #{expense_id}: {e}", "general")
                    import traceback
                    traceback.print_exc()
                    return False, f"Error submitting expense: {str(e)}", None

            # Create strategy
            if (expense_data.get('category') or '').lower() == 'per_diem':
                # Prefer single create with final PER_DIEM values first (best outcome)
                try:
                    self._log("Attempting single-create with PER_DIEM product", "bot_logic")
                    params_sc = {'args': [expense_values], 'kwargs': {}}
                    ok_sc, res_sc, renewed_sc = _make_request('hr.expense', 'create', params_sc)
                    if renewed_sc:
                        odoo_session_data.update(renewed_sc)  # Update session for subsequent requests
                    if ok_sc:
                        expense_id = res_sc
                        self._log(f"[REIMBURSEMENT DEBUG] PER_DIEM expense created with ID: {expense_id}", "bot_logic")
                        # DEBUG: Check expense state immediately after creation
                        try:
                            params_check_created = {
                                'args': [[expense_id]],
                                'kwargs': {'fields': ['state', 'name', 'employee_id']}
                            }
                            ok_check_created, res_check_created, renewed_check_created = _make_request('hr.expense', 'read', params_check_created)
                            if renewed_check_created:
                                odoo_session_data.update(renewed_check_created)
                            if ok_check_created and res_check_created:
                                expense_created = res_check_created[0] if isinstance(res_check_created, list) else res_check_created
                                created_state = expense_created.get('state', 'unknown')
                                self._log(f"[REIMBURSEMENT DEBUG] PER_DIEM expense #{expense_id} state immediately after creation: '{created_state}'", "bot_logic")
                        except Exception as e:
                            self._log(f"[REIMBURSEMENT DEBUG] Error checking PER_DIEM expense state after creation: {e}", "bot_logic")
                        # Submit expense to "Submitted" status
                        submit_ok, submit_msg, submit_renewed = _submit_expense(expense_id)
                        if submit_renewed:
                            odoo_session_data.update(submit_renewed)
                        if not submit_ok:
                            self._log(f"[REIMBURSEMENT DEBUG] PER_DIEM expense created but submission failed: {submit_msg}", "bot_logic")
                            # Still return success since expense was created
                        return True, {'id': expense_id, 'message': f"Expense record #{expense_id} created successfully."}, submit_renewed or renewed_sc
                    else:
                        self._log(f"Single-create failed, falling back to two-phase: {res_sc}", "bot_logic")
                except Exception as e:
                    self._log(f"Single-create exception, falling back to two-phase: {e}", "general")

                # Two-phase create to dodge automations on create
                try:
                    # 1) Create with safe product (miscellaneous) to avoid per diem automation on create
                    safe_values = dict(expense_values)
                    safe_product = self._get_product_id_for_category('miscellaneous')
                    if safe_product:
                        safe_values['product_id'] = safe_product
                    self._log(f"Creating PER_DIEM in two phases (safe create)", "bot_logic")
                    params_create = {'args': [safe_values], 'kwargs': {}}
                    ok_create, res_create, renewed_create = _make_request('hr.expense', 'create', params_create)
                    if renewed_create:
                        odoo_session_data.update(renewed_create)
                    if not ok_create:
                        self._log(f"Safe create failed: {res_create}", "bot_logic")
                        return False, res_create, renewed_create
                    expense_id = res_create

                    # 2a) First write: push dates, days_abroad, quantity, destination ONLY (no product change yet)
                    pre_vals = {}
                    for k in ['x_studio_from', 'x_studio_to', 'x_studio_days_abroad', 'quantity', 'x_studio_destination']:
                        if k in expense_values:
                            pre_vals[k] = expense_values[k]
                    # Ensure unit_amount is non-zero to avoid divide-by-zero in custom formulas
                    pre_vals['unit_amount'] = pre_vals.get('unit_amount', 1)
                    self._log(f"Applying pre-values to expense #{expense_id}: {pre_vals}", "bot_logic")
                    if pre_vals:
                        ok_pre, res_pre, renewed_pre = _make_request('hr.expense', 'write', {'args': [[expense_id], pre_vals], 'kwargs': {}})
                        if renewed_pre:
                            odoo_session_data.update(renewed_pre)
                        if not ok_pre:
                            self._log(f"Pre write failed for expense #{expense_id}: {res_pre}", "bot_logic")
                            return False, res_pre, renewed_pre

                    # 2b) Second write: switch product to PER_DIEM only
                    final_vals = {'product_id': expense_values.get('product_id')}
                    self._log(f"Switching product to PER_DIEM for expense #{expense_id}", "bot_logic")
                    ok_write, res_write, renewed_write = _make_request('hr.expense', 'write', {'args': [[expense_id], final_vals], 'kwargs': {}})
                    if renewed_write:
                        odoo_session_data.update(renewed_write)
                    if not ok_write:
                        self._log(f"Final write failed for expense #{expense_id}: {res_write}", "bot_logic")
                        # Fallback: attempt product switch with days fields again in same write
                        fallback_vals = {
                            'product_id': expense_values.get('product_id'),
                            'x_studio_from': pre_vals.get('x_studio_from'),
                            'x_studio_to': pre_vals.get('x_studio_to'),
                            'x_studio_days_abroad': pre_vals.get('x_studio_days_abroad'),
                            'quantity': pre_vals.get('quantity'),
                            'x_studio_destination': pre_vals.get('x_studio_destination'),
                            'unit_amount': pre_vals.get('unit_amount', 1)
                        }
                        self._log(f"Retrying product switch with fallback values for expense #{expense_id}: {fallback_vals}", "bot_logic")
                        ok_write2, res_write2, renewed_write2 = _make_request('hr.expense', 'write', {'args': [[expense_id], fallback_vals], 'kwargs': {}})
                        if renewed_write2:
                            odoo_session_data.update(renewed_write2)
                        if not ok_write2:
                            return False, res_write, renewed_write2 or renewed_write
                    
                    # Return the latest renewed session
                    latest_renewed = renewed_write2 if ok_write2 else renewed_write
                    self._log(f"[REIMBURSEMENT DEBUG] PER_DIEM two-phase expense created with ID: {expense_id}", "bot_logic")
                    # DEBUG: Check expense state immediately after creation
                    try:
                        params_check_created = {
                            'args': [[expense_id]],
                            'kwargs': {'fields': ['state', 'name', 'employee_id']}
                        }
                        ok_check_created, res_check_created, renewed_check_created = _make_request('hr.expense', 'read', params_check_created)
                        if renewed_check_created:
                            odoo_session_data.update(renewed_check_created)
                        if ok_check_created and res_check_created:
                            expense_created = res_check_created[0] if isinstance(res_check_created, list) else res_check_created
                            created_state = expense_created.get('state', 'unknown')
                            self._log(f"[REIMBURSEMENT DEBUG] PER_DIEM two-phase expense #{expense_id} state immediately after creation: '{created_state}'", "bot_logic")
                    except Exception as e:
                        self._log(f"[REIMBURSEMENT DEBUG] Error checking PER_DIEM two-phase expense state after creation: {e}", "bot_logic")
                    # Submit expense to "Submitted" status
                    submit_ok, submit_msg, submit_renewed = _submit_expense(expense_id)
                    if submit_renewed:
                        odoo_session_data.update(submit_renewed)
                    if not submit_ok:
                        self._log(f"[REIMBURSEMENT DEBUG] PER_DIEM two-phase expense created but submission failed: {submit_msg}", "bot_logic")
                        # Still return success since expense was created
                    return True, {'id': expense_id, 'message': f"Expense record #{expense_id} created successfully."}, submit_renewed or latest_renewed
                except Exception as e:
                    self._log(f"Two-phase create error: {e}", "general")
                    # Fallback to single create
                    params = {'args': [expense_values], 'kwargs': {}}
                    self._log(f"Fallback single create for PER_DIEM", "bot_logic")
                    success, result, renewed_fallback = _make_request('hr.expense', 'create', params)
                    if success:
                        expense_id = result
                        self._log(f"[REIMBURSEMENT DEBUG] PER_DIEM fallback expense created with ID: {expense_id}", "bot_logic")
                        # DEBUG: Check expense state immediately after creation
                        try:
                            params_check_created = {
                                'args': [[expense_id]],
                                'kwargs': {'fields': ['state', 'name', 'employee_id']}
                            }
                            ok_check_created, res_check_created, renewed_check_created = _make_request('hr.expense', 'read', params_check_created)
                            if renewed_check_created:
                                odoo_session_data.update(renewed_check_created)
                            if ok_check_created and res_check_created:
                                expense_created = res_check_created[0] if isinstance(res_check_created, list) else res_check_created
                                created_state = expense_created.get('state', 'unknown')
                                self._log(f"[REIMBURSEMENT DEBUG] PER_DIEM fallback expense #{expense_id} state immediately after creation: '{created_state}'", "bot_logic")
                        except Exception as e:
                            self._log(f"[REIMBURSEMENT DEBUG] Error checking PER_DIEM fallback expense state after creation: {e}", "bot_logic")
                        # Submit expense to "Submitted" status
                        submit_ok, submit_msg, submit_renewed = _submit_expense(expense_id)
                        if submit_renewed:
                            odoo_session_data.update(submit_renewed)
                        if not submit_ok:
                            self._log(f"[REIMBURSEMENT DEBUG] PER_DIEM fallback expense created but submission failed: {submit_msg}", "bot_logic")
                            # Still return success since expense was created
                        return True, {'id': expense_id, 'message': f"Expense record #{expense_id} created successfully."}, submit_renewed or renewed_fallback
                    else:
                        return False, result, renewed_fallback
            else:
                # Default single create for other categories
                params = {
                    'args': [expense_values],
                    'kwargs': {}
                }

                self._log(f"Making Odoo request to create expense", "bot_logic")
                success, result, renewed = _make_request('hr.expense', 'create', params)

                if success:
                    expense_id = result
                    self._log(f"[REIMBURSEMENT DEBUG] Expense record created successfully with ID: {expense_id}", "bot_logic")
                    
                    # DEBUG: Check expense state immediately after creation
                    try:
                        params_check_created = {
                            'args': [[expense_id]],
                            'kwargs': {'fields': ['state', 'name', 'employee_id']}
                        }
                        ok_check_created, res_check_created, renewed_check_created = _make_request('hr.expense', 'read', params_check_created)
                        if renewed_check_created:
                            odoo_session_data.update(renewed_check_created)
                        if ok_check_created and res_check_created:
                            expense_created = res_check_created[0] if isinstance(res_check_created, list) else res_check_created
                            created_state = expense_created.get('state', 'unknown')
                            self._log(f"[REIMBURSEMENT DEBUG] Expense #{expense_id} state immediately after creation: '{created_state}'", "bot_logic")
                    except Exception as e:
                        self._log(f"[REIMBURSEMENT DEBUG] Error checking expense state after creation: {e}", "bot_logic")
                    
                    # Submit expense to "Submitted" status
                    submit_ok, submit_msg, submit_renewed = _submit_expense(expense_id)
                    if submit_renewed:
                        odoo_session_data.update(submit_renewed)
                    if not submit_ok:
                        self._log(f"[REIMBURSEMENT DEBUG] Expense created but submission failed: {submit_msg}", "bot_logic")
                        # Still return success since expense was created
                    return True, {
                        'id': expense_id,
                        'message': f"Expense record #{expense_id} created successfully."
                    }, submit_renewed or renewed
                else:
                    self._log(f"[REIMBURSEMENT DEBUG] Failed to create expense record: {result}", "bot_logic")
                    return False, result, renewed

        except Exception as e:
            self._log(f"Error creating expense record: {e}", "general")
            import traceback
            traceback.print_exc()
            return False, str(e), None

    def _get_product_id_for_category(self, category: str) -> Optional[int]:
        """Resolve the Odoo product ID for a given expense category.
        Strategy:
        1) Search by Internal Reference (default_code) with can_be_expensed=True
        2) Fallback to search by exact name, then ilike
        3) If still not found, return None (do not fall back to an unsafe ID)
        """
        try:
            category_key = (category or '').strip().lower()
            default_code = self.category_default_codes.get(category_key)

            # 1) Search by default_code
            if default_code:
                params = {
                    'args': [[
                        ['default_code', '=', default_code],
                        ['can_be_expensed', '=', True]
                    ]],
                    'kwargs': {'limit': 1}
                }
                success, result = self._make_odoo_request('product.product', 'search', params)
                if success and result:
                    product_id_found = result[0]
                    self._log(f"Resolved product by default_code '{default_code}': {product_id_found}", "bot_logic")
                    return product_id_found

            # 2) Fallback to name-based search
            category_display_name = self.expense_categories.get(category_key)
            if category_display_name:
                # Try exact name match first (strip code prefix if present)
                # e.g., '[EXP_GEN] Miscellaneous' -> 'Miscellaneous'
                exact_name = category_display_name
                if ']' in exact_name:
                    exact_name = exact_name.split(']', 1)[1].strip()

                # 2a) Exact name
                params_exact = {
                    'args': [[
                        ['name', '=', exact_name],
                        ['can_be_expensed', '=', True]
                    ]],
                    'kwargs': {'limit': 1}
                }
                success_exact, result_exact = self._make_odoo_request('product.product', 'search', params_exact)
                if success_exact and result_exact:
                    product_id_found = result_exact[0]
                    self._log(f"Resolved product by exact name '{exact_name}': {product_id_found}", "bot_logic")
                    return product_id_found

                # 2b) ilike the full display (with code)
                params_ilike = {
                    'args': [[
                        ['name', 'ilike', category_display_name],
                        ['can_be_expensed', '=', True]
                    ]],
                    'kwargs': {'limit': 1}
                }
                success_ilike, result_ilike = self._make_odoo_request('product.product', 'search', params_ilike)
                if success_ilike and result_ilike:
                    product_id_found = result_ilike[0]
                    self._log(f"Resolved product by ilike name '{category_display_name}': {product_id_found}", "bot_logic")
                    return product_id_found

            # 3) Not found
            self._log(f"No product found for category '{category_key}'.", "bot_logic")
            return None

        except Exception as e:
            self._log(f"Error getting product ID for category {category}: {e}", "bot_logic")
            return None

    def _convert_date_format(self, date_str: str) -> str:
        """Convert date from DD/MM/YYYY to YYYY-MM-DD format for Odoo"""
        try:
            from datetime import datetime
            # Parse DD/MM/YYYY format
            parsed_date = datetime.strptime(date_str, '%d/%m/%Y')
            # Return in YYYY-MM-DD format
            return parsed_date.strftime('%Y-%m-%d')
        except ValueError:
            # If parsing fails, return current date in YYYY-MM-DD format
            return datetime.now().strftime('%Y-%m-%d')

    def validate_expense_data(self, expense_data: Dict) -> Tuple[bool, str]:
        """Validate expense data before submission"""
        required_fields = ['description', 'category', 'amount', 'date']

        for field in required_fields:
            if not expense_data.get(field):
                return False, f"Missing required field: {field}"

        # Validate amount
        try:
            amount = float(expense_data.get('amount', 0))
            if amount <= 0:
                return False, "Amount must be greater than 0"
        except (ValueError, TypeError):
            return False, "Invalid amount format"

        # Validate date format
        date_str = expense_data.get('date', '')
        try:
            datetime.strptime(date_str, '%d/%m/%Y')
        except ValueError:
            return False, "Invalid date format. Please use DD/MM/YYYY"

        return True, "Valid"

    def _make_odoo_request(self, model: str, method: str, params: Dict, odoo_session_data: Dict = None) -> Tuple[bool, Any]:
        """Make authenticated request to Odoo using web session or stateless request.
        
        Args:
            model: Odoo model name
            method: Method to call
            params: Request parameters
            odoo_session_data: Optional session data dict with session_id, user_id, username, password.
                             If provided, uses stateless requests (preferred to avoid shared state issues).
        
        Returns:
            Tuple[bool, Any]: (success, result_data)
        """
        try:
            # Use stateless requests if session data provided (preferred to avoid shared state issues)
            if odoo_session_data and odoo_session_data.get('session_id') and odoo_session_data.get('user_id'):
                try:
                    result_dict = self.odoo_service.make_authenticated_request(
                        model=model,
                        method=method,
                        args=params.get('args', []),
                        kwargs=params.get('kwargs', {}),
                        session_id=odoo_session_data['session_id'],
                        user_id=odoo_session_data['user_id'],
                        username=odoo_session_data.get('username'),
                        password=odoo_session_data.get('password')
                    )
                    
                    # Check if session was renewed
                    renewed_session = result_dict.pop('_renewed_session', None) if isinstance(result_dict, dict) else None
                    if renewed_session:
                        # Update Flask session if renewed
                        try:
                            from flask import session as flask_session
                            flask_session['odoo_session_id'] = renewed_session['session_id']
                            flask_session['user_id'] = renewed_session['user_id']
                            flask_session.modified = True
                        except Exception:
                            pass
                    
                    # Check for errors in result
                    if 'error' in result_dict:
                        error_data = result_dict.get('error', {})
                        error_msg = error_data.get('message', 'Unknown error') if isinstance(error_data, dict) else str(error_data)
                        error_details = error_data.get('data', {}) if isinstance(error_data, dict) else {}
                        self._log(f"Odoo error details: {error_data}", "bot_logic")
                        return False, f"Odoo error: {error_msg} - {error_details}"
                    
                    # Return success with result
                    if 'result' in result_dict:
                        return True, result_dict['result']
                    else:
                        return False, "No result in Odoo response"
                        
                except Exception as stateless_error:
                    self._log(f"Stateless request failed: {stateless_error}", "bot_logic")
                    return False, f"Stateless request failed: {stateless_error}"
            
            # Fallback to stateful request (using shared odoo_service session)
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
                import requests
                response = requests.post(
                    url,
                    json=data,
                    headers={'Content-Type': 'application/json'},
                    cookies=cookies,
                    timeout=15
                )

            if response.status_code == 200:
                try:
                    result = response.json()
                    self._log(f"Odoo response: {result}", "bot_logic")
                    if 'result' in result:
                        return True, result['result']
                    else:
                        error_data = result.get('error', {})
                        error_msg = error_data.get('message', 'Unknown error')
                        error_details = error_data.get('data', {})
                        error_debug = error_details.get('debug', '') if isinstance(error_details, dict) else ''
                        error_name = error_data.get('name', '') if isinstance(error_data, dict) else ''
                        self._log(f"Odoo error details: {error_data}", "bot_logic")
                        self._log(f"Odoo error name: {error_name}", "bot_logic")
                        self._log(f"Odoo error debug: {error_debug}", "bot_logic")
                        # Return more detailed error message
                        full_error = f"Odoo error: {error_msg}"
                        if error_debug:
                            full_error += f"\n\nDebug: {error_debug[:500]}"
                        return False, full_error
                except Exception as e:
                    return False, f"JSON parsing error: {str(e)}"
            else:
                self._log(f"HTTP error {response.status_code}: {response.text}", "bot_logic")
                return False, f"HTTP error: {response.status_code} - {response.text}"

        except Exception as e:
            self._log(f"Error making Odoo request: {e}", "general")
            return False, f"Request error: {str(e)}"

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
                error_debug = error_data.get('data', {}).get('debug', '') if isinstance(error_data, dict) and isinstance(error_data.get('data'), dict) else ''
                error_name = error_data.get('name', '') if isinstance(error_data, dict) else ''
                self._log(f"Odoo error: {error_msg}", "bot_logic")
                self._log(f"Odoo error name: {error_name}", "bot_logic")
                self._log(f"Odoo error debug: {error_debug}", "bot_logic")
                self._log(f"Full Odoo error data: {error_data}", "bot_logic")
                # Return more detailed error message
                full_error = error_msg
                if error_debug:
                    full_error += f"\n\nDebug: {error_debug[:500]}"  # Limit debug message length
                return False, full_error, renewed_session

            return True, result.get('result'), renewed_session

        except Exception as e:
            self._log(f"Error making stateless Odoo request: {e}", "general")
            return False, f"Request error: {str(e)}", None

    def handle_flow(self, message: str, thread_id: str, employee_data: Dict, odoo_session_data: Dict = None) -> Optional[Dict]:
        """
        Handle reimbursement flow - detect intent and manage form-based process
        Returns: response dict or None if not a reimbursement request
        """
        try:
            # Check for form submission messages first
            if message and message.startswith('submit_reimbursement_request:'):
                return self._handle_reimbursement_form_submission(message, thread_id, employee_data, odoo_session_data)
            
            # Check for confirmation
            if message and message == 'reimbursement_confirm':
                return self._handle_reimbursement_confirmation(thread_id, employee_data, odoo_session_data)
            
            # Check for cancellation
            if message and message == 'reimbursement_cancel':
                if self.session_manager:
                    self.session_manager.clear_session(thread_id)
                return {
                    'message': 'Reimbursement request cancelled.',
                    'thread_id': thread_id,
                    'source': 'reimbursement_service'
                }
            
            # Detect new reimbursement intent first
            is_reimbursement, confidence, extracted_data = self.detect_reimbursement_intent(message)

            # Get active session to check for conflicts with other flows
            active_session = self.session_manager.get_session(thread_id) if self.session_manager else None

            # CRITICAL: Block if another flow is active AND user is trying to start reimbursement
            # This prevents reimbursement from interrupting active timeoff/overtime flows
            if is_reimbursement and confidence >= 0.3:
                if active_session and active_session.get('type') not in (None, 'reimbursement') and active_session.get('state') in ['started', 'active']:
                    other_flow = active_session.get('type', 'another')
                    self._log(f"Blocking reimbursement - active {other_flow} flow detected on thread {thread_id}", "bot_logic")
                    return {
                        'message': 'Sorry, I cannot start a new request until you finish or cancel the current one. To cancel the request, type ***Cancel***.',
                        'thread_id': thread_id,
                        'source': 'reimbursement_service',
                        'session_handled': True
                    }

            if is_reimbursement and confidence >= 0.3:
                # Validate that we have employee data before starting session
                if not employee_data or not isinstance(employee_data, dict) or not employee_data.get('id'):
                    return {
                        'message': "I'd like to help with your reimbursement request, but I need to verify your employee information first. Please try logging out and logging back in, or contact HR for assistance.",
                        'thread_id': thread_id,
                        'source': 'reimbursement_service',
                        'confidence_score': 1.0
                    }

                # Start new reimbursement session
                return self._start_reimbursement_session(message, thread_id, extracted_data, employee_data, odoo_session_data)

            return None

        except Exception as e:
            self._log(f"Error in reimbursement flow: {e}", "general")
            import traceback
            traceback.print_exc()
            return None

    def _get_fresh_odoo_session_data(self, odoo_session_data: Dict = None) -> Dict:
        """Get fresh Odoo session data with refresh token handling - same approach as timeoff's get_current_odoo_session()
        
        Args:
            odoo_session_data: Optional existing session data to update
            
        Returns:
            Dict with session_id, user_id, username, password (or None if not authenticated)
        """
        try:
            from flask import g, session as flask_session, request
            
            # First try to get from Flask's g object (set by chat endpoint) - same as timeoff
            session_data = getattr(g, 'odoo_session_data', None)
            
            # If not available, try to get from Flask session directly
            if not session_data:
                if flask_session.get('authenticated'):
                    session_data = {
                        'session_id': flask_session.get('odoo_session_id'),
                        'user_id': flask_session.get('user_id'),
                        'username': flask_session.get('username'),
                        'password': flask_session.get('password')
                    }
                    # Only return if we have required fields
                    if session_data.get('session_id') and session_data.get('user_id'):
                        # If password is missing, we'll handle refresh token below
                        if session_data.get('password'):
                            return session_data
                    else:
                        session_data = None
            
            # Use provided session_data if available and has password
            if odoo_session_data and odoo_session_data.get('session_id') and odoo_session_data.get('user_id') and odoo_session_data.get('password'):
                return odoo_session_data
            
            # If we have session_id and user_id but missing password, try to get from refresh token
            # Same logic as timeoff's get_current_odoo_session()
            if session_data and session_data.get('session_id') and session_data.get('user_id') and not session_data.get('password'):
                try:
                    # Try to get refresh token from cookies or Flask session
                    refresh_token = None
                    
                    # Check cookies first
                    if hasattr(request, 'cookies'):
                        refresh_token = request.cookies.get('nasma_refresh_token')
                    
                    # If not in cookies, try to get from Flask session
                    if not refresh_token:
                        refresh_token = flask_session.get('refresh_token')
                    
                    if refresh_token:
                        # Get auth_token_service from self (set via set_services) or create new instance
                        # Same approach as timeoff
                        auth_token_service = self.auth_token_service
                        if not auth_token_service:
                            # Try importing directly if not available
                            try:
                                from .auth_token_service import AuthTokenService
                                from ..config.settings import Config
                                import os
                                auth_token_service = AuthTokenService(
                                    supabase_url=Config.SUPABASE_URL,
                                    supabase_key=Config.SUPABASE_SERVICE_ROLE,
                                    jwt_secret=os.getenv('JWT_SECRET_KEY') or getattr(Config, 'SUPABASE_JWT_SECRET', None)
                                )
                            except Exception:
                                pass
                        
                        if auth_token_service:
                            # Verify refresh token and get decrypted password
                            result = auth_token_service.verify_refresh_token(refresh_token)
                            if result:
                                user_id, username, password = result
                                # Update session_data with password
                                session_data['password'] = password
                                session_data['username'] = username
                                self._log(f"Retrieved password from refresh token for user_id: {user_id}", "bot_logic")
                                return session_data
                except Exception as e:
                    self._log(f"Failed to retrieve password from refresh token: {str(e)}", "bot_logic")
            
            # Return session_data if we have it, otherwise return provided odoo_session_data or None
            return session_data if session_data else odoo_session_data
            
        except Exception as e:
            self._log(f"Error getting fresh Odoo session data: {str(e)}", "bot_logic")
            # Return provided session_data as fallback
            return odoo_session_data

    def _start_reimbursement_session(self, message: str, thread_id: str, extracted_data: Dict, employee_data: Dict, odoo_session_data: Dict = None) -> Dict:
        """Start a new reimbursement request session - returns form widget"""
        try:
            # Validate thread_id
            if not thread_id:
                import time
                thread_id = f"reimbursement_{int(time.time())}"

            self._log(f"Starting reimbursement session with thread_id: {thread_id}", "bot_logic")

            # Validate employee data
            if not employee_data or not isinstance(employee_data, dict) or not employee_data.get('id'):
                return {
                    'message': "I'd like to help with your reimbursement request, but I need to verify your employee information first. Please try logging out and logging back in, or contact HR for assistance.",
                    'thread_id': thread_id,
                    'source': 'reimbursement_service'
                }

            # Clear any existing session first to prevent conflicts
            if self.session_manager:
                self.session_manager.clear_session(thread_id)

            # Start session
            session_data = {
                'extracted_data': extracted_data,
                'employee_data': employee_data,
                'employee_id': employee_data.get('id'),
                'step': 'form',
                'expense_data': {}
            }

            if self.session_manager:
                session = self.session_manager.start_session(thread_id, 'reimbursement', session_data)

            # Always get fresh Odoo session data (with refresh token if needed) - same approach as confirmation
            # This ensures we have valid credentials even if session expired
            odoo_session_data = self._get_fresh_odoo_session_data(odoo_session_data)
            if not odoo_session_data or not odoo_session_data.get('session_id') or not odoo_session_data.get('user_id'):
                if self.session_manager:
                    self.session_manager.clear_session(thread_id)
                return {
                    'message': 'Failed to authenticate with Odoo. Please try logging out and logging back in.',
                    'thread_id': thread_id,
                    'source': 'reimbursement_service'
                }
            
            # Log session data status for debugging
            has_password = bool(odoo_session_data.get('password'))
            self._log(f"Building form with session_data: has_session_id={bool(odoo_session_data.get('session_id'))}, has_user_id={bool(odoo_session_data.get('user_id'))}, has_password={has_password}", "bot_logic")

            # Build form data
            employee_id = employee_data.get('id')
            ok, form_data = self.build_reimbursement_request_form_data(employee_id, odoo_session_data)
            
            if not ok:
                return {
                    'message': "I encountered an error while loading the reimbursement form. Please try again or contact HR for assistance.",
                    'thread_id': thread_id,
                    'source': 'reimbursement_service'
                }

            response_text = "I'll help you create a reimbursement request! Please fill in the details below:"

            return {
                'message': response_text,
                'thread_id': thread_id,
                'source': 'reimbursement_service',
                'widgets': {
                    'reimbursement_request_form': True,
                    'category_options': form_data.get('category_options', []),
                    'currency_options': form_data.get('currency_options', []),
                    'destination_options': form_data.get('destination_options', []),
                    'project_options': form_data.get('project_options', []),
                    'market_options': form_data.get('market_options', []),
                    'pool_options': form_data.get('pool_options', []),
                    'default_currency': form_data.get('default_currency'),
                    'context_key': 'submit_reimbursement_request'
                }
            }

        except Exception as e:
            self._log(f"Error starting reimbursement session: {e}", "general")
            import traceback
            traceback.print_exc()
            return {
                'message': "I encountered an error while starting your reimbursement request. Please try again or contact HR for assistance.",
                'thread_id': thread_id,
                'source': 'reimbursement_service'
            }

    def build_reimbursement_request_form_data(self, employee_id: int, odoo_session_data: Dict = None) -> Tuple[bool, Any]:
        """Build form widget data for reimbursement request.
        
        Returns widget data with category options, currency options, destination options, and analytic account options.
        Similar structure to build_timeoff_request_form_data for consistency.
        """
        try:
            # Fetch currency, destination, and analytic account options in parallel for better performance
            import concurrent.futures
            
            def fetch_currency_options():
                """Fetch currency options from Odoo"""
                return self._get_currency_options(odoo_session_data)
            
            def fetch_destination_options():
                """Fetch destination options from Odoo"""
                return self._get_destination_options(odoo_session_data)
            
            def fetch_analytic_accounts():
                """Fetch analytic account options from Odoo, separated by plan"""
                return self._get_analytic_account_options_by_plan(odoo_session_data)
            
            # Execute API calls in parallel
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                currency_future = executor.submit(fetch_currency_options)
                destination_future = executor.submit(fetch_destination_options)
                analytic_future = executor.submit(fetch_analytic_accounts)
                
                # Get results
                currency_options, currency_error = currency_future.result()
                destination_options, destination_error = destination_future.result()
                analytic_options = analytic_future.result()  # Returns dict directly, not tuple
            
            # Handle errors gracefully
            if currency_error:
                self._log(f"Error fetching currency options: {currency_error}", "bot_logic")
                currency_options = []
            
            if destination_error:
                self._log(f"Error fetching destination options: {destination_error}", "bot_logic")
                destination_options = []
            
            # analytic_options is a dict with 'project', 'market', 'pool' keys
            if not isinstance(analytic_options, dict):
                self._log(f"Error fetching analytic account options: Invalid format", "bot_logic")
                analytic_options = {'project': [], 'market': [], 'pool': []}
            
            # Get default currency based on company
            default_currency_code = self._get_default_currency_for_company(
                {'id': employee_id} if employee_id else {}
            )
            
            # Build category options
            category_options = [
                {'value': 'miscellaneous', 'label': 'Miscellaneous'},
                {'value': 'per_diem', 'label': 'Per Diem'},
                {'value': 'travel_accommodation', 'label': 'Travel & Accommodation'}
            ]
            
            # Separate analytic options by plan (Project, Market, Pool)
            project_options = analytic_options.get('project', [])
            market_options = analytic_options.get('market', [])
            pool_options = analytic_options.get('pool', [])
            
            return True, {
                'category_options': category_options,
                'currency_options': currency_options,
                'destination_options': destination_options,
                'project_options': project_options,
                'market_options': market_options,
                'pool_options': pool_options,
                'default_currency': default_currency_code
            }
            
        except Exception as e:
            self._log(f"Error building reimbursement request form: {str(e)}", "general")
            import traceback
            traceback.print_exc()
            return False, f"Error building form: {str(e)}"

    def _handle_reimbursement_form_submission(self, message: str, thread_id: str, employee_data: Dict, odoo_session_data: Dict = None) -> Dict:
        """Handle reimbursement form submission - parse form data and show confirmation"""
        try:
            # Format: submit_reimbursement_request:CATEGORY|AMOUNT|CURRENCY_ID|DATE|DESTINATION_ID|LINK|DESCRIPTION|ANALYTIC_DISTRIBUTION
            # ANALYTIC_DISTRIBUTION format: JSON string like '[{"account_id": 123, "percentage": 50}, {"account_id": 456, "percentage": 50}]'
            # For Per Diem: submit_reimbursement_request:per_diem|||DD/MM/YYYY to DD/MM/YYYY|DESTINATION_ID|LINK|DESCRIPTION|ANALYTIC_DISTRIBUTION
            # For Miscellaneous: submit_reimbursement_request:miscellaneous|AMOUNT|CURRENCY_ID|DD/MM/YYYY||LINK|DESCRIPTION|ANALYTIC_DISTRIBUTION
            # For Travel & Accommodation: submit_reimbursement_request:travel_accommodation|AMOUNT|CURRENCY_ID|||LINK|DESCRIPTION|ANALYTIC_DISTRIBUTION
            
            parts = message.split(':', 1)
            if len(parts) < 2:
                return {
                    'message': 'Invalid form submission format.',
                    'thread_id': thread_id,
                    'source': 'reimbursement_service'
                }
            
            data_parts = parts[1].split('|')
            if len(data_parts) < 7:
                return {
                    'message': 'Invalid form data format.',
                    'thread_id': thread_id,
                    'source': 'reimbursement_service'
                }
            
            category = data_parts[0].strip()
            amount_str = data_parts[1].strip()
            currency_id_str = data_parts[2].strip()
            date_str = data_parts[3].strip()
            destination_id_str = data_parts[4].strip()
            link = data_parts[5].strip()
            description = data_parts[6].strip() if len(data_parts) > 6 else ''
            analytic_distribution_str = data_parts[7].strip() if len(data_parts) > 7 else ''
            
            # Validate category
            if category not in ['miscellaneous', 'per_diem', 'travel_accommodation']:
                return {
                    'message': 'Invalid category selected.',
                    'thread_id': thread_id,
                    'source': 'reimbursement_service'
                }
            
            # Parse and validate analytic distribution if provided
            analytic_distribution = None
            if analytic_distribution_str:
                try:
                    import json
                    import urllib.parse
                    # URL decode if needed
                    decoded = urllib.parse.unquote(analytic_distribution_str)
                    analytic_distribution = json.loads(decoded)
                    # Validate it's a list
                    if not isinstance(analytic_distribution, list) or len(analytic_distribution) == 0:
                        return {
                            'message': 'Invalid analytic distribution format. Must be a non-empty array.',
                            'thread_id': thread_id,
                            'source': 'reimbursement_service'
                        }
                    # Validate each line has project_id, market_id, pool_id
                    for idx, item in enumerate(analytic_distribution):
                        if not isinstance(item, dict):
                            return {
                                'message': f'Invalid analytic distribution entry at line {idx + 1}.',
                                'thread_id': thread_id,
                                'source': 'reimbursement_service'
                            }
                        if not item.get('project_id') or not item.get('market_id') or not item.get('pool_id'):
                            return {
                                'message': f'Line {idx + 1} must have project_id, market_id, and pool_id.',
                                'thread_id': thread_id,
                                'source': 'reimbursement_service'
                            }
                except json.JSONDecodeError as e:
                    return {
                        'message': 'Invalid analytic distribution JSON format.',
                        'thread_id': thread_id,
                        'source': 'reimbursement_service'
                    }
                except Exception as e:
                    self._log(f"Error parsing analytic distribution: {e}", "bot_logic")
                    return {
                        'message': 'Error parsing analytic distribution. Please try again.',
                        'thread_id': thread_id,
                        'source': 'reimbursement_service'
                    }
            else:
                # Analytic distribution is required
                return {
                    'message': 'Please fill in Analytic Distribution (Project, Market, and Pool). It is required for all reimbursement requests.',
                    'thread_id': thread_id,
                    'source': 'reimbursement_service'
                }
            
            # Build expense data based on category
            expense_data = {'category': category}
            if analytic_distribution:
                expense_data['analytic_distribution'] = analytic_distribution
            
            if category == 'miscellaneous':
                # Validate required fields
                if not amount_str or not currency_id_str or not date_str:
                    return {
                        'message': 'Please fill in all required fields: Amount, Currency, and Expense Date.',
                        'thread_id': thread_id,
                        'source': 'reimbursement_service'
                    }
                try:
                    amount = float(amount_str)
                    if amount <= 0:
                        return {
                            'message': 'Amount must be greater than 0.',
                            'thread_id': thread_id,
                            'source': 'reimbursement_service'
                        }
                    expense_data['amount'] = amount
                    expense_data['currency_id'] = int(currency_id_str)
                    expense_data['date'] = date_str
                    if link:
                        expense_data['attached_link'] = link
                    if description:
                        expense_data['description'] = description
                except ValueError:
                    return {
                        'message': 'Invalid amount format.',
                        'thread_id': thread_id,
                        'source': 'reimbursement_service'
                    }
            
            elif category == 'per_diem':
                # Validate required fields
                if not date_str or not destination_id_str:
                    return {
                        'message': 'Please fill in all required fields: Date Range and Destination.',
                        'thread_id': thread_id,
                        'source': 'reimbursement_service'
                    }
                # Parse date range (format: "DD/MM/YYYY to DD/MM/YYYY")
                date_parts = date_str.split(' to ')
                if len(date_parts) != 2:
                    return {
                        'message': 'Invalid date range format. Please select a date range.',
                        'thread_id': thread_id,
                        'source': 'reimbursement_service'
                    }
                expense_data['per_diem_from'] = date_parts[0].strip()
                expense_data['per_diem_to'] = date_parts[1].strip()
                expense_data['per_diem_destination_id'] = int(destination_id_str)
                if link:
                    expense_data['attached_link'] = link
                if description:
                    expense_data['description'] = description
            
            elif category == 'travel_accommodation':
                # Validate required fields
                if not amount_str or not currency_id_str or not link:
                    return {
                        'message': 'Please fill in all required fields: Amount, Currency, and Link.',
                        'thread_id': thread_id,
                        'source': 'reimbursement_service'
                    }
                try:
                    amount = float(amount_str)
                    if amount <= 0:
                        return {
                            'message': 'Amount must be greater than 0.',
                            'thread_id': thread_id,
                            'source': 'reimbursement_service'
                        }
                    expense_data['amount'] = amount
                    expense_data['currency_id'] = int(currency_id_str)
                    expense_data['attached_link'] = link
                    if description:
                        expense_data['description'] = description
                except ValueError:
                    return {
                        'message': 'Invalid amount format.',
                        'thread_id': thread_id,
                        'source': 'reimbursement_service'
                    }
            
            # Store expense data in session for confirmation
            if self.session_manager:
                # Get existing session data to preserve it
                existing_session = self.session_manager.get_session(thread_id)
                existing_data = existing_session.get('data', {}) if existing_session else {}
                
                # Merge expense data into existing session data
                updated_data = {
                    **existing_data,
                    'expense_data': expense_data,
                    'employee_data': employee_data,
                    'step': 'confirmation'
                }
                
                # Update session with nested data structure
                self.session_manager.update_session(thread_id, {'data': updated_data})
            
            # Build confirmation message
            return self._build_reimbursement_confirmation_message(expense_data, thread_id, employee_data, odoo_session_data)
            
        except Exception as e:
            self._log(f"Error handling reimbursement form submission: {e}", "general")
            import traceback
            traceback.print_exc()
            return {
                'message': 'An error occurred processing your form submission. Please try again.',
                'thread_id': thread_id,
                'source': 'reimbursement_service'
            }

    def _build_reimbursement_confirmation_message(self, expense_data: Dict, thread_id: str, employee_data: Dict, odoo_session_data: Dict = None) -> Dict:
        """Build confirmation message for reimbursement request"""
        try:
            category = expense_data.get('category', '')
            category_display = self.expense_categories.get(category, category)
            
            confirmation_lines = [f"Almost there! Let's review your reimbursement request:\n"]
            confirmation_lines.append(f"📂 **Category:** {category_display}\n")
            
            if category == 'miscellaneous':
                amount = expense_data.get('amount', 0)
                currency_id = expense_data.get('currency_id')
                currency_name = ''
                if currency_id:
                    currency_name = self._resolve_currency_name(currency_id, odoo_session_data) or ''
                amount_display = f"{amount:.2f} {currency_name}" if currency_name else f"${amount:.2f}"
                confirmation_lines.append(f"💰 **Amount:** {amount_display}\n")
                confirmation_lines.append(f"📅 **Expense Date:** {expense_data.get('date', 'N/A')}\n")
                if expense_data.get('attached_link'):
                    confirmation_lines.append(f"🔗 **Link:** {expense_data.get('attached_link')}\n")
                if expense_data.get('description'):
                    confirmation_lines.append(f"📝 **Description:** {expense_data.get('description')}\n")
            
            elif category == 'per_diem':
                confirmation_lines.append(f"📅 **Date Range:** {expense_data.get('per_diem_from', 'N/A')} to {expense_data.get('per_diem_to', 'N/A')}\n")
                destination_id = expense_data.get('per_diem_destination_id')
                destination_name = ''
                if destination_id:
                    destination_name = self._resolve_state_name(destination_id, odoo_session_data) or f"Destination ID: {destination_id}"
                confirmation_lines.append(f"🗺️ **Destination:** {destination_name}\n")
                if expense_data.get('attached_link'):
                    confirmation_lines.append(f"🔗 **Link:** {expense_data.get('attached_link')}\n")
                if expense_data.get('description'):
                    confirmation_lines.append(f"📝 **Description:** {expense_data.get('description')}\n")
            
            elif category == 'travel_accommodation':
                amount = expense_data.get('amount', 0)
                currency_id = expense_data.get('currency_id')
                currency_name = ''
                if currency_id:
                    currency_name = self._resolve_currency_name(currency_id, odoo_session_data) or ''
                amount_display = f"{amount:.2f} {currency_name}" if currency_name else f"${amount:.2f}"
                confirmation_lines.append(f"💰 **Amount:** {amount_display}\n")
                confirmation_lines.append(f"🔗 **Link:** {expense_data.get('attached_link', 'N/A')}\n")
                if expense_data.get('description'):
                    confirmation_lines.append(f"📝 **Description:** {expense_data.get('description')}\n")
            
            # Add analytic distribution to confirmation
            analytic_distribution = expense_data.get('analytic_distribution')
            if analytic_distribution and isinstance(analytic_distribution, list):
                confirmation_lines.append(f"📊 **Analytic Distribution:**\n")
                for idx, item in enumerate(analytic_distribution):
                    project_id = item.get('project_id')
                    market_id = item.get('market_id')
                    pool_id = item.get('pool_id')
                    
                    # Resolve account names
                    def resolve_account_name(account_id):
                        if not account_id or not odoo_session_data:
                            return f"Account ID: {account_id}"
                        try:
                            params = {
                                'args': [[account_id]],
                                'kwargs': {'fields': ['name']}
                            }
                            success, result = self._make_odoo_request('account.analytic.account', 'read', params, odoo_session_data)
                            if success and result:
                                rec = result[0] if isinstance(result, list) else result
                                return rec.get('name', f"Account ID: {account_id}")
                        except Exception:
                            pass
                        return f"Account ID: {account_id}"
                    
                    project_name = resolve_account_name(project_id)
                    market_name = resolve_account_name(market_id)
                    pool_name = resolve_account_name(pool_id)
                    
                    if len(analytic_distribution) > 1:
                        confirmation_lines.append(f"   **Line {idx + 1}:**\n")
                    confirmation_lines.append(f"   • Project: {project_name}\n")
                    confirmation_lines.append(f"   • Market: {market_name}\n")
                    confirmation_lines.append(f"   • Pool: {pool_name}\n")
            
            confirmation_lines.append("\nDo you want to submit this request? Reply or click 'Yes' to confirm or 'No' to cancel.")
            
            return {
                'message': ''.join(confirmation_lines),
                'thread_id': thread_id,
                'buttons': [
                    {'text': 'Yes', 'value': 'reimbursement_confirm', 'type': 'action_reimbursement'},
                    {'text': 'No', 'value': 'reimbursement_cancel', 'type': 'action_reimbursement'}
                ],
                'widgets': {
                    'reimbursement_confirmation_data': expense_data
                },
                'source': 'reimbursement_service'
            }
            
        except Exception as e:
            self._log(f"Error building confirmation message: {e}", "general")
            return {
                'message': 'Please confirm by typing "Yes" to submit or "No" to cancel.',
                'thread_id': thread_id,
                'buttons': [
                    {'text': 'Yes', 'value': 'reimbursement_confirm', 'type': 'action_reimbursement'},
                    {'text': 'No', 'value': 'reimbursement_cancel', 'type': 'action_reimbursement'}
                ],
                'source': 'reimbursement_service'
            }

    def _handle_reimbursement_confirmation(self, thread_id: str, employee_data: Dict, odoo_session_data: Dict = None) -> Dict:
        """Handle reimbursement confirmation - submit the request to Odoo with refresh token approach"""
        try:
            employee_id = employee_data.get('id') if employee_data else None
            self._log(f"[REIMBURSEMENT DEBUG] Starting confirmation for thread {thread_id}, employee_id: {employee_id}", "bot_logic")
            
            # Get expense data from session
            active_session = self.session_manager.get_session(thread_id) if self.session_manager else None
            if not active_session or active_session.get('type') != 'reimbursement':
                self._log(f"[REIMBURSEMENT DEBUG] ❌ No active reimbursement session found for thread {thread_id}", "bot_logic")
                return {
                    'message': 'No active reimbursement session found. Please start a new request.',
                    'thread_id': thread_id,
                    'source': 'reimbursement_service'
                }
            
            session_data = active_session.get('data', {})
            expense_data = session_data.get('expense_data', {})
            
            if not expense_data:
                self._log(f"[REIMBURSEMENT DEBUG] ❌ No expense data found in session for thread {thread_id}", "bot_logic")
                return {
                    'message': 'Expense data not found. Please start a new request.',
                    'thread_id': thread_id,
                    'source': 'reimbursement_service'
                }
            
            # Validate employee data
            if not employee_data or not isinstance(employee_data, dict) or not employee_data.get('id'):
                self._log(f"[REIMBURSEMENT DEBUG] ❌ Invalid employee data for thread {thread_id}", "bot_logic")
                return {
                    'message': 'Employee data not found. Please refresh the page and try again.',
                    'thread_id': thread_id,
                    'source': 'reimbursement_service'
                }
            
            session_employee_id = None
            if isinstance(session_data, dict):
                if session_data.get('employee_id'):
                    session_employee_id = session_data.get('employee_id')
                elif isinstance(session_data.get('employee_data'), dict):
                    session_employee_id = session_data['employee_data'].get('id')
            if session_employee_id and employee_id and session_employee_id != employee_id:
                self._log(
                    f"[REIMBURSEMENT DEBUG] Session employee_id mismatch: session has {session_employee_id}, current has {employee_id}",
                    "bot_logic"
                )
                if self.session_manager:
                    self.session_manager.clear_session(thread_id)
                return {
                    'message': 'This reimbursement session does not match your account. Please start a new request.',
                    'thread_id': thread_id,
                    'source': 'reimbursement_service'
                }
            
            self._log(f"[REIMBURSEMENT DEBUG] Expense data: category={expense_data.get('category')}, amount={expense_data.get('amount')}", "bot_logic")
            
            # Always get fresh Odoo session data (with refresh token if needed) - same approach as timeoff
            # This ensures we have valid credentials even if session expired
            odoo_session_data = self._get_fresh_odoo_session_data(odoo_session_data)
            if not odoo_session_data or not odoo_session_data.get('session_id') or not odoo_session_data.get('user_id'):
                self._log(f"[REIMBURSEMENT DEBUG] ❌ Failed to get fresh Odoo session data for thread {thread_id}", "bot_logic")
                return {
                    'message': 'Failed to authenticate with Odoo. Please try logging out and logging back in.',
                    'thread_id': thread_id,
                    'source': 'reimbursement_service'
                }
            
            if not odoo_session_data.get('password'):
                self._log(
                    f"[REIMBURSEMENT DEBUG] Proceeding without refresh token; using active session for thread {thread_id}",
                    "bot_logic"
                )
            
            self._log(f"[REIMBURSEMENT DEBUG] Calling create_expense_record for employee_id: {employee_id}", "bot_logic")
            
            # Create expense record
            success, result, renewed_session = self.create_expense_record(employee_data, expense_data, odoo_session_data)
            
            self._log(f"[REIMBURSEMENT DEBUG] create_expense_record returned: success={success}, result={result}", "bot_logic")
            
            # Update Flask session if session was renewed
            if renewed_session:
                try:
                    from flask import session as flask_session
                    flask_session['odoo_session_id'] = renewed_session['session_id']
                    flask_session['user_id'] = renewed_session['user_id']
                    flask_session.modified = True
                    self._log(f"[REIMBURSEMENT DEBUG] Updated Flask session with renewed session data", "bot_logic")
                except Exception as e:
                    self._log(f"[REIMBURSEMENT DEBUG] Failed to update Flask session: {e}", "bot_logic")
            
            # Clear session
            if self.session_manager:
                self.session_manager.clear_session(thread_id)
            
            if success:
                expense_id = result.get('id') if isinstance(result, dict) else result
                result_payload = result if isinstance(result, dict) else {'id': expense_id}
                
                # DEBUG: Verify final expense state after creation
                if expense_id:
                    try:
                        # Use stateless request helper
                        use_stateless = odoo_session_data and odoo_session_data.get('session_id') and odoo_session_data.get('user_id')
                        def _make_request(model, method, params):
                            if use_stateless:
                                ok, res, renewed = self._make_odoo_request_stateless(
                                    model, method, params,
                                    session_id=odoo_session_data['session_id'],
                                    user_id=odoo_session_data['user_id'],
                                    username=odoo_session_data.get('username'),
                                    password=odoo_session_data.get('password')
                                )
                                return ok, res, renewed
                            else:
                                ok, res = self._make_odoo_request(model, method, params)
                                return ok, res, None
                        
                        params_final_check = {
                            'args': [[expense_id]],
                            'kwargs': {'fields': ['state', 'sheet_id', 'name']}
                        }
                        ok_final, res_final, _ = _make_request('hr.expense', 'read', params_final_check)
                        if ok_final and res_final:
                            expense_final = res_final[0] if isinstance(res_final, list) else res_final
                            final_state = expense_final.get('state', 'unknown')
                            final_sheet_id = expense_final.get('sheet_id')
                            self._log(f"[REIMBURSEMENT DEBUG] ✅ FINAL CHECK: Expense #{expense_id} state: '{final_state}', sheet_id: {final_sheet_id}", "bot_logic")
                            
                            if final_state not in ['submitted', 'approve']:
                                self._log(f"[REIMBURSEMENT DEBUG] ⚠️ WARNING: Expense #{expense_id} is NOT in 'submitted' state after creation. State: '{final_state}'. This may indicate the submission process did not complete successfully.", "bot_logic")
                        else:
                            self._log(f"[REIMBURSEMENT DEBUG] Failed to verify final expense state: {res_final}", "bot_logic")
                    except Exception as e:
                        self._log(f"[REIMBURSEMENT DEBUG] Error checking final expense state: {e}", "bot_logic")
                
                self._log_metric(thread_id, expense_data, result_payload, employee_data)
                
                # Include state information in response message if available
                state_info = ""
                if expense_id:
                    try:
                        use_stateless = odoo_session_data and odoo_session_data.get('session_id') and odoo_session_data.get('user_id')
                        def _make_request(model, method, params):
                            if use_stateless:
                                ok, res, renewed = self._make_odoo_request_stateless(
                                    model, method, params,
                                    session_id=odoo_session_data['session_id'],
                                    user_id=odoo_session_data['user_id'],
                                    username=odoo_session_data.get('username'),
                                    password=odoo_session_data.get('password')
                                )
                                return ok, res, renewed
                            else:
                                ok, res = self._make_odoo_request(model, method, params)
                                return ok, res, None
                        
                        params_state = {
                            'args': [[expense_id]],
                            'kwargs': {'fields': ['state']}
                        }
                        ok_state, res_state, _ = _make_request('hr.expense', 'read', params_state)
                        if ok_state and res_state:
                            expense_state_obj = res_state[0] if isinstance(res_state, list) else res_state
                            current_state = expense_state_obj.get('state', 'unknown')
                            if current_state not in ['submitted', 'approve']:
                                state_info = f" (Current state: {current_state})"
                    except Exception:
                        pass
                
                return {
                    'message': f"✅ Your reimbursement request has been submitted successfully! Expense ID: {expense_id or 'N/A'}{state_info}",
                    'thread_id': thread_id,
                    'source': 'reimbursement_service'
                }
            else:
                self._log(f"[REIMBURSEMENT DEBUG] ❌ Expense creation FAILED: {result}", "bot_logic")
                return {
                    'message': f"❌ There was an error submitting your reimbursement request: {result}",
                    'thread_id': thread_id,
                    'source': 'reimbursement_service'
                }
                
        except Exception as e:
            self._log(f"[REIMBURSEMENT DEBUG] ❌ EXCEPTION in confirmation handler: {e}", "general")
            import traceback
            traceback.print_exc()
            if self.session_manager:
                self.session_manager.clear_session(thread_id)
            return {
                'message': 'An error occurred while submitting your request. Please try again.',
                'thread_id': thread_id,
                'source': 'reimbursement_service'
            }

    def _get_default_currency_for_company(self, employee_data: Dict) -> Optional[str]:
        """Get default currency code based on company name.
        Returns currency code (JOD, SAR, AED) or None.
        """
        try:
            company_details = employee_data.get('company_id_details')
            company_name = ''
            
            if isinstance(company_details, dict):
                company_name = company_details.get('name', '')
            elif isinstance(company_details, (list, tuple)) and len(company_details) > 1:
                company_name = company_details[1]
            
            if not company_name:
                # Try to get from company_id field directly
                company_val = employee_data.get('company_id')
                if isinstance(company_val, (list, tuple)) and len(company_val) > 1:
                    company_name = company_val[1]
            
            if not company_name:
                return None
            
            company_name_normalized = company_name.strip()
            
            # Map company names to currencies
            if company_name_normalized in ['Prezlab FZ LLC - Regional Office', 'ALOROD AL TAQADAMIAH LEL TASMEM CO']:
                return 'JOD'
            elif company_name_normalized == 'Prezlab Advanced Design Company':
                return 'SAR'
            elif company_name_normalized in ['Prezlab FZ LLC', 'Prezlab Digital Design Firm L.L.C. - O.P.C']:
                return 'AED'
            
            return None
        except Exception as e:
            self._log(f"Error determining default currency: {e}", "bot_logic")
            return None

    def _get_currency_options(self, odoo_session_data: Dict = None) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """Fetch currency options from res.currency for dropdown.
        Returns (options, error_message) where options is a list of {label, value}.
        """
        try:
            params = {
                'args': [[]],  # empty domain
                'kwargs': {
                    'fields': ['name'],
                    'limit': 2000,
                    'order': 'name asc'
                }
            }
            success, result = self._make_odoo_request('res.currency', 'search_read', params, odoo_session_data)
            if not success:
                return [], str(result)
            options = [{'label': r.get('name') or f"Currency {r.get('id')}", 'value': r.get('id')} for r in (result or [])]
            return options, None
        except Exception as e:
            return [], str(e)

    def _resolve_currency_name(self, currency_id: int, odoo_session_data: Dict = None) -> Optional[str]:
        """Resolve currency name from currency_id"""
        try:
            params = {
                'args': [[currency_id]],
                'kwargs': {'fields': ['name']}
            }
            success, result = self._make_odoo_request('res.currency', 'read', params, odoo_session_data)
            if success and result:
                rec = result[0] if isinstance(result, list) else result
                return rec.get('name')
            return None
        except Exception:
            return None

    def _get_destination_options(self, odoo_session_data: Dict = None) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """Fetch destination options from res.country.state for dropdown.
        Returns (options, error_message) where options is a list of {label, value}.
        """
        try:
            params = {
                'args': [[]],  # empty domain
                'kwargs': {
                    'fields': ['name'],
                    'limit': 2000,
                    'order': 'name asc'
                }
            }
            success, result = self._make_odoo_request('res.country.state', 'search_read', params, odoo_session_data)
            if not success:
                return [], str(result)
            options = [{'label': r.get('name') or f"State {r.get('id')}", 'value': r.get('id')} for r in (result or [])]
            return options, None
        except Exception as e:
            return [], str(e)

    def _resolve_state_name(self, state_id: int, odoo_session_data: Dict = None) -> Optional[str]:
        try:
            params = {
                'args': [[state_id]],
                'kwargs': {'fields': ['name']}
            }
            success, result = self._make_odoo_request('res.country.state', 'read', params, odoo_session_data)
            if success and result:
                rec = result[0] if isinstance(result, list) else result
                return rec.get('name')
            return None
        except Exception:
            return None

    def _get_analytic_account_options_by_plan(self, odoo_session_data: Dict = None) -> Dict[str, List[Dict[str, Any]]]:
        """Fetch analytic account options from account.analytic.account, separated by plan.
        Returns dict with 'project', 'market', 'pool' keys, each containing a list of {label, value}.
        """
        try:
            # First, resolve plan names to IDs
            plan_names = {
                'project': 'Projects',  # Updated: Live database uses "Projects" not "Projects [Archive]"
                'market': 'Market',
                'pool': 'Pool'
            }
            
            plan_ids = {}
            for key, plan_name in plan_names.items():
                params = {
                    'args': [[('name', '=', plan_name)]],
                    'kwargs': {
                        'fields': ['id', 'name'],
                        'limit': 1
                    }
                }
                success, result = self._make_odoo_request('account.analytic.plan', 'search_read', params, odoo_session_data)
                if success and result and len(result) > 0:
                    plan_ids[key] = result[0].get('id')
                else:
                    self._log(f"Could not find plan '{plan_name}'", "bot_logic")
                    plan_ids[key] = None
            
            # Fetch accounts for each plan
            result_dict = {'project': [], 'market': [], 'pool': []}
            
            for key, plan_id in plan_ids.items():
                if not plan_id:
                    continue
                
                params = {
                    'args': [[('plan_id', '=', plan_id)]],
                    'kwargs': {
                        'fields': ['id', 'name'],
                        'limit': 2000,
                        'order': 'name asc'
                    }
                }
                success, accounts = self._make_odoo_request('account.analytic.account', 'search_read', params, odoo_session_data)
                if success and accounts:
                    result_dict[key] = [
                        {'label': r.get('name') or f"Analytic Account {r.get('id')}", 'value': r.get('id')} 
                        for r in accounts
                    ]
                else:
                    self._log(f"Error fetching accounts for plan '{key}': {accounts if not success else 'No accounts found'}", "bot_logic")
            
            return result_dict
            
        except Exception as e:
            self._log(f"Error fetching analytic account options by plan: {e}", "bot_logic")
            import traceback
            traceback.print_exc()
            return {'project': [], 'market': [], 'pool': []}
