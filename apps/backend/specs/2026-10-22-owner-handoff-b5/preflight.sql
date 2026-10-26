-- Read-only. Run in the intended database/environment; no automatic repair.
-- 1. Stable resource identity: expect no normal live service-Bot exceptions.
SELECT id, bot_id, owner_id, entity_id, env, avernet_tenant
FROM ac_bots
WHERE is_delete = 0 AND bot_type = 'service' AND bot_id <> 'default'
  AND (entity_id IS NULL OR entity_id = '' OR owner_id <> entity_id)
LIMIT 200;

-- 2. Confirm the existing independent-execution table is present before OCB DI rollout.
SHOW COLUMNS FROM ac_bot_execution_identity_binding;

-- Existing configuration only; empty result means no binding records. Do not
-- infer configuration absence from owner == entity or from digital UI status.
SELECT avernet_tenant, env, status, identity_type, COUNT(*) AS binding_count
FROM ac_bot_execution_identity_binding
GROUP BY avernet_tenant, env, status, identity_type;

-- 3. Grant keys must match a stable live Bot address. Detached/deleted/creation
-- records are possible; classify returned records rather than delete them.
SELECT g.id, g.bot_id, g.owner_id AS resource_entity_locator,
       g.user_id AS delegator, g.env, g.avernet_tenant
FROM ac_bot_app_grant g
LEFT JOIN ac_bots b ON b.bot_id = g.bot_id AND b.entity_id = g.owner_id
  AND b.env = g.env AND b.avernet_tenant = g.avernet_tenant AND b.is_delete = 0
WHERE b.id IS NULL
LIMIT 200;

-- 4. Existing grant table has a user/app/bot unique slot; verify its actual
-- indexes before reasoning about same-id personal Bots or concurrency.
SHOW INDEX FROM ac_bot_app_grant;
