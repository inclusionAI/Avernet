-- 011_edge_permission.sql — 08-12 A2A edge-permission tables (friend unification).
-- Spec: src/bcs/docs/superpowers/specs/2026-08-18-friend-edge-permission-reform.md §3.1.
-- Applied externally (ops/CI); the bcs binary runs only SQLite migrations.
--
-- 5 张边权限表（edge_grants / permission_profiles / permission_requests /
-- capabilities / authz_decision_logs）+ bcs_bots 两列。`edge_grants` 是好友关系
-- 的唯一事实源（SoR）。建表约定：每表带 gmt_create/gmt_modified 审计列；JSON 语义
-- 列用 TEXT 存字符串；不引入与 gmt_* 重复的 created_at/updated_at。

-- === edge_grants ============================================================
-- 定向授权边 A→B：「BCS 已批准 A 使用 B」。好友关系唯一事实源：一条 friend 边 =
-- grant_kind=permission_profile 且 grant_ref_id == B 的默认 profile id（D12）。
-- 同一 (A→B) 可携带多条边（默认 + writer + 内联 rules）。
CREATE TABLE IF NOT EXISTS `edge_grants` (
  `edge_id`                VARCHAR(48)  NOT NULL COMMENT 'PK；opaque id（eg_<md5>）',
  `env`                    VARCHAR(16)  NOT NULL COMMENT '环境标签（仅标记写入，不参与查询隔离，§3.1）',
  `from_id`                VARCHAR(256) NOT NULL COMMENT '授权方 actor id（A）：human_<工号> 或 bot uuid',
  `to_id`                  VARCHAR(256) NOT NULL COMMENT '被授权目标（B）：bot uuid',
  `grant_kind`             VARCHAR(16)  NOT NULL COMMENT 'permission_profile | rules',
  `grant_ref_id`           VARCHAR(128) NOT NULL COMMENT 'permission_profile -> 目标 profile id；rules -> 不透明 ref',
  `rules`                  TEXT         DEFAULT NULL COMMENT '内联规则；grant_kind=rules 时非空（JSON 字符串）',
  `status`                 VARCHAR(16)  NOT NULL DEFAULT 'approved' COMMENT 'approved（生效）| revoked（撤回，不再授权）',
  `originator_policy_type` VARCHAR(16)  NOT NULL DEFAULT 'any' COMMENT 'any | same_as_from | specific | owner（friend 边恒为 any，D7）',
  `originator_policy_data` TEXT         DEFAULT NULL COMMENT 'policy_type=specific 时的发起方集合（JSON 字符串）',
  `gmt_create`             timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified`           timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`edge_id`),
  UNIQUE KEY `uk_edge_from_to_env_ref` (`from_id`, `to_id`, `env`, `grant_ref_id`) COMMENT '每个 (A,B,env,ref) 至多一行',
  KEY `idx_edge_from_env_status` (`from_id`, `env`, `status`) COMMENT 'list_friends / 出边扫描',
  KEY `idx_edge_to_env_status` (`to_id`, `env`, `status`) COMMENT 'admission / 入边扫描'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- === permission_profiles ====================================================
-- 打包的权限模板（角色：default/reader/writer/maintainer）。每个 bot 在 onboard 时
-- 恰好 seed 一条 `default` profile（wildcard-allow，§5.1.1）。revision/digest 随
-- upsert 递增，profile_id 不变（D12 rule 2：不覆盖、不 bump 既有 default）。
CREATE TABLE IF NOT EXISTS `permission_profiles` (
  `permission_profile_id` VARCHAR(48)  NOT NULL COMMENT 'PK；pp_<bot_uuid>_default 为默认 profile 约定',
  `bot_id`                VARCHAR(256) NOT NULL COMMENT '所属 bot uuid（被授权方的默认权限）',
  `env`                   VARCHAR(16)  NOT NULL COMMENT '环境标签',
  `name`                  VARCHAR(64)  NOT NULL DEFAULT 'default' COMMENT '角色名：default | reader | writer | maintainer',
  `description`           VARCHAR(512) DEFAULT NULL COMMENT '人类可读说明',
  `rules_template`        TEXT         NOT NULL COMMENT '规则模板（JSON 字符串，NOT NULL）',
  `revision`              BIGINT       NOT NULL DEFAULT 1 COMMENT '版本号，每次 upsert +1',
  `digest`                VARCHAR(128) NOT NULL COMMENT 'rules_template 的 sha256，用于幂等/比对',
  `is_default`            TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '1=该 bot 的默认 profile（friend 边引用它）',
  `status`                VARCHAR(16)  NOT NULL DEFAULT 'active' COMMENT 'active | deleted',
  `created_by`            VARCHAR(64)  NOT NULL COMMENT '创建者（默认 profile 为 system）',
  `updated_by`            VARCHAR(64)  DEFAULT NULL COMMENT '最近修订者（NULL 表示无人改过）',
  `gmt_create`            timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified`          timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`permission_profile_id`),
  UNIQUE KEY `uk_profile_bot_env_default` (`bot_id`, `env`, `is_default`, `status`) COMMENT '每 (bot,env) 至多一条 active default',
  KEY `idx_profile_bot_env` (`bot_id`, `env`, `status`) COMMENT '默认 profile 查找'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- === permission_requests ====================================================
