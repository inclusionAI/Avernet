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

-- Operational migration evidence only; this is not desired state. Each run
-- records exactly the rows it created so rollback never broad-deletes current
-- Installation state.
CREATE TABLE IF NOT EXISTS ac_bot_skill_installation_backfill_audit (
    run_id CHAR(36) NOT NULL,
    avernet_tenant VARCHAR(64) NOT NULL,
    env VARCHAR(20) NOT NULL,
    owner_id VARCHAR(128) NOT NULL,
    bot_id VARCHAR(100) NOT NULL,
    skill_id BIGINT UNSIGNED NOT NULL,
    gmt_created DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (run_id, avernet_tenant, env, owner_id, bot_id, skill_id),
    KEY idx_bot_skill_installation_backfill_audit_run (run_id)
);

-- One immutable rollout summary per tenant/environment. It archives the
-- observed legacy population and the apply result; it is not desired state.
CREATE TABLE IF NOT EXISTS ac_bot_skill_installation_backfill_run_audit (
    run_id CHAR(36) NOT NULL,
    avernet_tenant VARCHAR(64) NOT NULL,
    env VARCHAR(20) NOT NULL,
    legacy_active_local BIGINT UNSIGNED NOT NULL,
    live_exact_bot_candidates BIGINT UNSIGNED NOT NULL,
    ambiguous_live_bot_candidates BIGINT UNSIGNED NOT NULL,
    inserted_installations BIGINT UNSIGNED NOT NULL DEFAULT 0,
    missing_installations BIGINT UNSIGNED NOT NULL DEFAULT 0,
    gmt_created DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (run_id, avernet_tenant, env)
);
