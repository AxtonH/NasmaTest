-- Adds storage for the Odoo trusted-device key (auth_totp 'td_id') to
-- refresh tokens, so auto-login can silently re-authenticate accounts that
-- have two-factor authentication enabled (no TOTP prompt on every session).
-- The key is encrypted with the refresh token, same scheme as
-- encrypted_password. Code tolerates this column being absent, but 2FA
-- users will be prompted for a code on every auto-login until it exists.

ALTER TABLE refresh_tokens
    ADD COLUMN IF NOT EXISTS encrypted_td TEXT;
