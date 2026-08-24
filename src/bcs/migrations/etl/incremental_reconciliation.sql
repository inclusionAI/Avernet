-- incremental_reconciliation.sql
-- Phase 3: 增量对账（latest-status 双向 reconciliation，非 INSERT-only）
--
-- 与 full_etl_friend_migration.sql 的区别：
--   全量 ETL = INSERT-only（INSERT IGNORE，只加不删）—— 靠双写兜底删除。
--   增量对账 = 全量 INSERT + REVOKE（旧侧友谊消失→撤边）+ UPDATE（状态迁移）。
--
-- 幂等可重跑。手动触发（非 cron）。每次 shadow 前跑一轮，cutover 前跑最终对账。
--
-- 依赖: full_etl_friend_migration.sql 已跑过（或本文件包含同款 INSERT 逻辑）。
-- 来源: spec §8.5 双向对账。

USE <your_database>;

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
INNER JOIN (
  SELECT requester_entity_id, target_bot_id, target_entity_id, env, status,
         ROW_NUMBER() OVER (
           PARTITION BY requester_entity_id, target_bot_id, target_entity_id, env
           ORDER BY gmt_create DESC
         ) AS rn
  FROM ac_bot_friend
) latest ON latest.rn = 1 AND latest.status <> 'ACCEPTED'
INNER JOIN v_ac_bot_map m
  ON m.bot_id = latest.target_bot_id AND m.owner_id = latest.target_entity_id
SET e.status = 'revoked',
    e.gmt_modified = CURRENT_TIMESTAMP
WHERE e.from_id = CONCAT('human_', latest.requester_entity_id)
  AND e.to_id = m.bot_uuid
  AND e.env = latest.env
  AND e.status = 'approved'
  AND e.grant_kind = 'permission_profile'
  AND e.grant_ref_id = CONCAT('pp_', m.bot_uuid, '_default');

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
INNER JOIN (
  SELECT requester_entity_id, target_bot_id, target_entity_id, env, status,
         ROW_NUMBER() OVER (
           PARTITION BY requester_entity_id, target_bot_id, target_entity_id, env
           ORDER BY gmt_create DESC
         ) AS rn
  FROM ac_bot_friend
) latest ON latest.rn = 1
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

-- 3b. System B: bcs_friend_requests status → permission_requests status
UPDATE permission_requests r
INNER JOIN bcs_friend_requests fr
  ON fr.from_bot = r.from_id AND fr.to_bot = r.to_id AND fr.env = r.env
SET r.status = CASE fr.status
    WHEN 'pending'  THEN 'pending'
    WHEN 'rejected' THEN 'rejected'
  END,
    r.gmt_modified = CURRENT_TIMESTAMP
WHERE r.request_kind = 'connect'
  AND fr.status <> 'accepted';

-- =========================================================================
-- Part 4: 对账（同 full ETL，但新增 revoke 计数）
-- =========================================================================

-- 人→Bot 边数：ac_bot_friend latest ACCEPTED == edge_grants human_* approved
SELECT 'human_to_bot' AS metric,
  (SELECT COUNT(DISTINCT CONCAT(f.requester_entity_id, '|', CONCAT(f.target_bot_id, ':', f.target_entity_id))
   FROM ac_bot_friend f
   WHERE f.status = 'ACCEPTED' AND f.env = ?) AS old_active,
  (SELECT COUNT(*) FROM edge_grants
   WHERE from_id LIKE 'human_%' AND status = 'approved'
     AND grant_kind = 'permission_profile') AS new_active;
-- 期望相等

-- Bot↔Bot 边数：bcs_friendships*2 == edge_grants bot↔bot approved
SELECT 'bot_to_bot' AS metric,
  (SELECT COUNT(*) * 2 FROM bcs_friendships
   WHERE left_bot NOT LIKE 'human_%' AND right_bot NOT LIKE 'human_%') AS old_active,
  (SELECT COUNT(*) FROM edge_grants
   WHERE from_id NOT LIKE 'human_%' AND to_id NOT LIKE 'human_%'
     AND status = 'approved') AS new_active;
-- 期望相等

-- revoke 的边数（对账时观察迁移了多少 unfriend）
SELECT 'revoked_edges' AS metric,
  (SELECT COUNT(*) FROM edge_grants WHERE status = 'revoked') AS count;

-- =========================================================================
-- 使用说明:
-- 1. 先跑 full_etl_friend_migration.sql（全量 INSERT）。
-- 2. 再跑本文件（增量 REVOKE + UPDATE）。
-- 3. 观察 Part 4 对账：old_active == new_active？
-- 4. 若不等 → 查看 orphan 清单 / 漏迁某类 → 补迁后重跑。
-- 5. shadow 比对（scripts/shadow_compare_friend_checks.py）确认行为一致。
-- 6. cutover 前跑最终一轮 → 确认 old_active == new_active → 进 Phase 5。
-- =========================================================================