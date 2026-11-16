-- 047_message_activity_indexes: cover the read-time conversation-activity
-- aggregates behind `sort=last_activity` (design D2 evidence gate). The
-- existing (`group_id`, `created_at`) / (`session_id`, `created_at`) keys do
-- not contain `env`, so the batched MAX aggregates fetched table rows and, for
-- the group shape, the SQLite planner fell back to an env-wide scan. `env`
-- leads and `created_at` closes each key so the per-candidate range is
-- evaluated inside the index and MAX() needs no table fetch. Purely additive.
ALTER TABLE `bcs_messages`
  ADD INDEX `idx_messages_env_group_created` (`env`, `group_id`, `created_at`);

ALTER TABLE `bcs_messages`
  ADD INDEX `idx_messages_env_session_created` (`env`, `session_id`, `created_at`);
