-- Supabase Schema for Remember Me Tokens
-- Run this SQL in your Supabase SQL editor to create the table

CREATE TABLE IF NOT EXISTS remember_me_tokens (
    id BIGSERIAL PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    username TEXT NOT NULL,
    device_fingerprint TEXT NOT NULL,
    encrypted_password TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at TIMESTAMPTZ,
    CONSTRAINT unique_user_device UNIQUE (username, device_fingerprint)
);

-- Create indexes for faster lookups
CREATE INDEX IF NOT EXISTS idx_remember_me_token_hash ON remember_me_tokens(token_hash);
CREATE INDEX IF NOT EXISTS idx_remember_me_device_fingerprint ON remember_me_tokens(device_fingerprint);
CREATE INDEX IF NOT EXISTS idx_remember_me_username ON remember_me_tokens(username);
CREATE INDEX IF NOT EXISTS idx_remember_me_last_used_at ON remember_me_tokens(last_used_at);

-- Enable Row Level Security (RLS)
ALTER TABLE remember_me_tokens ENABLE ROW LEVEL SECURITY;

-- Create policy to allow service role full access
-- Note: Your backend uses SUPABASE_SERVICE_ROLE which bypasses RLS,
-- but this policy is good practice for explicit permissions
CREATE POLICY "Service role can manage all tokens"
ON remember_me_tokens
FOR ALL
TO service_role
USING (true)
WITH CHECK (true);

-- Optional: Add comment to table for documentation
COMMENT ON TABLE remember_me_tokens IS 'Stores encrypted remember-me tokens for persistent user authentication across sessions';
