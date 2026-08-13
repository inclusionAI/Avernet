-- Migration: avernet_application adopts API-key credentials.
--
-- `001_init_schema.sql` is `CREATE TABLE IF NOT EXISTS`, so it is a no-op on a
-- database that already has these tables — editing it does not migrate anyone.
-- This file carries the change for deployments created before the API-key
-- scheme. Apply it BEFORE deploying the gateway that reads these columns:
-- without it, `POST /admin/apps` fails on `token`'s NOT NULL constraint and
-- every app authentication fails on the missing `api_key_prefix` column.
--
-- Additive: no column is dropped, and the only relaxation is `token` becoming
-- nullable, so existing rows keep their token and keep authenticating through
-- the deprecated exact-match path for the whole transition window.
--
-- MySQL has no `ADD COLUMN IF NOT EXISTS`, so re-running this errors with
-- ER_DUP_FIELDNAME (1060) rather than silently doing nothing. That is the
-- intended behavior for a one-shot migration.

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
  ADD UNIQUE KEY `uk_avernet_application_api_key_prefix` (`api_key_prefix`);

-- Newly registered apps carry no `token`, so the column must accept NULL. It is
-- kept (not dropped) so pre-existing JWT holders keep authenticating; drop it,
-- with its unique index, once the deprecation warning has gone quiet.
--
-- BEFORE APPLYING, CHECK THIS STATEMENT AGAINST THE DEPLOYED SCHEMA.
-- MySQL's MODIFY restates a column's whole definition: attributes left out are
-- reset to the table default rather than preserved, so the charset/collation
-- must be spelled out to make this a pure nullability change. Spelling them out
-- rebuilds `uk_avernet_application_token`, and that index is over a
-- varchar(1024) utf8mb4 column — 4096 bytes, past InnoDB's 3072-byte key limit.
-- Whether the rebuild succeeds depends on how the deployed column was actually
-- created (see the KNOWN ISSUE note in `001_init_schema.sql`). DDL auto-commits
-- per statement, so if it fails the ALTER above has already landed and this file
-- is not re-runnable — verify, or split the two statements across two windows.
--
-- utf8mb4_bin matches `001` and makes the legacy exact-match lookup genuinely
-- exact. If the deployed column is currently case-insensitive this tightens it;
-- that only affects a holder presenting a case-variant of their own token.
ALTER TABLE `avernet_application`
  MODIFY COLUMN `token` varchar(1024) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin
    DEFAULT NULL
    COMMENT '[废弃] 旧版应用令牌(明文签名 JWT)，过渡期精确匹配查找键；待废弃日志静默后随查找路径一并删除';
