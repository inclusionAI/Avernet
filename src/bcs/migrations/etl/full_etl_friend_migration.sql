-- full_etl_friend_migration.sql
-- Phase 2 全量 ETL: 把 System A (ac_bot_friend, 人→Bot) + System B (bcs_friendships/bcs_friend_requests, Bot↔Bot)
-- 历史好友数据迁移到 08-12 边权限表 (edge_grants/permission_profiles/permission_requests).
--
-- 运行时: MySQL production. 所有 INSERT 用 INSERT IGNORE (幂等, 重复跑不报错).
-- 依赖: Phase 1 Build 已部署 (五表 DDL 011_edge_permission.sql 已 apply).
-- 顺序: 脚本 0 → 1 → 2 → 3 → 4 → 5 → reconciliation.
-- 来源: spec §8.4 + edge-permission-friend-migration-plan.md §3.

-- 用前备份: 对 ac_bot_friend, bcs_friendships, bcs_friend_requests, bcs_actor_relations 做快照.

USE <your_database>;

-- =========================================================================
-- 脚本 0 — default profile 批量 seed (每 bot 一条 wildcard-allow)
-- =========================================================================
INSERT IGNORE INTO permission_profiles
  (bot_id, env, name, description, rules_template,
   revision, digest, is_default, status, created_by)
SELECT
  b.bot_uuid, b.env, 'default', NULL,
  '[{"tool":"*","specifier":"*","effect":"allow"}]',
  1,
  SHA2('[{"tool":"*","specifier":"*","effect":"allow"}]', 256),
  TRUE, 'active', 'system'
FROM bcs_bots b;

DROP TEMPORARY TABLE IF EXISTS tmp_default_profile_map;
CREATE TEMPORARY TABLE tmp_default_profile_map AS
SELECT id AS permission_profile_id, bot_id, env
FROM permission_profiles
WHERE is_default = TRUE AND status = 'active';


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

-- latest-status 视图：ac_bot_friend 可能是历史流水表，迁移只认同一申请关系的最新行。
CREATE OR REPLACE VIEW v_ac_bot_friend_latest AS
SELECT
  id, requester_entity_id, target_bot_id, target_entity_id, env, status,
  requester_name, target_name, target_owner_name, ext, gmt_create, gmt_modified
FROM (
  SELECT f.*,
         ROW_NUMBER() OVER (
           PARTITION BY requester_entity_id, target_bot_id, target_entity_id, env
           ORDER BY gmt_create DESC, id DESC
         ) AS rn
  FROM ac_bot_friend f
) ranked
WHERE rn = 1;

-- latest-status 视图：bcs_friend_requests 如存在历史多版本，只迁同方向同 env 最新申请。
CREATE OR REPLACE VIEW v_bcs_friend_requests_latest AS
SELECT
  id, request_id, from_bot, to_bot, status, env, gmt_create, gmt_modified
FROM (
  SELECT fr.*,
         ROW_NUMBER() OVER (
           PARTITION BY from_bot, to_bot, env
           ORDER BY gmt_create DESC, id DESC
         ) AS rn
  FROM bcs_friend_requests fr
) ranked
WHERE rn = 1;


-- =========================================================================
-- 脚本 2 — System A: ac_bot_friend ACCEPTED → 人→Bot 边 (1 条) + approved request
-- =========================================================================

-- 2a. edge_grants（人→Bot 单向, 不建反向边）
INSERT IGNORE INTO edge_grants
  (env, from_id, to_id, grant_kind, grant_ref_id, rules,
   status, originator_policy_type, originator_policy_data)
SELECT
  f.env,
  CONCAT('human_', f.requester_entity_id),
  m.bot_uuid,
  'permission_profile',
  p.permission_profile_id,
  NULL, 'approved', 'any', NULL
FROM v_ac_bot_friend_latest f
JOIN v_ac_bot_map m
  ON m.bot_id = f.target_bot_id AND m.owner_id = f.target_entity_id
JOIN tmp_default_profile_map p
  ON p.bot_id = m.bot_uuid AND p.env = f.env
WHERE f.status = 'ACCEPTED';

-- 2b. approved permission_requests（connect）
INSERT IGNORE INTO permission_requests
  (edge_id, env, from_id, to_id, request_kind,
   requested_ref_id, requested_rules, message, status,
   decision_reason, created_by, decided_by,
   decided_at)
SELECT
  eg.id,
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
FROM v_ac_bot_friend_latest f
JOIN v_ac_bot_map m
  ON m.bot_id = f.target_bot_id AND m.owner_id = f.target_entity_id
JOIN tmp_default_profile_map p
  ON p.bot_id = m.bot_uuid AND p.env = f.env
