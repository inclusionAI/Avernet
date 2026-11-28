-- Approval consumption, migration 159, OceanBase/MySQL.
-- Schema-first rollout: obtain approval and apply through the approved external
-- schema-change process BEFORE deploying the server. Managed/DDL-denied runtime
-- accounts skip this ordinary migration; they must not provision this schema.
-- Inspect existing definitions first:
SHOW COLUMNS FROM approval_cards LIKE 'processed_at';
SHOW INDEX FROM approval_cards WHERE Key_name = 'idx_approval_cards_polling';
-- Confirm processed_at is nullable BIGINT, DEFAULT NULL, with the exact comment
-- below; confirm the non-unique index has the exact ordered columns below.
-- If partially applied, submit only the missing statement for approval. Stop
-- and review incompatible definitions; a duplicate name does not prove parity.
-- Do not rewrite status, delivery_mode, resolved_at, comments, or audit history,
-- and do not infer consumption from age. NULL means unconsumed or unknown.
-- Do not manually advance unrelated schema_version entries. Let the authorized
-- migration runner record migration 159 only after both statements succeed;
-- managed schemas remain externally governed without runtime ledger writes.

ALTER TABLE approval_cards ADD COLUMN processed_at BIGINT DEFAULT NULL COMMENT 'Workflow consumption time in unix seconds; NULL means unconsumed or unknown';
ALTER TABLE approval_cards ADD INDEX idx_approval_cards_polling (delivery_mode, processed_at, status, resolved_at, id);
