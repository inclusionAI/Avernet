-- Source immediately after the apply file in the SAME database session.
-- This is deliberately a separate explicit operator step: inspect the first
-- result, issue ROLLBACK if it is non-zero, otherwise execute COMMIT below.
SELECT COUNT(*) AS missing_installations
FROM ac_bot_skill_installation_backfill_audit audit
LEFT JOIN ac_bot_skill_installation installation
 ON installation.avernet_tenant = audit.avernet_tenant AND installation.env = audit.env
 AND installation.bot_id = audit.bot_id AND installation.skill_id = audit.skill_id
WHERE audit.run_id = @p1_01_installation_backfill_run_id AND installation.id IS NULL;

-- Only run this statement after the preceding count is zero.
COMMIT;
