-- P1-01 audited correction. Run during a Local writer freeze. This selection
-- is the only permitted legacy source: a local:// asset in its Default
-- SkillSet with no exclusion for that exact set. It does not infer state from
-- names, Runtime files, or arbitrary memberships.
START TRANSACTION;
SET @p1_01_installation_backfill_run_id = UUID();

-- Dry-run: capture and review this result before continuing.
SELECT DISTINCT rel.avernet_tenant, rel.env, skill_set.bolt_id, rel.skill_id
FROM ac_skill_set_skill rel
JOIN ac_skill_set skill_set ON skill_set.id = rel.skill_set_id
 AND skill_set.avernet_tenant = rel.avernet_tenant AND skill_set.env = rel.env
JOIN ac_skill skill ON skill.id = rel.skill_id
 AND skill.avernet_tenant = rel.avernet_tenant AND skill.env = rel.env
LEFT JOIN ac_default_skillset_skill_exclusion exclusion
 ON exclusion.avernet_tenant = rel.avernet_tenant
 AND exclusion.user_id = skill_set.user_id AND exclusion.bot_id = skill_set.bolt_id
 AND exclusion.skill_set_id = skill_set.id AND exclusion.skill_id = rel.skill_id
LEFT JOIN ac_bot_skill_installation installation
 ON installation.avernet_tenant = rel.avernet_tenant AND installation.env = rel.env
 AND installation.bot_id = skill_set.bolt_id AND installation.skill_id = rel.skill_id
WHERE skill_set.is_default = 1 AND skill.git_path LIKE 'local://%'
 AND exclusion.id IS NULL AND installation.id IS NULL;

INSERT INTO ac_bot_skill_installation_backfill_audit
  (run_id, avernet_tenant, env, bot_id, skill_id)
SELECT DISTINCT @p1_01_installation_backfill_run_id, rel.avernet_tenant, rel.env,
       skill_set.bolt_id, rel.skill_id
FROM ac_skill_set_skill rel
JOIN ac_skill_set skill_set ON skill_set.id = rel.skill_set_id
 AND skill_set.avernet_tenant = rel.avernet_tenant AND skill_set.env = rel.env
JOIN ac_skill skill ON skill.id = rel.skill_id
 AND skill.avernet_tenant = rel.avernet_tenant AND skill.env = rel.env
LEFT JOIN ac_default_skillset_skill_exclusion exclusion
 ON exclusion.avernet_tenant = rel.avernet_tenant
 AND exclusion.user_id = skill_set.user_id AND exclusion.bot_id = skill_set.bolt_id
 AND exclusion.skill_set_id = skill_set.id AND exclusion.skill_id = rel.skill_id
LEFT JOIN ac_bot_skill_installation installation
 ON installation.avernet_tenant = rel.avernet_tenant AND installation.env = rel.env
 AND installation.bot_id = skill_set.bolt_id AND installation.skill_id = rel.skill_id
WHERE skill_set.is_default = 1 AND skill.git_path LIKE 'local://%'
 AND exclusion.id IS NULL AND installation.id IS NULL;

INSERT INTO ac_bot_skill_installation (avernet_tenant, env, bot_id, skill_id)
SELECT avernet_tenant, env, bot_id, skill_id
FROM ac_bot_skill_installation_backfill_audit
WHERE run_id = @p1_01_installation_backfill_run_id;

-- Must be zero before reader cutover. Persist the returned run id as evidence.
SELECT COUNT(*) AS missing_installations
FROM ac_bot_skill_installation_backfill_audit audit
LEFT JOIN ac_bot_skill_installation installation
 ON installation.avernet_tenant = audit.avernet_tenant AND installation.env = audit.env
 AND installation.bot_id = audit.bot_id AND installation.skill_id = audit.skill_id
WHERE audit.run_id = @p1_01_installation_backfill_run_id AND installation.id IS NULL;
SELECT @p1_01_installation_backfill_run_id AS installation_backfill_run_id;
COMMIT;
