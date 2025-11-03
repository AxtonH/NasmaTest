-- Cleanup script for chat_messages table
-- This script deletes messages older than a specified retention period
-- Run this periodically (e.g., daily via cron or scheduled task)

-- Option 1: Delete messages older than 90 days (recommended)
DELETE FROM public.chat_messages
WHERE created_at < NOW() - INTERVAL '90 days';

-- Option 2: Delete messages older than 30 days (more aggressive)
-- DELETE FROM public.chat_messages
-- WHERE created_at < NOW() - INTERVAL '30 days';

-- Option 3: Archive old messages before deleting (if you want to keep a backup)
-- Create archive table first:
-- CREATE TABLE IF NOT EXISTS public.chat_messages_archive (LIKE public.chat_messages INCLUDING ALL);
-- 
-- INSERT INTO public.chat_messages_archive
-- SELECT * FROM public.chat_messages
-- WHERE created_at < NOW() - INTERVAL '90 days';
-- 
-- DELETE FROM public.chat_messages
-- WHERE created_at < NOW() - INTERVAL '90 days';

-- After cleanup, you can also cleanup orphaned threads (threads with no messages)
DELETE FROM public.chat_threads
WHERE thread_id NOT IN (
    SELECT DISTINCT thread_id FROM public.chat_messages
);

