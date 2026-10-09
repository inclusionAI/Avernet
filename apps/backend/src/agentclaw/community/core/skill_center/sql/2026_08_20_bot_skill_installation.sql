-- P1-01: active-only desired-state relationship for Bot Skill activation.
-- Rollback is additive: stop writers/readers first, then DROP TABLE only after
-- verifying no deployed binary still relies on Installation desired state.
CREATE TABLE IF NOT EXISTS ac_bot_skill_installation (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    avernet_tenant VARCHAR(64) NOT NULL DEFAULT 'teamclaw',
    env VARCHAR(20) NOT NULL,
    owner_id VARCHAR(128) NOT NULL,
    bot_id VARCHAR(100) NOT NULL,
    skill_id BIGINT UNSIGNED NOT NULL,
    gmt_created DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    gmt_modified DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_bot_skill_installation (avernet_tenant, env, owner_id, bot_id, skill_id),
    KEY idx_bot_skill_installation_bot (avernet_tenant, env, owner_id, bot_id),
    CONSTRAINT fk_bot_skill_installation_skill
      FOREIGN KEY (skill_id) REFERENCES ac_skill(id)
);