JOIN edge_grants eg
  ON eg.env = f.env
 AND eg.from_id = CONCAT('human_', f.requester_entity_id)
 AND eg.to_id = m.bot_uuid
 AND eg.grant_kind = 'permission_profile'
 AND eg.grant_ref_id = p.permission_profile_id
WHERE f.status = 'ACCEPTED';


-- =========================================================================
-- 脚本 3 — System A: ac_bot_friend PENDING/REJECTED/CANCELLED → permission_requests (无 edge)
-- =========================================================================
INSERT IGNORE INTO permission_requests
  (env, from_id, to_id, request_kind,
   requested_ref_id, requested_rules, message, status,
   decision_reason, created_by, decided_by,
   decided_at)
SELECT
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
FROM v_ac_bot_friend_latest f
JOIN v_ac_bot_map m
  ON m.bot_id = f.target_bot_id AND m.owner_id = f.target_entity_id
WHERE f.status IN ('PENDING', 'REJECTED', 'CANCELLED');


-- =========================================================================
-- 脚本 4 — System B: bcs_friendships pair → Bot↔Bot 双向 2 边 + 2 approved requests
-- =========================================================================

-- 4a. 两条 edge_grants（A→B ref=B.default, B→A ref=A.default）
INSERT IGNORE INTO edge_grants
  (env, from_id, to_id, grant_kind, grant_ref_id, rules,
   status, originator_policy_type, originator_policy_data)
SELECT
  b.env, b.from_id, b.to_id, b.grant_kind, b.grant_ref_id, b.rules,
  b.status, b.originator_policy_type, b.originator_policy_data
FROM (
  SELECT
    env,
    left_bot AS from_id,
    right_bot AS to_id,
    'permission_profile' AS grant_kind,
    p.permission_profile_id AS grant_ref_id,
    NULL AS rules,
    'approved' AS status,
    'any' AS originator_policy_type,
    NULL AS originator_policy_data
  FROM bcs_friendships
  JOIN tmp_default_profile_map p
    ON p.bot_id = bcs_friendships.right_bot AND p.env = bcs_friendships.env
  WHERE left_bot NOT LIKE 'human_%' AND right_bot NOT LIKE 'human_%'
  UNION ALL
  SELECT
    env,
    right_bot AS from_id,
    left_bot AS to_id,
    'permission_profile' AS grant_kind,
    p.permission_profile_id AS grant_ref_id,
    NULL AS rules,
    'approved' AS status,
    'any' AS originator_policy_type,
    NULL AS originator_policy_data
  FROM bcs_friendships
  JOIN tmp_default_profile_map p
    ON p.bot_id = bcs_friendships.left_bot AND p.env = bcs_friendships.env
  WHERE left_bot NOT LIKE 'human_%' AND right_bot NOT LIKE 'human_%'
) AS b;

-- 4b. 配套 approved permission_requests (每对 2 条, decided_by = 对端 bot 的 created_by)
INSERT IGNORE INTO permission_requests
  (edge_id, env, from_id, to_id, request_kind,
   requested_ref_id, requested_rules, message, status,
   decision_reason, created_by, decided_by,
   decided_at)
SELECT
  eg.id,
  fs.env, fs.from_id, fs.to_id, 'connect',
  NULL, NULL, NULL, 'approved',
  NULL, fs.from_id,
  (SELECT b.created_by FROM bcs_bots b WHERE b.bot_uuid = fs.to_id AND b.env = fs.env LIMIT 1),
  CURRENT_TIMESTAMP
FROM (
  SELECT left_bot AS from_id, right_bot AS to_id, env, right_bot AS target_bot
  FROM bcs_friendships
  WHERE left_bot NOT LIKE 'human_%' AND right_bot NOT LIKE 'human_%'
  UNION ALL
  SELECT right_bot AS from_id, left_bot AS to_id, env, left_bot AS target_bot
  FROM bcs_friendships
  WHERE left_bot NOT LIKE 'human_%' AND right_bot NOT LIKE 'human_%'
) AS fs
JOIN tmp_default_profile_map p
  ON p.bot_id = fs.target_bot AND p.env = fs.env
JOIN edge_grants eg
  ON eg.env = fs.env
 AND eg.from_id = fs.from_id
 AND eg.to_id = fs.to_id
 AND eg.grant_kind = 'permission_profile'
 AND eg.grant_ref_id = p.permission_profile_id;


-- =========================================================================
-- 脚本 5 — System B: bcs_friend_requests pending/rejected → permission_requests (accepted 已被脚本 4 覆盖)
-- =========================================================================
INSERT IGNORE INTO permission_requests
  (env, from_id, to_id, request_kind,
   requested_ref_id, requested_rules, message, status,
   decision_reason, created_by, decided_by,
   decided_at)
