-- Bring existing HumanInput tables to the same non-unique scope index as
-- the corrected 001/008 CREATE statements. Keep both VARCHAR(768) columns
-- and the full active_slot_key unique index unchanged.
-- One ALTER replaces the index atomically; a retry after a successful DDL
-- but before its version record can safely rebuild the same index again.
ALTER TABLE `bcs_human_input_requests`
  DROP INDEX `idx_human_input_scope_status`,
  ADD INDEX `idx_human_input_scope_status`
    (`reply_scope_key`(700), `status`, `deadline_ms`, `created_at`);
