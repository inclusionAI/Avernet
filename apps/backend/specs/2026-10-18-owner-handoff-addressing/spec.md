# Backend Bot stable addressing, B0-B3

## Scope

This stage prepares Backend resource addressing for a future service-Bot owner
handoff. It does not expose an owner-transfer operation and does not change
Frontend, OpenAPI, SecBaaS, or public response fields.

The stable Bot address is `tenant + env + bot_id + entity_id`; internal tables
that already carry `bot_pk` use that primary key. A legacy resource column named
`owner_id` continues to exist physically, but pure Bot resources store and read
the Bot's immutable `entity_id` through it.

Personal Bots and every `default` Bot remain outside owner-transfer scope.

## Compatibility resolution

1. Tenant and environment come from server context and repository guards.
2. An explicit `entity_id` uses exact `bot_id + entity_id` lookup.
3. A legacy `owner_id` is a locator and matches either immutable `entity_id` or
   current `owner_id`.
4. Zero matches returns not found; more than one match fails closed as ambiguous.
5. Once resolved, downstream resource access uses only `bot_pk` or `entity_id`.
6. Caller identity and authorization remain separate from resource identity.

## Stage boundaries

- B0: run `audit.sql`; do not enable transfer unless every required invariant is
  clean and every pure-resource table has no orphan or ambiguous association.
- B1: use `BotAddressResolver` and `ResolvedBotContext` at Backend boundaries.
- B2: use existing `source_bot_pk` / `bot_pk` paths for transferable service-Bot
  publish history. Personal Bots, `default`, and legacy orphan lineages retain
  their old owner-shaped compatibility lookup. Historical default lineages are
  never rebound to a newly created Bot PK.
- B3: pure Bot resources use `entity_id` in legacy physical `owner_id` columns.
  This includes new `ac_bot_publish` rows; the field name and response shape stay
  unchanged, but its value is the source Bot's immutable `entity_id`.

Mixed-semantics tables, product decisions for grants/whitelists/work orders,
Frontend/OpenAPI protocol changes, SecBaaS code, and the transfer transaction are
deferred.

## Rollout gate

Deployment is safe while transfer stays disabled. Enabling transfer additionally
requires the B0 audit to pass, all affected resource readers/writers to use the
resolved context, and end-to-end verification with `owner_id != entity_id`.
