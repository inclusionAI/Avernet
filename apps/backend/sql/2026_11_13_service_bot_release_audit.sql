-- Apply before enabling publication audit producers and delivery.
-- Existing publication rows are not backfilled with invented history.

CREATE TABLE ac_service_bot_release_audit (
	id BIGINT NOT NULL AUTO_INCREMENT,
	change_id VARCHAR(64) NOT NULL,
	source_bot_pk BIGINT NOT NULL,
	bot_id VARCHAR(512) NOT NULL,
	entity_id VARCHAR(1024) NOT NULL,
	env VARCHAR(64) NOT NULL,
	avernet_tenant VARCHAR(64) NOT NULL DEFAULT 'teamclaw',
	creator_id VARCHAR(1024),
	operator_id VARCHAR(1024) NOT NULL,
	bot_created_at DATETIME,
	change_created_at DATETIME NOT NULL,
	submitted_at DATETIME,
	before_stage VARCHAR(16),
	desired_stage VARCHAR(16) NOT NULL,
	before_status VARCHAR(128),
	publish_id BIGINT NOT NULL,
	version BIGINT,
	finished_at DATETIME,
	finish_status VARCHAR(16) NOT NULL,
	final_publish_status VARCHAR(128),
	revision INTEGER NOT NULL,
	ext TEXT NOT NULL,
	snapshot_json TEXT NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uk_release_audit_change UNIQUE (avernet_tenant, env, change_id)
);

CREATE INDEX idx_release_audit_bot ON ac_service_bot_release_audit (avernet_tenant, env, source_bot_pk, submitted_at);
CREATE INDEX idx_release_audit_publish ON ac_service_bot_release_audit (avernet_tenant, env, publish_id);

CREATE TABLE ac_service_bot_release_audit_event (
	id BIGINT NOT NULL AUTO_INCREMENT,
	event_id VARCHAR(96) NOT NULL,
	change_id VARCHAR(64) NOT NULL,
	revision INTEGER NOT NULL,
	publish_id BIGINT NOT NULL,
	env VARCHAR(64) NOT NULL,
	avernet_tenant VARCHAR(64) NOT NULL,
	payload_json TEXT NOT NULL,
	delivered_at DATETIME,
	attempts INTEGER NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (event_id)
);

CREATE INDEX idx_release_audit_event_change ON ac_service_bot_release_audit_event (avernet_tenant, env, change_id, revision);
CREATE INDEX idx_release_audit_pending ON ac_service_bot_release_audit_event (env, delivered_at, id);
