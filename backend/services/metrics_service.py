import json
import threading
from datetime import datetime
from typing import Any, Dict, Optional

try:
    from supabase import Client, create_client
    try:
        from gotrue.http_clients import SyncClient as _GoTrueSyncClient

        if not getattr(_GoTrueSyncClient, "_nasmapl_proxy_patch", False):
            _original_init = _GoTrueSyncClient.__init__

            def _patched_init(self, *args, **kwargs):
                proxy = kwargs.pop("proxy", None)
                if proxy and "proxies" not in kwargs:
                    kwargs["proxies"] = {
                        "http://": proxy,
                        "https://": proxy,
                    }
                return _original_init(self, *args, **kwargs)

            _GoTrueSyncClient.__init__ = _patched_init  # type: ignore[assignment]
            setattr(_GoTrueSyncClient, "_nasmapl_proxy_patch", True)
    except Exception:
        pass
except Exception:  # pragma: no cover - dependency may be missing in some environments
    Client = None  # type: ignore
    create_client = None  # type: ignore

try:
    from ..config.settings import Config
except Exception:  # pragma: no cover - allow relative import when running locally
    from config.settings import Config  # type: ignore


def _normalize_payload(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Ensure payload can be JSON-serialized by Supabase."""
    if not payload:
        return {}

    def _coerce(value: Any) -> Any:
        if isinstance(value, dict):
            return {str(k): _coerce(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [_coerce(v) for v in value]
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        # Fallback to string representation for unsupported types (e.g., datetime)
        return str(value)

    coerced = _coerce(payload)
    try:
        json.dumps(coerced, ensure_ascii=False)
        return coerced  # type: ignore[return-value]
    except TypeError:
        # Ultimate fallback: stringify entire payload
        return {"raw": str(payload)}


class MetricsService:
    """Lazy-initialized Supabase client for logging conversational metrics."""

    def __init__(self) -> None:
        self._client: Optional[Client] = None
        self._lock = threading.Lock()
        self._enabled = bool(getattr(Config, "SUPABASE_ENABLED", False) and create_client)
        self._last_error: Optional[str] = None
        if not self._enabled:
            reason = "missing credentials or supabase package"
            if getattr(Config, "SUPABASE_ENABLED", False) and not create_client:
                reason = "supabase client import failed"
            print(f"[MetricsService] Disabled: {reason}")

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    def _get_client(self) -> Optional[Client]:
        if not self._enabled:
            return None
        if self._client is not None:
            return self._client
        with self._lock:
            if self._client is None:
                try:
                    self._client = create_client(Config.SUPABASE_URL, Config.SUPABASE_SERVICE_ROLE)  # type: ignore[arg-type]
                except Exception as exc:  # pragma: no cover
                    self._enabled = False
                    self._last_error = str(exc)
                    return None
        return self._client

    def has_metric_for_thread(self, thread_id: str, metric_type: Optional[str] = None) -> bool:
        """Check if a metric already exists for this thread. Returns True if any metric exists (or specific type if provided)."""
        client = self._get_client()
        if not client or not thread_id:
            return False

        table_name = getattr(Config, "SUPABASE_METRIC_TABLE", "session_metrics")

        try:
            query = client.table(table_name).select("id").eq("thread_id", thread_id)
            if metric_type:
                query = query.eq("metric_type", metric_type)
            result = query.limit(1).execute()
            return len(result.data) > 0
        except Exception as exc:
            print(f"[MetricsService] has_metric_for_thread failed: {exc}")
            return False

    def log_metric(
        self,
        metric_type: str,
        thread_id: Optional[str],
        *,
        user_id: Optional[str] = None,
        user_name: Optional[str] = None,
        tenant_id: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        skip_if_exists: bool = False,
    ) -> bool:
        """Insert a row into the configured Supabase metrics table. Never raises."""
        client = self._get_client()
        if not client:
            if self._last_error:
                print(f"[MetricsService] log_metric skipped: {self._last_error}")
            return False

        # Skip if a metric already exists for this thread (prevents duplicate logging)
        if skip_if_exists and thread_id and self.has_metric_for_thread(thread_id):
            print(f"[MetricsService] Skipping {metric_type} metric - thread {thread_id} already has a metric")
            return False

        table_name = getattr(Config, "SUPABASE_METRIC_TABLE", "session_metrics")

        data = {
            "thread_id": thread_id,
            "metric_type": metric_type,
            "user_id": user_id,
            "user_name": user_name,
            "tenant_id": tenant_id,
            "payload": _normalize_payload(payload),
        }

        try:
            client.table(table_name).insert(data).execute()
            self._last_error = None
            return True
        except Exception as exc:  # pragma: no cover - network errors should not break flows
            self._last_error = str(exc)
            print(f"[MetricsService] log_metric failed: {exc}")
            return False

    def upsert_thread(
        self,
        thread_id: str,
        *,
        user_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        title: Optional[str] = None,
        last_message_preview: Optional[str] = None,
        last_sender: Optional[str] = None,
        last_message_at: Optional[str] = None,
    ) -> None:
        """Persist thread metadata for analytics dashboards."""
        client = self._get_client()
        if not client or not thread_id:
            return

        table_name = getattr(Config, "SUPABASE_THREAD_TABLE", "chat_threads")
        payload = {
            "thread_id": thread_id,
            "user_id": user_id,
            "tenant_id": tenant_id,
            "title": title,
            "last_message_preview": last_message_preview,
            "last_sender": last_sender,
        }
        payload = {k: v for k, v in payload.items() if v is not None}
        payload["thread_id"] = thread_id
        if last_message_at is None:
            last_message_at = datetime.utcnow().isoformat() + "Z"
        payload["last_message_at"] = last_message_at

        try:
            client.table(table_name).upsert(payload, on_conflict="thread_id").execute()
            self._last_error = None
        except Exception as exc:  # pragma: no cover
            self._last_error = str(exc)
            print(f"[MetricsService] upsert_thread failed: {exc}")

    def store_message(
        self,
        thread_id: str,
        *,
        role: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Persist chat messages for historical analytics."""
        client = self._get_client()
        if not client or not thread_id:
            if not client and self._last_error:
                print(f"[MetricsService] store_message skipped: {self._last_error}")
            return False

        table_name = getattr(Config, "SUPABASE_MESSAGE_TABLE", "chat_messages")
        payload = {
            "thread_id": thread_id,
            "role": role,
            "content": content,
            "metadata": _normalize_payload(metadata or {}),
        }

        try:
            result = client.table(table_name).insert(payload).execute()
            self._last_error = None
            print(f"[MetricsService] store_message SUCCESS: role={role}, thread_id={thread_id}, content_length={len(content)}")
            return True
        except Exception as exc:  # pragma: no cover
            self._last_error = str(exc)
            print(f"[MetricsService] store_message FAILED: role={role}, thread_id={thread_id}, error={exc}")
            import traceback
            traceback.print_exc()
            return False

    def fetch_threads(
        self,
        *,
        user_id: Optional[str],
        tenant_id: Optional[str] = None,
        limit: int = 50
    ) -> list:
        client = self._get_client()
        if not client or not user_id:
            return []
        try:
            table_name = getattr(Config, "SUPABASE_THREAD_TABLE", "chat_threads")
            query = client.table(table_name).select('*')
            query = query.eq('user_id', user_id)
            if tenant_id:
                query = query.eq('tenant_id', tenant_id)
            query = query.order('last_message_at', desc=True).limit(limit)
            response = query.execute()
            return list(response.data) if getattr(response, 'data', None) else []
        except Exception as exc:  # pragma: no cover
            self._last_error = str(exc)
            print(f"[MetricsService] fetch_threads failed: {exc}")
            return []

    def fetch_thread(self, thread_id: str) -> Optional[Dict[str, Any]]:
        client = self._get_client()
        if not client or not thread_id:
            return None
        try:
            table_name = getattr(Config, "SUPABASE_THREAD_TABLE", "chat_threads")
            response = client.table(table_name).select('*').eq('thread_id', thread_id).limit(1).execute()
            data = getattr(response, 'data', None) or []
            return data[0] if data else None
        except Exception as exc:  # pragma: no cover
            self._last_error = str(exc)
            print(f"[MetricsService] fetch_thread failed: {exc}")
            return None

    def fetch_messages(self, thread_id: str, limit: int = 200) -> list:
        client = self._get_client()
        if not client or not thread_id:
            return []
        try:
            table_name = getattr(Config, "SUPABASE_MESSAGE_TABLE", "chat_messages")
            query = client.table(table_name).select('*').eq('thread_id', thread_id).order('created_at', desc=False)
            if limit:
                query = query.limit(limit)
            response = query.execute()
            return list(response.data) if getattr(response, 'data', None) else []
        except Exception as exc:  # pragma: no cover
            self._last_error = str(exc)
            print(f"[MetricsService] fetch_messages failed: {exc}")
            return []
