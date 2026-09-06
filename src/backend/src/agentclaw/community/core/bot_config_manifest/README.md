A **configuration manifest** that belongs to a bot: one document declaring what
that bot should have — MCP servers, workspace resources, skills, engine config,
identity files, command-line tools — plus the imperative `script` that #926
already owned.

Six waves of `docs/bot-config-manifest/work-items.zh-CN.md` live in this
module now. W1 **stores, validates and describes** a manifest. W2 (the `fetch/`
package, #1470) is the guarded transport underneath the fetching wave: the
fetcher and the unpack pipeline every remote-source byte rides through. W3 (the
`credentials/` package, #1471) is where a secret may finally live: named
tenant-level credentials, presented by the platform within stored prefixes. W4
(the `apply/` package, #1472) **applies** a stored document to its bot. W11
(the `content/` package, #1510) is the platform-side copy: the
content-addressed blob store and the append-only provenance log that §2.8
demands, so that everything after fetch reads *the platform's* bytes. Bytes
fetched by W2 and kept by W11 are write-or-hash material, never run. W5
registers the first two **fetch-consuming** materialisers: `skills` — packages
that travel the manual-upload road (`SkillPackageValidator`, then
`upload_local_skill`, then direct activation) — and `identity` — the file set
minus the reserved names, written through the router's own
`IdentityService` path. The one funnel they fetch through is
`apply/entry_fetch.py`: substitute, consult the platform's copy, fetch under
a named credential, file the receipt. W6 adds `resources` — files and
archived directory trees, written through `ResourceFileService`'s one
dispatcher chain, directory entries replacing their declared tree in full.

A credential never enters a document (W1 refuses it at the boundary); W2
declares the injector protocol, and W3 binds it. W11 records a credential
*name* in provenance and nothing else. Apply holds none either: it calls the
services that own each area and passes no secret of its own.

## The one rule everything here is shaped by

**This surface never accepts something it cannot apply.**

W1 parses the *whole* v1 vocabulary while only part of it has code behind it. So
anything the schema can express but nothing can act on is reported unsupported
and refused at `PUT` — and the gap is **not confined to categories**: a *source
form* with no resolver fails in exactly the same way. That is why
`capabilities.py` answers per **construct** (category, section, source form)
rather than per bot or per category.

As of the first wave the unsupported constructs are (`cli_tools` left this
table when W9 materialised it — the surface accepts it because something now
applies it):

| Construct | Why nothing can apply it | Unblocked by |
| --- | --- | --- |
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

## The content store (W11): the platform's own copy

§2.8 is a hard requirement from audit and reconciliation: content fetched on a
manifest's behalf is kept as **the platform's durable copy**, and every step
after fetch reads that copy. The `content/` package is that mechanism — one
store behind all three consumers of the requirement (audit, delivery,
`keep_last`), not a copy per consumer:

- **Bytes live in a content-addressed blob tree** —
  `<root>/blobs/<hex[:2]>/<hex64>` under the `content_store_dir` from
  `application.yaml`. The digest (`sha256:<hex>`, the fetcher's own
  vocabulary) IS the address: identical bytes are written once, ever, and the
  write is durable before it is visible — fsync on the temp file, then
  `os.replace` (power loss must not leave an address holding half its
  bytes: this layer never re-fetches, so an in-flight retry cannot heal a
  torn write). They are not in the database — schema §5 lets one entry be
  100–200 MiB, and a column holding that is a self-destructing design.
- **Provenance lives in `ac_manifest_content`**, append-only, one row per
  store event — and a row is a **fetch event, not a delivery**: under §3.2's
  all-or-nothing overwrite, an entry can be fetched, verified and filed here
  and never materialised because a sibling in its category failed (read
  alone, this table over-reports; the apply linkage below joins the rest of
  the answer). Each row carries: the bot axes `(avernet_tenant, env,
  entity_id, bot_id)`, the digest, both URLs (entry source after
  `${BOT_*}` substitution, and the final hop — differing values mean a
  redirect happened), the credential **name** (W3's identifier; the value
  never crosses into this layer), size, fetch time, who triggered it — and
  the `apply_id` / `category` / `entry_identity` trio linking the row back
  to the apply and the entry the fetch served (nullable; present whenever
  the fetch pipeline knew them, and indexed for "what did apply X fetch").
  No unique key: fetching the same digest twice is two audit events, not
  one row to overwrite. Dedup lives in the blob layer by content address;
  repetition in the log is the fact.
- **`read(digest)` is the one read path**, shared by delivery and audit:
  read in chunks with the hash computed on the same pass, returned whole
  (peak ≈ 2× the blob at the §5 cap — a stated v1 trade; the consumer
  materialises the full payload anyway), so the store returns
  bytes it can prove or fails — a re-delivery that "mostly" matches its
  address would defeat the receipt contract exactly where it matters. A
  missing address is terminal; this layer **never re-fetches** (§2.8's
  decoupling: a retried apply re-reads here, and a source-side fault cannot
  pollute a delivery in progress).
- **URLs in provenance carry path but neither userinfo nor query** — the
  same posture as the fetcher's own log line (query strings are where
  signed-source tokens live). The reconciliation anchor for audit is the
  digest, never a one-time signed URL. IPv6 literals keep their brackets
  (httpx hands the host back bare; a bracketless authority with a port is
  unreadable, and this table is never corrected after write). The wire's
  ``Content-Type`` is advisory — a header wider than its column stores NULL
  plus a log line, because throwing away digest-verified bytes over
  advisory metadata hands the source a provenance eraser. Source **URL
  length** is refused upfront at `PUT` (2048, the provenance column width —
  admission, not a post-fetch surprise); the store keeps the width check as
  the last line of defence for what admission cannot see, a redirect
  destination's length.
- **Retention is stated, not defaulted**: v1 retains rows and blobs
  unconditionally — no delete, no sweep, no TTL. Until an audit horizon is
  named, any deletion is a manufactured audit gap. A retention window, when
  audit names one, lands as a DDL-comment change plus a sweep mechanism in
  that PR, not as a silent default. The one stated exception, so a future
  sweeper's boundary is already drawn: `.tmp-*` staging files from crashed
  writes are not audit facts, and a store into the shard collects the ones
  old enough that no live writer owns them.

The store is a declared machine part in the same shape as the fetcher: the
service takes its repository and the blob root as constructor values, and the
composition root that constructs it (reading
`user_config.bot_config_manifest.content_store_dir` through
`content.settings.content_store_root_from_config`) arrives with W5, the wave
that fetches — W4's apply (#1472) landed fetch-free, so it wires no store and
no caller exists yet. The per-entry digests that wave's apply records carry
are how `keep_last` reuses this store without a second addressing.

## Tenancy is load-bearing here

`ac_bots` is itself tenant-scoped, so a `bot_id` is unique only *within* a
tenant — legacy `default` bots carry documented residual collision on that
identifier. Without `avernet_tenant` in this table's key, two such bots share
one manifest row, and either tenant could overwrite the other's manifest, which
decides what is installed in the other tenant's container. The
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

## Applying (W4, #1472; the fetching categories W5, #1473)

`apply/` holds the engine: an orchestrator, an ordering table, a materialiser
registry, and the four materialisers shipped so far — `mcp` and `script` with
W4, `identity` and `skills` with W5.

**The ordering table is complete; the registry is sparse.** `APPLY_ORDER` names
every construct the vocabulary defines, including the ones nothing can act on
yet; `build_materialisers()` maps only those that some shipped code writes. A
declared construct with no materialiser is an **expected state**, not a gap —
its entries fail with a readable reason, its category is aborted so nothing is
destroyed, and later waves close the window by *registering* rather than by
deleting a branch (W5 closed `skills` and `identity` this way; W6 holds
`resources`).

**The orchestrator must never grow category knowledge.** Every category-level
rule lives there once — serialization, ordering, phase selection, the abort rule,
the `skipped` cascade, the outcome tally — and a materialiser knows none of it.
That is the whole of what W5, W6 and W13 get from this item: adding a category is
writing a materialiser, not rebuilding the engine. `tests/community/core/bot_config_manifest/apply/test_orchestrator_stays_generic.py`
asserts the module names no category, because the moment it does the registry
stops meaning anything and every later work item adds "just one" special case.

**Two phases, and the split is not organisational.** `script` needs no container
and on the creation path must be written *before*
`BaasService._build_create_bot_payload` composes the start command; everything
else resolves a device and raises if unbound. On a running bot the two run back
to back and the split is invisible; on the creation path they are separated by
the whole of container provisioning, which is why W13 can call them one at a
time. **This reverses design §3.4's order** — see work-items §2.12.

**Apply is started, not awaited.** `POST …/apply` answers `202` with an
`apply_id` and the work continues elsewhere. The lock is taken and the stored
document re-validated *before* an id is minted, so a caller never holds a handle
to an apply that did not start. A report stranded at `RUNNING` by a killed
process reads as `FAILED` once its lock is stale — derived at read time, so there
is no sweeper to keep alive.

**"Elsewhere" is the task queue, not a thread (W13).** W4 ran the work on
`threading.Thread(daemon=True)`; `core/task_queue`'s README names that pattern as
the one it exists to replace, and W13 makes the loss load-bearing — creation
depends on an apply completing, so a thread that dies with its pod does not lose
a report, it boots a bot without its script. All three paths now run through one
handler (`apply/apply_task.py`): the pre-container phase, the post-container
phase, and an explicit apply on a running bot. The lock is acquired by whoever
enqueues and released by whoever runs, so it spans the handoff; a task that never
runs leaves a lock the TTL reaps, exactly as a dead thread did.

**Re-running an apply is safe because apply converges — not because retry is
off.** The queue is at-least-once *structurally*: a crashed worker's task is
re-claimed once its lease expires, whether or not a handler ever returns `Retry`.
Anyone adding a materialiser that is not convergent breaks this, and no queue
configuration would save it.

**Apply records delivery, not execution** (§2.7). The `script` materialiser
writes one `ac_bot_startup_script` row and does nothing else: no restart, no
republish, no payload rebuild. The row executes at the bot's next **device
provisioning** — create, restart or republish — because
`_build_create_bot_payload` re-reads it on every payload it composes; it is never
re-run inside a container already up. Adding a restart here so a script "takes
effect now" is the tempting bug, and the one this boundary exists to prevent.

**Fetch lives in `resolve`, and only there** (W5). The registry's contract puts
everything that can fail before touching the bot into `resolve`, and a fetch is
exactly that kind of failure: one failed fetch aborts its whole category with
zero writes, by construction rather than by discipline. The pipeline every
fetching category runs is `apply/entry_fetch.py` — substitute ``${BOT_*}``
*before* prefix authorization, consult W11's newest receipt for the source
(pinned entries are served from the store: content addressing makes those bytes
*the* declared bytes, and unpinned entries re-fetch so an apply converges to the
source), fetch under W3's binding, file the receipt. `keep_last` reads that
receipt only when it may — a receipt disagreeing with a declared digest is
stale, not last. A `dry_run` may therefore fetch (it still writes nothing to the
bot); the receipt it files is the platform's record of what the bot was served,
true whether the apply proceeds.

**The two fetching categories and their areas** (§3.2). `identity` overwrites
the file set minus the reserved names — `MEMORY.md` and `IDENTITY.md` are
refused in `resolve` and subtracted from the removals, both halves, so the
guarantee holds for documents that never met the validator — writing through
`IdentityService` with the router's own coordinates
(`identity_coords_from_record`, resolved in core for exactly this consumer);
"removal" is an empty write because the domain's own contract is that absent
and empty are one state. `skills` overwrites the **active skill set**, narrowed
by the Set-governed members (`BotCapabilityStateReader.member_skill_ids` — the
write refuses them, so the plan refuses to plan them; the same narrowing `mcp`
applies to platform defaults): a skill one of the bot's Sets supplies is
neither declarable nor removable, a first apply uploads (the fetched zip
validated by the upload path's own `SkillPackageValidator`, tar.gz/subpath
unpacked by the guarded unpacker and re-packed canonically) and activates
through `DirectActivationService`, and convergence is observed through W11
receipts — an active name plus a store-served pin writes nothing.

## Creating a bot from a manifest (W13, #1696)

`creation.py` is the seam bot creation calls — preflight, persist, the
pre-container phase, discard, and the two operations that start and read the
creation job. `create_job.py` is the job itself. Both live here rather than in
`core/bot_management` because they are manifest concerns; creation decides *when*
to call them.

**The dependency runs one way.** `create_job` imports `creation` for the phase
triggers, so the seam is handed its job operations at construction (the DI module
wires them) rather than importing them back. `create_flow` must not import this
package at all — that closes a cycle through the creation graph — which is why
the seam's `persist` resolves the storage key itself and returns it.

**Preflight is stricter than `PUT`, deliberately.** `PUT` may accept a construct
no materialiser can act on: the document sits inert and nothing has been created.
Here the same acceptance costs a Passport application, a user's authorization
click and a live bot before the failure appears, so such a construct is refused
at submission. Every engine family creates here since W8 (#1476): what a family
cannot deliver is the validator's per-construct refusal (`script` on teclaw is
`unsupported_script`), and which order the creation runs in is the delivery
strategy's `creation_sequence` — see *Lifecycle apply points* below.

### Operational precondition

**Applying — and therefore creating a bot with a manifest — only progresses
where `task_queue_worker.enabled=true` and `ac_task_queue` is provisioned.**
Before W13 that flag gated an optimisation. It now gates the feature, and the
failure mode is not slowness: with the worker off, `POST …/apply` still answers
`202` and its report stays `RUNNING` until the apply lock's TTL expires, and a
create-with-manifest still answers `202` and its poll sits at
`AWAITING_AUTHORIZATION` until the creation deadline retires it as
`AUTHORIZATION_EXPIRED`. Neither ever completes. It is the first thing to check
when a creation or an apply appears stuck.

## Lifecycle apply points and the delivery seam (W8, #1476)

Two engine families deliver a bot's configuration by opposite mechanisms, and
the apply engine must not know which it is running for. `apply/delivery.py`
names the difference: a `DeliveryStrategy` owns the phase each construct
belongs to, the write ports the materialisers are handed, the creation sequence
the W13 job runs, and the step that closes an apply — and nothing else. The
orchestrator sees phases and the materialisers see ports; neither learns the
family.

|  | ARCA (`ArcaDelivery`) | teclaw, switch **on** (`TeclawDelivery`) | teclaw, switch **off** |
| --- | --- | --- | --- |
| Phases | `script` PRE_CONTAINER, the rest ON_CONTAINER — `APPLY_ORDER`'s own table | every construct PRE_CONTAINER (`script` is refused by the validator) | the rest ON_CONTAINER, as before W8 |
| Ports | the device-backed services | the store-backed ports over `managed_files/` + record-only activation | the device-backed services |
| Closing step | none — the owning services project as they write | one whole-artifact redeliver to the running container, none when unbound | none |
| Creation sequence | `CREATE_BETWEEN_PHASES`: phase A, create + provision, wait ACTIVE, phase B | `RECORD_APPLY_PROVISION`: record, the single phase against it, provision, wait ACTIVE | `CREATE_BETWEEN_PHASES` |

**The platform is the source of truth for what a manifest applies, on both
families** (spec D-3). On ARCA that was already so. On teclaw the artifact is
the delivery, so the platform holds its own copy of every manifest-delivered
file: bytes in the bot-data object store under the promotion key layout with a
`_manifest` segment, no index table beside them — the key layout is the record
(`managed_files/`, see its README). The teclaw composer writes the artifact's
**`ownership`** map — the engine contract's §9 — and **ownership follows the
operation**, not the bot's declarations (`ComposeOccasion` on the compose
request): the closing redeliver of a manifest apply, and the first artifact
of a bot that carries a manifest, are the platform's for every category, and
the composer lists the store for the file categories; every runtime edit — a
skill or resource upload, an MCP edit, a channel change, a publish build — is
the engine's for every category and reads no managed file. `mcp` is the
platform's on every occasion (the artifact has carried the whole MCP set
since W12); ARCA artifacts carry no map. A local skill the manifest installs
rides as a `SkillRef` with a store address (R-O3) plus its files as resources
refs; the collector emits it only while the bot has the skill active.

**The switch.** `user_config.bot_config_manifest.teclaw_platform_managed`
(default `false`), read once at boot by `DeliveryStrategyFactory` and nowhere
else, strict about booleans. It stays off until the teclaw engine implements
the `ownership` map (R-O1/R-O2/R-O3); off, teclaw runs the shape it ran before
W8 and the only artifact change is an all-`engine` map. **Before flipping it on
an existing deployment, explicitly apply each teclaw bot's manifest once** so
the store carries its files: the next apply's redeliver asserts `platform`
for every category, and an empty prefix under an asserted category means
"remove the area" to an engine that honours the map.

**`PUT` starts an apply** (§2.6): the document is stored and validated exactly
as before, then both phases are started under trigger `put`; the response's
`apply` field says `RUNNING` with the id or `NOT_STARTED` with why, and the
write is a `200` either way. `warnings` carries the script delivery note and,
on a bot that is not `ACTIVE` whose strategy has container-bound constructs,
the note that those will be recorded as failed. Restart and republish are
**not** apply points in this iteration (spec D-1): a change to the git repo a
manifest ref points at is picked up by an explicit apply or the next `PUT`.

**The legacy `…/startup-script` routes are untouched.** The manifest is a
layer the startup script does not know about (review decision on
inclusionAI/Avernet#1836): the routes read and write the
`ac_bot_startup_script` row as before, and a manifest that declares `script`
materialises into that same row on apply. An edit made through the legacy
route on a bot whose manifest declares `script` is therefore replaced by the
next apply — the manifest is the source of truth for what it declares.

**Trigger vocabulary** (`apply/triggers.py`): `explicit`, `put`,
`create:pre_container`, `create:on_container`. The apply record's column is 32
wide and a test pins every trigger to it.

**Deferred, recorded.** Restart and republish as apply points; the publish
gather for platform-managed teclaw files (the promotion step still snapshots
the container); a health surface for a failed closing redeliver beyond the
report's `notes`; and an ARCA pre-binding port (ARCA still needs the container
for every non-script construct).

## Where the HTTP seam is

Nothing here reads a framework, a request, or an HTTP status. The public surface
is `adapters/http/openapi_v1/bots/config_manifest.py` (`GET` / `PUT` / `DELETE`
on `/openapi/v1/bots/{bot_id}/config-manifest`, and `GET
…/config-manifest/capabilities`), plus
`adapters/http/openapi_v1/bots/create_with_manifest.py` for the W13 pair
(`POST /openapi/v1/bots/with-manifest` and its status poll). The Service API
contract the adapter depends on is `api/bot_config_manifest_service.py`. The fetch side has no HTTP
surface of its own — it is a core transport the apply orchestration (W4) calls.

**There is no feature switch over the group.** An earlier revision hid it until
apply landed; the surface ships enabled instead, and what keeps it honest is the
capability resolver above — which had to keep working after any switch was
removed anyway. What an accepted manifest actually changes is decided by
`GET …/capabilities` and by which materialisers are registered — see *Applying*
below.

## Known gaps, recorded rather than discovered

- **The apply-scope fetch budget is defined, not enforced** —
  `APPLY_FETCH_TOTAL_LIMIT` / `APPLY_BUDGET_S` have no mechanism behind
  them. Per-entry caps, per-hop timeouts and the apply-lock TTL bound one
  apply today; the ledger lands with the wave that owns more fetch
  consumers (W6/W7), threaded once through the entry fetcher.

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
purpose: Own a bot's configuration manifest — its storage, its schema v1 validation, and which constructs a bot can be told to have at all; plus (W2) the guarded fetch + unpack transport that fetches a manifest's remote sources on the platform's behalf, and (W11) the platform's own durable copy of what was fetched, with its provenance log.
provides:
  - BotCliToolModel  # W9: the platform's record of a bot's installed CLI tools
  - BotCliToolRecord
  - INSTALLED_BY_MANIFEST  # the installed_by value a manifest apply writes
  - CliToolStore  # W9: the platform's own copy of a bot's tool bytes
  - CliToolScope
  - CliToolStoreError
  - StoredCliTool
  - CliToolContext
  - CliToolDeliveryPort  # W9: the per-family delivery boundary, name-addressed
  - DeliverableCliTool  # W9 rev 8: one tool as the whole-set call carries it
  - CliToolDeliveryError
  - CliToolPlacementError
  - ArcaCliToolPort
  - TeclawCliToolPort
  - CliToolDriftUnobservableError
  - CliToolService  # W9: the one component both callers install through
  - CliToolDecl
  - CliToolOutcome
  - CliToolStatus
  - CliToolDrift
  - verify_amd64_elf  # W9: "can this machine run them", which the digest does not answer
  - select_subpath
  - FetchContext  # exactly what a fetch reads off its caller's context (W9 gave it a second caller)
  - BotCliToolService  # W9: the bot_id-addressed surface the HTTP routes bind to
  - BotCliToolServiceProtocol
  - CliToolsMaterialiser
  - CliToolNotFoundError
  - CliToolConflictError
  - CliToolRefusedError
  - CliToolUnsupportedError
  - BotConfigManifestApplyService
  - BotConfigManifestApplyServiceProtocol
  - BotConfigManifestApplyRecord
  - BotConfigManifestApplyLockRecord
  - ApplyOrchestrator
  - ApplyReport
  - ApplyStatus
  - ApplyPhase
  - ApplyContext
  - ApplyTaskHandler
  - ApplyTaskLifecycle
  - APPLY_TASK_TYPE
  - build_apply_task_payload
  - BotCreationManifestSeam (the ManifestCreationSeam implementation; only DI names it)
  - CREATE_PRE_CONTAINER_TRIGGER
  - CREATE_ON_CONTAINER_TRIGGER
  - preflight_creation_manifest
  - resolve_manifest_entity_id
  - BotCreateWithManifestHandler
  - CreateJobLifecycle
  - CREATE_JOB_TASK_TYPE
  - enqueue_create_job
  - find_create_job
  - EntryOutcome
  - EntryResult
  - CategoryResult
  - Materialiser
  - APPLY_ORDER
  - build_materialisers
  - EntryFetcher
  - FetchedEntry
  - EntryFetchError
  - scope_of
  - IdentityMaterialiser
  - SkillsMaterialiser
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
  - ManifestContentService
  - ManifestContentServiceProtocol
  - ManifestContentRepositoryProtocol
  - ManifestContentModel
  - StoredContentRecord
  - ContentScope
  - ContentStoreError
  - ContentMissingError
  - ContentIntegrityError
  - content_store_root_from_config
  - DEFAULT_CONTENT_STORE_DIR
consumes:
  - "BotConfigManifestRepositoryProtocol (core.repository) — persistence for the one table"
  - "BotConfigManifestApplyRepositoryProtocol / BotConfigManifestApplyLockRepositoryProtocol (core.repository) — the apply record and its serialization lock"
  - "BotStartupScriptServiceProtocol (core.bot_startup_script) — the `script` materialiser's only write"
  - "ActivationPort (core.ports) — the outbound port the `mcp` and `skills` materialisers write through: the activation service's six methods without `project`, because choosing whether a write projects is the delivery strategy's call, not a materialiser's. Both implementations live in apply/activation_delegates.py"
  - "SkillPackageUploadPort (core.ports) — the outbound port the `skills` materialiser installs packages through: the Service API's two apply-relevant methods, without the directory-upload route's `upload_local_skill_files`. DeviceSkillPackageUpload wraps LocalSkillUploadServiceProtocol (the same entry point the raw-zip router path takes, W5); PlatformSkillPackageUpload writes the store instead"
  - "BotCapabilityStateReaderProtocol (core.skill_center.capability_state_contract) — the flush-then-read active-set the `skills` materialiser enumerates its area from and narrows removals by (W5; the core contract module — not the api/ façade that re-exports it, which core deliberately does not depend on)"
  - "SkillPackageValidator (core.skill_center.skill_package) — the manual-upload package gate the `skills` materialiser validates fetched bytes with, so an installed skill is an uploaded one (W5)"
  - "ManifestContentServiceProtocol.latest_receipt — the per-source receipt lookup the entry fetch pipeline asks (W5)"
  - "MCPAuthServiceProtocol (api) — the same permission check DirectActivationService consults, asked up front so a category is all-or-nothing"
  - "ManifestContentRepositoryProtocol (core.repository) — persistence for the append-only provenance log"
  - "TeclawEngineTestProtocol (core.bot_startup_script, bound to core.bot_management TeclawProvisionService) — the single definition of 'runs in a teclaw container'"
  - "VALID_IDENTITY_FILES / CLAUDE_CODE_IDENTITY_FILES (core.services.identity) — the identity vocabulary, imported lazily because that module pulls in the device dispatcher"
  - "MAX_SCRIPT_BYTES (core.bot_startup_script) — script.body IS the #926 startup script, so it takes that cap rather than a second one"
  - "TokenVault (core.bot_management) — enc:v1: AES-GCM reversible encryption for the credential secret; the master key comes from the SecretResolver, and the fail-closed profiles refuse writes without one"
  - "SourceCredentialRepositoryProtocol (core.repository) — persistence for the credential table"
  - "ALLOWED_EXTENSIONS / MAX_FILE_SIZE (core.resources.services.file_service) — the workspace file surface's admission rule, re-asked in the `resources` materialiser's resolve by delegating to the one `admission_refusal` predicate so an undeliverable entry fails with the tree still standing (W6)"
  - "resolve_bot_engine (core.bot_management.engines.registry) — the pure runtime-engine routing policy (claude_code + a non-normalCC template ⇒ aicoding) the `resources` materialiser applies when addressing a bot's workspace, the same rule the resources router resolves through before it composes {bot_dir}/{engine}/workspace (W6)"
  - "TaskQueueService (core.task_queue) — every apply runs as a task, and a creation is a task of its own; reached through a lazy provider because that module imports the DI container at module scope"
  - "BotRepository (core.repository) — the apply task rebuilds its context by re-reading the bot rather than carrying it in a payload"
consumed_by:
  - "adapters/http/openapi_v1/bots — the public read/replace/clear/capabilities surface, and the create-with-manifest pair (W13), which reaches the seam and never the task queue"
  - "core.bot_management create_flow — submission calls the creation seam (preflight, persist, start the job); the dependency runs one way, so the seam is handed its job operations at construction rather than importing them"
  - "the apply orchestration (`apply/`, W4 #1472 + W5 #1473) — di/modules/manifest_fetch_module.py constructor-injects the transport_allowlist and the content store root (read via the W2/W11 pure parsers over config_module's seam) and holds the one EntryFetcher over the fetcher, the store, and W3's credentials"
  - "adapters/http/openapi_v1/source_credentials — the public tenant credential register/rotate/read/delete surface (OPEN admission; app-operated — the edge requires an app credential, owner-app guarded)"
internal_dependencies:
  - agentclaw.community.core.base
  - agentclaw.community.core.config_compose.models  # the collector-shaped refs the managed-files reader yields to the teclaw composer (W8)
  - agentclaw.community.kernel.bot_config  # OwnershipCategory — the artifact's own category names the managed-files reader answers the composer in (W8)
  - agentclaw.community.plugin_api.object_storage  # the bot-data object store the managed-files store writes a teclaw bot's manifest-delivered files into (W8), and the cli_tools store keeps a bot's tool bytes in (W9)
  - agentclaw.community.core.bot_startup_script
  - agentclaw.community.core.bot_management.engines.registry  # the pure runtime-engine routing policy the resources materialiser addresses workspaces through, the router's own rule (W6)
  - agentclaw.community.core.bot_management.manifest_seam  # the Protocol the creation seam is declared as, bound as, and injected as everywhere; declaring it rather than matching it by shape is what checks the contract in one place
  - agentclaw.community.core.bot_management.token_vault
  - agentclaw.community.core.bot_management.utils  # resolve_agent_code — the creation job asks whether completion's *second* write (the owner relationship) actually landed, since the bot record alone cannot tell it
  - agentclaw.community.core.mcp.mcp_auth_service_protocol  # the permission check DirectActivationService also consults
  - agentclaw.community.core.repository
  - agentclaw.community.core.resources.services.file_service  # the workspace file surface's admission constants, re-asked at resolve (W6)
  - agentclaw.community.core.services.identity  # the device-backed IdentityFilePort forwards to it (TYPE_CHECKING only — the module reaches the device dispatcher graph at import)
  - agentclaw.community.core.services.resource_file_service  # the device-backed ResourceFilePort forwards to it (TYPE_CHECKING only, same reason)
  - agentclaw.community.core.skill_center.capability_state_contract  # the flush-then-read active-set the `skills` materialiser enumerates (W5)
  - agentclaw.community.core.ports.resource_file_port  # ResourceFilePort — the outbound port both resource implementations declare
  - agentclaw.community.core.ports.identity_file_port  # IdentityFilePort — the outbound port both identity implementations declare
  - agentclaw.community.core.ports.activation_port  # ActivationPort — the outbound port both apply-side activation delegates declare
  - agentclaw.community.core.ports.skill_package_upload_port  # SkillPackageUploadPort — the outbound port both upload implementations declare
  - agentclaw.community.core.skill_center.direct_activation_service_protocol  # the activation Service API both delegates wrap and forward `project` to
  - agentclaw.community.core.skill_center.local_skill_upload_service_protocol  # the upload road a manifest skill travels (W5)
  - agentclaw.community.core.skill_center.skill_package  # the manual-upload package gate, reused per fetched skill (W5)
  - agentclaw.community.core.task_queue  # applying runs as a queue task, not a daemon thread (W13) — the queue module imports the DI container at module scope, so TaskQueueService is a TYPE_CHECKING-only annotation behind a lazy provider
  - agentclaw.community.core.workspace.constants
  - agentclaw.community.kernel.lifecycle  # the apply handler registers itself at boot
  - agentclaw.community.log
  - agentclaw.community.plugin_api.database
  - agentclaw.community.plugin_api.device_adapter_transport  # the one engine channel core can reach; the ARCA cli_tools port POSTs an install through it (W9)
  - agentclaw.community.plugin_api.passport  # the creation job reads its own authorization status; the Plugin API type, not the service graph behind it
  - agentclaw.community.utils.avernet_tenant
  - agentclaw.community.utils.avernet_tenant_guard
  - agentclaw.community.utils.env_utils
```

### Change impact

A change here changes **what is installed into a customer's container**, and
since W4 that is immediate rather than prospective. It also changes what a
caller is allowed to declare, and — on the W2/W3 side — which bytes the platform
is willing to fetch and under whose credential.
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
