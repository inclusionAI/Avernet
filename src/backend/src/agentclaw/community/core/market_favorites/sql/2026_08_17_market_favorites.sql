CREATE TABLE ac_market_favorite (
    id BIGINT(20) UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '收藏主键ID',

    space_id BIGINT(20) UNSIGNED NOT NULL COMMENT '收藏所属空间，关联ac_space.id',
    market_source VARCHAR(32) NOT NULL COMMENT '市场来源: SKILLCENTER、TEAMCLAW',
    target_type VARCHAR(64) NOT NULL COMMENT '资源类型: SKILL、MCP',
    target_code VARCHAR(128) NOT NULL COMMENT 'SC SkillCode 或 ServerCode',

    created_by VARCHAR(64) NOT NULL COMMENT '收藏操作人工号，仅用于审计',
    env VARCHAR(20) DEFAULT NULL COMMENT '环境：pre、prod；与现有表保持一致',
    gmt_created TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',

    PRIMARY KEY (id),
    UNIQUE KEY uk_space_target_env (
        space_id, market_source, target_type, target_code, env
    ),
    KEY idx_space_env_modified (
        space_id, env, market_source, target_type, gmt_modified
    )
) COMMENT = '空间市场收藏表';
