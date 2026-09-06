-- Tenant source credentials (issue #1471, W3).
--
-- One row per (avernet_tenant, name): a named credential a manifest entry
-- references by name to have the platform present a header while fetching.
--
-- The key has no env axis on purpose. A credential is a tenant-level object
-- — one presentation token per content host. An env column would split
-- prod/pre rows under one name and silently answer "does this tenant share
-- its git token across environments?" wrongly; the tenant chooses, not the
-- schema. Rotation is a plain re-PUT of the same name (no apply triggered,
-- the fetched-effect is read per request hop), so upsert, never insert-only.
--
-- The secret column stores TokenVault output: ``enc:v1:<AES-GCM>`` when a
-- master key resolved, or plaintext under a non fail-closed profile with
-- none (singlebox/CI passthrough). The write side refuses the plaintext
-- case under fail-closed profiles — see the service's guard — so this
-- database never holds tenant tokens in the clear outside local runs.
--
-- The value never crosses back: GET surfaces has_secret/header_name/prefixes
-- only, and apply reports carry the *name*. Deletion-with-references policy
-- belongs to the apply layer, where the referencing entry fails next apply
-- with "credential X does not exist" — what storage guards instead is
-- ownership: this is an application-operated surface (the gateway requires
-- an app credential on every call), and rotation/delete are the creating
-- application's alone (owner_app_id, stamped at insert, never re-stamped).
-- Every application of the tenant may read the masked inventory: the name
-- is the shared reference namespace manifests cite.
-- DIALECT: OCEANBASE, MYSQL MODE. What this file writes follows the deployed
-- tables there rather than portable MySQL -- compare ``SHOW CREATE TABLE
-- ac_bots``, and the sibling DDL in core/caller_identity/sql/.
--
--   * EVERY INDEX IS DECLARED ``GLOBAL``. On a partitioned table an index
--     without it is partition-local, and a partition-local UNIQUE enforces
--     uniqueness only *within* a partition. These tables are not
--     partitioned today, so it is a no-op now and cheap insurance later: what
--     it prevents surfaces as duplicate rows, silently, never as an error.
--     SQLAlchemy cannot render the modifier, so this file is the only place it
--     can live.
--   * ``TIMESTAMP``, NOT ``DATETIME``, for gmt_* and the business timestamps,
--     matching ac_bots and every table added since. Mixing the two is what
--     skill_center/sql/2026_09_03_align_space_skill_timestamps_with_gmt.sql
--     had to repair: TIMESTAMP converts by session time zone and DATETIME does
--     not, so a session outside Asia/Shanghai reads the two as disagreeing by
--     the offset. The ORM keeps ``DateTime`` either way -- ac_skill_version's
--     published_at is the precedent for that pairing.
--   * NO ``ENGINE`` CLAUSE, and no BLOCK_SIZE / REPLICA_NUM / COMPRESSION /
--     TABLET_SIZE / PCTFREE / ROW_FORMAT. OceanBase applies its own defaults
--     and echoes them back from SHOW CREATE TABLE; writing them here would be
--     copying its own output back at it.

CREATE TABLE `ac_source_credential` (
  `id`            bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `avernet_tenant` varchar(64)  NOT NULL DEFAULT 'teamclaw' COMMENT '数据隔离租户',
  `name`          varchar(128) NOT NULL COMMENT '凭证名（自由标识符）',
  `credential_type` varchar(32) NOT NULL COMMENT '认证机制：v1 仅 header；oss_aksk/basic 为预留字，写入即拒',
  `header_name`   varchar(256) NOT NULL COMMENT '注入的请求头名',
  `allowed_prefixes` text       NOT NULL COMMENT 'JSON 数组：绝对 https 前缀（授权出示范围）',
  `secret_ciphertext` text     NOT NULL COMMENT 'enc:v1:<AES-GCM 密文>（fail-closed profile 下明文写入被拒绝）',
  `owner_app_id`  bigint(20) NOT NULL COMMENT '归属应用（创建者 app id）：轮换与删除仅限它；插入时钉死，不再改写',
  `modifier`      varchar(1024) NOT NULL DEFAULT '' COMMENT '审计：最后写入者（app:<id> / app:<id>:on-behalf-of:<user>）',
  `gmt_create`    timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified`  timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_tenant_source_credential_name`
    (`avernet_tenant`, `name`) GLOBAL
) DEFAULT CHARSET = utf8mb4 COMMENT = '租户级源凭证';
