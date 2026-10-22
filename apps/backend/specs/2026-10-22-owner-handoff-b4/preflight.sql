-- B4 read-only checks. Review target env/tenant before executing.
-- No UPDATE/DELETE/DDL. Nonempty results require classification, not auto-repair.
-- Run against the production schema; table/column names come from current ORM.

-- 1. Live service Bots must have immutable, nonempty and unique stable addresses.
SELECT id, avernet_tenant, env, bot_id, entity_id, owner_id
FROM ac_bots
WHERE avernet_tenant = 'teamclaw' AND env = 'prod' AND is_delete = 0
  AND bot_type = 'service' AND bot_id <> 'default'
  AND (entity_id IS NULL OR TRIM(entity_id) = '');

SELECT avernet_tenant, env, bot_id, entity_id, COUNT(*) AS matches
FROM ac_bots
WHERE avernet_tenant = 'teamclaw' AND env = 'prod' AND is_delete = 0
  AND bot_type = 'service' AND bot_id <> 'default'
GROUP BY avernet_tenant, env, bot_id, entity_id HAVING COUNT(*) > 1;

-- Rollback assumes handoff is still disabled and owner/entity have not diverged.
SELECT id, avernet_tenant, env, bot_id, entity_id, owner_id
FROM ac_bots
WHERE avernet_tenant = 'teamclaw' AND env = 'prod' AND is_delete = 0
  AND bot_type = 'service' AND bot_id <> 'default' AND owner_id <> entity_id;

-- 2. Bot-bound resource locator anomalies for live service Bots.
-- Existing offline/deleted-Bot resources are deliberately not classified as errors.
-- Local Skill user_id is scoped here only to local:// assets, never shared assets.
WITH resource_keys AS (
  SELECT 'ac_skill/local' AS resource_table, id AS resource_id,
         avernet_tenant, env, bolt_id AS bot_id, user_id AS locator
  FROM ac_skill WHERE git_path LIKE 'local://%'
  UNION ALL
  SELECT 'ac_expert_chat_bot_sessions', id, avernet_tenant, env, bot_id, owner_id
  FROM ac_expert_chat_bot_sessions
  UNION ALL
  SELECT 'ac_expert_chat_owned_sessions', id, avernet_tenant, env, bot_id, owner_id
  FROM ac_expert_chat_owned_sessions
  UNION ALL
  SELECT 'ac_expert_chat_instance', id, avernet_tenant, env, bot_id, owner_id
  FROM ac_expert_chat_instance
  UNION ALL
  SELECT 'ac_bot_quality_task', id, avernet_tenant, env, bot_id, owner_id
  FROM ac_bot_quality_task WHERE bot_id IS NOT NULL AND owner_id IS NOT NULL
  UNION ALL
  SELECT 'ac_bot_whitelist', id, avernet_tenant, env, bot_id, owner_id
  FROM ac_bot_whitelist
)
SELECT r.*
FROM resource_keys r
WHERE r.avernet_tenant = 'teamclaw' AND r.env = 'prod'
  AND EXISTS (
    SELECT 1 FROM ac_bots b
    WHERE b.avernet_tenant = r.avernet_tenant AND b.env = r.env
      AND b.bot_id = r.bot_id AND b.bot_type = 'service'
      AND b.bot_id <> 'default' AND b.is_delete = 0
  )
  AND NOT EXISTS (
    SELECT 1 FROM ac_bots b
    WHERE b.avernet_tenant = r.avernet_tenant AND b.env = r.env
      AND b.bot_id = r.bot_id AND b.entity_id = r.locator
      AND b.bot_type = 'service' AND b.bot_id <> 'default' AND b.is_delete = 0
  )
LIMIT 200;

-- 3. Collaborator owner snapshots may differ; the existing Bot PK is authoritative.
SELECT c.id, c.bot_pk, c.bot_id, c.owner_id, c.env
FROM ac_bot_collaborator c
LEFT JOIN ac_bots b ON b.id = c.bot_pk AND b.avernet_tenant = c.avernet_tenant
WHERE c.avernet_tenant = 'teamclaw' AND c.env = 'prod'
  AND (b.id IS NULL OR b.bot_id <> c.bot_id OR b.env <> c.env)
LIMIT 200;

-- ac_discovered_tasks has no env column in the current model. Do not invent one
-- or infer an environment for historical rows in an automatic migration.
-- Render Screen is intentionally not reinterpreted by B4; see spec.md.
