-- B0 owner-handoff read-only preflight for OceanBase/MySQL.
-- Every statement is SELECT-only. Run against each online environment/schema.
-- For sections A-G, an empty result is PASS unless stated otherwise.

-- 0. Tenant distribution. Expected result: only teamclaw.
SELECT avernet_tenant, COUNT(*) AS bot_count
FROM ac_bots
GROUP BY avernet_tenant
ORDER BY bot_count DESC;

-- A1. Live service Bots must have a non-empty stable entity_id.
SELECT id, avernet_tenant, env, bot_id, entity_id, owner_id
FROM ac_bots
WHERE is_delete = 0
  AND bot_type = 'service'
  AND (entity_id IS NULL OR TRIM(entity_id) = '')
LIMIT 200;

-- A2. tenant + env + bot_id + entity_id must uniquely identify a live service Bot.
SELECT avernet_tenant, env, bot_id, entity_id, COUNT(*) AS row_count,
       GROUP_CONCAT(id ORDER BY id) AS bot_pks
FROM ac_bots
WHERE is_delete = 0
  AND bot_type = 'service'
GROUP BY avernet_tenant, env, bot_id, entity_id
HAVING COUNT(*) > 1
LIMIT 200;

-- B. Current pre-transfer production assumption. Expected result: empty.
-- Re-run before enabling transfer; after a real transfer this condition is expected.
SELECT id, avernet_tenant, env, bot_id, entity_id, owner_id
FROM ac_bots
WHERE is_delete = 0
  AND bot_type = 'service'
  AND NOT (owner_id <=> entity_id)
LIMIT 200;

-- C. A legacy locator must not match two different live service Bots.
-- Covers entity/entity, entity/owner, owner/entity and owner/owner collisions.
SELECT a.avernet_tenant, a.env, a.bot_id,
       a.id AS bot_pk_1, a.entity_id AS entity_id_1, a.owner_id AS owner_id_1,
       b.id AS bot_pk_2, b.entity_id AS entity_id_2, b.owner_id AS owner_id_2
FROM ac_bots a
JOIN ac_bots b
  ON b.avernet_tenant = a.avernet_tenant
 AND b.env = a.env
 AND b.bot_id = a.bot_id
 AND b.id > a.id
 AND b.is_delete = 0
 AND b.bot_type = 'service'
 AND (
      b.entity_id = a.entity_id
   OR b.entity_id = a.owner_id
   OR b.owner_id = a.entity_id
   OR b.owner_id = a.owner_id
 )
WHERE a.is_delete = 0
  AND a.bot_type = 'service'
LIMIT 200;

-- D1. Inventory all orphan publish rows. Historical default/personal lineages
-- are review items, not owner-handoff blockers by themselves.
SELECT p.id AS publish_id, p.source_bot_pk, p.source_bot_id,
       p.publish_bot_id, p.owner_id, p.env
FROM ac_bot_publish p
LEFT JOIN ac_bots b ON b.id = p.source_bot_pk
WHERE p.source_bot_pk IS NULL
   OR b.id IS NULL
LIMIT 200;

-- D1b. Classify orphan publish rows against a current Bot with the same
-- env + source_bot_id + stable entity. A candidate default/personal Bot is a
-- legacy compatibility case; a non-default service candidate is a blocker.
SELECT p.id AS publish_id, p.source_bot_pk AS missing_source_bot_pk,
       p.source_bot_id, p.owner_id AS resource_entity_id, p.env,
       c.id AS current_bot_pk, c.bot_type, c.is_delete,
       c.entity_id, c.owner_id AS current_owner_id
FROM ac_bot_publish p
LEFT JOIN ac_bots old_bot ON old_bot.id = p.source_bot_pk
LEFT JOIN ac_bots c
  ON c.avernet_tenant = 'teamclaw'
 AND c.env = p.env
 AND c.bot_id = p.source_bot_id
 AND c.entity_id = p.owner_id
 AND c.is_delete = 0
WHERE old_bot.id IS NULL
ORDER BY p.source_bot_pk, p.version
LIMIT 200;

-- D1c. Actual owner-handoff blockers: an orphan/mismatched lineage that maps
-- to a current transferable service Bot. Expected result: empty.
SELECT p.id AS publish_id, p.source_bot_pk AS recorded_source_bot_pk,
       c.id AS current_bot_pk, p.source_bot_id, p.owner_id, p.env,
       p.status, p.version
FROM ac_bot_publish p
JOIN ac_bots c
  ON c.avernet_tenant = 'teamclaw'
 AND c.env = p.env
 AND c.bot_id = p.source_bot_id
 AND c.entity_id = p.owner_id
 AND c.is_delete = 0
 AND c.bot_type = 'service'
 AND c.bot_id <> 'default'
WHERE p.source_bot_pk <> c.id
LIMIT 200;

-- D2. Inventory historical publish/Bot mismatches. Deleted Bots and old rows
-- may legitimately show the former owner snapshot semantics; do not backfill
-- these rows automatically.
SELECT p.id AS publish_id, p.source_bot_pk,
       p.source_bot_id, b.bot_id AS actual_bot_id,
       p.owner_id AS publish_resource_owner_id, b.entity_id,
       p.env AS publish_env, b.env AS bot_env,
       b.is_delete AS bot_is_delete
