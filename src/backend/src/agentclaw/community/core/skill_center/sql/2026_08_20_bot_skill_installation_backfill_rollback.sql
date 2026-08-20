-- Use the recorded run id while the same Local Skill writer freeze is active.
-- Do not release writers between apply and rollback: after that boundary, a
-- newly-created Installation can share an audited identity and must not be
-- deleted as though it were the migration's row.
-- This deletes only identities recorded in the reviewed apply run.
SET @p1_01_installation_backfill_run_id = 'REPLACE_WITH_RECORDED_RUN_ID';
START TRANSACTION;
DELETE installation FROM ac_bot_skill_installation installation
JOIN ac_bot_skill_installation_backfill_audit audit
 ON audit.avernet_tenant = installation.avernet_tenant AND audit.env = installation.env
 AND audit.owner_id = installation.owner_id AND audit.bot_id = installation.bot_id
 AND audit.skill_id = installation.skill_id
WHERE audit.run_id = @p1_01_installation_backfill_run_id;
SELECT ROW_COUNT() AS rolled_back_installations;
COMMIT;
