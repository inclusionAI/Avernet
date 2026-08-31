# `agentclaw.community.core.bot_config_manifest`

A **configuration manifest** that belongs to a bot: one document declaring what
that bot should have — MCP servers, workspace resources, skills, engine config,
identity files, command-line tools — plus the imperative `script` that #926
already owned.

This module is the first wave of that feature (W1 of
`docs/bot-config-manifest/work-items.zh-CN.md`). It **stores, validates and
describes** a manifest. It applies nothing, fetches nothing, and holds no
credential. A document accepted here sits inert until the apply engine lands.

## The one rule everything here is shaped by

**This surface never accepts something it cannot apply.**

W1 parses the *whole* v1 vocabulary while only part of it has code behind it. So
anything the schema can express but nothing can act on is reported unsupported
and refused at `PUT` — and the gap is **not confined to categories**: a *source
form* with no resolver fails in exactly the same way. That is why
`capabilities.py` answers per **construct** (category, section, source form)
rather than per bot or per category.

As of the first wave the unsupported constructs are:

| Construct | Why nothing can apply it | Unblocked by |
| --- | --- | --- |
| category `cli_tools` | delivery deferred by business priority — no materializer, and nothing puts a tool on PATH | its work item |
| category `engine_config` | moved out of the first wave, so the fetch-free materializer covers only `mcp` and `script` | its materializer returning |
| `from` naming a **named source** | named sources are resolved by W7 | W7 |
| a **git** source | same resolver, same work item | W7 |
| `script` on teclaw / desktop | those containers get no start command from the shared sequence | — (a standing capability, not a wave) |

Anyone adding to the vocabulary adds a row here or adds the code that applies
it. **"Let this surface accept something nothing applies" is never the third
option** — every row above has that shape, and each was derived independently
before the rule was written down.

## What this module decides, and what it deliberately does not

- **The document is stored verbatim.** Not a re-serialisation of the parse.
  `script.body` is a shell body: a quote, a `$(id)`, a `{token}`, an indent and
  a trailing newline are its meaning, and a YAML round trip preserves the value
  rather than the bytes. The parse exists to validate; what `GET` returns is
  what `PUT` was given.
- **"Absent is not an error."** A bot that never had a manifest reads as an
  empty document — the rule `bot_startup_script` set, for the same reason: a
  404 would make "has none" indistinguishable from "no such bot".
- **`PUT` is all-or-nothing**, and it answers with the *whole* list of reasons.
  A refusal per problem would turn fixing a document into a queue.
- **Capabilities are one function with two entry points.**
  `resolve_capabilities(active_engine, bot_type)` and
  `capabilities_for_bot(record)` — never two implementations. W13 validates a
  manifest during bot creation, before any `ac_bots` row exists; a resolver that
  needed a record could not be reused there.
- **Support never reads live state.** Engine (`is_teclaw`, the canonical test,
  injected rather than re-derived) and `bot_type` are both on the bot record, so
  there is no third "we could not find out" state that a failing device lookup
  could produce.
- **It asks no registry anything.** Whether an `mcp.server_code` exists and
  whether the tenant may enable it are apply-time questions; asking them here
  would need a tenant-scoped service the pre-creation entry point does not have.

## Tenancy is load-bearing here

`ac_bots` is itself tenant-scoped, so a `bot_id` is unique only *within* a
tenant — legacy `default` bots carry documented residual collision on that
identifier. Without `avernet_tenant` in this table's key, two such bots share
one manifest row, and either tenant could overwrite the other's manifest, which
once apply lands decides what is installed in the other tenant's container. The
column, the guard registration and the tenant's place in the uniqueness key are
one mechanism; none of the three works alone.

The uniqueness key is budgeted against InnoDB's 3072-byte index limit, which is
why it is keyed on `(avernet_tenant, manifest_key)` — a sha256 of
`(env, entity_id, bot_id)` — rather than on those columns directly. `entity_id`
alone is 1024 utf8mb4 characters, 4096 bytes, past the cap on its own. The
digest is **length-prefixed, not delimiter-joined**: a separator only
disambiguates while it cannot occur inside a component, and nothing validates
`bot_id` or `entity_id` against control characters. See
`core/repository/implementations/bot/config_manifest/_key.py`.

## Where the HTTP seam is

