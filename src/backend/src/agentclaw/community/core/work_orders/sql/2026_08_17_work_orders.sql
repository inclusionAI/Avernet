-- Unified work orders and recipient-scoped notifications.
CREATE TABLE IF NOT EXISTS ac_work_order (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    work_order_no VARCHAR(64) NOT NULL,
    biz_type VARCHAR(64) NOT NULL COMMENT 'SPACE_JOIN',
    biz_id VARCHAR(128) NOT NULL,
    applicant_user_id VARCHAR(256) NOT NULL,
    apply_reason VARCHAR(512) NULL,
    status VARCHAR(32) NOT NULL COMMENT 'PENDING | APPROVED | REJECTED',
    reviewer_user_id VARCHAR(256) NULL,
    review_remark VARCHAR(512) NULL,
    reviewed_at DATETIME NULL,
    env VARCHAR(20) NOT NULL,
    avernet_tenant VARCHAR(64) NOT NULL DEFAULT 'teamclaw',
    gmt_created DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    gmt_modified DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_work_order_no_env (avernet_tenant, work_order_no, env),
    KEY idx_work_order_applicant_status_env (avernet_tenant, applicant_user_id, status, env),
    KEY idx_work_order_reviewer_status_env (avernet_tenant, reviewer_user_id, status, env),
    KEY idx_work_order_biz_status_env (avernet_tenant, biz_type, biz_id, status, env)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS ac_work_order_notification (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    work_order_id BIGINT UNSIGNED NULL,
    recipient_user_id VARCHAR(256) NOT NULL,
    notification_category VARCHAR(32) NOT NULL COMMENT 'APPROVAL | NOTICE',
    event_type VARCHAR(64) NOT NULL,
    biz_type VARCHAR(64) NOT NULL,
    biz_id VARCHAR(128) NOT NULL,
    title VARCHAR(256) NOT NULL,
    content TEXT NULL,
    is_read TINYINT(1) NOT NULL DEFAULT 0,
    read_at DATETIME NULL,
    env VARCHAR(20) NOT NULL,
    avernet_tenant VARCHAR(64) NOT NULL DEFAULT 'teamclaw',
    gmt_created DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    gmt_modified DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_work_order_notification_recipient_read (avernet_tenant, recipient_user_id, is_read, env, gmt_modified),
    KEY idx_work_order_notification_work_order (avernet_tenant, work_order_id, recipient_user_id, env),
    KEY idx_work_order_notification_biz (avernet_tenant, biz_type, biz_id, recipient_user_id, env)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Unified approval extension (existing environments may apply these statements separately).
ALTER TABLE ac_work_order
    ADD COLUMN biz_data LONGTEXT NULL COMMENT '业务扩展数据，JSON格式，由业务模块传入';

CREATE TABLE IF NOT EXISTS ac_work_order_approver (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    work_order_id BIGINT UNSIGNED NOT NULL,
    approver_user_id VARCHAR(256) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
    review_remark VARCHAR(512) NULL,
    reviewed_at DATETIME NULL,
    env VARCHAR(20) NOT NULL,
    gmt_created DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    gmt_modified DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_work_order_approver (work_order_id, approver_user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
