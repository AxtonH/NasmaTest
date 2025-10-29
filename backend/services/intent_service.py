from typing import Optional, Dict, Any, Tuple
from difflib import SequenceMatcher


class IntentService:
    """Lightweight fuzzy intent detector for document-related requests.

    Returns (intent, confidence, metadata).
    """

    def __init__(self):
        # Synonyms/phrases for employment letter
        self.employment_letter_keywords = [
            'employment letter', 'employment certificate', 'employment verification',
            'employment confirmation', 'work certificate', 'work letter', 'proof of employment'
        ]
        # Synonyms/phrases for experience letter
        self.experience_letter_keywords = [
            'experience letter', 'experience certificate', 'work experience letter',
            'employment experience', 'certificate of experience'
        ]
        # Embassy / travel related cues
        self.embassy_keywords = [
            'embassy', 'consulate', 'visa',
            'travel', 'travelling', 'traveling',
            'travel letter', 'travel document', 'travel documents',
            'travel paper', 'travel papers', 'travel pappers',
            'teavel', 'teavel letter', 'teavel document', 'teavel documents',
            'schengen', 'letter to', 'to embassy', 'for travelling', 'for traveling'
        ]
        self.document_keywords = ['document', 'letter', 'certificate', 'doc', 'file', 'documents', 'letters', 'papers', 'paper']
        self.generate_keywords = ['generate', 'make', 'create', 'prepare', 'issue', 'download', 'need', 'want', 'get']
        self.arabic_keywords = ['arabic', 'ar', 'عربي', 'العربية']
        self.english_keywords = ['english', 'en']
        
        # Reimbursement/expense related keywords
        self.reimbursement_keywords = [
            'reimbursement', 'expense', 'expense report', 'reimburse', 'expenses',
            'business expense', 'work expense', 'company expense', 'claim expense',
            'submit expense', 'file expense', 'request reimbursement'
        ]
        
        # Time-off/leave related keywords
        self.timeoff_keywords = [
            'sick leave', 'sick day', 'annual leave', 'vacation', 'holiday',
            'time off', 'leave', 'day off', 'days off', 'unpaid leave',
            'medical leave', 'personal leave', 'emergency leave'
        ]

    def _normalize(self, text: str) -> str:
        return ' '.join((text or '').lower().strip().split())

    def _contains_any(self, text: str, phrases: list) -> bool:
        # Retained for backward compatibility, no longer used for decisions
        return any(p in text for p in phrases)

    def _fuzzy_similarity(self, a: str, b: str) -> float:
        """Return a fuzzy similarity between 0..1 using multiple heuristics.

        - Raw ratio via SequenceMatcher
        - Token-sort ratio to handle word order variations
        """
        if not a or not b:
            return 0.0
        a_norm = ' '.join(a.split())
        b_norm = ' '.join(b.split())
        ratio_raw = SequenceMatcher(None, a_norm, b_norm).ratio()
        a_tokens = ' '.join(sorted(a_norm.split()))
        b_tokens = ' '.join(sorted(b_norm.split()))
        ratio_token = SequenceMatcher(None, a_tokens, b_tokens).ratio()
        return max(ratio_raw, ratio_token)

    def _best_fuzzy_score(self, text: str, phrases: list) -> float:
        """Max fuzzy similarity of text against any target phrase."""
        if not text or not phrases:
            return 0.0
        return max((self._fuzzy_similarity(text, p) for p in phrases), default=0.0)

    def detect(self, message: str) -> Tuple[Optional[str], float, Dict[str, Any]]:
        text = self._normalize(message)
        if not text:
            return None, 0.0, {}

        # Fuzzy scores per category (0..1)
        employment_score = self._best_fuzzy_score(text, self.employment_letter_keywords)
        experience_score = self._best_fuzzy_score(text, self.experience_letter_keywords)
        embassy_score = self._best_fuzzy_score(text, self.embassy_keywords)
        reimbursement_score = self._best_fuzzy_score(text, self.reimbursement_keywords)
        timeoff_score = self._best_fuzzy_score(text, self.timeoff_keywords)
        doc_hint_score = self._best_fuzzy_score(text, self.document_keywords)
        gen_hint_score = self._best_fuzzy_score(text, self.generate_keywords)
        ar_hint = self._best_fuzzy_score(text, self.arabic_keywords) >= 0.7
        en_hint = self._best_fuzzy_score(text, self.english_keywords) >= 0.7

        # Confidence components
        has_embassy_anchor = embassy_score >= 0.55

        # Confidence heuristic (weighted by hints)
        confidence = max(employment_score, experience_score, embassy_score, reimbursement_score, timeoff_score)
        if doc_hint_score >= 0.5:
            confidence += 0.15
        if gen_hint_score >= 0.5:
            confidence += 0.1
        confidence = min(confidence, 1.0)

        # Time-off/leave requests (rule 1) - check first, highest priority
        if timeoff_score >= 0.6 or (timeoff_score >= 0.45 and gen_hint_score >= 0.5):
            return 'timeoff_request', max(confidence, 0.65), {}

        # Reimbursement/expense requests (rule 4) - check before embassy
        if reimbursement_score >= 0.6 or (reimbursement_score >= 0.45 and gen_hint_score >= 0.5):
            return 'reimbursement_request', max(confidence, 0.65), {}

        # Embassy/travel letters (rule 3)
        if has_embassy_anchor:
            base_conf = max(0.6, embassy_score)
            return 'embassy_letter', min(1.0, base_conf + (0.1 if doc_hint_score >= 0.5 else 0.0) + (0.05 if gen_hint_score >= 0.5 else 0.0)), {}

        # Experience letters (rule 1) - require explicit anchor "experience"
        if experience_score >= 0.6 or (experience_score >= 0.45 and doc_hint_score >= 0.5):
            return 'experience_letter', max(confidence, 0.65), {}

        # Employment letters (rule 2) - require employment/work cues and not experience
        if (employment_score >= 0.6) or (employment_score >= 0.45 and (doc_hint_score >= 0.5 or gen_hint_score >= 0.5)):
            # Threshold ~0.6 for actionable intent
            meta = {}
            if ar_hint:
                meta['lang'] = 'ar'
            elif en_hint:
                meta['lang'] = 'en'
            return 'employment_letter', max(confidence, 0.65), meta

        # Generic document request intent
        if doc_hint_score >= 0.5 and gen_hint_score >= 0.5:
            # Use token overlap over doc phrases as a weak score
            generic_conf = 0.6
            return 'document_request', generic_conf, {}

        return None, 0.0, {}



