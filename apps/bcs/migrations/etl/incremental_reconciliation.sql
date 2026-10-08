-- incremental_reconciliation.sql
-- Phase 3: 增量对账（latest-status 双向 reconciliation，非 INSERT-only）
--
-- 与 full_etl_friend_migration.sql 的区别：
--   全量 ETL = latest-status INSERT-only（INSERT IGNORE，只加不删），用于初次灌数。
--   增量对账 = 重跑全量 INSERT + REVOKE（旧侧友谊消失→撤边）+ UPDATE（状态迁移）。
--
-- 幂等可重跑。手动触发（非 cron）。每次 shadow 前跑一轮，cutover 前跑最终对账。
--
-- 依赖: full_etl_friend_migration.sql 已跑过（或本文件包含同款 INSERT 逻辑）。
-- 来源: spec §8.5 双向对账。

USE <your_database>;

-- 与 full_etl 保持一致的 latest-status 视图，保证本脚本可独立重跑。
CREATE OR REPLACE VIEW v_ac_bot_map AS
SELECT
  CONCAT(bot_id, ':', owner_id) AS bot_uuid,
  bot_id, owner_id, public,
  JSON_EXTRACT(ext, '$.friend_approval') AS friend_approval
FROM ac_bots
WHERE is_delete = 0;

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
-- Part 1: 重新运行全量 INSERT（幂等——新行被 IGNORE 拾取）
-- 就是对 full_etl_friend_migration.sql 的脚本 0-5 重跑。此处省略重复，
-- 实际执行时先跑 full_etl，再跑本文件的 Part 2/3。
-- =========================================================================

-- =========================================================================
-- Part 2: REVOKE — 旧侧友谊已消失 → 撤 edge（双向对账核心）
-- =========================================================================

-- 2a. System A: ac_bot_friend 最新行 status ≠ ACCEPTED → 撤人→Bot default 边
-- (取每对 gmt_create 最新行, 镜像 backend get_by_entity_ids: ORDER BY gmt_create DESC)
UPDATE edge_grants e
INNER JOIN v_ac_bot_friend_latest latest
  ON latest.status <> 'ACCEPTED'
INNER JOIN v_ac_bot_map m
  ON m.bot_id = latest.target_bot_id AND m.owner_id = latest.target_entity_id
INNER JOIN tmp_default_profile_map p
  ON p.bot_id = m.bot_uuid AND p.env = latest.env
SET e.status = 'revoked',
    e.gmt_modified = CURRENT_TIMESTAMP
WHERE e.from_id = CONCAT('human_', latest.requester_entity_id)
  AND e.to_id = m.bot_uuid
  AND e.env = latest.env
  AND e.status = 'approved'
  AND e.grant_kind = 'permission_profile'
  AND e.grant_ref_id = p.permission_profile_id;

-- 2b. System B: bcs_friendships pair 已不存在 → 撤 Bot↔Bot 双向 default 边
UPDATE edge_grants e
SET e.status = 'revoked',
    e.gmt_modified = CURRENT_TIMESTAMP
WHERE e.from_id NOT LIKE 'human_%'
  AND e.to_id NOT LIKE 'human_%'
  AND e.status = 'approved'
  AND e.grant_kind = 'permission_profile'
  AND NOT EXISTS (
    SELECT 1 FROM bcs_friendships p
    WHERE p.env = e.env
      AND ( (p.left_bot = e.from_id AND p.right_bot = e.to_id)
         OR (p.left_bot = e.to_id  AND p.right_bot = e.from_id) )
  );

-- =========================================================================
-- Part 3: UPDATE request status — 旧侧状态迁移 → 同步 request
-- =========================================================================

-- 3a. System A: ac_bot_friend latest status → permission_requests status
-- (INSERT IGNORE 已处理新行；此处处理已存在 request 的状态迁移：pending→approved/rejected/cancelled)
UPDATE permission_requests r
INNER JOIN v_ac_bot_friend_latest latest
  ON TRUE
INNER JOIN v_ac_bot_map m
  ON m.bot_id = latest.target_bot_id AND m.owner_id = latest.target_entity_id
