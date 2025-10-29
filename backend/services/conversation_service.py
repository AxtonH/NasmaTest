"""
Conversation History Service
Manages conversation retrieval and management for chat history feature.
"""
from typing import Dict, List, Optional, Any
from datetime import datetime

try:
    from ..config.settings import Config
    from .metrics_service import MetricsService
except Exception:
    from config.settings import Config  # type: ignore
    from metrics_service import MetricsService  # type: ignore


class ConversationService:
    """Service for managing conversation history operations."""

    def __init__(self, metrics_service: Optional[MetricsService] = None):
        """Initialize with an optional MetricsService instance."""
        self.metrics_service = metrics_service or MetricsService()

    def get_user_conversations(
        self,
        user_id: str,
        tenant_id: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Retrieve all conversations for a specific user.

        Args:
            user_id: The user's ID to filter conversations
            tenant_id: Optional tenant ID for multi-tenant isolation
            limit: Maximum number of conversations to return

        Returns:
            List of conversation objects with metadata
        """
        if not user_id:
            return []

        threads = self.metrics_service.fetch_threads(
            user_id=user_id,
            tenant_id=tenant_id,
            limit=limit
        )

        return threads

    def get_conversation_messages(
        self,
        thread_id: str,
        user_id: str,
        limit: int = 200
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieve all messages for a specific conversation.

        Args:
            thread_id: The conversation thread ID
            user_id: The requesting user's ID (for security verification)
            limit: Maximum number of messages to return

        Returns:
            Dictionary with thread metadata and messages, or None if not found/unauthorized
        """
        if not thread_id or not user_id:
            return None

        # Verify the thread belongs to this user
        thread = self.metrics_service.fetch_thread(thread_id)
        if not thread or thread.get('user_id') != user_id:
            return None

        # Fetch messages for the thread
        messages = self.metrics_service.fetch_messages(thread_id, limit=limit)

        return {
            'thread': thread,
            'messages': messages
        }

    def create_conversation(
        self,
        thread_id: str,
        user_id: str,
        tenant_id: Optional[str] = None,
        title: Optional[str] = None
    ) -> bool:
        """
        Create a new conversation thread.

        Args:
            thread_id: Unique identifier for the conversation
            user_id: The user's ID who owns the conversation
            tenant_id: Optional tenant ID for multi-tenant systems
            title: Optional title for the conversation

        Returns:
            True if successful, False otherwise
        """
        if not thread_id or not user_id:
            return False

        self.metrics_service.upsert_thread(
            thread_id=thread_id,
            user_id=user_id,
            tenant_id=tenant_id,
            title=title or "New Conversation",
            last_message_at=datetime.utcnow().isoformat() + "Z"
        )

        return True

    def update_conversation_metadata(
        self,
        thread_id: str,
        user_id: str,
        title: Optional[str] = None,
        last_message_preview: Optional[str] = None,
        last_sender: Optional[str] = None
    ) -> bool:
        """
        Update conversation metadata.

        Args:
            thread_id: The conversation thread ID
            user_id: The user's ID (for verification)
            title: Optional new title
            last_message_preview: Optional preview of the last message
            last_sender: Optional sender of the last message

        Returns:
            True if successful, False otherwise
        """
        if not thread_id or not user_id:
            return False

        # Verify ownership
        thread = self.metrics_service.fetch_thread(thread_id)
        if not thread or thread.get('user_id') != user_id:
            return False

        self.metrics_service.upsert_thread(
            thread_id=thread_id,
            user_id=user_id,
            tenant_id=thread.get('tenant_id'),
            title=title,
            last_message_preview=last_message_preview,
            last_sender=last_sender,
            last_message_at=datetime.utcnow().isoformat() + "Z"
        )

        return True


# Global singleton instance
_conversation_service_instance: Optional[ConversationService] = None


def get_conversation_service() -> ConversationService:
    """Get or create the global ConversationService instance."""
    global _conversation_service_instance
    if _conversation_service_instance is None:
        _conversation_service_instance = ConversationService()
    return _conversation_service_instance
