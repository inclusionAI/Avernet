ALTER TABLE ac_skill_publication_attempt
  ADD COLUMN IF NOT EXISTS materialization_retry_count INT UNSIGNED NOT NULL DEFAULT 0
    COMMENT 'automatic retries scheduled after an exact Version materialization failure',
  DROP CONSTRAINT IF EXISTS ck_skill_publication_attempt_status,
  ADD CONSTRAINT ck_skill_publication_attempt_status
    CHECK (status IN ('PREPARING', 'SC_SUBMITTING', 'WAITING_SC',
      'RESULT_UNKNOWN', 'MATERIALIZING', 'MATERIALIZATION_FAILED', 'SUCCEEDED', 'FAILED'));
