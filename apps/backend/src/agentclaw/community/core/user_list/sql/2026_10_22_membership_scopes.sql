-- OFFLINE migration template. Never run before the scope/index preflight in README.md.
-- Pause allowlist writes and keep new rule types disabled until every reader/writer is upgraded.
ALTER TABLE ac_entity_user_list
    ADD COLUMN subject_type VARCHAR(20) NOT NULL DEFAULT 'user',
    ADD COLUMN bot_type VARCHAR(20) NOT NULL DEFAULT 'all',
    DROP INDEX uk_entity_user_list_scope,
    DROP INDEX idx_entity_user_list_lookup,
    ADD UNIQUE KEY uk_entity_user_list_scope (entity_id, user_list_type, env, subject_type, bot_type),
    ADD KEY idx_entity_user_list_lookup (env, user_list_type, subject_type, bot_type, entity_id);
