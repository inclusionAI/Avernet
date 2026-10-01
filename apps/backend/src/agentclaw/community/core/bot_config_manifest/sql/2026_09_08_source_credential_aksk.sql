-- AK/SK for tenant source credentials — the ``oss_aksk`` mechanism becoming real.
--
-- Additive and nullable, deliberately with no backfill: every existing row is a
-- ``header`` credential, for which both columns are meaningless. A NOT NULL
-- column with a default would put a value on those rows that reads like
-- configuration and governs nothing — the failure mode this whole change set
-- exists to remove.
--
-- WHY A SECOND VALUE AT ALL. Every mechanism before this one presented a single
-- opaque secret in a header, so one ciphertext column was the whole model.
-- Request signing is not a presentation of a secret: the key id travels in
-- clear on every request (it is *in* the Authorization header the signer
-- builds) while the secret key never leaves the platform and is used only to
-- compute a signature. They are two different things with two different
-- exposures, so they get two columns rather than one packed one — a packed
-- pair would have to be split by string surgery at every read, and read-back
-- would have to redact half a value.
--
-- ``access_key_id`` IS READABLE BACK, and that is not a relaxation of the
-- never-readable rule. It is an identifier, not a secret: rotation is
-- impossible to operate without being able to see which key id is currently
-- installed. ``secret_ciphertext`` keeps its existing contract — no
-- representation in any response, log, or apply report, ever.
--
-- ``region`` is signing input, not a secret and not a location the platform
-- browses: SigV4 binds a signature to a region string, and an object store
-- that is not AWS still has one (MinIO accepts any; most S3-compatible stores
-- name theirs). NULL means the signer's default.
--
-- DIALECT: OCEANBASE, MYSQL MODE — see 2026_08_31_source_credential.sql for the
-- conventions this file follows (GLOBAL indexes, TIMESTAMP vs DATETIME, no
-- ENGINE clause). Neither column is indexed: both are read only after a row
-- has already been found by ``(avernet_tenant, name)``.

ALTER TABLE `ac_source_credential`
  ADD COLUMN `access_key_id` varchar(256) NULL COMMENT 'oss_aksk：访问密钥 ID（标识符，非密钥；可回读）'
    AFTER `header_name`,
  ADD COLUMN `region` varchar(64) NULL COMMENT 'oss_aksk：签名区域（SigV4 输入；NULL 取签名器默认）'
    AFTER `access_key_id`;
