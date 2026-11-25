-- PostgreSQL incremental catalogue DDL generated from ExpertModel.
-- Apply together with 2026_10_27_expert_json_postgresql.sql.
CREATE TABLE IF NOT EXISTS ac_experts (
	id BIGSERIAL NOT NULL,
	avernet_tenant VARCHAR(64) NOT NULL,
	env VARCHAR(20) NOT NULL,
	source_repo_id VARCHAR(64) NOT NULL,
	expert_id VARCHAR(64) NOT NULL,
	name VARCHAR(40) NOT NULL,
	description VARCHAR(200) NOT NULL,
	category VARCHAR(40) NOT NULL,
	tags TEXT NOT NULL,
	enabled BOOLEAN NOT NULL,
	is_deleted BOOLEAN NOT NULL,
	first_added_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL,
	source_commit VARCHAR(40) NOT NULL,
	expert_path VARCHAR(256) NOT NULL,
	content_digest VARCHAR(64) NOT NULL,
	effective_input TEXT NOT NULL,
	last_seen_commit VARCHAR(40) NOT NULL,
	sync_error TEXT,
	PRIMARY KEY (id),
	CONSTRAINT uk_expert_scope UNIQUE (avernet_tenant, env, source_repo_id, expert_id)
);

CREATE INDEX IF NOT EXISTS idx_expert_discovery ON ac_experts (avernet_tenant, env, source_repo_id, enabled, is_deleted);
