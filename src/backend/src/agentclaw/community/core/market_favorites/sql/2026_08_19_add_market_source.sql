-- ac_market_favorite 是新表且没有历史数据，无需回填。
ALTER TABLE ac_market_favorite
    ADD COLUMN market_source VARCHAR(32) NOT NULL
    COMMENT '市场来源: SKILLCENTER、TEAMCLAW'
    AFTER space_id;

ALTER TABLE ac_market_favorite
    DROP INDEX uk_space_target_env,
    ADD UNIQUE KEY uk_space_target_env (
        space_id,
        market_source,
        target_type,
        target_code,
        env
    );

ALTER TABLE ac_market_favorite
    DROP INDEX idx_space_env_modified,
    ADD KEY idx_space_env_modified (
        space_id,
        env,
        market_source,
        target_type,
        gmt_modified
    );
