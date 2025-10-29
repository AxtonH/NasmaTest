import json
import os
from typing import Dict, Optional, Any
from datetime import datetime, timedelta
import threading

class SessionManager:
    """Manages multi-step conversation flows and sessions"""
    
    def __init__(self):
        self.sessions: Dict[str, Dict] = {}  # thread_id -> session_data
        self.session_expiry: Dict[str, datetime] = {}
        self.session_duration = timedelta(minutes=15)  # Session timeout for time-off requests
        self.lock = threading.Lock()
        
        # Storage directory for persistent sessions
        self.storage_dir = "conversation_storage"
        if not os.path.exists(self.storage_dir):
            os.makedirs(self.storage_dir)
    
    def _get_session_file(self, thread_id: str) -> str:
        """Get the session storage file path"""
        return os.path.join(self.storage_dir, f"session_{thread_id}.json")
    
    def _save_session(self, thread_id: str, session_data: Dict):
        """Save session to persistent storage (with lock)"""
        try:
            print(f"DEBUG: _save_session called for thread: {thread_id}")
            print(f"DEBUG: Session data keys: {list(session_data.keys()) if session_data else 'None'}")
            
            with self.lock:
                print(f"DEBUG: _save_session acquired lock")
                self._save_session_internal(thread_id, session_data)
        except Exception as e:
            print(f"DEBUG: Error saving session: {e}")
            import traceback
            traceback.print_exc()
    
    def _save_session_internal(self, thread_id: str, session_data: Dict):
        """Save session to persistent storage (without lock - assumes lock is already held)"""
        try:
            print(f"DEBUG: _save_session_internal called for thread: {thread_id}")
            
            # Try simple JSON serialization first
            try:
                print(f"DEBUG: Attempting direct JSON serialization...")
                session_file = self._get_session_file(thread_id)
                with open(session_file, 'w', encoding='utf-8') as f:
                    json.dump(session_data, f, indent=2, ensure_ascii=False, default=str)
                print(f"DEBUG: Direct JSON serialization successful")
                return
            except Exception as direct_error:
                print(f"DEBUG: Direct JSON serialization failed: {direct_error}")
            
            # Fallback to data cleaning
            print(f"DEBUG: About to clean session data...")
            cleaned_data = self._clean_session_data(session_data)
            print(f"DEBUG: Session data cleaned successfully")
            
            session_file = self._get_session_file(thread_id)
            print(f"DEBUG: About to write cleaned data to file: {session_file}")
            with open(session_file, 'w', encoding='utf-8') as f:
                json.dump(cleaned_data, f, indent=2, ensure_ascii=False, default=str)
            print(f"DEBUG: File written successfully")
        except Exception as e:
            print(f"DEBUG: Error in _save_session_internal: {e}")
            import traceback
            traceback.print_exc()
    
    def _clean_session_data(self, data, depth=0):
        """Clean session data to prevent serialization issues"""
        print(f"DEBUG: _clean_session_data called at depth {depth}, type: {type(data)}")
        
        # Prevent infinite recursion
        if depth > 10:
            print(f"DEBUG: Maximum depth reached in _clean_session_data")
            return f"<max_depth_reached>"
            
        if isinstance(data, dict):
            print(f"DEBUG: Processing dict with {len(data)} keys at depth {depth}")
            cleaned = {}
            for key, value in data.items():
                print(f"DEBUG: Processing key '{key}' of type {type(value)}")
                try:
                    # For simple types, just test serialization without recursion
                    if isinstance(value, (str, int, float, bool, type(None))):
                        json.dumps(value)
                        cleaned[key] = value
                        print(f"DEBUG: Simple type key '{key}' processed successfully")
                    else:
                        # For complex types, recurse with depth tracking
                        print(f"DEBUG: Recursing into complex type for key '{key}'")
                        cleaned[key] = self._clean_session_data(value, depth + 1)
                        print(f"DEBUG: Complex type key '{key}' processed successfully")
                except (TypeError, ValueError) as e:
                    print(f"DEBUG: Skipping unserializable session data key '{key}': {e}")
                    cleaned[key] = f"<unserializable: {type(value)}>"
            print(f"DEBUG: Dict processing complete at depth {depth}")
            return cleaned
        elif isinstance(data, list):
            print(f"DEBUG: Processing list with {len(data)} items at depth {depth}")
            cleaned = []
            for i, item in enumerate(data):
                if i % 5 == 0:  # Log every 5th item to avoid spam
                    print(f"DEBUG: Processing list item {i}/{len(data)} of type {type(item)}")
                try:
                    # For simple types, just test serialization without recursion
                    if isinstance(item, (str, int, float, bool, type(None))):
                        json.dumps(item)
                        cleaned.append(item)
                    else:
                        # For complex types, recurse with depth tracking
                        cleaned.append(self._clean_session_data(item, depth + 1))
                except (TypeError, ValueError) as e:
                    print(f"DEBUG: Skipping unserializable session data item {i}: {e}")
                    cleaned.append(f"<unserializable: {type(item)}>")
            print(f"DEBUG: List processing complete at depth {depth}")
            return cleaned
        else:
            # Simple types - just test and return
            print(f"DEBUG: Processing simple type {type(data)}")
            try:
                json.dumps(data, default=str)
                return data
            except (TypeError, ValueError):
                return f"<unserializable: {type(data)}>"
    
    def _load_session(self, thread_id: str) -> Optional[Dict]:
        """Load session from persistent storage (public method with lock)"""
        with self.lock:
            return self._load_session_internal(thread_id)
    
    def _load_session_internal(self, thread_id: str) -> Optional[Dict]:
        """Load session from persistent storage (internal method without lock)"""
        try:
            session_file = self._get_session_file(thread_id)
            if os.path.exists(session_file):
                with open(session_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            return None
        except Exception as e:
            print(f"DEBUG: Error loading session: {e}")
            return None
    
    def start_session(self, thread_id: str, session_type: str, data: Dict = None) -> Dict:
        """Start a new multi-step session"""
        session_data = {
            'session_id': thread_id,
            'type': session_type,
            'state': 'started',
            'step': 1,
            'created_at': datetime.now().isoformat(),
            'data': data or {},
            'completed_steps': []
        }
        
        with self.lock:
            self.sessions[thread_id] = session_data
            self.session_expiry[thread_id] = datetime.now() + self.session_duration
        
        self._save_session(thread_id, session_data)
        print(f"DEBUG: Started {session_type} session for thread {thread_id}")
        return session_data
    
    def get_session(self, thread_id: str) -> Optional[Dict]:
        """Get current session for thread"""
        print(f"DEBUG: SessionManager.get_session called for thread: {thread_id}")
        with self.lock:
            print(f"DEBUG: SessionManager acquired lock for thread: {thread_id}")
            # Check memory first
            if thread_id in self.sessions:
                if self._is_session_valid(thread_id):
                    return self.sessions[thread_id]
                else:
                    # Session expired
                    self._clear_session_internal(thread_id)
                    return None
            
            # Try to load from storage
            session_data = self._load_session_internal(thread_id)
            if session_data:
                # Check if still valid based on created_at
                try:
                    created_at = datetime.fromisoformat(session_data['created_at'])
                    if datetime.now() - created_at < self.session_duration:
                        self.sessions[thread_id] = session_data
                        self.session_expiry[thread_id] = datetime.now() + self.session_duration
                        return session_data
                    else:
                        # Expired, remove file
                        self._clear_session_internal(thread_id)
                except Exception as e:
                    print(f"DEBUG: SessionManager error loading session: {e}")
                    pass
            
            print(f"DEBUG: SessionManager returning None for thread: {thread_id}")
            return None

    def get_active_session(self, thread_id: str) -> Optional[Dict]:
        """Return the active (started/active) session for this thread, or None."""
        try:
            s = self.get_session(thread_id)
            if s and s.get('state') in ['started', 'active']:
                return s
            return None
        except Exception:
            return None

    def get_active_flow_type(self, thread_id: str) -> Optional[str]:
        """Return the type of the currently active flow for this thread, if any."""
        s = self.get_active_session(thread_id)
        if s:
            return s.get('type')
        return None
    
    def update_session(self, thread_id: str, updates: Dict) -> bool:
        """Update session data"""
        print(f"DEBUG: SessionManager.update_session called for thread: {thread_id}")
        print(f"DEBUG: Updates keys: {list(updates.keys()) if updates else 'None'}")
        
        with self.lock:
            print(f"DEBUG: SessionManager acquired lock for update")
            if thread_id in self.sessions and self._is_session_valid(thread_id):
                print(f"DEBUG: Session exists and is valid, updating...")
                self.sessions[thread_id].update(updates)
                print(f"DEBUG: Session data updated in memory")
                self.session_expiry[thread_id] = datetime.now() + self.session_duration
                print(f"DEBUG: Session expiry updated")
                print(f"DEBUG: About to save session (without lock)...")
                self._save_session_internal(thread_id, self.sessions[thread_id])
                print(f"DEBUG: Session saved successfully")
                return True
            else:
                print(f"DEBUG: Session not found or invalid for thread: {thread_id}")
                return False
    
    def advance_session_step(self, thread_id: str, step_data: Dict = None) -> bool:
        """Advance session to next step"""
        session = self.get_session(thread_id)
        if session:
            current_step = session.get('step', 1)
            updates = {
                'step': current_step + 1,
                'completed_steps': session.get('completed_steps', []) + [current_step]
            }
            if step_data:
                # Store step data at the root level as well as in nested data
                updates.update(step_data)
                updates['data'] = {**session.get('data', {}), **step_data}

            return self.update_session(thread_id, updates)
        return False
    
    def complete_session(self, thread_id: str, result: Dict = None) -> bool:
        """Mark session as completed"""
        updates = {
            'state': 'completed',
            'completed_at': datetime.now().isoformat()
        }
        if result:
            updates['result'] = result
        
        success = self.update_session(thread_id, updates)
        if success:
            # Keep completed session for a short while, then clean up
            print(f"DEBUG: Completed session for thread {thread_id}")
        return success
    
    def cancel_session(self, thread_id: str, reason: str = None) -> bool:
        """Cancel an active session"""
        updates = {
            'state': 'cancelled',
            'cancelled_at': datetime.now().isoformat()
        }
        if reason:
            updates['cancel_reason'] = reason
        
        success = self.update_session(thread_id, updates)
        if success:
            print(f"DEBUG: Cancelled session for thread {thread_id}: {reason}")
        return success
    
    def clear_session(self, thread_id: str):
        """Clear session from memory and storage"""
        with self.lock:
            self._clear_session_internal(thread_id)
    
    def _clear_session_internal(self, thread_id: str):
        """Internal method to clear session (assumes lock is already held)"""
        if thread_id in self.sessions:
            del self.sessions[thread_id]
        if thread_id in self.session_expiry:
            del self.session_expiry[thread_id]
        
        # Remove persistent file
        try:
            session_file = self._get_session_file(thread_id)
            if os.path.exists(session_file):
                os.remove(session_file)
        except Exception as e:
            print(f"DEBUG: Error removing session file: {e}")
    
    def _is_session_valid(self, thread_id: str) -> bool:
        """Check if session is still valid (not expired)"""
        if thread_id not in self.session_expiry:
            return False
        return datetime.now() < self.session_expiry[thread_id]
    
    def cleanup_expired_sessions(self):
        """Clean up expired sessions"""
        expired_threads = []
        with self.lock:
            for thread_id in list(self.session_expiry.keys()):
                if not self._is_session_valid(thread_id):
                    expired_threads.append(thread_id)

        for thread_id in expired_threads:
            self.clear_session(thread_id)

    def find_active_timeoff_sessions(self) -> list:
        """Find all active timeoff sessions"""
        active_sessions = []

        try:
            # First check in-memory sessions
            with self.lock:
                for thread_id, session_data in self.sessions.items():
                    if (session_data.get('type') == 'timeoff' and
                        session_data.get('state') in ['active', 'started'] and
                        self._is_session_valid(thread_id)):
                        active_sessions.append((thread_id, session_data))

            # Also check persistent storage for sessions not in memory
            if os.path.exists(self.storage_dir):
                for filename in os.listdir(self.storage_dir):
                    if filename.startswith('session_') and filename.endswith('.json'):
                        thread_id = filename[8:-5]  # Remove 'session_' prefix and '.json' suffix

                        # Skip if already checked in memory
                        if thread_id in self.sessions:
                            continue

                        try:
                            session_file = self._get_session_file(thread_id)
                            with open(session_file, 'r', encoding='utf-8') as f:
                                session_data = json.load(f)

                            # Check if it's an active timeoff session (accept both 'started' and 'active')
                            if (session_data.get('type') == 'timeoff' and
                                session_data.get('state') in ['active', 'started']):

                                # Check expiry
                                created_at = session_data.get('created_at')
                                if created_at:
                                    created_time = datetime.fromisoformat(created_at)
                                    if datetime.now() - created_time < self.session_duration:
                                        active_sessions.append((thread_id, session_data))
                        except Exception:
                            continue

        except Exception as e:
            print(f"DEBUG: Error finding active sessions: {e}")

        return active_sessions

    def get_session_stats(self) -> Dict:
        """Get session statistics"""
        with self.lock:
            active_sessions = len(self.sessions)
            session_types = {}
            for session in self.sessions.values():
                session_type = session.get('type', 'unknown')
                session_types[session_type] = session_types.get(session_type, 0) + 1
            
            return {
                'active_sessions': active_sessions,
                'session_types': session_types,
                'memory_usage': len(self.sessions)
            }
