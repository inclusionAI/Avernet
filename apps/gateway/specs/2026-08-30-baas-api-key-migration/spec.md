# secbaas API-key migration (self-service, key-authenticated)

- Date: 2026-08-30
- Status: implemented
- Related: `specs/2026-08-13-application-api-key-credentials/spec.md` (adopts the
  secbaas credential scheme so this migration is possible at all),
  `specs/2026-07-30-application-tenant-accesskey-schema/spec.md` (the
  `avernet_application` table), secbaas API-gateway key service
  (`src/baas/src/secbaas/community/core/service/api_gateway/`, the source
  records), backend bot→app grants
  (`src/backend/src/agentclaw/community/core/bot_app_grant/`, the destination for
  the authorizations a key carried)

## Summary

`POST /admin/apps/migrate-from-baas` moves one secbaas API key onto the gateway:
it copies the `baas_api_key` row into `avernet_application` and flattens the bot
authorizations that key carried into the backend's `ac_bot_app_grant`. The caller
keeps using the key they already hold — nothing is minted, nothing is returned,
and no rotation is forced.

The migration is **self-service and authenticated by the credential it moves**.
The caller presents their own plaintext key; it is migrated only if it verifies
against an ACTIVE `baas_api_key` row.

## Motivation

secbaas's OpenAPI surface is moving to the gateway. Its callers authenticate with
API keys the gateway does not have and cannot obtain: `baas_api_key` stores only
a salted one-way hash, and there is no mapping from a person to their keys that
an operator could drive a bulk migration from. A batch job would therefore have
to either invent new credentials for everyone — the rotation event this whole
workstream exists to avoid — or copy rows on behalf of people whose consent it
cannot check.

Possession of the key resolves both problems at once. It proves the caller is
entitled to the row, and it is the only proof anybody has. That is why this is an
endpoint rather than a script.

The copy works because `core/app/_key_gen.py` is a byte-identical copy of
secbaas's `APIKeyGenerator` (pinned by `tests/unit/plugins/test_app_key_gen.py`):
the stored hash records only its salt, so a hash moved between the tables keeps
verifying against the same plaintext.

## User Stories

- As a secbaas API-key holder, I want to move my key to the gateway myself and
  keep using it unchanged, so that migration costs me one request and no
  redeployment.
- As a key holder whose app was granted several bots, I want those bots to be
  reachable through the gateway immediately after migrating, so that I do not
  have to re-authorize each one.
- As a key holder, I want to choose my application's name, so that I am not stuck
  with whatever the secbaas record happened to call it.
- As a key holder, I want a name clash to tell me the name is taken and in which
  environment, so that I can retry with a different one instead of guessing.
- As a platform operator, I want a migration to be all-or-nothing, so that no
  caller is ever left with a credential that authenticates but authorizes
  nothing.

## Contract

**Request** — `POST /admin/apps/migrate-from-baas`

| Field | Type | Notes |
| --- | --- | --- |
| `api_key` | string, required | The caller's plaintext secbaas key. Never stored, logged, or echoed. |
| `app_name` | string, required, ≤256 | Caller's choice; unique within the migrated `env`. Need not match anything in secbaas. |

Both are surrounding-whitespace tolerant (pasted values) and are declared to the
request model with defaults rather than as required fields — deliberately.
FastAPI renders a "field required" error with `input` set to the **whole request
body**, which here holds the plaintext key, so a client that forgot `app_name`
would get its own credential quoted back in a 422. Emptiness and length are
checked past that point instead, and reported as `400`/`missing` and
`422`/`value_too_long` through the envelope below.

Everything else is derived from the source row. `env`, `app_type`, `owners` and
the audit columns are copied; `tenant` is not (see Decisions).

**Success** — `201`, carrying `id`, `app_name`, `app_type`, `owners`, `tenant`,
`env`, `api_key_prefix`, `source_baas_key_id`, `grants_created` and `grants`.
There is deliberately **no `api_key` field**: this endpoint mints nothing.

**Refusals** — the standard admin envelope
(`{"code": status*1000+subcode, "message": …, "data": …}`), with `data.outcome`
naming the case. Every refusal writes nothing.

| Outcome | Status | Meaning |
| --- | --- | --- |
| `key_not_found` | 404 | No ACTIVE `baas_api_key` matches. Covers "no such key" and "wrong key" identically. |
| `already_migrated` | 409 | This exact key already has an application row. Stop. |
| `app_name_taken` | 409 | `(app_name, env)` is claimed. Retry with another name. |
| `prefix_conflict` | 409 | Another app holds this key's prefix. Unrecoverable; reissue. |
| `wildcard_policy` | 422 | `allowed_bots: ["*"]` has no per-bot representation. |
| `invalid_grant_targets` | 422 | A bot reference is not `{bot_id}:{entity_id}`. |
| `unsupported_app_type` | 422 | `app_type` is neither `app` nor `bot`. |
| `value_too_long` | 422 | A copied value (or `app_name`) does not fit a destination column. |

A blank or absent field is `400` with `data.missing` naming it.

### Grant derivation

secbaas carries a key's bots in one of two places, and both resolve to
`{real_bot_id}:{entity_id}` references:

- `app_type=bot` — the key is a bot's own; `app_id` holds the single reference.
- `app_type=app` — the key is a third-party app's; `policy.allowed_bots` holds
  however many were granted to it, flattened one row per bot.

