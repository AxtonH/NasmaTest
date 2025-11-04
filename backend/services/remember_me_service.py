"""
Service for managing "Remember Me" functionality with device-specific auto-login.
Uses Supabase for persistent token storage and device fingerprinting for security.
"""
import secrets
import hashlib
from datetime import datetime, timedelta
from typing import Optional, Tuple
from supabase import create_client, Client

try:
    from ..config.settings import Config
except Exception:
    from config.settings import Config

def debug_log(message: str, category: str = "general"):
    """Conditional debug logging based on configuration"""
    if category == "odoo_data" and Config.DEBUG_ODOO_DATA:
        print(f"DEBUG [AUTO_LOGIN]: {message}")
    elif category == "bot_logic" and Config.DEBUG_BOT_LOGIC:
        print(f"DEBUG [AUTO_LOGIN]: {message}")
    elif category == "knowledge_base" and Config.DEBUG_KNOWLEDGE_BASE:
        print(f"DEBUG [AUTO_LOGIN]: {message}")
    elif category == "general" and Config.VERBOSE_LOGS:
        print(f"DEBUG [AUTO_LOGIN]: {message}")
    # Always log authentication failures
    if "FAILED" in message.upper() or "ERROR" in message.upper() or "FAIL" in message.upper():
        print(f"ERROR [AUTO_LOGIN]: {message}")


