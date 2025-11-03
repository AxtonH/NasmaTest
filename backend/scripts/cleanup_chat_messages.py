"""
Cleanup script for chat_messages table.
Run this periodically (e.g., daily via cron or scheduled task) to delete old messages.

Usage:
    python backend/scripts/cleanup_chat_messages.py

Or set up as a scheduled task:
    - Railway: Use a cron job
    - Heroku: Use Heroku Scheduler addon
    - Local: Use cron or Task Scheduler
"""

import os
import sys
from datetime import datetime, timedelta

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from supabase import create_client
    from config.settings import Config
except ImportError:
    print("Error: Could not import required modules. Make sure you're running from the project root.")
    sys.exit(1)


def cleanup_old_messages(retention_days: int = 90):
    """Delete chat messages older than retention_days."""
    if not Config.SUPABASE_ENABLED:
        print("Supabase is not enabled. Skipping cleanup.")
        return
    
    try:
        client = create_client(Config.SUPABASE_URL, Config.SUPABASE_SERVICE_ROLE)
        
        cutoff_date = datetime.now() - timedelta(days=retention_days)
        cutoff_iso = cutoff_date.isoformat() + "Z"
        
        table_name = getattr(Config, "SUPABASE_MESSAGE_TABLE", "chat_messages")
        
        # Delete old messages
        result = client.table(table_name).delete().lt("created_at", cutoff_iso).execute()
        
        deleted_count = len(result.data) if result.data else 0
        print(f"✅ Cleaned up {deleted_count} messages older than {retention_days} days (before {cutoff_date.strftime('%Y-%m-%d')})")
        
        # Cleanup orphaned threads (threads with no messages)
        try:
            # Get all thread_ids that still have messages
            messages_result = client.table(table_name).select("thread_id").execute()
            active_thread_ids = set(msg.get("thread_id") for msg in (messages_result.data or []))
            
            # Get all threads
            threads_result = client.table("chat_threads").select("thread_id").execute()
            all_thread_ids = set(thread.get("thread_id") for thread in (threads_result.data or []))
            
            orphaned_thread_ids = all_thread_ids - active_thread_ids
            
            if orphaned_thread_ids:
                # Delete orphaned threads
                for thread_id in orphaned_thread_ids:
                    client.table("chat_threads").delete().eq("thread_id", thread_id).execute()
                print(f"✅ Cleaned up {len(orphaned_thread_ids)} orphaned threads")
        
        except Exception as e:
            print(f"⚠️  Warning: Could not cleanup orphaned threads: {e}")
        
    except Exception as e:
        print(f"❌ Error during cleanup: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    retention_days = Config.CHAT_MESSAGES_RETENTION_DAYS or 90
    print(f"Starting cleanup with {retention_days} day retention period...")
    cleanup_old_messages(retention_days)
    print("Cleanup complete.")

