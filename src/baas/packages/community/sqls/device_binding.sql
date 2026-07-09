-- 设备绑定表
-- 对应实体: ac_entity_device_binding

CREATE TABLE IF NOT EXISTS ac_entity_device_binding (
    id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
    entity_id VARCHAR(64) NOT NULL COMMENT '实体ID(用户工号或团队ID)',
    entity_type VARCHAR(32) NOT NULL COMMENT '实体类型(staff/team/proj)',
    device_id VARCHAR(64) NOT NULL COMMENT '设备ID',
    device_provider VARCHAR(32) NOT NULL COMMENT '设备提供方',
    env VARCHAR(16) NOT NULL DEFAULT 'dev' COMMENT '环境标识: dev/pre/prod',
    device_props JSON NOT NULL COMMENT '设备属性(JSON格式)',
    status VARCHAR(16) NOT NULL COMMENT '状态: PENDING/ACTIVE/RELEASED',
    apply_reason VARCHAR(256) NULL COMMENT '申请原因',
    applied_by VARCHAR(64) NOT NULL COMMENT '申请人',
    release_reason VARCHAR(256) NULL COMMENT '释放原因',
    released_by VARCHAR(64) NULL COMMENT '释放人',
    released_at DATETIME NULL COMMENT '释放时间',
    last_alive_at DATETIME NULL COMMENT '最后活跃时间',
    gmt_create DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    gmt_modified DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
    INDEX idx_entity_status (entity_type, entity_id, status) COMMENT '实体状态索引',
    INDEX idx_env_entity_status (env, entity_type, entity_id, status) COMMENT '环境实体状态索引',
    INDEX idx_device_id (device_id) COMMENT '设备ID索引'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='设备绑定表';