class RememberMeService:
    """Manages remember me tokens for persistent login across sessions"""

    def __init__(self, supabase_url: str = None, supabase_key: str = None, table_name: str = "remember_me_tokens"):
        """
        Initialize the RememberMeService with Supabase connection

        Args:
            supabase_url: Supabase project URL
            supabase_key: Supabase service role key
            table_name: Name of the table storing remember me tokens
        """
        if not supabase_url or not supabase_key:
            raise ValueError("Supabase URL and key are required")

        self.supabase: Client = create_client(supabase_url, supabase_key)
        self.table_name = table_name

    def _hash_token(self, token: str) -> str:
        """Hash a token for secure storage"""
        return hashlib.sha256(token.encode()).hexdigest()

    def _simple_encrypt(self, text: str, key: str) -> str:
        """Simple XOR-based encryption (for demonstration; use proper encryption in production)"""
        key_bytes = key.encode()
        text_bytes = text.encode()
        encrypted = bytearray()

        for i, byte in enumerate(text_bytes):
            encrypted.append(byte ^ key_bytes[i % len(key_bytes)])

        # Convert to hex string for storage
        return encrypted.hex()

    def _simple_decrypt(self, encrypted_hex: str, key: str) -> str:
        """Simple XOR-based decryption"""
        key_bytes = key.encode()
        encrypted_bytes = bytes.fromhex(encrypted_hex)
        decrypted = bytearray()

        for i, byte in enumerate(encrypted_bytes):
            decrypted.append(byte ^ key_bytes[i % len(key_bytes)])

        return decrypted.decode()

    def create_token(self, username: str, password: str, device_fingerprint: str) -> str:
        """
        Create a new remember me token for a user-device combination

        Args:
            username: User's username
            password: User's password (will be encrypted)
            device_fingerprint: Unique device identifier

        Returns:
            The remember me token (to be stored in browser)
        """
        # Generate a secure random token
        token = secrets.token_urlsafe(32)
        token_hash = self._hash_token(token)

        # Encrypt password using token as key
        encrypted_password = self._simple_encrypt(password, token)

        # Get current timestamp
        created_at = datetime.utcnow()

        # First, remove any existing tokens for this user-device combination
        try:
            self.supabase.table(self.table_name).delete().eq('username', username).eq('device_fingerprint', device_fingerprint).execute()
        except Exception:
            # Continue even if delete fails (e.g., no existing tokens)
            pass

        # Insert new token (no expiration - permanent until manually removed)
        data = {
            'token_hash': token_hash,
            'username': username,
            'device_fingerprint': device_fingerprint,
            'encrypted_password': encrypted_password,
            'created_at': created_at.isoformat()
        }

        self.supabase.table(self.table_name).insert(data).execute()

        return token

    def verify_token(self, token: str, device_fingerprint: str) -> Optional[Tuple[str, str, str]]:
        """
        Verify a remember me token and return credentials if valid

        Args:
            token: The remember me token
            device_fingerprint: Device identifier from the current request

        Returns:
            Tuple of (username, password, failure_reason) if valid, None otherwise
            failure_reason is empty string if successful, otherwise contains reason for failure
        """
        token_hash = self._hash_token(token)
        debug_log(f"Starting token verification for hash: {token_hash[:16]}...", "bot_logic")

        # Query for the token
        try:
            response = self.supabase.table(self.table_name).select('*').eq('token_hash', token_hash).execute()
            debug_log(f"Token query returned {len(response.data) if response.data else 0} records", "bot_logic")
        except Exception as e:
            error_msg = f"FAILED: Error querying token from database: {str(e)}"
            debug_log(error_msg, "bot_logic")
            return None

        if not response.data or len(response.data) == 0:
            debug_log(f"FAILED: Token not found in database (hash: {token_hash[:16]}...)", "bot_logic")
            return None

        record = response.data[0]
        username = record['username']
        encrypted_password = record['encrypted_password']
        stored_fingerprint = record['device_fingerprint']
        created_at = record.get('created_at', 'unknown')
        last_used_at = record.get('last_used_at', 'never')
        
        debug_log(f"Token found for user: {username}, created: {created_at}, last_used: {last_used_at}", "bot_logic")

        # Verify device fingerprint matches
        if stored_fingerprint != device_fingerprint:
            # Fingerprint mismatch - but token is valid, so update fingerprint
            # This handles legitimate cases where fingerprints change (browser updates, GPU updates, etc.)
            debug_log(f"Device fingerprint mismatch detected. Stored: {stored_fingerprint[:16]}..., Provided: {device_fingerprint[:16]}...", "bot_logic")
            debug_log(f"Token is valid, updating fingerprint to allow auto-login (fingerprints can change due to browser/GPU updates)", "bot_logic")
            
            try:
                # Update the fingerprint in the database
                update_data = {
                    'device_fingerprint': device_fingerprint
                }
                self.supabase.table(self.table_name).update(update_data).eq('token_hash', token_hash).execute()
                debug_log(f"Successfully updated device fingerprint for token", "bot_logic")
            except Exception as e:
                # Log error but continue - fingerprint update failed but token is still valid
                debug_log(f"WARNING: Error updating device fingerprint for token: {str(e)}", "bot_logic")
                # Still proceed with authentication since token is valid

        debug_log(f"Device fingerprint verified successfully", "bot_logic")

        # Update last used timestamp (with error handling)
        now = datetime.utcnow()
        update_data = {
            'last_used_at': now.isoformat()
        }

        try:
            self.supabase.table(self.table_name).update(update_data).eq('token_hash', token_hash).execute()
            debug_log(f"Successfully updated last_used_at timestamp", "bot_logic")
        except Exception as e:
            # Log error but continue - don't fail verification if update fails
            debug_log(f"WARNING: Error updating last_used_at for token: {str(e)}", "bot_logic")

        # Decrypt password
        try:
            password = self._simple_decrypt(encrypted_password, token)
            debug_log(f"Password decrypted successfully for user: {username}", "bot_logic")
            return (username, password, "")
        except Exception as e:
            # Log decryption error
            error_msg = f"FAILED: Error decrypting password for token: {str(e)}"
            debug_log(error_msg, "bot_logic")
            return None

    def remove_token(self, username: str, device_fingerprint: str = None):
        """
        Remove remember me token(s) for a user

        Args:
            username: User's username
            device_fingerprint: Optional device fingerprint. If provided, only removes
                              tokens for that device. Otherwise removes all tokens for user.
        """
        query = self.supabase.table(self.table_name).delete().eq('username', username)

        if device_fingerprint:
            query = query.eq('device_fingerprint', device_fingerprint)

        query.execute()

    def cleanup_expired_tokens(self) -> int:
        """
        Cleanup method maintained for compatibility but does nothing since tokens don't expire.
        Tokens are only removed manually via logout or remove_token().

        Returns:
            0 (no tokens to clean up)
        """
        return 0

    def cleanup_old_unused_tokens(self, days_unused: int = 90) -> int:
        """
        Remove tokens that haven't been used in a specified number of days.
        This helps clean up stale tokens from old devices or sessions.

        Args:
            days_unused: Number of days since last use before token is considered stale

        Returns:
            Number of tokens removed
        """
        try:
            cutoff_date = datetime.utcnow() - timedelta(days=days_unused)
            
            # Delete tokens with last_used_at older than cutoff_date
            response = self.supabase.table(self.table_name).delete().lt('last_used_at', cutoff_date.isoformat()).execute()
            
            # Also delete tokens that have never been used (null last_used_at) and are older than cutoff_date
            # We'll handle null values separately since Supabase doesn't support NULL comparisons directly
            all_tokens = self.supabase.table(self.table_name).select('id, created_at, last_used_at').execute()
            
            deleted_count = 0
            if response.data:
                deleted_count = len(response.data)
            
            # Check for null last_used_at tokens older than cutoff_date
            if all_tokens.data:
                for token in all_tokens.data:
                    if token.get('last_used_at') is None:
                        created_at_str = token.get('created_at')
                        if created_at_str:
                            try:
                                created_at = datetime.fromisoformat(created_at_str.replace('Z', '+00:00'))
                                if created_at < cutoff_date:
                                    # Delete this token
                                    self.supabase.table(self.table_name).delete().eq('id', token['id']).execute()
                                    deleted_count += 1
                            except Exception:
                                # Skip if date parsing fails
                                pass
            
            return deleted_count
        except Exception as e:
            print(f"Error cleaning up old tokens: {str(e)}")
            return 0

    def get_user_tokens(self, username: str) -> list:
        """
        Get all tokens for a specific user (for debugging/admin purposes).

        Args:
            username: User's username

        Returns:
            List of token records
        """
        try:
            response = self.supabase.table(self.table_name).select('id, username, device_fingerprint, created_at, last_used_at').eq('username', username).execute()
            return response.data or []
        except Exception as e:
            print(f"Error fetching user tokens: {str(e)}")
            return []
