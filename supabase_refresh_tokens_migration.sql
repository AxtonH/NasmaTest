-- Migration: Add encrypted_password column to refresh_tokens table
-- Run this if you already created the refresh_tokens table without encrypted_password

ALTER TABLE refresh_tokens 
ADD COLUMN IF NOT EXISTS encrypted_password TEXT;

-- Update comment
COMMENT ON COLUMN refresh_tokens.encrypted_password IS 'Encrypted password (XOR encrypted with refresh token as key)';

-- Note: Existing tokens will have NULL encrypted_password, which means they won't work for auto-login
-- Users will need to log in again to create new tokens with encrypted passwords

