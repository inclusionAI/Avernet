-- P1-01 Installation backfill apply. Run only after the separate dry-run has
-- been reviewed and while the same Local Skill writer freeze remains active.
--
-- 1. Run 2026_08_20_bot_skill_installation_backfill_dry_run.sql and retain its
--    complete result as the change record.
-- 2. Replace the run id below with a newly generated UUID and change approval
--    from 0 to 1. Leaving either placeholder/default intact produces no writes.
-- 3. Commit only after missing_installations is zero; retain the returned run
--    id with the dry-run evidence. The audit rows identify the exact rollback
--    set.
SET @p1_01_installation_backfill_run_id = 'REPLACE_WITH_NEW_UUID';
SET @p1_01_installation_backfill_approved = 0;

START TRANSACTION;

INSERT INTO ac_bot_skill_installation_backfill_audit
  (run_id, avernet_tenant, env, bot_id, skill_id)
SELECT DISTINCT @p1_01_installation_backfill_run_id, rel.avernet_tenant, rel.env,
       skill_set.bolt_id, rel.skill_id
FROM ac_skill_set_skill rel
JOIN ac_skill_set skill_set ON skill_set.id = rel.skill_set_id
 AND skill_set.avernet_tenant = rel.avernet_tenant AND skill_set.env = rel.env
JOIN ac_skill skill ON skill.id = rel.skill_id
 AND skill.avernet_tenant = rel.avernet_tenant AND skill.env = rel.env
WHERE @p1_01_installation_backfill_approved = 1
 AND @p1_01_installation_backfill_run_id <> 'REPLACE_WITH_NEW_UUID'
 AND skill_set.is_default = 1
 AND skill.git_path LIKE 'local://%'
 AND NOT EXISTS (
     SELECT 1
     FROM ac_default_skillset_skill_exclusion exclusion
     WHERE exclusion.avernet_tenant = rel.avernet_tenant
       AND exclusion.user_id = skill_set.user_id
       AND exclusion.bot_id = skill_set.bolt_id
       AND exclusion.skill_id = rel.skill_id
 )
 AND NOT EXISTS (
     SELECT 1
     FROM ac_bot_skill_installation installation
     WHERE installation.avernet_tenant = rel.avernet_tenant
       AND installation.env = rel.env
       AND installation.bot_id = skill_set.bolt_id
       AND installation.skill_id = rel.skill_id
 );
SELECT ROW_COUNT() AS audited_candidates;

INSERT INTO ac_bot_skill_installation (avernet_tenant, env, bot_id, skill_id)
SELECT avernet_tenant, env, bot_id, skill_id
FROM ac_bot_skill_installation_backfill_audit
WHERE run_id = @p1_01_installation_backfill_run_id;
SELECT ROW_COUNT() AS created_installations;

-- Do not COMMIT in this script. Source the paired verify-and-commit file in
-- this same database session. If the check is non-zero it executes ROLLBACK;
-- a disconnected session rolls this transaction back automatically.
SELECT COUNT(*) AS missing_installations
FROM ac_bot_skill_installation_backfill_audit audit
LEFT JOIN ac_bot_skill_installation installation
 ON installation.avernet_tenant = audit.avernet_tenant AND installation.env = audit.env
 AND installation.bot_id = audit.bot_id AND installation.skill_id = audit.skill_id
WHERE audit.run_id = @p1_01_installation_backfill_run_id AND installation.id IS NULL;
SELECT @p1_01_installation_backfill_run_id AS installation_backfill_run_id;
