-- Freeze legacy writers before this backfill and keep them stopped after cutover.
-- Preserve every external ID and original timestamp. A newer connect decision
-- with the same ID wins on rerun (no UPDATE of live permission_requests).
-- Legacy senders/receivers allow 255 characters and env allows 32. Widen
-- before copying so strict mode cannot reject valid history and non-strict
-- mode cannot truncate created_by (which legacy listings compare to from_id).
-- These ALTERs are repeatable if the draft migration is interrupted; keep all
-- writers stopped through DDL and backfill. Preserve NO PAD actor identity.
ALTER TABLE `permission_requests`
    MODIFY COLUMN `env` VARCHAR(32) NOT NULL COMMENT '环境标签',
    MODIFY COLUMN `created_by` VARCHAR(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin NOT NULL COMMENT '发起者标识',
    MODIFY COLUMN `decided_by` VARCHAR(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin DEFAULT NULL COMMENT '决定者；未决定时 NULL';

INSERT INTO permission_requests
    (request_id, env, from_id, to_id, request_kind, status, created_by,
     decided_at, gmt_create, gmt_modified)
SELECT legacy.request_id, legacy.env, legacy.from_bot, legacy.to_bot, 'connect',
       CASE legacy.status WHEN 'accepted' THEN 'approved' ELSE legacy.status END,
       legacy.from_bot,
       CASE WHEN legacy.status = 'pending' THEN NULL ELSE legacy.gmt_modified END,
       legacy.gmt_create, legacy.gmt_modified
FROM bcs_friend_requests legacy
WHERE NOT EXISTS (
    SELECT 1 FROM permission_requests migrated WHERE migrated.request_id = legacy.request_id
);
