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

    def __init__(self, odoo_service, employee_service, metrics_service=None):
        self.odoo_service = odoo_service
        self.employee_service = employee_service
        self.metrics_service = metrics_service

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

    def create_expense_record(self, employee_data: Dict, expense_data: Dict) -> Tuple[bool, Any]:
        """
        Create expense record in Odoo
        Returns: (success, result_data)
        """
        try:
            # Validate employee data early to avoid NoneType errors when sessions expire
            if not employee_data or not isinstance(employee_data, dict) or not employee_data.get('id'):
                self._log("Missing or invalid employee_data while creating expense record", "bot_logic")
                return False, (
                    "I couldn't verify your employee profile (session may have expired). "
                    "Please log in again and resubmit your reimbursement."
                )
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
                    success, fields = self._make_odoo_request(model, 'fields_get', params)
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

            # Create strategy
            if (expense_data.get('category') or '').lower() == 'per_diem':
                # Prefer single create with final PER_DIEM values first (best outcome)
                try:
                    self._log("Attempting single-create with PER_DIEM product", "bot_logic")
                    params_sc = {'args': [expense_values], 'kwargs': {}}
                    ok_sc, res_sc = self._make_odoo_request('hr.expense', 'create', params_sc)
                    if ok_sc:
                        expense_id = res_sc
                        return True, {'id': expense_id, 'message': f"Expense record #{expense_id} created successfully."}
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
                    ok_create, res_create = self._make_odoo_request('hr.expense', 'create', params_create)
                    if not ok_create:
                        self._log(f"Safe create failed: {res_create}", "bot_logic")
                        return False, res_create
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
                        ok_pre, res_pre = self._make_odoo_request('hr.expense', 'write', {'args': [[expense_id], pre_vals], 'kwargs': {}})
                        if not ok_pre:
                            self._log(f"Pre write failed for expense #{expense_id}: {res_pre}", "bot_logic")
                            return False, res_pre

                    # 2b) Second write: switch product to PER_DIEM only
                    final_vals = {'product_id': expense_values.get('product_id')}
                    self._log(f"Switching product to PER_DIEM for expense #{expense_id}", "bot_logic")
                    ok_write, res_write = self._make_odoo_request('hr.expense', 'write', {'args': [[expense_id], final_vals], 'kwargs': {}})
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
                        ok_write2, res_write2 = self._make_odoo_request('hr.expense', 'write', {'args': [[expense_id], fallback_vals], 'kwargs': {}})
                        if not ok_write2:
                            return False, res_write

                    return True, {'id': expense_id, 'message': f"Expense record #{expense_id} created successfully."}
                except Exception as e:
                    self._log(f"Two-phase create error: {e}", "general")
                    # Fallback to single create
                    params = {'args': [expense_values], 'kwargs': {}}
                    self._log(f"Fallback single create for PER_DIEM", "bot_logic")
                    success, result = self._make_odoo_request('hr.expense', 'create', params)
                    if success:
                        expense_id = result
                        return True, {'id': expense_id, 'message': f"Expense record #{expense_id} created successfully."}
                    else:
                        return False, result
            else:
                # Default single create for other categories
                params = {
                    'args': [expense_values],
                    'kwargs': {}
                }

                self._log(f"Making Odoo request to create expense", "bot_logic")
                success, result = self._make_odoo_request('hr.expense', 'create', params)

                if success:
                    expense_id = result
                    self._log(f"Expense record created successfully with ID: {expense_id}", "bot_logic")
                    return True, {
                        'id': expense_id,
                        'message': f"Expense record #{expense_id} created successfully."
                    }
                else:
                    self._log(f"Failed to create expense record: {result}", "bot_logic")
                    return False, result

        except Exception as e:
            self._log(f"Error creating expense record: {e}", "general")
            import traceback
            traceback.print_exc()
            return False, str(e)

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
                        self._log(f"Odoo error details: {error_data}", "bot_logic")
                        return False, f"Odoo error: {error_msg} - {error_details}"
                except Exception as e:
                    return False, f"JSON parsing error: {str(e)}"
            else:
                self._log(f"HTTP error {response.status_code}: {response.text}", "bot_logic")
                return False, f"HTTP error: {response.status_code} - {response.text}"

        except Exception as e:
            self._log(f"Error making Odoo request: {e}", "general")
            return False, f"Request error: {str(e)}"

    def handle_flow(self, message: str, thread_id: str, employee_data: Dict) -> Optional[Dict]:
        """
        Handle reimbursement flow - detect intent and manage multi-step process
        Returns: response dict or None if not a reimbursement request
        """
        try:
            # Check for active reimbursement session first
            active_session = self.session_manager.get_session(thread_id) if self.session_manager else None

            if active_session and active_session.get('type') == 'reimbursement':
                # Continue existing session
                return self._continue_reimbursement_session(message, thread_id, active_session, employee_data)

            # CRITICAL: Block if another flow is active (BEFORE intent detection)
            # This prevents reimbursement from interrupting active timeoff/overtime flows
            if active_session and active_session.get('type') not in (None, 'reimbursement') and active_session.get('state') in ['started', 'active']:
                other_flow = active_session.get('type', 'another')
                self._log(f"Blocking reimbursement - active {other_flow} flow detected on thread {thread_id}", "bot_logic")
                # Return None to let the active flow handle the message
                return None

            # Detect new reimbursement intent
            is_reimbursement, confidence, extracted_data = self.detect_reimbursement_intent(message)

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
                return self._start_reimbursement_session(message, thread_id, extracted_data, employee_data)

            return None

        except Exception as e:
            self._log(f"Error in reimbursement flow: {e}", "general")
            import traceback
            traceback.print_exc()
            return None

    def _start_reimbursement_session(self, message: str, thread_id: str, extracted_data: Dict, employee_data: Dict) -> Dict:
        """Start a new reimbursement request session"""
        try:
            # Validate thread_id
            if not thread_id:
                import time
                thread_id = f"reimbursement_{int(time.time())}"

            self._log(f"Starting reimbursement session with thread_id: {thread_id}", "bot_logic")

            # Clear any existing session first to prevent conflicts
            if self.session_manager:
                self.session_manager.clear_session(thread_id)

            # Start session
            session_data = {
                'extracted_data': extracted_data,
                'employee_data': employee_data,
                'step': 'category',
                'expense_data': {}
            }

            if self.session_manager:
                session = self.session_manager.start_session(thread_id, 'reimbursement', session_data)

            # Present category options
            categories = self.get_expense_categories()

            response_text = "I'll help you create a reimbursement request! Please select the expense category:"

            return {
                'message': response_text,
                'thread_id': thread_id,
                'buttons': categories,
                'source': 'reimbursement_service'
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

    def _continue_reimbursement_session(self, message: str, thread_id: str, active_session: Dict, employee_data: Dict) -> Dict:
        """Continue an existing reimbursement session"""
        try:
            # Check for exit/cancel commands FIRST before any step logic (with typo tolerance)
            def _is_cancel_intent(txt: str) -> bool:
                try:
                    import difflib
                    txt = (txt or '').strip().lower()
                    hard = {'cancel','exit','stop','quit','nevermind','no thanks','end','abort','undo','no','n'}
                    if txt in hard:
                        return True
                    for token in ['cancel','stop','exit','quit','abort','end','undo']:
                        if difflib.SequenceMatcher(a=txt, b=token).ratio() >= 0.8:
                            return True
                    return False
                except Exception:
                    return txt in {'cancel','exit','stop','quit','nevermind','no thanks','end','abort','undo','no','n'}
            message_lower = (message or '').lower().strip()
            if _is_cancel_intent(message_lower):
                self._log(f"User requested to exit reimbursement flow with: '{message_lower}'", "bot_logic")
                if self.session_manager:
                    try:
                        self.session_manager.cancel_session(thread_id, f"User exited reimbursement: {message_lower}")
                    finally:
                        self.session_manager.clear_session(thread_id)
                return {
                    'message': 'request cancelled, can i help you with anything else',
                    'thread_id': thread_id,
                    'source': 'reimbursement_service'
                }

            session_data = active_session.get('data', {})
            current_step = session_data.get('step', 'category')
            expense_data = session_data.get('expense_data', {})
            # Prefer current request employee_data, fall back to session-stored copy
            effective_employee = employee_data if (employee_data and isinstance(employee_data, dict) and employee_data.get('id')) else session_data.get('employee_data')

            self._log(f"Continuing reimbursement session at step: {current_step}, message: '{message}'", "bot_logic")

            if current_step == 'category':
                # Handle category selection
                normalized = message.strip().lower()
                selected_category = None
                if normalized in ['miscellaneous', 'misc', 'general', 'other']:
                    selected_category = 'miscellaneous'
                elif normalized in ['per_diem', 'per diem', 'perdiem', 'daily allowance', 'daily_allowance']:
                    selected_category = 'per_diem'
                elif normalized in [
                    'travel_accommodation', 'travel & accommodation', 'travel accommodation', 'trans & acc',
                    'travel', 'accommodation', 'hotel', 'flight', 'transport', 'transportation'
                ]:
                    selected_category = 'travel_accommodation'

                if selected_category:
                    expense_data['category'] = selected_category
                    session_data['expense_data'] = expense_data
                    # Branch PER_DIEM into its dedicated flow
                    if selected_category == 'per_diem':
                        session_data['step'] = 'per_diem_dates'
                        if self.session_manager:
                            self.session_manager.update_session(thread_id, session_data)
                        return {
                            'message': 'Please select your Per Diem date range:',
                            'thread_id': thread_id,
                            'widgets': {
                                'date_range_picker': True,
                                'context_key': 'per_diem_date_range'
                            },
                            'source': 'reimbursement_service'
                        }
                    # Branch TRANS & ACC into amount entry directly
                    if selected_category == 'travel_accommodation':
                        session_data['step'] = 'ta_amount'
                        if self.session_manager:
                            self.session_manager.update_session(thread_id, session_data)
                        return {
                            'message': "Please enter the total amount for Travel & Accommodation (e.g., 150.00):",
                            'thread_id': thread_id,
                            'source': 'reimbursement_service'
                        }
                    # For MISCELLANEOUS, skip early description/link and go to amount
                    if selected_category == 'miscellaneous':
                        session_data['step'] = 'amount'
                        if self.session_manager:
                            self.session_manager.update_session(thread_id, session_data)
                        return {
                            'message': "Please enter the amount you paid (e.g., 50.00):",
                            'thread_id': thread_id,
                            'source': 'reimbursement_service'
                        }
                    # Default flow for other categories
                    session_data['step'] = 'description'

                    if self.session_manager:
                        self.session_manager.update_session(thread_id, session_data)

                    return {
                        'message': "Great! Now please provide a description of the expense:",
                        'thread_id': thread_id,
                        'source': 'reimbursement_service'
                    }
                else:
                    return {
                        'message': "Please select a valid category from the options above.",
                        'thread_id': thread_id,
                        'source': 'reimbursement_service'
                    }

            # PER_DIEM flow: collect date range and destination
            elif current_step == 'per_diem_dates':
                raw = (message or '').strip()
                # Accept widget structured message
                if raw.startswith('per_diem_date_range='):
                    raw = raw.split('=', 1)[1].strip()
                # Expect "DD/MM/YYYY to DD/MM/YYYY"
                parts = [p.strip() for p in raw.split(' to ') if p.strip()]
                if len(parts) != 2:
                    # Try fallback split by dash or other separators
                    import re as _re
                    mparts = _re.split(r"\s*(?:-|to|until|till|through|\u2013|\u2014)\s*", raw)
                    parts = [p.strip() for p in mparts if p.strip()]
                if len(parts) != 2:
                    return {
                        'message': 'Please select a full date range using the calendar.',
                        'thread_id': thread_id,
                        'widgets': {
                            'date_range_picker': True,
                            'context_key': 'per_diem_date_range'
                        },
                        'source': 'reimbursement_service'
                    }
                start_date, end_date = parts[0], parts[1]
                # Basic validation of DD/MM/YYYY
                try:
                    from datetime import datetime as _dt
                    _dt.strptime(start_date, '%d/%m/%Y')
                    _dt.strptime(end_date, '%d/%m/%Y')
                except Exception:
                    return {
                        'message': 'Invalid date format. Please pick dates from the calendar above.',
                        'thread_id': thread_id,
                        'widgets': {
                            'date_range_picker': True,
                            'context_key': 'per_diem_date_range'
                        },
                        'source': 'reimbursement_service'
                    }
                expense_data['per_diem_from'] = start_date
                expense_data['per_diem_to'] = end_date
                session_data['expense_data'] = expense_data
                # Next: destination dropdown
                # Fetch destination options from res.country.state
                options, error = self._get_destination_options()
                if error:
                    # Fallback to manual entry
                    session_data['step'] = 'per_diem_destination_text'
                    if self.session_manager:
                        self.session_manager.update_session(thread_id, session_data)
                    return {
                        'message': "Please type your Destination (state/region name):",
                        'thread_id': thread_id,
                        'source': 'reimbursement_service'
                    }
                session_data['step'] = 'per_diem_destination_select'
                if self.session_manager:
                    self.session_manager.update_session(thread_id, session_data)
                return {
                    'message': 'Select your Destination:',
                    'thread_id': thread_id,
                    'widgets': {
                        'select_dropdown': True,
                        'options': options,
                        'context_key': 'per_diem_destination',
                        'placeholder': 'Choose destination state'
                    },
                    'source': 'reimbursement_service'
                }

            elif current_step in ['per_diem_destination_select', 'per_diem_destination_text']:
                # Handle dropdown (structured) or text destination
                raw = (message or '').strip()
                if raw.startswith('per_diem_destination='):
                    val = raw.split('=', 1)[1].strip()
                    try:
                        dest_id = int(val)
                    except Exception:
                        dest_id = None
                    if not dest_id:
                        return {
                            'message': 'Invalid selection. Please choose a destination again.',
                            'thread_id': thread_id,
                            'source': 'reimbursement_service'
                        }
                    # Resolve name for confirmation
                    name = self._resolve_state_name(dest_id)
                    expense_data['per_diem_destination_id'] = dest_id
                    expense_data['per_diem_destination_name'] = name or f"ID {dest_id}"
                else:
                    # Text entry fallback
                    if not raw:
                        return {
                            'message': 'Please provide a destination name.',
                            'thread_id': thread_id,
                            'source': 'reimbursement_service'
                        }
                    expense_data['per_diem_destination_name'] = raw

                # After destination, offer supporting additions before confirmation
                session_data['expense_data'] = expense_data
                session_data['step'] = 'per_diem_supporting_additions'
                if self.session_manager:
                    self.session_manager.update_session(thread_id, session_data)
                return self._show_supporting_additions(thread_id)

            elif current_step == 'ta_amount':
                # Handle numeric total for Travel & Accommodation
                try:
                    extracted = self._extract_amount((message or '').lower())
                    if extracted is None or extracted <= 0:
                        return {
                            'message': "Please enter a valid amount (numbers only, e.g., 150.00).",
                            'thread_id': thread_id,
                            'source': 'reimbursement_service'
                        }
                    expense_data['amount'] = float(extracted)
                    # For TRANS & ACC, go straight to confirmation (use today's date by default)
                    from datetime import datetime as _dt
                    expense_data['date'] = _dt.now().strftime('%d/%m/%Y')
                    session_data['expense_data'] = expense_data
                    # After amount, offer supporting additions (description/link/next)
                    session_data['step'] = 'ta_supporting_additions'
                    if self.session_manager:
                        self.session_manager.update_session(thread_id, session_data)
                    return self._show_supporting_additions(thread_id)
                except Exception:
                    return {
                        'message': "Please enter a valid amount (numbers only, e.g., 150.00).",
                        'thread_id': thread_id,
                        'source': 'reimbursement_service'
                    }

            elif current_step == 'description':
                # Handle description input
                if message.strip():
                    expense_data['description'] = message.strip()
                    session_data['expense_data'] = expense_data
                    session_data['step'] = 'attached_link'

                    if self.session_manager:
                        self.session_manager.update_session(thread_id, session_data)

                    return {
                        'message': "Please provide a link to the receipt or supporting document (or click 'Skip' if you don't have one):",
                        'thread_id': thread_id,
                        'buttons': [
                            {'text': 'Skip', 'value': 'skip_link', 'type': 'action_reimbursement'}
                        ],
                        'source': 'reimbursement_service'
                    }
                else:
                    return {
                        'message': "Please provide a description for your expense.",
                        'thread_id': thread_id,
                        'source': 'reimbursement_service'
                    }

            elif current_step == 'attached_link':
                # Handle link input or skip
                if message.lower() == 'skip_link' or 'skip' in message.lower():
                    expense_data['attached_link'] = ''
                else:
                    # Basic URL validation
                    if message.strip().startswith(('http://', 'https://')):
                        expense_data['attached_link'] = message.strip()
                    else:
                        return {
                            'message': "Please provide a valid URL starting with http:// or https://, or click 'Skip'.",
                            'thread_id': thread_id,
                            'buttons': [
                                {'text': 'Skip', 'value': 'skip_link', 'type': 'action_reimbursement'}
                            ],
                            'source': 'reimbursement_service'
                        }

                session_data['expense_data'] = expense_data
                session_data['step'] = 'amount'

                if hasattr(self, 'session_manager'):
                    self.session_manager.update_session(thread_id, session_data)

                return {
                    'message': "Please enter the amount you paid (e.g., 50.00):",
                    'thread_id': thread_id,
                    'source': 'reimbursement_service'
                }

            elif current_step == 'amount':
                # Handle amount input
                try:
                    extracted = self._extract_amount((message or '').lower())
                    if extracted is None or extracted <= 0:
                        return {
                            'message': "Please enter a valid amount (numbers only, e.g., 50.00).",
                            'thread_id': thread_id,
                            'source': 'reimbursement_service'
                        }

                    expense_data['amount'] = float(extracted)
                    session_data['expense_data'] = expense_data
                    session_data['step'] = 'date'

                    if self.session_manager:
                        self.session_manager.update_session(thread_id, session_data)

                    return {
                        'message': "Please select the expense date:",
                        'thread_id': thread_id,
                        'widgets': {
                            'single_date_picker': True,
                            'context_key': 'reimbursement_expense_date'
                        },
                        'source': 'reimbursement_service'
                    }
                except Exception:
                    return {
                        'message': "Please enter a valid amount (numbers only, e.g., 50.00).",
                        'thread_id': thread_id,
                        'source': 'reimbursement_service'
                    }

            elif current_step == 'date':
                # Handle date input from calendar widget or manual input
                date_str = message.strip()
                self._log(f"Date step - received message: '{date_str}'", "bot_logic")

                # Handle calendar widget input format: "reimbursement_expense_date=DD/MM/YYYY to DD/MM/YYYY"
                if date_str.startswith('reimbursement_expense_date='):
                    date_str = date_str.split('=', 1)[1].strip()
                    self._log(f"Extracted date from widget: '{date_str}'", "bot_logic")

                # Extract the first date from "DD/MM/YYYY to DD/MM/YYYY" format (for both widget and direct input)
                if ' to ' in date_str:
                    date_str = date_str.split(' to ')[0].strip()
                    self._log(f"Extracted first date: '{date_str}'", "bot_logic")

                try:
                    # Parse DD/MM/YYYY format (use local alias to avoid shadowing module-level datetime)
                    from datetime import datetime as _dt
                    parsed_date = _dt.strptime(date_str, '%d/%m/%Y')
                    self._log(f"Successfully parsed date: {parsed_date}", "bot_logic")
                    expense_data['date'] = date_str
                    session_data['expense_data'] = expense_data
                    # If Miscellaneous, show Supporting Additions step before confirmation
                    if (expense_data.get('category') or '').lower() == 'miscellaneous':
                        session_data['step'] = 'supporting_additions'
                        if self.session_manager:
                            self.session_manager.update_session(thread_id, session_data)
                        return self._show_supporting_additions(thread_id)
                    # Otherwise proceed to confirmation
                    session_data['step'] = 'confirmation'
                    if self.session_manager:
                        self.session_manager.update_session(thread_id, session_data)
                    return self._show_confirmation(expense_data, thread_id)

                except ValueError as e:
                    self._log(f"Date parsing error: {e}, date_str: '{date_str}'", "bot_logic")
                    # Try to provide more helpful error message
                    if not date_str:
                        error_msg = "Please select a date using the calendar widget above."
                    else:
                        error_msg = f"Invalid date format: '{date_str}'. Please select a valid date using the calendar widget above."

                    return {
                        'message': error_msg,
                        'thread_id': thread_id,
                        'widgets': {
                            'single_date_picker': True,
                            'context_key': 'reimbursement_expense_date'
                        },
                        'source': 'reimbursement_service'
                    }

            elif current_step == 'confirmation':
                # Handle confirmation
                if message.lower() in ['yes', 'confirm', 'submit', 'y', 'confirm_submit']:
                    # Create expense record
                    success, result = self.create_expense_record(effective_employee, expense_data)

                    # Clear session
                    if self.session_manager:
                        self.session_manager.clear_session(thread_id)

                    if success:
                        result_payload = result if isinstance(result, dict) else {'message': result}
                        self._log_metric(thread_id, expense_data, result_payload, effective_employee)
                        return {
                            'message': f"✅ Your reimbursement request has been submitted successfully! Expense ID: {result.get('id', 'N/A')}",
                            'thread_id': thread_id,
                            'source': 'reimbursement_service'
                        }
                    else:
                        return {
                            'message': f"❌ There was an error submitting your reimbursement request: {result}",
                            'thread_id': thread_id,
                            'source': 'reimbursement_service'
                        }
                elif message.lower() in ['no', 'cancel', 'n', 'cancel_submit']:
                    # Clear session
                    if self.session_manager:
                        self.session_manager.clear_session(thread_id)

                    return {
                        'message': "Reimbursement request cancelled. You can start a new request anytime.",
                        'thread_id': thread_id,
                        'source': 'reimbursement_service'
                    }
                else:
                    return {
                        'message': "Please confirm by clicking 'Yes' or 'No' below.",
                        'thread_id': thread_id,
                        'buttons': [
                            {'text': 'Yes', 'value': 'confirm_submit', 'type': 'action_reimbursement'},
                            {'text': 'No', 'value': 'cancel_submit', 'type': 'action_reimbursement'}
                        ],
                        'source': 'reimbursement_service'
                    }

            elif current_step == 'supporting_additions':
                # Offer additions: descriptions, links, or next to confirmation
                normalized = (message or '').strip().lower()
                if normalized in ['descriptions', 'description', 'add_description', 'support_add_desc']:
                    session_data['step'] = 'supporting_additions_description'
                    if self.session_manager:
                        self.session_manager.update_session(thread_id, session_data)
                    return {
                        'message': "Please type a description to add:",
                        'thread_id': thread_id,
                        'source': 'reimbursement_service'
                    }
                if normalized in ['links', 'link', 'add_link', 'support_add_link']:
                    session_data['step'] = 'supporting_additions_link'
                    if self.session_manager:
                        self.session_manager.update_session(thread_id, session_data)
                    return {
                        'message': "Please paste a link (receipt or supporting doc).",
                        'thread_id': thread_id,
                        'source': 'reimbursement_service'
                    }
                if normalized in ['next', 'continue', 'proceed', 'support_next']:
                    session_data['step'] = 'confirmation'
                    if self.session_manager:
                        self.session_manager.update_session(thread_id, session_data)
                    return self._show_confirmation(expense_data, thread_id)
                # Re-show additions menu on unrecognized input
                if self.session_manager:
                    self.session_manager.update_session(thread_id, session_data)
                return self._show_supporting_additions(thread_id)

            # PER_DIEM: supporting additions after destination selection
            elif current_step == 'per_diem_supporting_additions':
                normalized = (message or '').strip().lower()
                if normalized in ['descriptions', 'description', 'add_description', 'support_add_desc']:
                    session_data['step'] = 'per_diem_supporting_additions_description'
                    if self.session_manager:
                        self.session_manager.update_session(thread_id, session_data)
                    return {
                        'message': "Please type a description to add:",
                        'thread_id': thread_id,
                        'source': 'reimbursement_service'
                    }
                if normalized in ['links', 'link', 'add_link', 'support_add_link']:
                    session_data['step'] = 'per_diem_supporting_additions_link'
                    if self.session_manager:
                        self.session_manager.update_session(thread_id, session_data)
                    return {
                        'message': "Please paste a link (receipt or supporting doc).",
                        'thread_id': thread_id,
                        'source': 'reimbursement_service'
                    }
                if normalized in ['next', 'continue', 'proceed', 'support_next']:
                    session_data['step'] = 'per_diem_confirmation'
                    if self.session_manager:
                        self.session_manager.update_session(thread_id, session_data)
                    return self._show_per_diem_confirmation(expense_data, thread_id)
                # Re-show additions menu on unrecognized input
                if self.session_manager:
                    self.session_manager.update_session(thread_id, session_data)
                return self._show_supporting_additions(thread_id)

            elif current_step == 'per_diem_supporting_additions_description':
                # Save description and return to additions menu
                if message and message.strip():
                    expense_data['description'] = message.strip()
                    session_data['expense_data'] = expense_data
                session_data['step'] = 'per_diem_supporting_additions'
                if self.session_manager:
                    self.session_manager.update_session(thread_id, session_data)
                return self._show_supporting_additions(thread_id)

            elif current_step == 'per_diem_supporting_additions_link':
                # Save valid link and return to additions menu
                raw = (message or '').strip()
                if raw and raw.lower() not in ['skip', 'none']:
                    if raw.startswith(('http://', 'https://')):
                        expense_data['attached_link'] = raw
                        session_data['expense_data'] = expense_data
                    else:
                        # Ask again if invalid URL
                        return {
                            'message': "Please provide a valid URL starting with http:// or https://, or type 'skip' to skip.",
                            'thread_id': thread_id,
                            'source': 'reimbursement_service'
                        }
                session_data['step'] = 'per_diem_supporting_additions'
                if self.session_manager:
                    self.session_manager.update_session(thread_id, session_data)
                return self._show_supporting_additions(thread_id)

            elif current_step == 'supporting_additions_description':
                # Save description and return to additions menu
                if message and message.strip():
                    expense_data['description'] = message.strip()
                    session_data['expense_data'] = expense_data
                session_data['step'] = 'supporting_additions'
                if self.session_manager:
                    self.session_manager.update_session(thread_id, session_data)
                return self._show_supporting_additions(thread_id)

            elif current_step == 'supporting_additions_link':
                # Save valid link and return to additions menu
                raw = (message or '').strip()
                if raw and raw.lower() not in ['skip', 'none']:
                    if raw.startswith(('http://', 'https://')):
                        expense_data['attached_link'] = raw
                        session_data['expense_data'] = expense_data
                    else:
                        # Ask again if invalid URL
                        return {
                            'message': "Please provide a valid URL starting with http:// or https://, or type 'skip' to skip.",
                            'thread_id': thread_id,
                            'source': 'reimbursement_service'
                        }
                session_data['step'] = 'supporting_additions'
                if self.session_manager:
                    self.session_manager.update_session(thread_id, session_data)
                return self._show_supporting_additions(thread_id)

            elif current_step == 'ta_confirmation':
                if message.lower() in ['yes', 'confirm', 'submit', 'y', 'confirm_submit']:
                    # Create expense record for TRANS & ACC
                    success, result = self.create_expense_record(effective_employee, expense_data)
                    if self.session_manager:
                        self.session_manager.clear_session(thread_id)
                    if success:
                        result_payload = result if isinstance(result, dict) else {'message': result}
                        self._log_metric(thread_id, expense_data, result_payload, effective_employee)
                        return {
                            'message': f"✅ Your Travel & Accommodation request has been submitted! Expense ID: {result.get('id', 'N/A')}",
                            'thread_id': thread_id,
                            'source': 'reimbursement_service'
                        }
                    else:
                        return {
                            'message': f"❌ There was an error submitting your request: {result}",
                            'thread_id': thread_id,
                            'source': 'reimbursement_service'
                        }
                elif message.lower() in ['no', 'cancel', 'n', 'cancel_submit']:
                    if self.session_manager:
                        self.session_manager.clear_session(thread_id)
                    return {
                        'message': 'Travel & Accommodation request cancelled.',
                        'thread_id': thread_id,
                        'source': 'reimbursement_service'
                    }
                else:
                    return self._show_ta_confirmation(expense_data, thread_id)

            # TRANS & ACC: supporting additions after amount
            elif current_step == 'ta_supporting_additions':
                normalized = (message or '').strip().lower()
                if normalized in ['descriptions', 'description', 'add_description', 'support_add_desc']:
                    session_data['step'] = 'ta_supporting_additions_description'
                    if self.session_manager:
                        self.session_manager.update_session(thread_id, session_data)
                    return {
                        'message': "Please type a description to add:",
                        'thread_id': thread_id,
                        'source': 'reimbursement_service'
                    }
                if normalized in ['links', 'link', 'add_link', 'support_add_link']:
                    session_data['step'] = 'ta_supporting_additions_link'
                    if self.session_manager:
                        self.session_manager.update_session(thread_id, session_data)
                    return {
                        'message': "Please paste a link (receipt or supporting doc).",
                        'thread_id': thread_id,
                        'source': 'reimbursement_service'
                    }
                if normalized in ['next', 'continue', 'proceed', 'support_next']:
                    session_data['step'] = 'ta_confirmation'
                    if self.session_manager:
                        self.session_manager.update_session(thread_id, session_data)
                    return self._show_ta_confirmation(expense_data, thread_id)
                # Re-show additions menu on unrecognized input
                if self.session_manager:
                    self.session_manager.update_session(thread_id, session_data)
                return self._show_supporting_additions(thread_id)

            elif current_step == 'ta_supporting_additions_description':
                # Save description and return to additions menu
                if message and message.strip():
                    expense_data['description'] = message.strip()
                    session_data['expense_data'] = expense_data
                session_data['step'] = 'ta_supporting_additions'
                if self.session_manager:
                    self.session_manager.update_session(thread_id, session_data)
                return self._show_supporting_additions(thread_id)

            elif current_step == 'ta_supporting_additions_link':
                # Save valid link and return to additions menu
                raw = (message or '').strip()
                if raw and raw.lower() not in ['skip', 'none']:
                    if raw.startswith(('http://', 'https://')):
                        expense_data['attached_link'] = raw
                        session_data['expense_data'] = expense_data
                    else:
                        # Ask again if invalid URL
                        return {
                            'message': "Please provide a valid URL starting with http:// or https://, or type 'skip' to skip.",
                            'thread_id': thread_id,
                            'source': 'reimbursement_service'
                        }
                session_data['step'] = 'ta_supporting_additions'
                if self.session_manager:
                    self.session_manager.update_session(thread_id, session_data)
                return self._show_supporting_additions(thread_id)

            elif current_step == 'per_diem_confirmation':
                # Submit PER_DIEM with collected fields
                if message.lower() in ['yes', 'confirm', 'submit', 'y', 'confirm_submit']:
                    success, result = self.create_expense_record(effective_employee, expense_data)
                    if self.session_manager:
                        self.session_manager.clear_session(thread_id)
                    if success:
                        result_payload = result if isinstance(result, dict) else {'message': result}
                        self._log_metric(thread_id, expense_data, result_payload, effective_employee)
                        return {
                            'message': f"✅ Your [PER_DIEM] request has been submitted! Expense ID: {result.get('id', 'N/A')}",
                            'thread_id': thread_id,
                            'source': 'reimbursement_service'
                        }
                    else:
                        return {
                            'message': f"❌ There was an error submitting your request: {result}",
                            'thread_id': thread_id,
                            'source': 'reimbursement_service'
                        }
                elif message.lower() in ['no', 'cancel', 'n', 'cancel_submit']:
                    if self.session_manager:
                        self.session_manager.clear_session(thread_id)
                    return {
                        'message': 'Per Diem request cancelled.',
                        'thread_id': thread_id,
                        'source': 'reimbursement_service'
                    }
                else:
                    return self._show_per_diem_confirmation(expense_data, thread_id)

            return {
                'message': "I'm not sure how to handle that. Please try again or start a new reimbursement request.",
                'thread_id': thread_id,
                'source': 'reimbursement_service'
            }

        except Exception as e:
            self._log(f"Error continuing reimbursement session: {e}", "general")
            import traceback
            traceback.print_exc()
            return {
                'message': "I encountered an error processing your request. Please try again or contact HR for assistance.",
                'thread_id': thread_id,
                'source': 'reimbursement_service'
            }

    def _show_confirmation(self, expense_data: Dict, thread_id: str) -> Dict:
        """Show confirmation summary before submission"""
        try:
            category_display = self.expense_categories.get(expense_data.get('category', ''), 'Unknown')
            amount = expense_data.get('amount', 0)
            date = expense_data.get('date', '')
            description = expense_data.get('description', '')
            # Ensure Miscellaneous shows a sensible default if user didn't add one
            try:
                cat_key = (expense_data.get('category') or '').lower()
                if (not description) and cat_key == 'miscellaneous':
                    description = '[EXP_GEN] Miscellaneous'
            except Exception:
                pass
            link = expense_data.get('attached_link', '')

            confirmation_text = f"""Almost there! Let's review your reimbursement request:

📂 **Category:** {category_display}
📝 **Description:** {description}
💰 **Amount:** ${amount:.2f}
📅 **Date:** {date}
🔗 **Receipt Link:** {link if link else 'None'}

Do you want to submit this request? Reply or click 'Yes' to confirm or 'No' to cancel."""

            return {
                'message': confirmation_text,
                'thread_id': thread_id,
                'buttons': [
                    {'text': 'Yes', 'value': 'confirm_submit', 'type': 'action_reimbursement'},
                    {'text': 'No', 'value': 'cancel_submit', 'type': 'action_reimbursement'}
                ],
                'source': 'reimbursement_service'
            }

        except Exception as e:
            self._log(f"Error showing confirmation: {e}", "general")
            return {
                'message': "Please confirm by typing 'Yes' to submit or 'No' to cancel.",
                'thread_id': thread_id,
                'source': 'reimbursement_service'
            }

    def _show_supporting_additions(self, thread_id: str) -> Dict:
        """Show the supporting additions bubble with Descriptions, Links, Next."""
        return {
            'message': "Would you like to add any supporting additions?",
            'thread_id': thread_id,
            'buttons': [
                {'text': 'Descriptions', 'value': 'support_add_desc', 'type': 'action_reimbursement'},
                {'text': 'Links', 'value': 'support_add_link', 'type': 'action_reimbursement'},
                {'text': 'Next', 'value': 'support_next', 'type': 'action_reimbursement'}
            ],
            'source': 'reimbursement_service'
        }

    def _show_per_diem_confirmation(self, expense_data: Dict, thread_id: str) -> Dict:
        """Confirmation bubble specific to PER_DIEM flow"""
        try:
            category_display = self.expense_categories.get(expense_data.get('category', ''), '[PER_DIEM] Per Diem')
            from_date = expense_data.get('per_diem_from', '-')
            to_date = expense_data.get('per_diem_to', '-')
            dest_name = expense_data.get('per_diem_destination_name', '-')
            # Include optional description and link (default description if missing)
            description = expense_data.get('description', '') if isinstance(expense_data.get('description'), str) else ''
            description = description.strip() if description else ''
            if not description:
                description = '[PER_DIEM] Per Diem flow'
            link = expense_data.get('attached_link', '')

            msg = f"""Great! Here's a summary of your Per Diem request:

📂 **Category:** {category_display}
📝 **Description:** {description}
📅 **From:** {from_date}
📅 **To:** {to_date}
🗺️ **Destination:** {dest_name}
🔗 **Receipt Link:** {link if link else 'None'}

Do you want to submit this request? Reply or click 'yes' to confirm or 'no' to cancel"""

            return {
                'message': msg,
                'thread_id': thread_id,
                'buttons': [
                    {'text': 'Yes', 'value': 'confirm_submit', 'type': 'action_reimbursement'},
                    {'text': 'No', 'value': 'cancel_submit', 'type': 'action_reimbursement'}
                ],
                'source': 'reimbursement_service'
            }
        except Exception:
            return {
                'message': "Confirm submission?",
                'thread_id': thread_id,
                'buttons': [
                    {'text': 'Yes', 'value': 'confirm_submit', 'type': 'action_reimbursement'},
                    {'text': 'No', 'value': 'cancel_submit', 'type': 'action_reimbursement'}
                ],
                'source': 'reimbursement_service'
            }

    def _get_destination_options(self) -> Tuple[List[Dict[str, Any]], Optional[str]]:
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
            success, result = self._make_odoo_request('res.country.state', 'search_read', params)
            if not success:
                return [], str(result)
            options = [{'label': r.get('name') or f"State {r.get('id')}", 'value': r.get('id')} for r in (result or [])]
            return options, None
        except Exception as e:
            return [], str(e)

    def _resolve_state_name(self, state_id: int) -> Optional[str]:
        try:
            params = {
                'args': [[state_id]],
                'kwargs': {'fields': ['name']}
            }
            success, result = self._make_odoo_request('res.country.state', 'read', params)
            if success and result:
                rec = result[0] if isinstance(result, list) else result
                return rec.get('name')
            return None
        except Exception:
            return None

    def _show_ta_confirmation(self, expense_data: Dict, thread_id: str) -> Dict:
        """Confirmation summary for Travel & Accommodation flow"""
        category_display = self.expense_categories.get(expense_data.get('category', ''), '[TRANS & ACC] Travel & Accommodation')
        amount = expense_data.get('amount', 0)
        date = expense_data.get('date', datetime.now().strftime('%d/%m/%Y'))
        # Default description when none provided
        raw_desc = expense_data.get('description', '')
        description = raw_desc.strip() if isinstance(raw_desc, str) else ''
        if not description:
            description = '[TRANS & ACC] Travel & Accommodation'
        link = expense_data.get('attached_link', '')

        msg = f"""Great! Here's a summary of your Travel & Accommodation request:

📂 **Category:** {category_display}
📝 **Description:** {description}
💰 **Amount:** ${amount:.2f}
📅 **Date:** {date}
🔗 **Receipt Link:** {link if link else 'None'}

Do you want to submit this request? Reply or click 'yes' to confirm or 'no' to cancel"""

        return {
            'message': msg,
            'thread_id': thread_id,
            'buttons': [
                {'text': 'Yes', 'value': 'confirm_submit', 'type': 'action_reimbursement'},
                {'text': 'No', 'value': 'cancel_submit', 'type': 'action_reimbursement'}
            ],
            'source': 'reimbursement_service'
        }
