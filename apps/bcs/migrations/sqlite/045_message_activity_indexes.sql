-- 045_message_activity_indexes: cover the read-time conversation-activity
-- aggregates behind `sort=last_activity` (design D2 evidence gate). The
-- existing (group_id, created_at) / (session_id, created_at) keys do not
-- contain env, so the batched MAX aggregates fetched table rows and the
-- planner fell back to an env-wide scan for wide IN lists. env leads and
-- created_at closes each key so the per-candidate range is evaluated inside
-- the index and MAX() needs no table fetch. Purely additive.
CREATE INDEX IF NOT EXISTS idx_messages_env_group_created
    ON bcs_messages(env, group_id, created_at);

CREATE INDEX IF NOT EXISTS idx_messages_env_session_created
    ON bcs_messages(env, session_id, created_at);