SELECT
  env, from_bot, to_bot, 'connect',
  NULL, NULL, NULL,
  CASE status
    WHEN 'pending'   THEN 'pending'
    WHEN 'rejected'  THEN 'rejected'
  END,
  NULL,
  from_bot, NULL, NULL
FROM v_bcs_friend_requests_latest
WHERE status <> 'accepted';


-- =========================================================================
-- 对账 (reconciliation)
-- =========================================================================

-- bcs_actor_relations 说明：好友迁移以 bcs_friendships 为 System B SoR；
-- bcs_actor_relations 中 is_creator=0 的 bot↔bot 关系应只是 bcs_friendships 镜像，不单独迁移。
-- 如下 drift 检查非 0，说明镜像与 SoR 已分叉，必须先人工修复或补充迁移来源。
SELECT 'actor_relation_friend_mirror_drift' AS check_name,
       r.env,
       COUNT(*) AS drift_count,
       CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'FAIL' END AS result
FROM bcs_actor_relations r
WHERE r.is_creator = 0
  AND r.from_id NOT LIKE 'human_%'
  AND r.to_id NOT LIKE 'human_%'
  AND NOT EXISTS (
    SELECT 1 FROM bcs_friendships f
    WHERE f.env = r.env
      AND ((f.left_bot = r.from_id AND f.right_bot = r.to_id)
        OR (f.left_bot = r.to_id AND f.right_bot = r.from_id))
  )
GROUP BY r.env;

-- orphan bot 映射：System A 好友记录找不到 ac_bots 映射会漏迁。
SELECT 'system_a_orphan_bot_map' AS check_name,
       f.env,
       COUNT(*) AS orphan_count,
       CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'FAIL' END AS result
FROM v_ac_bot_friend_latest f
LEFT JOIN v_ac_bot_map m
  ON m.bot_id = f.target_bot_id AND m.owner_id = f.target_entity_id
WHERE m.bot_uuid IS NULL
GROUP BY f.env;

-- orphan bot：System B friendship 中任一 bot 不存在会导致 default profile 或 owner 信息缺失。
SELECT 'system_b_friendship_orphan_bot' AS check_name,
       fs.env,
       COUNT(*) AS orphan_count,
       CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'FAIL' END AS result
FROM bcs_friendships fs
LEFT JOIN bcs_bots lb ON lb.bot_uuid = fs.left_bot AND lb.env = fs.env
LEFT JOIN bcs_bots rb ON rb.bot_uuid = fs.right_bot AND rb.env = fs.env
WHERE fs.left_bot NOT LIKE 'human_%'
  AND fs.right_bot NOT LIKE 'human_%'
  AND (lb.bot_uuid IS NULL OR rb.bot_uuid IS NULL)
GROUP BY fs.env;

-- 人→Bot: latest ACCEPTED 数 == edge_grants from human_* approved 数（按 env）
SELECT 'human_to_bot_edges' AS check_name,
       old_side.env,
       old_side.old_count,
       COALESCE(new_side.new_count, 0) AS new_count,
       old_side.old_count - COALESCE(new_side.new_count, 0) AS diff,
       CASE WHEN old_side.old_count = COALESCE(new_side.new_count, 0) THEN 'PASS' ELSE 'FAIL' END AS result
FROM (
  SELECT f.env, COUNT(*) AS old_count
  FROM v_ac_bot_friend_latest f
  JOIN v_ac_bot_map m ON m.bot_id=f.target_bot_id AND m.owner_id=f.target_entity_id
  WHERE f.status='ACCEPTED'
  GROUP BY f.env
) old_side
LEFT JOIN (
  SELECT e.env, COUNT(*) AS new_count
  FROM edge_grants e
  JOIN tmp_default_profile_map p
    ON p.bot_id = e.to_id AND p.env = e.env
  WHERE e.from_id LIKE 'human_%'
    AND e.status='approved'
    AND e.grant_kind='permission_profile'
    AND e.grant_ref_id = p.permission_profile_id
  GROUP BY e.env
) new_side ON new_side.env = old_side.env;

-- Bot↔Bot: bcs_friendships*2 == edge_grants 两端均非 human_ 数（按 env）
SELECT 'bot_to_bot_edges' AS check_name,
       old_side.env,
       old_side.old_count,
       COALESCE(new_side.new_count, 0) AS new_count,
       old_side.old_count - COALESCE(new_side.new_count, 0) AS diff,
       CASE WHEN old_side.old_count = COALESCE(new_side.new_count, 0) THEN 'PASS' ELSE 'FAIL' END AS result
