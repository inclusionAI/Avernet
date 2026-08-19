-- Preserve the historic actor audit value while moving favorites to the
-- tenant/env/user/object identity required by the public contract.
ALTER TABLE ac_market_favorite
  ADD COLUMN user_id VARCHAR(256) NULL AFTER space_id;

UPDATE ac_market_favorite
SET user_id = created_by
WHERE user_id IS NULL;

-- The new user-level identity collapses historical duplicates from different
-- Spaces. Keep the earliest auditable favorite deterministically.
DELETE FROM ac_market_favorite
WHERE id IN (
  SELECT id FROM (
    SELECT id,
      ROW_NUMBER() OVER (
        PARTITION BY avernet_tenant, env, user_id, target_type, target_code
        ORDER BY gmt_created ASC, id ASC
      ) AS duplicate_rank
    FROM ac_market_favorite
  ) ranked_favorites
  WHERE duplicate_rank > 1
) duplicate_ids;

ALTER TABLE ac_market_favorite
  MODIFY COLUMN user_id VARCHAR(256) NOT NULL,
  DROP INDEX uk_market_favorite_target_env,
  ADD UNIQUE KEY uk_market_favorite_user_target_env
    (avernet_tenant, env, user_id, target_type, target_code),
  DROP INDEX idx_market_favorite_space,
  ADD KEY idx_market_favorite_user_space
    (avernet_tenant, env, user_id, space_id, gmt_modified);
