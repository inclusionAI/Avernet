-- Freeze legacy writers before this backfill and keep them stopped after cutover.
-- Preserve every external ID and original timestamp. A newer connect decision
-- with the same ID wins on rerun (no UPDATE of live permission_requests).
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
