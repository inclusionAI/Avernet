-- full_etl_friend_migration.sql
-- Phase 2 全量 ETL: 把 System A (ac_bot_friend, 人→Bot) + System B (bcs_friendships/bcs_friend_requests, Bot↔Bot)
-- 历史好友数据迁移到 08-12 边权限表 (edge_grants/permission_profiles/permission_requests).
--
-- 运行时: MySQL production. 所有 INSERT 用 INSERT IGNORE (幂等, 重复跑不报错).
-- 依赖: Phase 1 Build 已部署 (五表 DDL 009_edge_permission.sql 已 apply).
-- 顺序: 脚本 0 → 1 → 2 → 3 → 4 → 5 → reconciliation.
-- 来源: spec §8.4 + edge-permission-friend-migration-plan.md §3.

-- 用前备份: 对 ac_bot_friend, bcs_friendships, bcs_friend_requests, bcs_actor_relations 做快照.

USE <your_database>;

-- =========================================================================
-- 脚本 0 — default profile 批量 seed (每 bot 一条 wildcard-allow)
-- =========================================================================
INSERT IGNORE INTO permission_profiles
  (permission_profile_id, bot_id, env, name, description, rules_template,
   revision, digest, is_default, status, created_by)
SELECT
  CONCAT('pp_', b.bot_uuid, '_default'),
  b.bot_uuid, b.env, 'default', NULL,
  '[{"tool":"*","specifier":"*","effect":"allow"}]',
  1,
  SHA2('[{"tool":"*","specifier":"*","effect":"allow"}]', 256),
  TRUE, 'active', 'system'
FROM bcs_bots b;


-- =========================================================================
-- 脚本 1 — bot_uuid 映射视图 (D11: 复合 id = CONCAT(bot_id, ':', owner_id))
-- =========================================================================
CREATE OR REPLACE VIEW v_ac_bot_map AS
SELECT
  CONCAT(bot_id, ':', owner_id) AS bot_uuid,
  bot_id, owner_id, public,
  JSON_EXTRACT(ext, '$.friend_approval') AS friend_approval
FROM ac_bots
WHERE is_delete = 0;


-- =========================================================================
-- 脚本 2 — System A: ac_bot_friend ACCEPTED → 人→Bot 边 (1 条) + approved request
-- =========================================================================

-- 2a. edge_grants（人→Bot 单向, 不建反向边）
INSERT IGNORE INTO edge_grants
  (edge_id, env, from_id, to_id, grant_kind, grant_ref_id, rules,
   status, originator_policy_type, originator_policy_data)
SELECT
  CONCAT('eg_', MD5(CONCAT(
    'human_', f.requester_entity_id, '|',
    m.bot_uuid, '|', f.env, '|pp_', m.bot_uuid, '_default'
  ))),
  f.env,
  CONCAT('human_', f.requester_entity_id),
  m.bot_uuid,
  'permission_profile',
  CONCAT('pp_', m.bot_uuid, '_default'),
  NULL, 'approved', 'any', NULL
FROM ac_bot_friend f
JOIN v_ac_bot_map m
  ON m.bot_id = f.target_bot_id AND m.owner_id = f.target_entity_id
WHERE f.status = 'ACCEPTED';

-- 2b. approved permission_requests（connect）
INSERT IGNORE INTO permission_requests
  (request_id, edge_id, env, from_id, to_id, request_kind,
   requested_ref_id, requested_rules, message, status,
   decision_reason, created_by, decided_by,
   decided_at)
SELECT
  CONCAT('req_', MD5(CONCAT(
    'human_', f.requester_entity_id, '|', m.bot_uuid, '|', f.env, '|connect'
  ))),
  CONCAT('eg_', MD5(CONCAT(
    'human_', f.requester_entity_id, '|', m.bot_uuid, '|', f.env, '|pp_', m.bot_uuid, '_default'
  ))),
  f.env,
  CONCAT('human_', f.requester_entity_id),
  m.bot_uuid,
  'connect',
  NULL, NULL,
  NULL, 'approved',
  NULL,
  f.requester_entity_id,
  COALESCE(JSON_EXTRACT(f.ext, '$.approvals[0].approver'), f.target_entity_id),
  CURRENT_TIMESTAMP