-- connect/apply/revoke 请求记录。pending 时无 edge_id；approve 后回填 edge_id 并把
-- decided_* 置位。Bot↔Bot 单次 accept 会连带批准反向 pending（AC-20，§4.1）。
CREATE TABLE IF NOT EXISTS `permission_requests` (
  `request_id`        VARCHAR(48)  NOT NULL COMMENT 'PK；req_<md5>',
  `edge_id`           VARCHAR(48)  DEFAULT NULL COMMENT '批准后回填对应 edge_grants.edge_id；pending 时 NULL',
  `env`               VARCHAR(16)  NOT NULL COMMENT '环境标签',
  `from_id`           VARCHAR(256) NOT NULL COMMENT '发起方 actor id',
  `to_id`             VARCHAR(256) NOT NULL COMMENT '目标 actor id',
  `request_kind`      VARCHAR(16)  NOT NULL COMMENT 'connect | permission_profile | rules | revoke',
  `requested_ref_id`  VARCHAR(128) DEFAULT NULL COMMENT 'permission_profile/rules 请求的目标 ref；connect 时 NULL',
  `requested_rules`   TEXT         DEFAULT NULL COMMENT 'rules 请求带的内联规则（JSON 字符串）',
  `message`           TEXT         DEFAULT NULL COMMENT '发起方留言',
  `status`            VARCHAR(16)  NOT NULL DEFAULT 'pending' COMMENT 'pending | approved | rejected | cancelled',
  `decision_reason`   TEXT         DEFAULT NULL COMMENT '决定理由/说明',
  `created_by`        VARCHAR(64)  NOT NULL COMMENT '发起者标识',
  `decided_by`        VARCHAR(64)  DEFAULT NULL COMMENT '决定者；未决定时 NULL',
  `decided_at`        timestamp    NULL DEFAULT NULL COMMENT '决定时刻（DB 托管，CURRENT_TIMESTAMP 写入）；未决定时 NULL',
  `gmt_create`        timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified`      timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`request_id`),
  KEY `idx_req_to_env_status` (`to_id`, `env`, `status`) COMMENT '收件箱（received）扫描',
  KEY `idx_req_from_env_status` (`from_id`, `env`, `status`) COMMENT '发件箱（sent）扫描',
  KEY `idx_req_edge` (`edge_id`) COMMENT '按边反查请求'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- === capabilities ===========================================================
-- bot 暴露的工具/操作能力（catalog 行）。异步从 AgentCard/tool 注册表采集
-- （source=agent_card）；**非** 默认/friend 访问的前提（friend 边用 wildcard-allow
-- 默认 profile，与 capabilities 无关，§3.1）。
CREATE TABLE IF NOT EXISTS `capabilities` (
  `capability_id`     VARCHAR(48)  NOT NULL COMMENT 'PK',
  `bot_id`            VARCHAR(256) NOT NULL COMMENT '所属 bot uuid',
  `env`               VARCHAR(16)  NOT NULL COMMENT '环境标签',
  `tool`              VARCHAR(64)  NOT NULL COMMENT '工具名（如 bcs_fuse）',
  `operation`         VARCHAR(64)  DEFAULT NULL COMMENT '操作名（可选；为空表示整工具）',
  `specifier_schema`  TEXT         DEFAULT NULL COMMENT '参数/说明符 schema（JSON 字符串）',
  `source`            VARCHAR(16)  NOT NULL COMMENT 'system | agent_card | manual',
  `status`            VARCHAR(16)  NOT NULL DEFAULT 'active' COMMENT 'active | inactive',
  `raw_metadata`      TEXT         DEFAULT NULL COMMENT '原始元数据（JSON 字符串，透传）',
  `gmt_create`        timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified`      timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`capability_id`),
  KEY `idx_cap_bot_env` (`bot_id`, `env`, `status`) COMMENT '按 bot/env 列能力'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- === authz_decision_logs ====================================================
-- 准入决策审计日志（append-only）。记录某次 A→B 准入判定结果与命中的授权边，
-- 供运营排查与 shadow 比对（§8.5/§8.6）。不计入业务查询路径，仅写入。
CREATE TABLE IF NOT EXISTS `authz_decision_logs` (
  `decision_id`   VARCHAR(48)  NOT NULL COMMENT 'PK',
  `env`           VARCHAR(16)  NOT NULL COMMENT '环境标签',
  `task_id`       VARCHAR(128) DEFAULT NULL COMMENT '关联任务 id（可空）',
  `run_id`        VARCHAR(128) DEFAULT NULL COMMENT '关联 run id（可空）',
  `from_id`       VARCHAR(256) NOT NULL COMMENT '发起方 actor id',
  `to_id`         VARCHAR(256) NOT NULL COMMENT '目标 actor id',
  `originator`    VARCHAR(256) DEFAULT NULL COMMENT '实际发起方（originator_policy 校验用）',
  `context_type`  VARCHAR(16)  NOT NULL COMMENT '上下文类型（如 a2a_call）',
  `decision`      VARCHAR(16)  NOT NULL COMMENT 'allow | deny',
  `reason_code`   VARCHAR(64)  NOT NULL COMMENT '机器原因码（ok | public_default | no_edge | bot_hidden | bot_not_found…）',
  `grant_refs`    TEXT         NOT NULL COMMENT '命中的授权边引用（JSON 数组字符串，NOT NULL）',
  `context_json`  TEXT         DEFAULT NULL COMMENT '决策上下文快照（JSON 字符串）',
  `gmt_create`    timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified`  timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`decision_id`),
  KEY `idx_adl_env_from_to` (`env`, `from_id`, `to_id`) COMMENT '按对查决策历史'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- === bcs_bots（增量列）======================================================
-- 旧版人方向加好友开关 / 审批列已并入 bot_info 内部属性读取，不再在迁移里新增列。
