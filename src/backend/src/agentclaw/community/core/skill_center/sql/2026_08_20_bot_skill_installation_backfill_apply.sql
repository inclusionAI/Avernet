-- P1-01 Installation backfill apply. Run only after the separate dry-run has
-- been reviewed and while the same Local Skill writer freeze remains active.
--
-- 1. Run the paired dry-run and retain all three result sets.
-- 2. Any ambiguous live Bot identity is a hard stop. This script records the
--    metric but inserts no Installation/audit candidates while ambiguity > 0.
-- 3. Replace the run id and change approval from 0 to 1. Otherwise this script
--    only builds a session-local candidate view and writes nothing persistent.
-- 4. Source verify-and-commit in this same session. It commits only after every
--    recorded candidate has an Installation row.
SET @p1_01_installation_backfill_run_id = 'REPLACE_WITH_NEW_UUID';
SET @p1_01_installation_backfill_approved = 0;

DROP TEMPORARY TABLE IF EXISTS p1_01_legacy_active_local_candidates;
CREATE TEMPORARY TABLE p1_01_legacy_active_local_candidates AS
SELECT skill.avernet_tenant,
       skill.env,
       skill.user_id AS owner_id,
       skill.bolt_id AS bot_id,
       skill.id AS skill_id,
       COUNT(bot.id) AS live_bot_count
FROM ac_skill skill
LEFT JOIN ac_bots bot
  ON bot.avernet_tenant = skill.avernet_tenant
 AND bot.env = skill.env
 AND bot.owner_id = skill.user_id
 AND bot.bot_id = skill.bolt_id
 AND bot.is_delete = 0
WHERE skill.git_path LIKE 'local://%'
  AND NOT EXISTS (
      SELECT 1
      FROM ac_default_skillset_skill_exclusion exclusion
      WHERE exclusion.avernet_tenant = skill.avernet_tenant
        AND exclusion.user_id = skill.user_id
        AND exclusion.bot_id = skill.bolt_id
        AND exclusion.skill_id = skill.id
  )
GROUP BY skill.avernet_tenant, skill.env, skill.user_id, skill.bolt_id, skill.id;

SELECT COUNT(*) INTO @p1_01_installation_backfill_ambiguous_count
FROM p1_01_legacy_active_local_candidates
WHERE live_bot_count > 1;

START TRANSACTION;

INSERT INTO ac_bot_skill_installation_backfill_run_audit
  (run_id, avernet_tenant, env, legacy_active_local,
   live_exact_bot_candidates, ambiguous_live_bot_candidates,
   inserted_installations, missing_installations)
SELECT @p1_01_installation_backfill_run_id,
       avernet_tenant,
       env,
       COUNT(*),
       SUM(live_bot_count = 1),
       SUM(live_bot_count > 1),
       0,
       0
FROM p1_01_legacy_active_local_candidates
WHERE @p1_01_installation_backfill_approved = 1
  AND @p1_01_installation_backfill_run_id <> 'REPLACE_WITH_NEW_UUID'
GROUP BY avernet_tenant, env;

INSERT INTO ac_bot_skill_installation_backfill_audit
  (run_id, avernet_tenant, env, owner_id, bot_id, skill_id)
SELECT @p1_01_installation_backfill_run_id,
       candidate.avernet_tenant,
       candidate.env,
       candidate.owner_id,
       candidate.bot_id,
       candidate.skill_id
FROM p1_01_legacy_active_local_candidates candidate
WHERE @p1_01_installation_backfill_approved = 1
  AND @p1_01_installation_backfill_run_id <> 'REPLACE_WITH_NEW_UUID'
  AND @p1_01_installation_backfill_ambiguous_count = 0
  AND candidate.live_bot_count = 1
  AND NOT EXISTS (
      SELECT 1
      FROM ac_bot_skill_installation installation
      WHERE installation.avernet_tenant = candidate.avernet_tenant
        AND installation.env = candidate.env
        AND installation.owner_id = candidate.owner_id
        AND installation.bot_id = candidate.bot_id
        AND installation.skill_id = candidate.skill_id
  );

INSERT INTO ac_bot_skill_installation
  (avernet_tenant, env, owner_id, bot_id, skill_id)
SELECT avernet_tenant, env, owner_id, bot_id, skill_id
FROM ac_bot_skill_installation_backfill_audit
WHERE run_id = @p1_01_installation_backfill_run_id;

UPDATE ac_bot_skill_installation_backfill_run_audit run_audit
SET inserted_installations = (
        SELECT COUNT(*)
        FROM ac_bot_skill_installation_backfill_audit audit
        WHERE audit.run_id = run_audit.run_id
          AND audit.avernet_tenant = run_audit.avernet_tenant
          AND audit.env = run_audit.env
    ),
    missing_installations = (
        SELECT COUNT(*)
        FROM ac_bot_skill_installation_backfill_audit audit
        LEFT JOIN ac_bot_skill_installation installation
          ON installation.avernet_tenant = audit.avernet_tenant
         AND installation.env = audit.env
         AND installation.owner_id = audit.owner_id
         AND installation.bot_id = audit.bot_id
         AND installation.skill_id = audit.skill_id
        WHERE audit.run_id = run_audit.run_id
          AND audit.avernet_tenant = run_audit.avernet_tenant
          AND audit.env = run_audit.env
          AND installation.id IS NULL
    )
WHERE run_audit.run_id = @p1_01_installation_backfill_run_id;

SELECT @p1_01_installation_backfill_ambiguous_count AS ambiguous_live_bot_candidates,
       CASE WHEN @p1_01_installation_backfill_ambiguous_count = 0 THEN 1 ELSE 0 END
         AS apply_allowed;
SELECT *
FROM ac_bot_skill_installation_backfill_run_audit
WHERE run_id = @p1_01_installation_backfill_run_id
ORDER BY avernet_tenant, env;

-- Do not COMMIT in this script. Source the paired verify-and-commit file in
-- this same database session. A disconnected session rolls this transaction
-- back automatically.
