-- P1-01 Installation backfill dry-run. This file is intentionally read-only.
--
-- First freeze every Local Skill writer. Keep that same writer freeze through
-- the separately-reviewed apply step, otherwise the reviewed candidate set is
-- no longer evidence for the rows that would be written.
--
-- This exactly mirrors the published Local active read: any exclusion matching
-- tenant + user_id + bot_id + skill_id makes the Local Skill inactive. Do not
-- scope the exclusion to the current Default SkillSet; former-default rows are
-- still observed by the legacy contract.
SELECT DISTINCT rel.avernet_tenant, rel.env, skill_set.bolt_id, rel.skill_id
FROM ac_skill_set_skill rel
JOIN ac_skill_set skill_set ON skill_set.id = rel.skill_set_id
 AND skill_set.avernet_tenant = rel.avernet_tenant AND skill_set.env = rel.env
JOIN ac_skill skill ON skill.id = rel.skill_id
 AND skill.avernet_tenant = rel.avernet_tenant AND skill.env = rel.env
WHERE skill_set.is_default = 1
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
 )
ORDER BY rel.avernet_tenant, rel.env, skill_set.bolt_id, rel.skill_id;