FROM ac_bot_friend f
JOIN v_ac_bot_map m
  ON m.bot_id = f.target_bot_id AND m.owner_id = f.target_entity_id
WHERE f.status = 'ACCEPTED';


-- =========================================================================
-- 脚本 3 — System A: ac_bot_friend PENDING/REJECTED/CANCELLED → permission_requests (无 edge)
-- =========================================================================
INSERT IGNORE INTO permission_requests
  (request_id, edge_id, env, from_id, to_id, request_kind,
   requested_ref_id, requested_rules, message, status,
   decision_reason, created_by, decided_by,
   decided_at)
SELECT
  CONCAT('req_', MD5(CONCAT(
    'human_', f.requester_entity_id, '|', m.bot_uuid, '|', f.env, '|connect'
  ))),
  NULL,
  f.env,
  CONCAT('human_', f.requester_entity_id),
  m.bot_uuid,
  'connect',
  NULL, NULL,
  NULL,
  CASE f.status
    WHEN 'PENDING'   THEN 'pending'
    WHEN 'REJECTED'  THEN 'rejected'
    WHEN 'CANCELLED' THEN 'cancelled'
  END,
  CASE WHEN f.status = 'PENDING' THEN NULL
       ELSE COALESCE(JSON_EXTRACT(f.ext, '$.approvals[0].approver'), f.target_entity_id)
  END,
  f.requester_entity_id,
  CASE WHEN f.status = 'PENDING' THEN NULL
       ELSE COALESCE(JSON_EXTRACT(f.ext, '$.approvals[0].approver'), f.target_entity_id)
  END,
  CURRENT_TIMESTAMP
FROM ac_bot_friend f
JOIN v_ac_bot_map m
  ON m.bot_id = f.target_bot_id AND m.owner_id = f.target_entity_id
WHERE f.status IN ('PENDING', 'REJECTED', 'CANCELLED');


-- =========================================================================
-- 脚本 4 — System B: bcs_friendships pair → Bot↔Bot 双向 2 边 + 2 approved requests
-- =========================================================================

-- 4a. 两条 edge_grants（A→B ref=B.default, B→A ref=A.default）
INSERT IGNORE INTO edge_grants
  (edge_id, env, from_id, to_id, grant_kind, grant_ref_id, rules,
   status, originator_policy_type, originator_policy_data)
SELECT
  CONCAT('eg_', MD5(CONCAT(left_bot, '|', right_bot, '|', env, '|pp_', right_bot, '_default'))),
  env, left_bot, right_bot,
  'permission_profile', CONCAT('pp_', right_bot, '_default'),
  NULL, 'approved', 'any', NULL
FROM bcs_friendships
WHERE left_bot NOT LIKE 'human_%' AND right_bot NOT LIKE 'human_%'
UNION ALL
SELECT
  CONCAT('eg_', MD5(CONCAT(right_bot, '|', left_bot, '|', env, '|pp_', left_bot, '_default'))),
  env, right_bot, left_bot,
  'permission_profile', CONCAT('pp_', left_bot, '_default'),
  NULL, 'approved', 'any', NULL
FROM bcs_friendships
WHERE left_bot NOT LIKE 'human_%' AND right_bot NOT LIKE 'human_%';

-- 4b. 配套 approved permission_requests (每对 2 条, decided_by = 对端 bot 的 created_by)
INSERT IGNORE INTO permission_requests
  (request_id, edge_id, env, from_id, to_id, request_kind,
   requested_ref_id, requested_rules, message, status,
   decision_reason, created_by, decided_by,
   decided_at)
