-- Open aggregate groups for recipient-scoped work-order notices.
CREATE TABLE IF NOT EXISTS ac_work_order_notification_group (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    recipient_user_id VARCHAR(256) NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    group_id VARCHAR(128) NOT NULL,
    notification_id BIGINT UNSIGNED NOT NULL,
    env VARCHAR(20) NOT NULL,
    avernet_tenant VARCHAR(64) NOT NULL DEFAULT 'teamclaw',
    gmt_created DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    gmt_modified DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_work_order_notification_group
        (avernet_tenant, recipient_user_id, event_type, group_id, env),
    KEY idx_work_order_notification_group_notice
        (avernet_tenant, notification_id, env)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
