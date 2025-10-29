"""
Service for managing "Remember Me" functionality with device-specific auto-login.
Uses Supabase for persistent token storage and device fingerprinting for security.
"""
import secrets
import hashlib
from datetime import datetime, timedelta
from typing import Optional, Tuple
from supabase import create_client, Client


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

    def verify_token(self, token: str, device_fingerprint: str) -> Optional[Tuple[str, str]]:
        """
        Verify a remember me token and return credentials if valid

        Args:
            token: The remember me token
            device_fingerprint: Device identifier from the current request

        Returns:
            Tuple of (username, password) if valid, None otherwise
        """
        token_hash = self._hash_token(token)

        # Query for the token
        response = self.supabase.table(self.table_name).select('*').eq('token_hash', token_hash).execute()

        if not response.data or len(response.data) == 0:
            return None

        record = response.data[0]
        username = record['username']
        encrypted_password = record['encrypted_password']
        stored_fingerprint = record['device_fingerprint']

        # Verify device fingerprint matches
        if stored_fingerprint != device_fingerprint:
            return None

        # Update last used timestamp
        now = datetime.utcnow()
        update_data = {
            'last_used_at': now.isoformat()
        }

        self.supabase.table(self.table_name).update(update_data).eq('token_hash', token_hash).execute()

        # Decrypt password
        try:
            password = self._simple_decrypt(encrypted_password, token)
            return (username, password)
        except Exception:
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