`entity_id` becomes both `user_id` and `owner_id`. secbaas could only express
"the bot's own owner authorized this" — its permission check was
`operator == entity_id` — so there is no second person to recover.

The policy column is read with exactly secbaas's semantics, all fail-closed:
`NULL`/blank/unparseable/non-object/missing key → no bots; the legacy `"NONE"`
sentinel is filtered; `"*"` wins outright.

## Decisions

**Refuse rather than approximate.** A wildcard policy, a malformed bot reference,
an unknown `app_type` and an over-long identity are all refused whole. The shared
reason: each alternative produces a credential that *looks* migrated while
reaching fewer bots than before, and the holder discovers that at some later,
unrelated moment. Under-granting silently is the one failure mode a credential
migration cannot have.

**One transaction.** The application row, every grant, and every log row are one
unit of work. Two commits would leave a window in which a live credential
authorizes nothing — indistinguishable, to its holder, from success — and there
is no compensating delete to fall back on, since nothing in the gateway deletes
app rows. This is why the write does not go through `AppRepository`, which
otherwise owns every `avernet_application` insert.

**Tenant is normalized to `teamclaw`, not copied.** secbaas's `tenant` defaults to
`team_claw` and the backend's grant tables default to `teamclaw`; they are not the
same namespace. A grant written under a tenant the backend's guard does not scope
to is invisible to every lookup that would authorize it — a silent authorization
failure. The source value is preserved on the application row's `config`.

**Provenance on `config`, not in the audit columns.** `creator`/`modifier` keep
secbaas's real people: who created a credential is a fact about the credential and
survives its move. `config.migrated_from` records the source row, including its
original `tenant`.

**The grant log is written too.** `ac_bot_app_grant_log` is read precisely when
the live row is gone. A migrated grant with no log entry is one whose provenance
can never be answered.

**Idempotency is reported, not silent.** Re-migrating a key is refused as
`already_migrated` rather than treated as success, and the two are told apart by
**hash equality** on the row holding the prefix — not by the prefix alone.
Reporting a genuine collision as an idempotent re-run would tell a caller their
key works when it in fact resolves to someone else's application.

## Schema change

`avernet_application` gains `UNIQUE KEY uk_avernet_application_app_name_env
(app_name, env)`, replacing the plain `idx_avernet_application_app_name` — the new
key's leading column serves every lookup the old index served. Applied to
deployed databases by `migrations/mysql/003_application_app_name_env_unique.sql`
and folded back into `001_init_schema.sql` for fresh ones.

It has to be a database constraint: two concurrent registrations both pass an
application-level "is this name free?" check and only the index stops the second.
`env` is in the key because one database backs several environments and the same
application legitimately exists in each.

The migration **fails loudly** (ER_DUP_ENTRY) if deployed rows already violate it;
the SQL file carries the query that finds the duplicates. Because the same key now
constrains `POST /admin/apps`, a name clash there is mapped to `409` rather than
being left to surface as a `500`.

**Environment is copied, not enforced.** secbaas's own validator pins its lookup
to the process's `env` so that a shared database cannot let one environment's key
authenticate in another. This endpoint does not, for two reasons: the gateway's
core layer has no injected notion of its own environment (`SERVER_ENV` is read
only in `config` / `bootstrap` / `plugins`), and the registry being migrated
*into* does not filter on `env` either — `AppRepository` resolves a credential by
prefix and status alone. Filtering here would narrow who may migrate without
narrowing who may authenticate, which is the wrong half of the pair. The source
row's `env` is copied faithfully regardless. Known consequence: a key holder may
migrate through any environment's gateway. Pin both sides together if the gateway
gains a configured environment.

## Cross-module writes

`baas_api_key` is read and `ac_bot_app_grant` / `ac_bot_app_grant_log` are written
across module boundaries — the first is secbaas's, the latter two are the
backend's. This is a **scoped, time-bounded exception**, taken because a
service-to-service API built for a one-shot backfill would outlive the backfill.
Its bounds:

- The mirrored models live in `core/baas_migration/_orm.py` and are excluded from
  the MariaDB plugin's `create_all` whitelist, so no deployment ever provisions a
  foreign table from them (the treatment `bcs_bots` already gets).
- They must track the owning modules' definitions column for column for as long
  as this package exists.
- No tenant guard is registered; the tenant is passed explicitly by the
  composition root instead.

## Out of scope

- Bulk or operator-driven migration. Nobody can enumerate a user's secbaas keys,
  which is the premise of the whole design.
- Admin authentication for `/admin`. These endpoints remain unauthenticated
  (single-box / dev), documented in `adapters/web/admin.py`; this endpoint's own
  authorization is the presented key.
- Deactivating or deleting the source `baas_api_key` row. Migration is a copy, so
  the key keeps working on both sides until secbaas retires it.
- Migrating anything other than the credential and its bot grants — rate limits,
  key names and descriptions are not carried over.

## Deletion criteria

This is temporary by construction. When the population of unmigrated
`baas_api_key` rows is empty, delete together: the endpoint and its request model,
`api/baas_migration/`, `core/baas_migration/`, and the tests named after them. The
`(app_name, env)` unique key and the `409` on `POST /admin/apps` stay — they are
improvements to the registry in their own right, not migration scaffolding.
