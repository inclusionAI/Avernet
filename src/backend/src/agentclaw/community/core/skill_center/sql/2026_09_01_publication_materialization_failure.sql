-- ODC accepts one ALTER action per statement. Apply these statements in order.
-- v2 is added before the legacy constraint is removed, so the table is never
-- temporarily unconstrained. Keep legacy statuses until their writers retire.
ALTER TABLE ac_skill_publication_attempt
  ADD COLUMN materialization_retry_count INT UNSIGNED NOT NULL DEFAULT 0
    COMMENT 'automatic retries scheduled after an exact Version materialization failure';

ALTER TABLE ac_skill_publication_attempt
  ADD CONSTRAINT ck_skill_publication_attempt_status_v2
    CHECK (status IN ('PREPARING', 'VALIDATING', 'SCANNING', 'SC_SUBMITTING',
      'WAITING_SC', 'RESULT_UNKNOWN', 'MATERIALIZING', 'MATERIALIZATION_FAILED',
      'SUCCEEDED', 'FAILED', 'MANUAL_RECONCILIATION'));

ALTER TABLE ac_skill_publication_attempt
  DROP CONSTRAINT ck_skill_publication_attempt_status;
