-- Migration: avernet_application adopts API-key credentials.
--
-- `001_init_schema.sql` is `CREATE TABLE IF NOT EXISTS`, so it is a no-op on a
-- database that already has these tables — editing it does not migrate anyone.
-- This file carries the change for deployments created before the API-key
-- scheme. Apply it BEFORE deploying the gateway that reads these columns:
-- without it, `POST /admin/apps` fails on `token`'s NOT NULL constraint and
-- every app authentication fails on the missing `api_key_prefix` column.
--
-- ONE statement, deliberately. DDL auto-commits per statement, so splitting this
-- across several ALTERs risks a partially migrated, non-rerunnable schema if a
-- later one fails. MySQL 8's DDL is atomic per statement, so as a single ALTER
-- this either lands completely or not at all.
--
-- Re-running it errors with ER_DUP_FIELDNAME (1060) rather than silently doing
-- nothing — the intended behavior for a one-shot migration.
--
-- Why the token index is replaced rather than left alone: `token` must become
-- nullable, and MySQL's MODIFY restates a column's entire definition (attributes
-- omitted are reset to the table default, not preserved), so the column is
-- rebuilt either way — and with it any index over it. `uk_avernet_application_token`
-- spans the whole varchar(1024); at utf8mb4 that is a 4096-byte key, past
-- InnoDB's 3072-byte limit, so the rebuild would fail. It is therefore dropped
-- and re-added over a 700-character prefix (2800 bytes). Uniqueness is
-- unaffected in practice: gateway-issued app tokens are ~261 characters, so the
-- prefix spans the entire value. The index is renamed so that the drop and the
-- add cannot collide on one name within a single statement.

ALTER TABLE `avernet_application`
  ADD COLUMN `api_key_hash` varchar(256) DEFAULT NULL
    COMMENT 'API Key 哈希(PBKDF2-SHA256，格式 base64(salt):base64(dk))' AFTER `app_type`,
  -- CHARACTER SET stated explicitly, not inherited: a table created before
  -- utf8mb4 was the default would reject a bare `COLLATE utf8mb4_bin`.
  --
  -- utf8mb4_bin because the server default collation is case-insensitive, which
  -- would make two prefixes differing only in case collide on the unique index
  -- below and let a lookup match the wrong row — whose hash then fails, locking
  -- the real app out. It also cuts effective prefix entropy from 62^8 to 36^8.
  ADD COLUMN `api_key_prefix` varchar(8) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin
    DEFAULT NULL COMMENT 'API Key 前 8 位，查找键(哈希加盐，无法按哈希查找)' AFTER `api_key_hash`,
  ADD UNIQUE KEY `uk_avernet_application_api_key_prefix` (`api_key_prefix`),
  -- Newly registered apps carry no `token`, so the column must accept NULL. It
  -- is kept (not dropped) so pre-existing JWT holders keep authenticating; drop
  -- it, with its index, once the deprecation warning has gone quiet.
  DROP INDEX `uk_avernet_application_token`,
  MODIFY COLUMN `token` varchar(1024) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin
    DEFAULT NULL
    COMMENT '[废弃] 旧版应用令牌(明文签名 JWT)，过渡期精确匹配查找键；待废弃日志静默后随查找路径一并删除',
  ADD UNIQUE KEY `uk_avernet_application_token_prefix` (`token`(700));
