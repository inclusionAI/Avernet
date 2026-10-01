-- Finalize the Phase 2 permanent Draft Edit Lease contract on existing schemas.
-- New installations already omit these legacy TTL experiment columns from the
-- additive schema. These guarded statements make the upgrade repeat-safe.

-- Preserve a holder whose experimental TTL is still live by converting that
-- Lease to the new permanent semantics. An already-expired holder must not be
-- made permanent: release it and advance the token before removing expires_at,
-- so every page holding the historical token remains fenced forever.
SET @finalize_expired_lease = IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'ac_skill_draft_edit_lease'
      AND COLUMN_NAME = 'expires_at'
  ),
  'UPDATE ac_skill_draft_edit_lease '
  'SET holder_user_id = NULL, fencing_token = fencing_token + 1 '
  'WHERE holder_user_id IS NOT NULL '
  'AND expires_at IS NOT NULL AND expires_at <= CURRENT_TIMESTAMP',
  'SELECT 1'
);
PREPARE finalize_expired_lease_stmt FROM @finalize_expired_lease;
EXECUTE finalize_expired_lease_stmt;
DEALLOCATE PREPARE finalize_expired_lease_stmt;

SET @drop_lease_expires_at = IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'ac_skill_draft_edit_lease'
      AND COLUMN_NAME = 'expires_at'
  ),
  'ALTER TABLE ac_skill_draft_edit_lease DROP COLUMN expires_at',
  'SELECT 1'
);
PREPARE drop_lease_expires_at_stmt FROM @drop_lease_expires_at;
EXECUTE drop_lease_expires_at_stmt;
DEALLOCATE PREPARE drop_lease_expires_at_stmt;

SET @drop_lease_renewed_at = IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'ac_skill_draft_edit_lease'
      AND COLUMN_NAME = 'renewed_at'
  ),
  'ALTER TABLE ac_skill_draft_edit_lease DROP COLUMN renewed_at',
  'SELECT 1'
);
PREPARE drop_lease_renewed_at_stmt FROM @drop_lease_renewed_at;
EXECUTE drop_lease_renewed_at_stmt;
DEALLOCATE PREPARE drop_lease_renewed_at_stmt;
