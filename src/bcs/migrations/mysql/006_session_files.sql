-- Session workspace files: per-session shared file metadata. The byte payload
-- lives in a StoragePlugin backend (local fs / baas / OSS); BCS DB is the only
-- authoritative source for list/metadata. object_handle is the serialized
-- UploadHandle (Pending) / StorageHandle (Ready) — opaque, never exposed to
-- clients. sha256 is NULL in v1 (integrity stub).
CREATE TABLE IF NOT EXISTS `bcs_session_files` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `env` varchar(32) NOT NULL,
  `file_id` varchar(32) NOT NULL,
  `session_id` varchar(64) NOT NULL,
  `owner_actor_kind` varchar(16) NOT NULL,
  `owner_actor_id` varchar(256) NOT NULL,
  `file_name` varchar(512) NOT NULL,
  `mime_type` varchar(256) NOT NULL,
  `size` bigint(20) unsigned NOT NULL,
  `sha256` char(64) DEFAULT NULL,
  `storage_backend` varchar(32) NOT NULL,
  `object_handle` text NOT NULL,
  `status` varchar(16) NOT NULL,
  `created_at` bigint(20) unsigned NOT NULL,
  `updated_at` bigint(20) unsigned NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_session_file` (`env`, `session_id`, `file_id`),
  KEY `idx_session_files_session` (`env`, `session_id`, `created_at`)
) DEFAULT CHARSET = utf8mb4;