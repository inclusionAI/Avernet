-- BBS context collaboration protocol phase 1: minimal aggregate + append-only event DAG.
-- Production dialect is OceanBase MySQL mode (GLOBAL keys span partitions); tests use SQLite tables built by ORM create_all, which mirrors the mediumtext JSON columns as TEXT.

-- OceanBase / MySQL mode. SQLite tests mirror the JSON columns as TEXT.

CREATE TABLE `ac_context_collab_collet` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `avernet_tenant` varchar(64) NOT NULL DEFAULT 'teamclaw',
  `env` varchar(20) NOT NULL,
  `collab_context_id` varchar(128) NOT NULL,
  `context_spec` mediumtext NOT NULL COMMENT 'JSON: create request references and method fields',
  `current_snapshot` mediumtext NOT NULL COMMENT 'JSON: current objective, status, executor and progress',
  `current_event_id` varchar(128) DEFAULT NULL,
  `context_version` bigint(20) NOT NULL DEFAULT 1,
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_context_collab_collet_context` (`avernet_tenant`, `env`, `collab_context_id`) GLOBAL,
  KEY `idx_context_collab_collet_modified` (`avernet_tenant`, `env`, `gmt_modified`) GLOBAL
) AUTO_INCREMENT_MODE = 'ORDER' DEFAULT CHARSET = utf8mb4
  COMMENT = 'Minimal context collaboration aggregate snapshot';

CREATE TABLE `ac_context_collab_event` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `avernet_tenant` varchar(64) NOT NULL DEFAULT 'teamclaw',
  `env` varchar(20) NOT NULL,
  `collab_context_id` varchar(128) NOT NULL,
  `event_id` varchar(128) NOT NULL,
  `request_id` varchar(256) NOT NULL,
  `sequence_no` bigint(20) NOT NULL,
  `parent_event` varchar(128) DEFAULT NULL,
  `is_trunk` tinyint(1) NOT NULL DEFAULT 0,
  `event_kind` varchar(128) NOT NULL,
  `primitive_type` varchar(64) NOT NULL,
  `actor_ref` mediumtext NOT NULL COMMENT 'JSON actor reference',
  `visibility` varchar(32) NOT NULL,
  `payload` mediumtext NOT NULL COMMENT 'JSON event payload; user interaction uses payload.content',
  `context_version_after` bigint(20) NOT NULL,
  `occurred_at` datetime NOT NULL,
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_context_collab_event` (`avernet_tenant`, `env`, `collab_context_id`, `event_id`) GLOBAL,
  UNIQUE KEY `uk_context_collab_event_request` (`avernet_tenant`, `env`, `collab_context_id`, `request_id`) GLOBAL,
  UNIQUE KEY `uk_context_collab_event_seq` (`avernet_tenant`, `env`, `collab_context_id`, `sequence_no`) GLOBAL,
  KEY `idx_context_collab_event_parent` (`avernet_tenant`, `env`, `collab_context_id`, `parent_event`, `sequence_no`) GLOBAL,
  KEY `idx_context_collab_event_trunk` (`avernet_tenant`, `env`, `collab_context_id`, `is_trunk`, `sequence_no`) GLOBAL
) AUTO_INCREMENT_MODE = 'ORDER' DEFAULT CHARSET = utf8mb4
  COMMENT = 'Minimal append-only context collaboration event DAG';
