-- Repair the historical index that made one session accept only one resource.
-- Keep the same lookup columns as a non-unique index so new files in an
-- existing user/Bot/session tuple do not fail with MySQL 1062.
ALTER TABLE ac_session_resource
  DROP INDEX uk_idx_session_resource_owner_bot_session,
  ADD KEY idx_session_resource_owner_bot_session
    (owner_id, bot_id, session_key_hash);