SET r.status = CASE latest.status
    WHEN 'ACCEPTED' THEN 'approved'
    WHEN 'PENDING'  THEN 'pending'
    WHEN 'REJECTED' THEN 'rejected'
    WHEN 'CANCELLED' THEN 'cancelled'
  END,
    r.gmt_modified = CURRENT_TIMESTAMP
WHERE r.from_id = CONCAT('human_', latest.requester_entity_id)
  AND r.to_id = m.bot_uuid
  AND r.env = latest.env
  AND r.request_kind = 'connect';

-- 3b. System B: bcs_friend_requests latest status → permission_requests status
UPDATE permission_requests r
INNER JOIN v_bcs_friend_requests_latest fr
  ON fr.from_bot = r.from_id AND fr.to_bot = r.to_id AND fr.env = r.env
SET r.status = CASE fr.status
    WHEN 'pending'  THEN 'pending'
    WHEN 'rejected' THEN 'rejected'
  END,
    r.gmt_modified = CURRENT_TIMESTAMP
WHERE r.request_kind = 'connect'
  AND fr.status <> 'accepted';

-- =========================================================================
-- Part 4: 对账（latest-status + request/edge 一致性 + revoke 计数）
-- =========================================================================

-- bcs_actor_relations 仅作为 bcs_friendships 的镜像缓存，不单独作为迁移 SoR；非 0 必须先人工处理。
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

-- orphan bot 映射。
SELECT 'system_a_orphan_bot_map' AS check_name,
       f.env,
       COUNT(*) AS orphan_count,
       CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'FAIL' END AS result
FROM v_ac_bot_friend_latest f
LEFT JOIN v_ac_bot_map m
  ON m.bot_id = f.target_bot_id AND m.owner_id = f.target_entity_id
WHERE m.bot_uuid IS NULL
GROUP BY f.env;

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

-- 人→Bot 边数：ac_bot_friend latest ACCEPTED == edge_grants human_* approved（按 env）。
SELECT 'human_to_bot' AS check_name,
       old_side.env,
       old_side.old_count,
       COALESCE(new_side.new_count, 0) AS new_count,
       old_side.old_count - COALESCE(new_side.new_count, 0) AS diff,
       CASE WHEN old_side.old_count = COALESCE(new_side.new_count, 0) THEN 'PASS' ELSE 'FAIL' END AS result
FROM (
  SELECT f.env, COUNT(*) AS old_count
  FROM v_ac_bot_friend_latest f
  JOIN v_ac_bot_map m ON m.bot_id=f.target_bot_id AND m.owner_id=f.target_entity_id
  WHERE f.status = 'ACCEPTED'
  GROUP BY f.env
) old_side
LEFT JOIN (
  SELECT e.env, COUNT(*) AS new_count
  FROM edge_grants e
  JOIN tmp_default_profile_map p
    ON p.bot_id = e.to_id AND p.env = e.env
  WHERE e.from_id LIKE 'human_%'
    AND e.status = 'approved'
    AND e.grant_kind = 'permission_profile'
    AND e.grant_ref_id = p.permission_profile_id
  GROUP BY e.env
) new_side ON new_side.env = old_side.env;

-- Bot↔Bot 边数：bcs_friendships*2 == edge_grants bot↔bot approved（按 env）。
SELECT 'bot_to_bot' AS check_name,
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
    AND e.status = 'approved'
    AND e.grant_kind = 'permission_profile'
    AND e.grant_ref_id = p.permission_profile_id
  GROUP BY e.env
) new_side ON new_side.env = old_side.env;

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

-- request/edge 一致性。
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

-- revoke 的边数（对账时观察迁移了多少 unfriend）。
SELECT 'revoked_edges' AS check_name,
       env,
       COUNT(*) AS count
FROM edge_grants
WHERE status = 'revoked'
GROUP BY env;

-- =========================================================================
-- 使用说明:
-- 1. 先跑 full_etl_friend_migration.sql（全量 INSERT）。
-- 2. 再跑本文件（增量 REVOKE + UPDATE）。
-- 3. 观察 Part 4 对账：old_active == new_active？
-- 4. 若不等 → 查看 orphan 清单 / 漏迁某类 → 补迁后重跑。
-- 5. shadow 比对（scripts/shadow_compare_friend_checks.py）确认行为一致。
-- 6. cutover 前跑最终一轮 → 确认 old_active == new_active → 进 Phase 5。
-- =========================================================================
