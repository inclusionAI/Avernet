-- Group Card templates only; composition, version and revision are assembled on read.
CREATE TABLE IF NOT EXISTS bcs_group_agent_cards (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    env VARCHAR(64) NOT NULL,
    group_id VARCHAR(64) NOT NULL,
    card_template LONGTEXT NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_group_agent_card (env, group_id)
) DEFAULT CHARSET = utf8mb4;
