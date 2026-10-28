-- Operator-applied migration; never run at application startup.
-- Preflight: SHOW INDEX FROM ac_bots. Skip if an available, full-column
-- equivalent index already exists with these five leading columns in order.
-- If this name exists with a different definition, STOP for operator review.
-- Keep existing primary/unique keys and idx_space_id; do not replace them.
CREATE INDEX idx_bot_knowledge_candidates
    ON ac_bots (avernet_tenant, env, space_id, is_delete, id);
