-- SC Public lazy materialization creates a TeamClaw Skill Version without a
-- TeamClaw Publication Attempt.  MySQL/OceanBase unique indexes allow multiple
-- NULL values, so uk_version_attempt continues to enforce one Version per
-- non-null Space Publication Attempt while public-market Versions remain valid.
ALTER TABLE ac_skill_version
  MODIFY COLUMN publication_attempt_id BIGINT UNSIGNED NULL;
