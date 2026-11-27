-- Bootstrap schema (current). Existing installations apply 2026_11_27_multi_rollout.sql instead.

CREATE TABLE ac_product_configuration_v2 (
	id BIGINT NOT NULL AUTO_INCREMENT,
	definition_json MEDIUMTEXT NOT NULL,
	owner_json VARCHAR(1024) NOT NULL,
	env VARCHAR(20) COLLATE utf8mb4_bin NOT NULL,
	avernet_tenant VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARACTER SET utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE ac_product_identity_v2 (
	id BIGINT NOT NULL AUTO_INCREMENT,
	kind VARCHAR(16) COLLATE utf8mb4_bin NOT NULL,
	collet_id BIGINT NOT NULL,
	env VARCHAR(20) COLLATE utf8mb4_bin NOT NULL,
	avernet_tenant VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARACTER SET utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE ac_product_runtime_v2 (
	id BIGINT NOT NULL AUTO_INCREMENT,
	profile_id BIGINT NOT NULL,
	revision BIGINT NOT NULL,
	snapshot_json MEDIUMTEXT NOT NULL,
	env VARCHAR(20) COLLATE utf8mb4_bin NOT NULL,
	avernet_tenant VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(profile_id) REFERENCES ac_product_configuration_v2 (id)
) ENGINE=InnoDB DEFAULT CHARACTER SET utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE ac_product_receipt_v2 (
	id BIGINT NOT NULL AUTO_INCREMENT,
	actor_key VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
	request_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
	operation VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
	request_digest VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
	result_json MEDIUMTEXT NOT NULL,
	env VARCHAR(20) COLLATE utf8mb4_bin NOT NULL,
	avernet_tenant VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARACTER SET utf8mb4 COLLATE=utf8mb4_bin;

CREATE UNIQUE INDEX uk_product_receipt_v2 ON ac_product_receipt_v2 (avernet_tenant, env, actor_key, request_id);

CREATE TABLE ac_product_intent_v2 (
	id BIGINT NOT NULL AUTO_INCREMENT,
	collet_id BIGINT NOT NULL,
	revision BIGINT NOT NULL,
	kind VARCHAR(16) COLLATE utf8mb4_bin NOT NULL,
	payload_json MEDIUMTEXT NOT NULL,
	state VARCHAR(16) COLLATE utf8mb4_bin NOT NULL,
	logical_key VARCHAR(64) COLLATE utf8mb4_bin,
	claim_owner VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
	lease_until BIGINT NOT NULL,
	env VARCHAR(20) COLLATE utf8mb4_bin NOT NULL,
	avernet_tenant VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(collet_id) REFERENCES ac_product_runtime_v2 (id)
) ENGINE=InnoDB DEFAULT CHARACTER SET utf8mb4 COLLATE=utf8mb4_bin;

CREATE INDEX ix_product_intent_v2_ready ON ac_product_intent_v2 (avernet_tenant, env, state, lease_until);

CREATE UNIQUE INDEX uk_product_intent_v2_key ON ac_product_intent_v2 (avernet_tenant, env, logical_key);

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