FROM (
  SELECT env, COUNT(*) * 2 AS old_count
  FROM bcs_friendships
  WHERE left_bot NOT LIKE 'human_%' AND right_bot NOT LIKE 'human_%'
  GROUP BY env
) old_side
LEFT JOIN (
  SELECT e.env, COUNT(*) AS new_count
  FROM edge_grants e
  JOIN tmp_default_profile_map p
    ON p.bot_id = e.to_id AND p.env = e.env
  WHERE e.from_id NOT LIKE 'human_%' AND e.to_id NOT LIKE 'human_%'
    AND e.status='approved'
    AND e.grant_kind='permission_profile'
    AND e.grant_ref_id = p.permission_profile_id
  GROUP BY e.env
) new_side ON new_side.env = old_side.env;

-- default profile 覆盖: 每 bcs_bots bot 都有一条 active default profile（按 env）
SELECT 'default_profile_coverage' AS check_name,
       bots.env,
       bots.bot_count AS old_count,
       COALESCE(profiles.profile_count, 0) AS new_count,
       bots.bot_count - COALESCE(profiles.profile_count, 0) AS diff,
       CASE WHEN bots.bot_count = COALESCE(profiles.profile_count, 0) THEN 'PASS' ELSE 'FAIL' END AS result
FROM (
  SELECT env, COUNT(*) AS bot_count FROM bcs_bots GROUP BY env
) bots
LEFT JOIN (
  SELECT env, COUNT(*) AS profile_count
  FROM permission_profiles
  WHERE is_default=TRUE AND status='active'
  GROUP BY env
) profiles ON profiles.env = bots.env;

-- System B latest accepted request 必须能找到 friendship；否则 accepted request 无法由脚本 4 生成 edge。
SELECT 'system_b_accepted_request_without_friendship' AS check_name,
       fr.env,
       COUNT(*) AS mismatch_count,
       CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'FAIL' END AS result
FROM v_bcs_friend_requests_latest fr
WHERE fr.status = 'accepted'
  AND NOT EXISTS (
    SELECT 1 FROM bcs_friendships fs
    WHERE fs.env = fr.env
      AND ((fs.left_bot = fr.from_bot AND fs.right_bot = fr.to_bot)
        OR (fs.left_bot = fr.to_bot AND fs.right_bot = fr.from_bot))
  )
GROUP BY fr.env;

-- System B latest 非 accepted request 不应仍有 friendship；否则旧侧 request 与 friendship 事实分叉。
SELECT 'system_b_non_accepted_request_with_friendship' AS check_name,
       fr.env,
       COUNT(*) AS mismatch_count,
       CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'FAIL' END AS result
FROM v_bcs_friend_requests_latest fr
WHERE fr.status <> 'accepted'
  AND EXISTS (
    SELECT 1 FROM bcs_friendships fs
    WHERE fs.env = fr.env
      AND ((fs.left_bot = fr.from_bot AND fs.right_bot = fr.to_bot)
        OR (fs.left_bot = fr.to_bot AND fs.right_bot = fr.from_bot))
  )
GROUP BY fr.env;

-- approved request 必须有对应 approved edge。
SELECT 'approved_request_without_edge' AS check_name,
       r.env,
       COUNT(*) AS mismatch_count,
       CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'FAIL' END AS result
FROM permission_requests r
LEFT JOIN edge_grants e
  ON e.id = r.edge_id
 AND e.env = r.env
 AND e.status = 'approved'
WHERE r.request_kind = 'connect'
  AND r.status = 'approved'
  AND e.id IS NULL
GROUP BY r.env;

-- approved edge 必须有对应 approved request。
SELECT 'approved_edge_without_request' AS check_name,
       e.env,
       COUNT(*) AS mismatch_count,
       CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'FAIL' END AS result
FROM edge_grants e
JOIN tmp_default_profile_map p
  ON p.bot_id = e.to_id AND p.env = e.env
LEFT JOIN permission_requests r
  ON r.edge_id = e.id
 AND r.env = e.env
 AND r.request_kind = 'connect'
 AND r.status = 'approved'
WHERE e.grant_kind = 'permission_profile'
  AND e.grant_ref_id = p.permission_profile_id
  AND e.status = 'approved'
  AND r.id IS NULL
GROUP BY e.env;

-- 非通过状态 request 不应有关联 approved edge。
SELECT 'non_approved_request_with_edge' AS check_name,
       r.env,
       COUNT(*) AS mismatch_count,
       CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'FAIL' END AS result
FROM permission_requests r
JOIN edge_grants e
  ON e.id = r.edge_id
 AND e.env = r.env
 AND e.status = 'approved'
WHERE r.request_kind = 'connect'
  AND r.status <> 'approved'
GROUP BY r.env;

-- spot check: 某个老好友在新表是否 are_friends
-- SELECT * FROM edge_grants WHERE from_id='human_88001';
-- 准入实测: GET /bots/{A_uuid}/admission?actor=human_88001 → allowed=true
