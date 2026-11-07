-- 落地说明:见 spec 2026-11-02-task-config §DDL;ORM 在 core/task/task_config/orm.py,两套保持平行。
-- task_config: 按 (config_name, env) 维护的通用类型化配置存储。
-- config_type ∈ single_string|bool|set,决定 config_value(JSON 文本)的解释与匹配方式;
-- 详见 spec 2026-11-02-task-config。env 由后端按当前部署上下文填充,不入接口参数,
-- 列级无 DEFAULT(路由恒显式传 env);ORM 侧以 callable get_current_env 作回退兜底。
-- config_value 取 TEXT(64KB):set 元素为 bot_id 列表、其余为单值 JSON,均远小于 64KB,
-- 不必上 mediumtext。config_type 无 DB 枚举约束(governed 枚举在写入/匹配路径做校验)。
-- 无外键(task 域表间不建 FK,与 task_artifact / task_trajectory 同款)。
-- 唯一键 (config_name, env) 支撑 upsert 的 ON DUPLICATE KEY UPDATE /
-- ON CONFLICT(config_name, env) DO UPDATE;idx_task_config_env 支撑按环境过滤。
CREATE TABLE IF NOT EXISTS `task_config` (
    `id`           bigint(20)    NOT NULL AUTO_INCREMENT                       COMMENT '主键ID',
    `config_name`  varchar(128)  NOT NULL                                      COMMENT '配置名(自由字符串,按需创建;未做预定义清单枚举)',
    `config_type`  varchar(32)   NOT NULL                                      COMMENT '配置类型 single_string|bool|set,决定 config_value 解释与匹配方式(governed 枚举,DB 不约束)',
    `config_value` text          NOT NULL                                      COMMENT '配置值(JSON 文本):single_string=字符串,bool=布尔,set=字符串元素数组;TEXT 家族禁显式 DEFAULT',
    `env`          varchar(32)   NOT NULL                                      COMMENT '数据环境 dev|pre|prod(后端按部署上下文填,不入接口参数;ORM callable 兜底 get_current_env)',
    `creator`      varchar(128)  NULL                                         COMMENT '创建者(operatorName 或 staffId;首次写入填,upsert 不覆盖;NULL=未记录)',
    `modifier`     varchar(128)  NULL                                         COMMENT '修改者(每次写入刷新含首次;NULL=未记录)',
    `gmt_create`   timestamp     NOT NULL DEFAULT CURRENT_TIMESTAMP            COMMENT '创建时间(首次写入设,upsert 不覆盖)',
    `gmt_modify`   timestamp     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后修改时间(每次写入刷新)',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_task_config_name_env` (`config_name`, `env`),
    KEY `idx_task_config_env` (`env`)
) DEFAULT CHARSET = utf8mb4 COMMENT='通用类型化配置存储(spec 2026-11-02-task-config)';
