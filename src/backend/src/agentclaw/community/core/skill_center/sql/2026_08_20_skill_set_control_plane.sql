-- Canonical SkillSet create replay record.  Additive: historical /api routes
-- keep using ac_skill_set unchanged while canonical POST deduplicates retries.
-- Upgrade the earlier checkpoint in place before enforcing the new required
-- fingerprint. ``JSON_QUOTE`` preserves the exact canonical JSON order used
-- by the application hash function below.
ALTER TABLE ac_skill_set_create_idempotency
    ADD COLUMN IF NOT EXISTS request_hash CHAR(64) NULL;
UPDATE ac_skill_set_create_idempotency
SET request_hash = SHA2(
    CONCAT('{"description":', IFNULL(JSON_QUOTE(request_description), 'null'),
           ',"name":', JSON_QUOTE(request_name), '}'),
    256
)
WHERE request_hash IS NULL;
ALTER TABLE ac_skill_set_create_idempotency
    MODIFY COLUMN request_hash CHAR(64) NOT NULL;
CREATE TABLE IF NOT EXISTS ac_skill_set_create_idempotency (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    avernet_tenant VARCHAR(64) NOT NULL DEFAULT 'teamclaw',
    env VARCHAR(20) NOT NULL,
    bot_id VARCHAR(100) NOT NULL,
    owner_id VARCHAR(128) NOT NULL,
    idempotency_key VARCHAR(190) NOT NULL,
    request_name VARCHAR(100) NOT NULL,
    request_description TEXT NULL,
    request_hash CHAR(64) NOT NULL,
    skill_set_id INT NOT NULL,
    gmt_created DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_skill_set_create_idempotency
      (avernet_tenant, env, bot_id, owner_id, idempotency_key)
);
