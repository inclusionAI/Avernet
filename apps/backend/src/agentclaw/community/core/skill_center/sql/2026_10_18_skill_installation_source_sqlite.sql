-- Deploy BEFORE Backend code that reads installation provenance.
-- Existing installations remain UNKNOWN: intentionally no historical backfill.
-- Removing a Default member/Set must not cascade into this table.
CREATE TABLE IF NOT EXISTS ac_bot_skill_installation_source (
    installation_id BIGINT NOT NULL,
    source_kind VARCHAR(16) NOT NULL,
    source_set_id BIGINT NULL,
    avernet_tenant VARCHAR(64) NOT NULL DEFAULT 'teamclaw',
    PRIMARY KEY (installation_id),
    CONSTRAINT fk_skill_installation_source_installation
        FOREIGN KEY (installation_id) REFERENCES ac_bot_skill_installation(id)
        ON DELETE CASCADE
);
-- Rollback: retain this additive table when rolling back application code.
-- A later schema rollback must first stop all provenance-aware writers.
