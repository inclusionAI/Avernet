-- Canonical SkillSet create replay record.  Additive: historical /api routes
-- keep using ac_skill_set unchanged while canonical POST deduplicates retries.
CREATE TABLE IF NOT EXISTS ac_skill_set_create_idempotency (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    avernet_tenant VARCHAR(64) NOT NULL DEFAULT 'teamclaw',
    env VARCHAR(20) NOT NULL,
    bot_id VARCHAR(100) NOT NULL,
    owner_id VARCHAR(128) NOT NULL,
    idempotency_key VARCHAR(190) NOT NULL,
    request_name VARCHAR(100) NOT NULL,
    request_description TEXT NULL,
    skill_set_id INT NOT NULL,
    gmt_created DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_skill_set_create_idempotency
      (avernet_tenant, env, bot_id, owner_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS ac_skill_set_name_claim (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    avernet_tenant VARCHAR(64) NOT NULL DEFAULT 'teamclaw',
    env VARCHAR(20) NOT NULL,
    bot_id VARCHAR(100) NOT NULL,
    name VARCHAR(100) NOT NULL,
    skill_set_id INT NOT NULL,
    gmt_created DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_skill_set_name_claim (avernet_tenant, env, bot_id, name)
);
