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
    request_hash CHAR(64) NOT NULL,
    skill_set_id INT NOT NULL,
    gmt_created DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_skill_set_create_idempotency
      (avernet_tenant, env, bot_id, owner_id, idempotency_key)
);

-- Upgrade the earlier checkpoint only after a clean install has created the
-- table. ``JSON_QUOTE`` preserves the canonical JSON ordering used by the
-- application request fingerprint.
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

-- Denormalize the parent Bot identity so the database can enforce that one
-- Skill belongs to at most one ordinary SkillSet for a Bot. System Default and
-- historical orphan rows remain NULL; every ordinary-set writer persists bot_id.
ALTER TABLE ac_skill_set_skill
    ADD COLUMN IF NOT EXISTS bot_id VARCHAR(100) NULL;
UPDATE ac_skill_set_skill AS relation
JOIN ac_skill_set AS skill_set
 ON skill_set.id = relation.skill_set_id
 AND skill_set.avernet_tenant = relation.avernet_tenant
 AND skill_set.env = relation.env
SET relation.bot_id = skill_set.bolt_id
WHERE relation.bot_id IS NULL
  AND skill_set.is_default = 0;
CREATE UNIQUE INDEX IF NOT EXISTS uk_bot_skill_set_skill
    ON ac_skill_set_skill (avernet_tenant, env, bot_id, skill_id);

-- MCP uses the same active-only Installation and ordinary-SkillSet ownership
-- semantics as Skill.  The existing association rows remain compatible:
-- historical and System Default rows retain NULL bot_id.
CREATE TABLE IF NOT EXISTS ac_bot_mcp_installation (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    avernet_tenant VARCHAR(64) NOT NULL DEFAULT 'teamclaw',
    env VARCHAR(50) NOT NULL,
    bot_id VARCHAR(100) NOT NULL,
    server_code VARCHAR(256) NOT NULL,
    gmt_created DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    gmt_modified DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_bot_mcp_installation
      (avernet_tenant, env, bot_id, server_code)
);

ALTER TABLE ac_skill_set_mcp
    ADD COLUMN IF NOT EXISTS bot_id VARCHAR(100) NULL;
UPDATE ac_skill_set_mcp AS relation
JOIN ac_skill_set AS skill_set
 ON skill_set.id = relation.skill_set_id
 AND skill_set.avernet_tenant = relation.avernet_tenant
 AND skill_set.env = relation.env
SET relation.bot_id = skill_set.bolt_id
WHERE relation.bot_id IS NULL
  AND skill_set.is_default = 0;
CREATE UNIQUE INDEX IF NOT EXISTS uk_skill_set_mcp
    ON ac_skill_set_mcp (avernet_tenant, env, skill_set_id, server_code);
CREATE UNIQUE INDEX IF NOT EXISTS uk_bot_skill_set_mcp
    ON ac_skill_set_mcp (avernet_tenant, env, bot_id, server_code);