Nothing here reads a framework, a request, or an HTTP status. The public surface
is `adapters/http/openapi_v1/bots/config_manifest.py` (`GET` / `PUT` / `DELETE`
on `/openapi/v1/bots/{bot_id}/config-manifest`, and `GET
…/config-manifest/capabilities`), and the Service API contract the adapter
depends on is `api/bot_config_manifest_service.py`.

**There is no feature switch over the group.** An earlier revision hid it until
apply landed; the surface ships enabled instead, and what keeps it honest is the
capability resolver above — which had to keep working after any switch was
removed anyway. Until apply lands, an accepted manifest is stored and read back
and does nothing else, which is what `GET …/capabilities` and this README say.

## Known gaps, recorded rather than discovered

- **Deleting a bot does not delete its manifest.** Bot deletion is a soft update
  and no cascade reaches this table. The row cannot be inherited (the key names
  one bot for the life of the data), so nothing will read it, but a caller's
  configuration outlives the bot they deleted. The purge belongs with this
  feature's other per-bot state — the apply record, W4 — rather than as a lone
  seam wired into the deletion path ahead of it.
- **Fetch-time limits are absent on purpose.** Schema §5's download sizes,
  unpacked sizes, archive file counts and timeouts cannot be known by a surface
  that never fetches; they belong to the fetcher (W2) and to apply (W4/W5).
  Declaring them here beside enforced limits would make them read as enforced.
- **`cli_tools` is validated against the flattened shape** — one entry is one
  command is one file, selected inside an archive with `subpath` (schema §3.7).
  The earlier "directory plus an `entrypoints` list" draft is gone, and with it
  the rule set that existed only to constrain it (in-package traversal, symlink
  escape, basename collisions). A document still using `entrypoints` is refused
  by name rather than silently ignored.

## Context Boundary

```yaml
purpose: Own a bot's configuration manifest — its storage, its schema v1 validation, and which constructs a bot can be told to have at all.
provides:
  - BotConfigManifestService
  - BotConfigManifestServiceProtocol
  - BotConfigManifestRecord
  - BotConfigManifestModel
  - ManifestCapabilities
  - Capability
  - Construct
  - ConstructKind
  - ManifestCategory
  - ManifestSection
  - SourceForm
  - kind_of
  - parse_category
  - ManifestWriteResult
  - ManifestValidationError
  - ManifestTooLargeError
  - ManifestNotEncodableError
  - Violation
  - ValidationResult
  - resolve_capabilities
  - capabilities_for_bot
  - validate_document
  - MAX_DOCUMENT_BYTES
  - MAX_ENTRIES_PER_CATEGORY
  - MAX_INLINE_CONTENT_BYTES
consumes:
  - "BotConfigManifestRepositoryProtocol (core.repository) — persistence for the one table"
  - "TeclawEngineTestProtocol (core.bot_startup_script, bound to core.bot_management TeclawProvisionService) — the single definition of 'runs in a teclaw container'"
  - "VALID_IDENTITY_FILES / CLAUDE_CODE_IDENTITY_FILES (core.services.identity) — the identity vocabulary, imported lazily because that module pulls in the device dispatcher"
  - "MAX_SCRIPT_BYTES (core.bot_startup_script) — script.body IS the #926 startup script, so it takes that cap rather than a second one"
consumed_by:
  - "adapters/http/openapi_v1/bots — the public read/replace/clear/capabilities surface"
internal_dependencies:
  - agentclaw.community.core.base
  - agentclaw.community.core.bot_startup_script
  - agentclaw.community.core.repository
  - agentclaw.community.core.services.identity
  - agentclaw.community.core.workspace.constants
  - agentclaw.community.log
  - agentclaw.community.plugin_api.database
  - agentclaw.community.utils.avernet_tenant_guard
  - agentclaw.community.utils.env_utils
```

### Change impact

Once apply lands, a change here changes **what is installed into a customer's
container**. Today it changes what a caller is allowed to declare — which is the
same authority one wave earlier.

Two things are close to authorization boundaries already:

- **No secret may enter a document.** A source URL carrying userinfo
  (`https://user:token@host/…`) is refused rather than accepted-and-redacted,
  because the document is stored as written, read back verbatim by `GET`, and
  recorded as provenance by the platform's own materialisation. Redaction cannot
  un-store what three other places already hold.
- **The reserved identity files (`MEMORY.md`, `IDENTITY.md`) are refused at
  write time**, not skipped at apply time. They are engine-generated runtime
  state that apply is guaranteed never to write or remove, so a document
  declaring one would be accepted and then never converge. Refusing it is what
  keeps "accepted" and "appliable" the same set.