SELECT
  CONCAT('req_', MD5(CONCAT(left_bot, '|', right_bot, '|', env, '|connect'))),
  CONCAT('eg_', MD5(CONCAT(left_bot, '|', right_bot, '|', env, '|pp_', right_bot, '_default'))),
  env, left_bot, right_bot, 'connect',
  NULL, NULL, NULL, 'approved',
  NULL, left_bot,
  (SELECT b.created_by FROM bcs_bots b WHERE b.bot_uuid = bcs_friendships.right_bot AND b.env = bcs_friendships.env LIMIT 1),
  CURRENT_TIMESTAMP
FROM bcs_friendships
WHERE left_bot NOT LIKE 'human_%' AND right_bot NOT LIKE 'human_%'
UNION ALL
SELECT
  CONCAT('req_', MD5(CONCAT(right_bot, '|', left_bot, '|', env, '|connect'))),
  CONCAT('eg_', MD5(CONCAT(right_bot, '|', left_bot, '|', env, '|pp_', left_bot, '_default'))),
  env, right_bot, left_bot, 'connect',
  NULL, NULL, NULL, 'approved',
  NULL, right_bot,
  (SELECT b.created_by FROM bcs_bots b WHERE b.bot_uuid = bcs_friendships.left_bot AND b.env = bcs_friendships.env LIMIT 1),
  CURRENT_TIMESTAMP
FROM bcs_friendships
WHERE left_bot NOT LIKE 'human_%' AND right_bot NOT LIKE 'human_%';


-- =========================================================================
-- 脚本 5 — System B: bcs_friend_requests pending/rejected → permission_requests (accepted 已被脚本 4 覆盖)
-- =========================================================================
INSERT IGNORE INTO permission_requests
  (request_id, edge_id, env, from_id, to_id, request_kind,
   requested_ref_id, requested_rules, message, status,
   decision_reason, created_by, decided_by,
   decided_at)
SELECT
  CONCAT('req_', MD5(CONCAT(from_bot, '|', to_bot, '|', env, '|connect'))),
  NULL,
  env, from_bot, to_bot, 'connect',
  NULL, NULL, NULL,
  CASE status
    WHEN 'pending'   THEN 'pending'
    WHEN 'rejected'  THEN 'rejected'
  END,
  NULL,
  from_bot, NULL, NULL
FROM bcs_friend_requests
WHERE status <> 'accepted';


-- =========================================================================
-- 对账 (reconciliation)
-- =========================================================================

-- 人→Bot: ac_bot_friend ACCEPTED 数 == edge_grants from LIKE 'human_%' 数
SELECT 'human_to_bot_edges' AS check_name,
       (SELECT COUNT(*) FROM ac_bot_friend f
        JOIN v_ac_bot_map m ON m.bot_id=f.target_bot_id AND m.owner_id=f.target_entity_id
        WHERE f.status='ACCEPTED') AS old_count,
       (SELECT COUNT(*) FROM edge_grants WHERE from_id LIKE 'human_%' AND status='approved') AS new_count;
-- 期望: old_count == new_count

-- Bot↔Bot: bcs_friendships*2 == edge_grants 两端均非 human_ 数
SELECT 'bot_to_bot_edges' AS check_name,
       (SELECT COUNT(*)*2 FROM bcs_friendships
        WHERE left_bot NOT LIKE 'human_%' AND right_bot NOT LIKE 'human_%') AS old_count,
       (SELECT COUNT(*) FROM edge_grants
        WHERE from_id NOT LIKE 'human_%' AND to_id NOT LIKE 'human_%'
          AND status='approved') AS new_count;
-- 期望: old_count == new_count

-- default profile 覆盖: 每 bcs_bots bot 都有一条 default profile
SELECT 'default_profile_coverage' AS check_name,
       (SELECT COUNT(*) FROM bcs_bots) AS bots,
       (SELECT COUNT(*) FROM permission_profiles WHERE is_default=TRUE AND status='active') AS profiles;
-- 期望: bots == profiles

-- spot check: 某个老好友在新表是否 are_friends
-- SELECT * FROM edge_grants WHERE from_id='human_88001';
-- 准入实测: GET /bots/{A_uuid}/admission?actor=human_88001 → allowed=true