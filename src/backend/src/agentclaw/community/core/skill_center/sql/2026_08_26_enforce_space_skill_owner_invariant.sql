-- Enforce the active OWNER slot equivalence for databases created before
-- 2026-08-26. Run after verifying that every ACTIVE OWNER already has slot 1.

ALTER TABLE ac_skill_grant
  ADD COLUMN IF NOT EXISTS grant_reason VARCHAR(1024) NULL
  COMMENT '授权或 Owner 转移的审计原因' AFTER granted_by;

SELECT skill_id, COUNT(*) AS invalid_owner_count
FROM ac_skill_grant
WHERE (
    role = 'OWNER' AND status = 'ACTIVE'
    AND (owner_slot IS NULL OR owner_slot <> 1)
  ) OR (
    (role <> 'OWNER' OR status <> 'ACTIVE') AND owner_slot IS NOT NULL
  )
GROUP BY skill_id;

ALTER TABLE ac_skill_grant
  ADD CONSTRAINT IF NOT EXISTS ck_skill_active_owner_required CHECK (
    (role = 'OWNER' AND status = 'ACTIVE' AND owner_slot IS NOT NULL AND owner_slot = 1)
    OR ((role <> 'OWNER' OR status <> 'ACTIVE') AND owner_slot IS NULL)
  );
