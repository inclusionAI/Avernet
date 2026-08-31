A **configuration manifest** that belongs to a bot: one document declaring what
that bot should have — MCP servers, workspace resources, skills, engine config,
identity files, command-line tools — plus the imperative `script` that #926
already owned.

Three waves of `docs/bot-config-manifest/work-items.zh-CN.md` live in this
module now. W1 **stores, validates and describes** a manifest. W2 (the `fetch/`
package, #1470) is the guarded transport underneath the fetching wave: the
fetcher and the unpack pipeline every remote-source byte rides through. W3 (the
`credentials/` package, #1471) is where a secret may finally live: named
tenant-level credentials, presented by the platform within stored prefixes.
No wave applies anything — a document accepted by W1 sits inert until the
apply engine lands, and bytes fetched by W2 are write-or-hash material, never
run. A credential never enters a document (W1 refuses it at the boundary); W2
declares the injector protocol, and W3 binds it.

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

## What W1 decides, and what it deliberately does not

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

## The fetch side (W2): transport safety only

The guarded fetcher answers "the platform fetched THESE bytes from THAT
validated source, within THESE limits, pinned and hashed" — and stops there. It
does not store (W11), does not materialize (W5/W6), does not resolve credentials
(W3 binds the injector protocol declared here), and schedules nothing (W4
carries the budget in).

It owns, for every byte the platform fetches on a manifest's behalf:

- **the address is public and pinned**: URL shape first, DNS resolution next
  (every resolved address must be globally routable — the deployment
  allowlist in `application.yaml`'s `user_config.bot_config_manifest`
  block can exempt a named internal mirror, exact-host), and the
  connection goes to the *validated*
  address with the original Host header and SNI preserved, so a hostname
  re-resolving between check and connect cannot reach a refused target;
- **every redirect hop re-validated** (scheme, address, authorization
  policy) with a hard hop budget;
- **limits enforced while streaming** — per-entry byte caps are counted
  as bytes arrive, never trusted from a declared `Content-Length`; timeouts
  and the apply-time budget/concurrency come in as an injected
  `FetchBudget` (W4 will pass one; the default is per-entry only);
- **the digest is the receipt**: sha256 computed on the same pass as the
  byte cap; a declared digest that does not match is a *fetch failure*, not
  a corrupted success — the fetcher never hands back bytes it was told to
  pin and did not.

It deliberately does not own:

- **credential presentation** — a `CredentialInjector` protocol is declared
  here (headers for a URL) and nothing more; W3 binds it, because prefix
  authorization is a credentials concern, not a transport concern. The
  transport *calls* it per request and per redirect hop (a credential that
  leaves its authorized prefixes across a redirect is refused).
- **unpacked semantics** — `unpack.py` turns a fetched archive into a file
  tree with zip-slip/symlink/device guards, member and size caps, exact
  `strip_components`, and flattened permissions. What the tree *means*
  (directory ownership, convergence) is W6's contract, read from W1's
  schema.
- **execution** — nothing fetched or unpacked executes, ever. Executable
  bits are stripped on unpack; an artifact that must run is `cli_tools`
  (W9) with its own supply-chain gate.

Precedent: the engine repo's `resource_materialization.py` guarded-URL
downloader (URL shape → global-only resolution → pinned connection with
SNI, streaming double caps). Structure follows it; this is a backend-side
re-implementation behind its own tests, not an import.

## The credentials side (W3): presentation, named and scoped

A manifest references a credential **by name**; the platform presents the
secret while fetching within the stored `allowed_prefixes`. The name is the
only spelling that ever appears in a document, a log, an error, or an apply
report — the value is decryptable only in the fetch hop's memory.

What `credentials/` owns:

- **the write path's truth**: one body schema whose discriminating key is the
  *mechanism* (`type`), v1 implementing `header` only, reserved mechanisms
  (`oss_aksk`, `basic`) refused at write so the stored type is real from
  day one;
- **the boundary of presentation**: absolute `https` prefixes, matched on
  path-segment boundaries (`…/team/content` never authorizes
  `…/team/content-secret`), decode-then-normalize on both sides so a
  doubly-encoded slash is an equivalent spelling, not an escape. A target
  outside every prefix fails the hop; the platform never falls back to
  fetching *without* the credential;
- **fail-closed storage**: under the corp/community deployment columns a write
  with no resolvable master key is refused before persistence (a loud 503) —
  `TokenVault`'s empty-key plaintext passthrough is right for singlebox and
  must never catch tenant tokens at rest;
- **the rotation contract**: a rotation is a same-name re-PUT, no apply is
  triggered, and the binding re-reads per hop — "the next fetch uses the new
  value" needs no signal;
- **the ownership boundary**: the surface is application-operated (the edge
  requires an app credential on every route); a name belongs to the
  application that created it, and rotation — a whole-row replace, of the
  secret, the header, *and* the prefixes every manifest citation of the
  name depends on — and delete are the owner's calls alone, refused with
  403 before storage for any other application of the tenant. Reads stay
  tenant-wide: the name is the shared reference namespace manifests cite.

What it deliberately does not own:

- **transport**: the binding duck-satisfies W2's injector/policy seams — W2
  enforces the pinned connection and per-hop revalidation; the fetcher
  *calls* `reauthorize` on every hop, which is what keeps a credential from
  leaving its prefixes across a redirect;
- **reference integrity**: deleting a still-referenced credential is allowed;
  the referencing entries fail their next fetch *with the name*, recorded
  where failures are recorded (apply, W4). Storage never guesses the
  reference graph;
- **the fetch-failure vocabulary**: "credential X was rejected" (401/403) is
  the apply report's wording; the binding surfaces the name and the boundary,
  not a classified outcome.

## Tenancy is load-bearing here

`ac_bots` is itself tenant-scoped, so a `bot_id` is unique only *within* a
tenant — legacy `default` bots carry documented residual collision on that
identifier. Without `avernet_tenant` in this table's key, two such bots share
one manifest row, and either tenant could overwrite the other's manifest, which
once apply lands decides what is installed in the other tenant's container. The
column, the guard registration and the tenant's place in the uniqueness key are
one mechanism; none of the three works alone.

The uniqueness key is `(avernet_tenant, env, entity_id, bot_id)` — the logical
key itself, carried directly. It is budgeted against InnoDB's 3072-byte index
limit, which is what fixes `entity_id` at `varchar(256)` rather than the
`varchar(1024)` it has on `ac_bots`: at 1024 that one column would be 4096
utf8mb4 bytes and past the cap on its own, while at 256 the four columns come to
2384 bytes. So the width here is a constraint, not a copied default — see the
column comment in `repository/models.py` for why 256 and not the 64 that would
also fit.

## Where the HTTP seam is

Nothing here reads a framework, a request, or an HTTP status. The public surface
is `adapters/http/openapi_v1/bots/config_manifest.py` (`GET` / `PUT` / `DELETE`
on `/openapi/v1/bots/{bot_id}/config-manifest`, and `GET
…/config-manifest/capabilities`), and the Service API contract the adapter
depends on is `api/bot_config_manifest_service.py`. The fetch side has no HTTP
surface of its own — it is a core transport the apply orchestration (W4) calls.

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
- **The community column refuses every credential write until a master key
  is configured.** The DI provider binds `fail_closed=True` for corp and
  community alike; community deploys Resolve `TokenVault` from env vars, so
  until `AGENTCLAW_SECRET_*` carries a master key every PUT answers the loud
  503 rather than storing plaintext. That is the guard working, not a defect
  — the alternative is tenant tokens in the clear.
- **`mcp[].config` was removed from schema v1, and the gap it exposed is
  recorded rather than quietly patched.** `manifest-schema` §3.1 defined it as
  per-bot configuration *"the same shape as the existing MCP config API"* —
  which cannot be true of both halves. That API writes `ac_user_mcp_config`,
  keyed `(user_id, server_code)`, and its write calls
  `sync_mcp_detail_to_all_bots`: applying **one** bot's manifest would have
  changed MCP configuration for **every** bot its owner has, a blast radius no
  other category has and one §3.2's per-category area rule never sanctioned. Its
  payload is `api_key` and `custom_headers`, which design §4.5 keeps out of a
  manifest regardless. What *is* per-bot — `ac_bot_mcp_installation`, the
  enabled-server set — is exactly what §3.2 names as the category's area and
  exactly what apply converges. The follow-up, additive and non-breaking, is
  `ac_bot_mcp_call_config`'s `call_type`: genuinely per-bot, but outside §3.2's
  area and carrying draft/lock-epoch/irreversibility semantics an idempotent
  re-apply has to answer for first.
- **Fetch-time limits are absent from the write surface on purpose.** Schema
  §5's download sizes, unpacked sizes, archive file counts and timeouts cannot
  be enforced by a surface that never fetches; they are **the fetcher's
  numbers** now — `fetch/limits.py` is their single source — while the
  apply-scope budget stays with apply (W4). Declaring them beside the
  write-time enforced limits would make them read as enforced there too.
- **`cli_tools` is validated against the flattened shape** — one entry is one
  command is one file, selected inside an archive with `subpath` (schema §3.7).
  The earlier "directory plus an `entrypoints` list" draft is gone, and with it
  the rule set that existed only to constrain it (in-package traversal, symlink
  escape, basename collisions). A document still using `entrypoints` is refused
  by name rather than silently ignored.

## Context Boundary

```yaml
purpose: Own a bot's configuration manifest — its storage, its schema v1 validation, and which constructs a bot can be told to have at all; plus (W2) the guarded fetch + unpack transport that fetches a manifest's remote sources on the platform's behalf.
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
  - GuardedFetcher
  - FetchRequest
  - FetchedObject
  - FetchBudget
  - Resolver
  - CredentialInjector
  - AuthorizationPolicy
  - FetchRefusedError
  - FetchFailedError
  - unpack_archive
  - UnpackedTree
  - UnpackError
  - SourceCredentialService
  - SourceCredentialServiceProtocol
  - SourceCredentialRecord
  - SourceCredentialRow
  - SourceCredentialModel
  - SourceCredentialBinding
  - SourceCredentialRepositoryProtocol
  - CredentialError
  - CredentialNotFoundError
  - MasterKeyUnavailableError
  - PrefixAuthorizationPolicy
  - PrefixAuthorizationError
  - CanonicalPrefix
  - validate_prefixes
consumes:
  - "BotConfigManifestRepositoryProtocol (core.repository) — persistence for the one table"
  - "TeclawEngineTestProtocol (core.bot_startup_script, bound to core.bot_management TeclawProvisionService) — the single definition of 'runs in a teclaw container'"
  - "VALID_IDENTITY_FILES / CLAUDE_CODE_IDENTITY_FILES (core.services.identity) — the identity vocabulary, imported lazily because that module pulls in the device dispatcher"
  - "MAX_SCRIPT_BYTES (core.bot_startup_script) — script.body IS the #926 startup script, so it takes that cap rather than a second one"
  - "TokenVault (core.bot_management) — enc:v1: AES-GCM reversible encryption for the credential secret; the master key comes from the SecretResolver, and the fail-closed profiles refuse writes without one"
  - "SourceCredentialRepositoryProtocol (core.repository) — persistence for the credential table"
consumed_by:
  - "adapters/http/openapi_v1/bots — the public read/replace/clear/capabilities surface"
  - "the apply orchestration (W4, future) — constructor-injected transport_allowlist and FetchBudget"
  - "adapters/http/openapi_v1/source_credentials — the public tenant credential register/rotate/read/delete surface (OPEN admission; app-operated — the edge requires an app credential, owner-app guarded)"
internal_dependencies:
  - agentclaw.community.core.base
  - agentclaw.community.core.bot_startup_script
  - agentclaw.community.core.bot_management.token_vault
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
same authority one wave earlier — and, on the W2 side, which bytes the platform
is willing to fetch at all.

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
