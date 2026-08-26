-- F01 pre/post verification.  Save every result with the deployment record.
-- PRE: all must be zero before tightening constraints.
SELECT 'ac_skill missing/empty env' AS check_name, COUNT(*) AS count
  FROM ac_skill WHERE env IS NULL OR env = '';
SELECT 'ac_skill duplicate uuid' AS check_name, avernet_tenant, env, skill_uuid, COUNT(*) AS count
  FROM ac_skill WHERE skill_uuid IS NOT NULL
 GROUP BY avernet_tenant, env, skill_uuid HAVING COUNT(*) > 1;
SELECT 'ac_skill_set_skill duplicate' AS check_name, avernet_tenant, env, skill_set_id, skill_id, COUNT(*) AS count
  FROM ac_skill_set_skill
 GROUP BY avernet_tenant, env, skill_set_id, skill_id HAVING COUNT(*) > 1;
SELECT 'ac_skill_set_skill orphan skill' AS check_name, COUNT(*) AS count
  FROM ac_skill_set_skill rel LEFT JOIN ac_skill skill ON skill.id = rel.skill_id
 WHERE skill.id IS NULL;
SELECT 'ac_skill_set_skill orphan set' AS check_name, COUNT(*) AS count
  FROM ac_skill_set_skill rel LEFT JOIN ac_skill_set skill_set ON skill_set.id = rel.skill_set_id
 WHERE skill_set.id IS NULL;
SELECT 'ac_space non-numeric sc team id' AS check_name, COUNT(*) AS count
  FROM ac_space
 WHERE sc_team_id IS NOT NULL AND CAST(sc_team_id AS CHAR) REGEXP '[^0-9]';

-- Apply the separately reviewed, exact three-row duplicate cleanup here.
-- This script deliberately contains no broad DELETE/window-function cleanup.

-- POST: confirm all new facts and required keys exist; repeat these queries
-- after re-running the additive DDL to prove idempotence.
SELECT table_name, table_rows
  FROM information_schema.tables
 WHERE table_schema = DATABASE()
   AND table_name IN ('ac_space', 'ac_space_member', 'ac_skill_space_binding',
                      'ac_skill_grant', 'ac_skill_draft_edit_lease',
                      'ac_skill_version', 'ac_skill_publication_attempt');
SELECT table_name, index_name
  FROM information_schema.statistics
 WHERE table_schema = DATABASE()
   AND index_name IN ('uk_skill_uuid', 'uk_skill_set_skill',
                      'uk_center_version_materialization', 'uk_space_code',
                      'uk_skill_ownership', 'uk_skill_active_owner',
                      'uk_skill_version_ordinal', 'uk_publish_request');
SELECT table_name, column_name, is_nullable
  FROM information_schema.columns
 WHERE table_schema = DATABASE() AND column_name = 'env'
   AND table_name IN ('ac_space', 'ac_space_member', 'ac_skill_space_binding',
                      'ac_skill_grant', 'ac_skill_draft_edit_lease',
                      'ac_skill_version', 'ac_skill_publication_attempt');
