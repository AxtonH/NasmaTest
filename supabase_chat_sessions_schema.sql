-- Supabase Schema for Chat Sessions
-- Run this SQL in your Supabase SQL editor to create the table
-- This table stores multi-step conversation flows (time-off requests, log hours, etc.)

CREATE TABLE IF NOT EXISTS chat_sessions (
    id BIGSERIAL PRIMARY KEY,
    thread_id TEXT NOT NULL UNIQUE,
    session_data JSONB NOT NULL,
    session_type TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'started',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Create indexes for faster lookups
CREATE INDEX IF NOT EXISTS idx_chat_sessions_thread_id ON chat_sessions(thread_id);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_session_type ON chat_sessions(session_type);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_state ON chat_sessions(state);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_expires_at ON chat_sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_created_at ON chat_sessions(created_at);

-- Enable Row Level Security (RLS)
ALTER TABLE chat_sessions ENABLE ROW LEVEL SECURITY;

-- Create policy to allow service role full access
-- Note: Your backend uses SUPABASE_SERVICE_ROLE which bypasses RLS,
-- but this policy is good practice for explicit permissions
CREATE POLICY "Service role can manage all sessions"
ON chat_sessions
FOR ALL
TO service_role
USING (true)
WITH CHECK (true);

-- Optional: Add comment to table for documentation
COMMENT ON TABLE chat_sessions IS 'Stores multi-step conversation session data for time-off requests, log hours, and other flows';

-- Optional: Add trigger to auto-update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

CREATE TRIGGER update_chat_sessions_updated_at BEFORE UPDATE
    ON chat_sessions FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

