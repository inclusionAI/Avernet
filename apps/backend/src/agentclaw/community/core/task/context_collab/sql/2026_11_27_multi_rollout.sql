-- Existing v2 installations only. Stop workers/writers before applying once.
-- Unknown external runs and unconfirmed cancellations must be drained before rollback.
ALTER TABLE ac_product_intent_v2
  ADD COLUMN logical_key VARCHAR(64) COLLATE utf8mb4_bin NULL,
  ADD COLUMN claim_owner VARCHAR(64) COLLATE utf8mb4_bin NOT NULL DEFAULT '',
  ADD COLUMN lease_until BIGINT NOT NULL DEFAULT 0;
CREATE UNIQUE INDEX uk_product_intent_v2_key ON ac_product_intent_v2 (avernet_tenant, env, logical_key);
CREATE INDEX ix_product_intent_v2_ready ON ac_product_intent_v2 (avernet_tenant, env, state, lease_until);
-- Pre-upgrade claims have no fence. Reopen for engine-level reconciliation;
-- persisted unknown allocation fences still prohibit redispatch.
UPDATE ac_product_intent_v2 SET state = 'pending' WHERE state = 'claimed';

CREATE TABLE ac_product_candidate_slot_v1 (
	id BIGINT NOT NULL AUTO_INCREMENT,
	ordinal INTEGER NOT NULL,
	collet_id BIGINT NOT NULL,
	rollout_id BIGINT NOT NULL,
	env VARCHAR(20) COLLATE utf8mb4_bin NOT NULL,
	avernet_tenant VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARACTER SET utf8mb4 COLLATE=utf8mb4_bin;

CREATE UNIQUE INDEX uk_product_candidate_run_v1 ON ac_product_candidate_slot_v1 (avernet_tenant, env, rollout_id);

CREATE UNIQUE INDEX uk_product_candidate_slot_v1 ON ac_product_candidate_slot_v1 (avernet_tenant, env, ordinal);
