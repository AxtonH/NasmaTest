import os
from pathlib import Path

from dotenv import load_dotenv


def _to_bool(value: str, default: bool = False) -> bool:
    """Convert common truthy/falsey strings to bool with a default fallback."""
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


# Resolve project root (two levels up: backend/config -> backend -> project root)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
# Allow overriding the env location via NASMA_ENV_PATH, default to project .env
_custom_env = os.environ.get("NASMA_ENV_PATH")
ENV_PATH = Path(_custom_env).expanduser() if _custom_env else PROJECT_ROOT / ".env"
load_dotenv(dotenv_path=ENV_PATH, override=True)


class Config:
    """Configuration class for the chatbot application"""
    
    # OpenAI Configuration
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
    GPT_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o")  # Primary GPT model
    GPT_FALLBACK_MODEL = os.environ.get("OPENAI_FALLBACK_MODEL", "gpt-4o")  # Fallback model if GPT-5 unavailable
    VERBOSE_LOGS = _to_bool(os.environ.get("VERBOSE_LOGS"), default=False)  # Reduce noisy debug logs when False
    DEBUG_ODOO_DATA = _to_bool(os.environ.get("DEBUG_ODOO_DATA"), default=False)  # Show Odoo data gathering details when True
    DEBUG_BOT_LOGIC = _to_bool(os.environ.get("DEBUG_BOT_LOGIC"), default=False)  # Show bot logic and actions when True
    DEBUG_KNOWLEDGE_BASE = _to_bool(os.environ.get("DEBUG_KNOWLEDGE_BASE"), default=False)  # Show knowledge base loading details when True
    # Chat history controls
    MAX_HISTORY_MESSAGES = int(os.environ.get("MAX_HISTORY_MESSAGES", "2"))  # include only the last N messages as discrete turns
    HISTORY_CONTEXT_LIMIT = int(os.environ.get("HISTORY_CONTEXT_LIMIT", "1200"))  # chars to keep when condensing older turns
    INCLUDE_CONDENSED_HISTORY = _to_bool(os.environ.get("INCLUDE_CONDENSED_HISTORY"), default=True)  # if True, send older history as one system block
    # Message retention (in days, None = keep forever)
    CHAT_MESSAGES_RETENTION_DAYS = int(os.environ.get("CHAT_MESSAGES_RETENTION_DAYS", "90")) if os.environ.get("CHAT_MESSAGES_RETENTION_DAYS") else None
    # Simple KB settings
    KB_ENABLED = True
    KB_DIR = os.path.join(os.path.dirname(__file__), '..', 'knowledge_base')
    KB_MAX_CHARS = 8000
    
    # Flask Configuration
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-key-change-in-production'
    DEBUG = _to_bool(os.environ.get('FLASK_DEBUG'), default=False)
    PERMANENT_SESSION_LIFETIME = 3600  # 1 hour in seconds
    # Cookie settings for cross-site usage
    SESSION_COOKIE_SAMESITE = 'None'
    SESSION_COOKIE_SECURE = True
    
    # Odoo Configuration
    ODOO_URL = os.environ.get("ODOO_URL", "https://prezlab-staging-23183574.dev.odoo.com")
    ODOO_DB = os.environ.get("ODOO_DB", "prezlab-staging-23183574")
    ODOO_USERNAME = os.environ.get("ODOO_USERNAME")
    ODOO_PASSWORD = os.environ.get("ODOO_PASSWORD")
    # User credentials will be provided during login if not supplied via environment

    # Supabase Configuration
    SUPABASE_URL = os.environ.get("SUPABASE_URL")
    SUPABASE_SERVICE_ROLE = os.environ.get("SUPABASE_SERVICE_ROLE")
    SUPABASE_METRIC_TABLE = os.environ.get("SUPABASE_METRIC_TABLE", "session_metrics")
    SUPABASE_THREAD_TABLE = os.environ.get("SUPABASE_THREAD_TABLE", "chat_threads")
    SUPABASE_MESSAGE_TABLE = os.environ.get("SUPABASE_MESSAGE_TABLE", "chat_messages")
    SUPABASE_REMEMBER_ME_TABLE = os.environ.get("SUPABASE_REMEMBER_ME_TABLE", "remember_me_tokens")
    SUPABASE_SESSION_TABLE = os.environ.get("SUPABASE_SESSION_TABLE", "chat_sessions")
    SUPABASE_ENABLED = bool(SUPABASE_URL and SUPABASE_SERVICE_ROLE)
    # Use Supabase for sessions if available (required for cloud deployments)
    # Falls back to filesystem storage if Supabase is disabled
    USE_SUPABASE_SESSIONS = _to_bool(os.environ.get("USE_SUPABASE_SESSIONS"), default=True)
