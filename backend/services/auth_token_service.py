"""
Service for managing JWT access tokens and refresh tokens for authentication.
Implements a secure token-based authentication system without password storage.
"""
import secrets
import hashlib
import jwt
from datetime import datetime, timedelta
from typing import Optional, Dict, Tuple
from supabase import create_client, Client

try:
    from ..config.settings import Config
except Exception:
    from config.settings import Config

def debug_log(message: str, category: str = "general"):
    """Conditional debug logging based on configuration"""
    if category == "odoo_data" and Config.DEBUG_ODOO_DATA:
        print(f"DEBUG [AUTH_TOKEN]: {message}")
    elif category == "bot_logic" and Config.DEBUG_BOT_LOGIC:
        print(f"DEBUG [AUTH_TOKEN]: {message}")
    elif category == "general" and Config.VERBOSE_LOGS:
        print(f"DEBUG [AUTH_TOKEN]: {message}")
    if "FAILED" in message.upper() or "ERROR" in message.upper() or "FAIL" in message.upper():
        print(f"ERROR [AUTH_TOKEN]: {message}")


class AuthTokenService:
    """Manages JWT access tokens and refresh tokens for authentication"""

    def __init__(self, supabase_url: str = None, supabase_key: str = None, table_name: str = "refresh_tokens"):
        """
        Initialize the AuthTokenService with Supabase connection

        Args:
            supabase_url: Supabase project URL
            supabase_key: Supabase service role key
            table_name: Name of the table storing refresh tokens
        """
        if not supabase_url or not supabase_key:
            raise ValueError("Supabase URL and key are required")

        self.supabase: Client = create_client(supabase_url, supabase_key)
        self.table_name = table_name
        
        # JWT secret key (should be in environment variables in production)
        self.jwt_secret = getattr(Config, 'JWT_SECRET_KEY', secrets.token_urlsafe(32))
        self.jwt_algorithm = 'HS256'
        
        # Token expiration times
        self.access_token_expiry = timedelta(days=7)  # 7 days
        self.refresh_token_expiry = timedelta(days=365)  # 1 year (or never expires)

    def _hash_token(self, token: str) -> str:
        """Hash a refresh token for secure storage"""
        return hashlib.sha256(token.encode()).hexdigest()

    def _simple_encrypt(self, text: str, key: str) -> str:
        """Simple XOR-based encryption (for password storage)"""
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

    def create_access_token(self, user_id: int, username: str, email: str = None) -> str:
        """
        Create a JWT access token

        Args:
            user_id: User ID from Odoo
            username: Username
            email: Optional email address

        Returns:
            JWT access token string
        """
        payload = {
            'user_id': user_id,
            'username': username,
            'email': email or username,
            'exp': datetime.utcnow() + self.access_token_expiry,
            'iat': datetime.utcnow(),
            'type': 'access'
        }
        
        token = jwt.encode(payload, self.jwt_secret, algorithm=self.jwt_algorithm)
        debug_log(f"Created access token for user_id={user_id}, username={username}", "bot_logic")
        return token

    def create_refresh_token(self, user_id: int, username: str, password: str) -> str:
        """
        Create a refresh token and store it in the database with encrypted password

        Args:
            user_id: User ID from Odoo
            username: Username
            password: User password (will be encrypted and stored)

        Returns:
            Refresh token string (to be stored in browser)
        """
        # Generate a secure random token
        refresh_token = secrets.token_urlsafe(64)  # 86 characters
        token_hash = self._hash_token(refresh_token)

        # Encrypt password using refresh token as key
        encrypted_password = self._simple_encrypt(password, refresh_token)

        # Get current timestamp
        created_at = datetime.utcnow()

        # Remove any existing tokens for this user (optional - can allow multiple devices)
        # For now, we'll allow multiple tokens per user (one per device/browser)
        
        # Insert new token
        data = {
            'token_hash': token_hash,
            'user_id': user_id,
            'username': username,
            'encrypted_password': encrypted_password,
            'created_at': created_at.isoformat(),
            'revoked_at': None
        }

        try:
            self.supabase.table(self.table_name).insert(data).execute()
            debug_log(f"Created refresh token for user_id={user_id}, username={username}", "bot_logic")
            return refresh_token
        except Exception as e:
            debug_log(f"FAILED: Error creating refresh token: {str(e)}", "bot_logic")
            raise

    def verify_access_token(self, token: str) -> Optional[Dict]:
        """
        Verify and decode a JWT access token

        Args:
            token: JWT access token string

        Returns:
            Decoded token payload if valid, None otherwise
        """
        try:
            payload = jwt.decode(token, self.jwt_secret, algorithms=[self.jwt_algorithm])
            
            # Verify token type
            if payload.get('type') != 'access':
                debug_log(f"FAILED: Token is not an access token", "bot_logic")
                return None
            
            debug_log(f"Access token verified for user_id={payload.get('user_id')}, username={payload.get('username')}", "bot_logic")
            return payload
        except jwt.ExpiredSignatureError:
            debug_log(f"FAILED: Access token expired", "bot_logic")
            return None
        except jwt.InvalidTokenError as e:
            debug_log(f"FAILED: Invalid access token: {str(e)}", "bot_logic")
            return None

    def verify_refresh_token(self, refresh_token: str) -> Optional[Tuple[int, str, str]]:
        """
        Verify a refresh token and return user info with decrypted password if valid

        Args:
            refresh_token: Refresh token string

        Returns:
            Tuple of (user_id, username, password) if valid, None otherwise
        """
        token_hash = self._hash_token(refresh_token)
        
        try:
            # Query for the token
            response = self.supabase.table(self.table_name).select('*').eq('token_hash', token_hash).execute()
            
            if not response.data or len(response.data) == 0:
                debug_log(f"FAILED: Refresh token not found in database", "bot_logic")
                return None
            
            record = response.data[0]
            
            # Check if token is revoked
            if record.get('revoked_at'):
                debug_log(f"FAILED: Refresh token has been revoked", "bot_logic")
                return None
            
            # Check if token has expired (based on created_at)
            created_at_str = record.get('created_at')
            if created_at_str:
                try:
                    created_at = datetime.fromisoformat(created_at_str.replace('Z', '+00:00'))
                    if datetime.utcnow() - created_at > self.refresh_token_expiry:
                        debug_log(f"FAILED: Refresh token has expired (created: {created_at_str})", "bot_logic")
                        # Mark as revoked for cleanup
                        self.revoke_refresh_token(refresh_token)
                        return None
                except Exception as e:
                    debug_log(f"WARNING: Could not parse created_at timestamp: {str(e)}", "bot_logic")
                    # Continue verification if date parsing fails
            
            user_id = record['user_id']
            username = record['username']
            encrypted_password = record.get('encrypted_password')
            
            if not encrypted_password:
                debug_log(f"FAILED: No encrypted password found for refresh token", "bot_logic")
                return None
            
            # Decrypt password using refresh token as key
            try:
                password = self._simple_decrypt(encrypted_password, refresh_token)
                debug_log(f"Refresh token verified for user_id={user_id}, username={username}", "bot_logic")
                return (user_id, username, password)
            except Exception as e:
                debug_log(f"FAILED: Error decrypting password: {str(e)}", "bot_logic")
                return None
            
        except Exception as e:
            debug_log(f"FAILED: Error verifying refresh token: {str(e)}", "bot_logic")
            return None

    def revoke_refresh_token(self, refresh_token: str) -> bool:
        """
        Revoke a refresh token by setting revoked_at timestamp

        Args:
            refresh_token: Refresh token string

        Returns:
            True if token was revoked, False otherwise
        """
        token_hash = self._hash_token(refresh_token)
        
        try:
            update_data = {
                'revoked_at': datetime.utcnow().isoformat()
            }
            self.supabase.table(self.table_name).update(update_data).eq('token_hash', token_hash).execute()
            debug_log(f"Refresh token revoked successfully", "bot_logic")
            return True
        except Exception as e:
            debug_log(f"FAILED: Error revoking refresh token: {str(e)}", "bot_logic")
            return False

    def revoke_all_user_tokens(self, user_id: int) -> int:
        """
        Revoke all refresh tokens for a user

        Args:
            user_id: User ID

        Returns:
            Number of tokens revoked
        """
        try:
            update_data = {
                'revoked_at': datetime.utcnow().isoformat()
            }
            response = self.supabase.table(self.table_name).update(update_data).eq('user_id', user_id).is_('revoked_at', 'null').execute()
            count = len(response.data) if response.data else 0
            debug_log(f"Revoked {count} refresh tokens for user_id={user_id}", "bot_logic")
            return count
        except Exception as e:
            debug_log(f"FAILED: Error revoking user tokens: {str(e)}", "bot_logic")
            return 0

