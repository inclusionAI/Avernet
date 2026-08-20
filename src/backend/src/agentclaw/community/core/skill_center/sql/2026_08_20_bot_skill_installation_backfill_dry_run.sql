-- P1-01 Installation backfill dry-run. This file is intentionally read-only.
--
-- Freeze every Local Skill writer before this query and retain that same freeze
-- through the separately reviewed apply step. The published Local contract is
-- independent of Default SkillSet membership: a local:// Skill is active when
-- no exact historical exclusion exists.
--
-- ``live_bot_count`` must be exactly one. A duplicated live Bot identity is
-- an exception, not a tie to break: apply will fail closed while any such row
-- exists. This matters especially for bot_id='default'.

-- 1. Archive these result rows with the release change record before apply.
WITH legacy_active_local AS (
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
    GROUP BY skill.avernet_tenant, skill.env, skill.user_id, skill.bolt_id, skill.id
)
SELECT avernet_tenant,
       env,
       COUNT(*) AS legacy_active_local,
       SUM(live_bot_count = 1) AS live_exact_bot_candidates,
       SUM(live_bot_count > 1) AS ambiguous_live_bot_candidates,
       CASE WHEN SUM(live_bot_count > 1) = 0 THEN 1 ELSE 0 END AS apply_allowed
FROM legacy_active_local
GROUP BY avernet_tenant, env
ORDER BY avernet_tenant, env;

-- 2. Every row here is a blocking exception. Do not run apply until empty.
WITH legacy_active_local AS (
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
    GROUP BY skill.avernet_tenant, skill.env, skill.user_id, skill.bolt_id, skill.id
)
SELECT avernet_tenant, env, owner_id, bot_id, skill_id, live_bot_count
FROM legacy_active_local
WHERE live_bot_count > 1
ORDER BY avernet_tenant, env, owner_id, bot_id, skill_id;

-- 3. These are the exact candidate identities. They are returned only when
-- no ambiguity exists anywhere in the frozen rollout scope.
WITH legacy_active_local AS (
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
    GROUP BY skill.avernet_tenant, skill.env, skill.user_id, skill.bolt_id, skill.id
)
SELECT candidate.avernet_tenant,
       candidate.env,
       candidate.owner_id,
       candidate.bot_id,
       candidate.skill_id
FROM legacy_active_local candidate
WHERE candidate.live_bot_count = 1
  AND NOT EXISTS (
      SELECT 1 FROM legacy_active_local ambiguity WHERE ambiguity.live_bot_count > 1
  )
  AND NOT EXISTS (
      SELECT 1
      FROM ac_bot_skill_installation installation
      WHERE installation.avernet_tenant = candidate.avernet_tenant
        AND installation.env = candidate.env
        AND installation.owner_id = candidate.owner_id
        AND installation.bot_id = candidate.bot_id
        AND installation.skill_id = candidate.skill_id
  )
ORDER BY candidate.avernet_tenant, candidate.env, candidate.owner_id,
         candidate.bot_id, candidate.skill_id;