FROM ac_bot_publish p
JOIN ac_bots b ON b.id = p.source_bot_pk
WHERE p.source_bot_id <> b.bot_id
   OR p.env <> b.env
   OR p.owner_id <> b.entity_id
LIMIT 200;

-- D2b. Current owner-handoff blocker check. New/live transferable service-Bot
-- publish rows must agree with stable source identity. Expected result: empty.
SELECT p.id AS publish_id, p.source_bot_pk,
       p.source_bot_id, b.bot_id AS actual_bot_id,
       p.owner_id AS publish_resource_owner_id, b.entity_id,
       p.env AS publish_env, b.env AS bot_env
FROM ac_bot_publish p
JOIN ac_bots b ON b.id = p.source_bot_pk
WHERE b.is_delete = 0
  AND b.bot_type = 'service'
  AND b.bot_id <> 'default'
  AND (
       p.source_bot_id <> b.bot_id
    OR p.env <> b.env
    OR p.owner_id <> b.entity_id
  )
LIMIT 200;

-- D3. One source Bot cannot have duplicate versions in one environment.
SELECT source_bot_pk, env, version, COUNT(*) AS row_count,
       GROUP_CONCAT(id ORDER BY id) AS publish_ids
FROM ac_bot_publish
WHERE version IS NOT NULL
GROUP BY source_bot_pk, env, version
HAVING COUNT(*) > 1
LIMIT 200;

-- E1-E3. Skill/MCP desired-state owner_id physically stores entity_id.
SELECT r.id, r.avernet_tenant, r.env, r.bot_id, r.owner_id
FROM ac_bot_skill_installation r
LEFT JOIN ac_bots b
  ON b.avernet_tenant = r.avernet_tenant
 AND b.env = r.env
 AND b.bot_id = r.bot_id
 AND b.entity_id = r.owner_id
 AND b.is_delete = 0
WHERE b.id IS NULL
LIMIT 200;

SELECT r.id, r.avernet_tenant, r.env, r.bot_id, r.owner_id
FROM ac_bot_mcp_installation r
LEFT JOIN ac_bots b
  ON b.avernet_tenant = r.avernet_tenant
 AND b.env = r.env
 AND b.bot_id = r.bot_id
 AND b.entity_id = r.owner_id
 AND b.is_delete = 0
WHERE b.id IS NULL
LIMIT 200;

SELECT r.id, r.avernet_tenant, r.env, r.bot_id, r.owner_id
FROM ac_bot_mcp_config r
LEFT JOIN ac_bots b
  ON b.avernet_tenant = r.avernet_tenant
 AND b.env = r.env
 AND b.bot_id = r.bot_id
 AND b.entity_id = r.owner_id
 AND b.is_delete = 0
WHERE b.id IS NULL
LIMIT 200;

-- F1-F2. Bot-scoped Skill Set and Local Skill use user_id + bolt_id.
-- Shared rows with NULL user_id or NULL bolt_id are intentionally excluded.
SELECT r.id, r.avernet_tenant, r.env, r.bolt_id AS bot_id, r.user_id
FROM ac_skill_set r
LEFT JOIN ac_bots b
  ON b.avernet_tenant = r.avernet_tenant
 AND b.env = r.env
 AND b.bot_id = r.bolt_id
 AND b.entity_id = r.user_id
 AND b.is_delete = 0
WHERE r.user_id IS NOT NULL
  AND r.bolt_id IS NOT NULL
  AND b.id IS NULL
LIMIT 200;

SELECT r.id, r.avernet_tenant, r.env, r.bolt_id AS bot_id, r.user_id
FROM ac_skill r
LEFT JOIN ac_bots b
  ON b.avernet_tenant = r.avernet_tenant
 AND b.env = r.env
 AND b.bot_id = r.bolt_id
 AND b.entity_id = r.user_id
 AND b.is_delete = 0
WHERE r.user_id IS NOT NULL
  AND r.bolt_id IS NOT NULL
  AND b.id IS NULL
LIMIT 200;

-- G1-G2. Default exclusions have no env column. They must match at least one
-- live Bot in the same tenant by bot_id + entity_id.
SELECT r.id, r.avernet_tenant, r.bot_id, r.user_id
FROM ac_default_skillset_skill_exclusion r
WHERE NOT EXISTS (
    SELECT 1
    FROM ac_bots b
    WHERE b.avernet_tenant = r.avernet_tenant
      AND b.bot_id = r.bot_id
      AND b.entity_id = r.user_id
      AND b.is_delete = 0
)
LIMIT 200;

SELECT r.id, r.avernet_tenant, r.bot_id, r.user_id
FROM ac_default_skillset_mcp_exclusion r
WHERE NOT EXISTS (
    SELECT 1
    FROM ac_bots b
    WHERE b.avernet_tenant = r.avernet_tenant
      AND b.bot_id = r.bot_id
      AND b.entity_id = r.user_id
      AND b.is_delete = 0
)
LIMIT 200;
