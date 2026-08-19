-- Use the recorded run id, while the matching Local writer freeze remains active.
SET @p1_01_installation_backfill_run_id = 'REPLACE_WITH_RECORDED_RUN_ID';
START TRANSACTION;
DELETE installation FROM ac_bot_skill_installation installation
JOIN ac_bot_skill_installation_backfill_audit audit
 ON audit.avernet_tenant = installation.avernet_tenant AND audit.env = installation.env
 AND audit.bot_id = installation.bot_id AND audit.skill_id = installation.skill_id
WHERE audit.run_id = @p1_01_installation_backfill_run_id;
SELECT ROW_COUNT() AS rolled_back_installations;
COMMIT;
