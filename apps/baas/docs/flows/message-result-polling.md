# Open API message result polling

`GET /openapi/v1/messages/{message_id}` looks up the BaaS run created by
message delivery. The [polling handler](../../src/secbaas/community/adapters/web/routers/open_api/message_router.py)
checks the API key, bot policy, and stored key prefix before returning run data.
An absent run still produces HTTP 404; a key-prefix mismatch still produces a
business error with `data: null`.

When `data` is present, `data.error_code` is always included by the new
polling handler:

| Run status | `data.error_code` |
| --- | --- |
| `FAILED` with a stored code | The original string, without conversion or trimming (for example, `"007"`). |
| `FAILED` without a code (including historical runs) | `"DEFAULT"`. |
| Any other status, including `TIME_OUT` and `ABORTED` | `null`. |

The existing `status` and `error` fields keep their meanings. The field does
not change Gateway polling, POST delivery, SSE, or completion callbacks.

## BaaS persistence and producer boundary

The [run repository](../../src/secbaas/community/core/repository/bot_run/_protocol.py)
can write `FAILED`, `error`, and an optional `error_code` together while the run
is active. Only a trusted producer that has verified the code belongs to the
same attempt may supply it. Blank, malformed or invalid UTF-8 values and values
over 65,535 UTF-8 bytes are discarded without losing the failure or its error
text. `DEFAULT` is a response fallback, not a database default or backfill.
Existing execution paths do not pass a code; this change does not add an engine
code producer.

## Schema rollout and rollback

1. On an existing MySQL/MariaDB database, check `baas_bot_run.error_code` in
   `INFORMATION_SCHEMA.COLUMNS`. Apply the [one-time expansion](../../sqls/migrate_baas_bot_run_error_code.sql)
   if the column is absent, then verify it is nullable `TEXT` with character
   set `utf8mb4` before starting any new BaaS replica. If it exists with the
   expected definition, do not rerun the expansion. Stop and investigate any
   incompatible definition. For new databases, use the [current schema](../../sqls/baas_schema.sql)
   or current ORM auto-creation. If a deployment uses an older external schema
   snapshot (including a simulator storage init with `create_schema: false`),
   run the same check and, if needed, expand **after** schema initialization
   but **before** BaaS starts, even on a fresh database.
2. SQLite with automatic schema management upgrades existing files at startup.
   If schema management is disabled, check `PRAGMA table_info(baas_bot_run)` and
   run `ALTER TABLE baas_bot_run ADD COLUMN error_code TEXT;` once before
   starting the new binary if the column is absent.
3. Consumers must accept an omitted key while old polling replicas are serving
   traffic, including during rollout or binary rollback. Roll back the binary
   without dropping the nullable column. Before enabling any original-code
   producer in a later release, drain legacy writers that could reopen a run
   without clearing a previously stored code.

The [polling tests](../../tests/unit/adapters/web/open_api/test_message_polling_contract.py)
cover the JSON and OpenAPI contracts, authorization, and unchanged Gateway and
POST responses. The [SQLite upgrade test](../../tests/unit/plugins/database/sqlite/test_sqlite_orm.py)
covers persistent old data and repeat startup.
