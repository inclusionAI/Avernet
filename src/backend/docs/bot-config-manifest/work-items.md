# Bot Config Manifest — Implementation Work Items

> **Status: PLANNING.** The design is settled in the four documents beside this
> one (`README.zh-CN.md`, `design.zh-CN.md`, `manifest-schema.zh-CN.md`,
> `examples.zh-CN.md`, `engine-requirements.zh-CN.md`, merged as #1031). This
> document turns that design into a sequence of independently reviewable work
> items. It contains no new design decisions except where a section is
> explicitly labelled as a blocking question.

## 1. How to use this document

Each work item **W1–W11** is scoped to be picked up on its own, by one person or
one session, with no need to re-derive the design from the Chinese docs. An item
states what it delivers, what it deliberately leaves alone, what must land
first, and criteria concrete enough to review against.

Three kinds of entry gate the work and are tracked separately from it:

| Prefix | Meaning |
| --- | --- |
| `W1`–`W11` | Implementation work items — one PR each |
| `D1`–`D4` | **Design questions**, found while checking the design against the code. Each gates a specific work item. D1–D3 are now **resolved**; **D4 is open and is the largest unanswered question in the feature** |
| `X1`–`X4` | **External confirmations** owed by other teams. None blocks W1–W4; they gate W7, W8 and W9 |

Where this document diverges from the merged design docs — §2.5 (capability
scope), §2.6 (PUT triggers a restart), §2.7 (first-boot readiness gate) — it
says so explicitly. Those docs are not edited here; amending them is a separate
change.

### Trackers

Each entry has a GitHub issue. The issue carries scope and acceptance criteria;
this document remains the source of truth for detail and for how the items fit
together.

| | Issue | | Issue |
| --- | --- | --- | --- |
| **D1** capability model — *resolved* | #1466 | **W4** apply engine | #1472 |
| **D2** manifest-upgrade diff policy — *open* | #1467 | **W5** skills + identity | #1473 |
| **D3** reconcile verification — *resolved* | #1468 | **W6** resources | #1474 |
| **D4** pre-boot delivery — **open, blocking** | #1508 | **W7** named + git sources | #1475 |
| **W1** manifest document | #1469 | **W8** lifecycle apply points | #1476 |
| **W2** guarded fetcher | #1470 | **W9** `cli_tools` (deferred) | #1477 |
| **W3** source credentials | #1471 | **W10** service-layer seam | #1509 |
| | | **W11** platform-side materialisation | #1510 |

Planning PR: #1465.

## 2. Settled decisions

These were decided after #1031 merged and are not open. They are recorded here
because the design docs predate them.

### 2.1 The manifest is the upper layer

The manifest document sits **above** the existing per-category APIs, services
and tables. Apply materialises **downward**: it writes real platform entities
through the same core services the TC Open API already uses. This is design
§2.3's "route B", stated as an ownership rule rather than an implementation
note.

```text
         config-manifest document          ← the source of truth users write
                    │
                    │  apply  (materialise downward)
                    ▼
   skills · identity · resources · mcp · engine_config · script
                    │                        ← existing entities, existing services
                    │  delivery
                    ▼
        BotConfigArtifact (teclaw)  ·  push / NAS (ARCA family)
```

The one place this rule cannot be followed as written is delivery **before a
container exists**, because the existing services require a live device. That is
**D4**, and it is unresolved.

### 2.2 `script` is not a special case

The startup script lives in the manifest document like every other category, and
apply materialises it into the existing `ac_bot_startup_script` store through
`BotStartupScriptService`. Consequences, all of them deliberate:

- `BaasService._get_start_cmd` never learns the manifest exists, so #935's
  byte-identical-without-a-script invariant is preserved by construction rather
  than defended by a test (design §10.4 still gets its test).
- The existing `GET/PUT/DELETE /openapi/v1/bots/{bot_id}/startup-script`
  endpoints keep working unchanged, because their table is still the
  materialisation target.
- Whether to later collapse `ac_bot_startup_script` into the manifest module is
  a reversible decision, deferred. This resolves design §8's open choice.

### 2.3 A manifest-materialised entity is indistinguishable from a manual one

Because the manifest sits one layer above the existing services, an entity it
creates is stored **exactly** as the same entity created by hand: same service,
same table, same shape. Nothing downstream — reconcile, compose, the UI, the
engine — can tell them apart, and nothing downstream needs to.

The manifest therefore does **not** stamp a marker on the entities themselves.
It keeps its own record of *what the last apply materialised*, which exists for
two jobs and no others:

1. **Un-marking.** `skills: []` or `DELETE` means "stop managing what you used
   to manage", and the current document alone cannot say what that was.
2. **`keep_last` and audit** (§2.8), which need the last successfully
   materialised content anyway.

This is deliberately smaller than design §3.2's "`managed by manifest` marker
visible in UI/API". That marker is a **v2 product nicety**, not a mechanism: no
component in v1 needs to ask "is this entity manifest-managed". The user-facing
question — *what is my current configuration, and what did the last apply do* —
is answered by `GET .../config-manifest` and `GET .../last-apply`, not by a
per-entity flag.

### 2.4 `skills` and `identity` lead the fetch-backed categories

They are the driving business scenario ("fetch content → install it").
`resources` follows in W6 with the directory work rather than riding along in
W5.

### 2.5 Capability is a two-value question — scope is new bots only

**This feature targets new bots.** In production a new bot resolves to exactly
two device providers: `baas` and `teclaw`. `arca`, `daas` and `local` are not
reachable for a bot created after this ships.

That collapses the capability model to questions the bot record answers on its
own, with no live lookup and no third "we could not find out" state:

- `is_teclaw(active_engine)` — the canonical engine test, which is also what
  `provider_resolver.resolve` itself keys on (`teclaw` or else the `baas`
  default);
- `bot_type == "desktop"` — a separate axis, and a bot-record field.

**Divergence from `engine-requirements.zh-CN.md` §2:** its matrix says
ARCA-direct and LOCAL/singlebox are refused at write time. Under new-bots-only
scope there are no such bots to refuse, so v1 keeps #935's verdict surface
(teclaw and desktop refused for `script`) and the matrix rows for those forms
are out of scope rather than newly enforced. Singlebox remains a dev/test
concern only: `script` there reports supported and is not dispatched — #935's
existing behaviour, not something this feature introduces.

### 2.6 A manifest `PUT` triggers a restart

**Divergence from design §3.1**, which makes `PUT` lazy (no apply; effective at
the next restart for unrelated reasons). Instead: writing a new manifest version
explicitly restarts the bot, so the running bot always reflects the manifest that
was last accepted.

This is the better semantics and it removes a hazard the lazy model carries — an
operator restarting a bot to clear a hang would otherwise silently pick up a
manifest edit made weeks earlier. With `PUT`-triggers-restart, a restart is not
a reconfiguration event; it is a replay of the configuration already in force.

One residual case survives and belongs to **D2**: a manifest whose source is a
**moving ref** (a branch rather than a tag or SHA) can resolve to different
content on a restart nobody associated with a configuration change.

### 2.7 Fetch failure: report, never disturb a running bot, gate the first boot

Sources are git or object storage — highly available, and an outage is a
fetch-time event rather than a configuration error. The policy:

| Situation | Behaviour |
| --- | --- |
| Bot already running, fetch fails | **Do nothing to it.** It keeps running its current configuration. Record the failure and surface it |
| First boot, fetch fails | The bot **does not become active**. There is no previous configuration to fall back to, and a bot running with configuration its manifest does not describe is worse than a bot that visibly failed to start |
| Either case | The user must be told — an apply report, and a surfaced notification |

`keep_last` follows from this rather than needing its own mechanism: "reuse what
we materialised last time" is what "do nothing to a running bot" already means.
The storage it needs is the same store §2.8 requires.

**Divergence from design §4.3**, which puts apply failure outside the readiness
gate in v1 and defers strict mode to v2. The first-boot arm above *is* a
readiness gate. It needs a landing point in both engine families — the start
command's exit status for the BaaS family, publish-poll for teclaw — and that is
part of W8.

### 2.8 The platform materialises and persists manifest content itself

**A hard requirement, from audit and reconciliation.** When content is fetched,
the platform stores its own copy rather than only passing it through to a
delivery channel. Later steps read from that copy.

Two properties beyond audit make this load-bearing:

- **It decouples the pipeline.** Fetch and delivery stop being one operation, so
  a delivery retry does not re-fetch and a source outage cannot corrupt a
  delivery already in flight.
- **It is where `keep_last` lives** (§2.7), so the two requirements are one
  store, not two.

Tracked as **W11**.

### 2.9 Validation and authorisation move to a reusable seam

Apply calls the service layer, but a good deal of validation and authorisation
currently lives in the `openapi_v1` routers — ownership and grant checks,
package validation, path-list validation. Apply must enforce the same rules, and
duplicating them by hand guarantees drift.

They are therefore re-expressed as a declared dependency/interceptor that both
the router and apply consume, following the precedent already set for
collaborator permissions (`CollaboratorPermissionInterceptor` internally, and the
`specs/2026-08-21-openapi-v1-collaborator-authorization-seam` table-driven gate
on the public surface). Reimplementing the checks is acceptable; reimplementing
them *twice, independently* is not.

Tracked as **W10**, sequenced before W4.

## 3. Design questions

### 3.1 D1 — capability model · #1466 · **RESOLVED**

**Was:** design §5.1 requires support to be answered from the bot record alone,
*and* requires ARCA-direct and LOCAL/singlebox to be refused — but neither
discriminator is on the bot record (`device_provider` lives on
`EntityDeviceBinding`; the LOCAL discriminator comes from the BaaS template).

**Resolved by scope.** New bots reach only `baas` and `teclaw` in production, so
there is no third case to detect and no ambiguous state to represent. See §2.5.
`engine-requirements.zh-CN.md` §2's matrix rows for ARCA-direct and
LOCAL/singlebox are out of scope rather than enforced; amending that matrix is a
separate docs change.

### 3.2 D2 — manifest-upgrade diff policy · #1467 · **OPEN**

The failure half of this question is settled (§2.7) and `keep_last` no longer
needs a mechanism of its own. What remains open is the part that was always the
harder half:

> **When a manifest moves from version N to N+1, what exactly happens to what is
> already there — and is the answer deterministic and published?**

"Declaration wins, drift is corrected" (design §3.2) is well defined for
whole-file replacement and undefined for everything else. The policy must answer,
per category:

1. **What is the convergence unit?** A file, a directory tree, a top-level config
   key, a whole archive.
2. **What happens to in-place modifications** made by the agent or the user
   between applies? Declaration-wins means they are discarded. That is defensible
   and must be *stated*, because it is destructive and currently unstated.
3. **What happens to entries that disappear** between N and N+1 — removed, or
   left behind un-managed?
4. **Agent-written files.** `MEMORY.md` is the clear case: an engine-generated
   file that the design merely warns against declaring. A warning is a
   documentation answer to a data-loss question.
5. **Agent-created files inside a declared directory.** design §3.2 replaces the
   tree wholesale, so they are deleted. Same requirement: state it.
6. **Moving refs** (§2.6's residual case). A branch can resolve to new content on
   a restart nobody associated with a configuration change. Options: refuse
   moving refs; or record the resolved SHA and reuse it for any restart not
   triggered by a manifest `PUT`, so only a `PUT` can change content.

**Done when** a written, per-category policy exists that a user could predict
behaviour from without reading the implementation, and W4's spec encodes it.

### 3.3 D3 — reconcile vs manifest-installed skills · #1468 · **RESOLVED**

**Correction to the original finding.** It read `skills_pool`'s "quarantine
cleanup" as a drift reaper that might delete unrecognised skills. It is not:
quarantine is a **migration** mechanism — during pool cutover the bot's legacy
skill directory is renamed aside and retained for 7 days before cleanup. No
component deletes skills for being unrecognised, so there was never a risk of
manifest-installed skills being reaped as drift.

**Resolved by §2.3.** A manifest-installed skill goes through the same upload and
activate path as a manual one and is stored identically, so `skills_pool` cannot
distinguish them and does not need to. Two facts worth carrying into W5 as
acceptance criteria rather than as a question:

- The skill must be **registered** (service call, DB row) — activation
  enumerates unregistered filesystem content into the pool without creating
  records, so dropping files on disk is the failure mode to avoid.
- teclaw does not participate in `skills_pool` at all, so this is a BaaS-family
  concern only.

### 3.4 D4 — delivery before a container exists · #1508 · **OPEN — the blocking question**

*Nothing downstream of W4 can be specified until this has an answer.*

**The problem.** design §3.1 promises that the first configuration a bot receives
already contains the manifest result — apply runs *before* the container. But the
services apply must call cannot run before a container:

- `IdentityService._device_write` resolves a `DeviceFileSystem` through the
  device binding and **raises if unbound**;
- `EngineConfigService.write_bot_config` does the same, and raises
  `EngineStageNotLiveError` when the named stage has nothing up.

This is deliberate, not accidental. `identity.py` states it: *"a bot with no
resolvable device context is a bug and surfaces as the resolver's error (fail
early, never silently touch a dead local path)."*

**teclaw is the easy half.** The artifact *is* the delivery vehicle, so
materialised content becomes `{store, path}` refs in `BotConfigArtifact` and the
first artifact carries them. One backend change is required and should be named
rather than discovered: `ConfigComposerInputCollector.identity_files()` currently
returns `[]` for teclaw by design —

```python
if req.engine_type == "teclaw":
    return []   # teclaw owns its identity files in the running container;
                # gathered from the engine at promotion, like resources
```

— so that branch must learn about manifest-materialised identity. The teclaw
*team* still does zero work; the "delivery layer: zero additions" claim in
`engine-requirements.zh-CN.md` §1 does not survive, but only on the backend side.

**The BaaS family is the open part.** Content must reach the bot's workspace
before the container starts. The path is computable without a device —
`get_bot_file_path` is a pure `path_factory` computation, and the composer
already calls `.exists()` on those paths, so the backend can see the bot-data
root. But *writing* there is exactly what the existing service refuses to do
without a device context, and bypassing it is the one thing §2.1 forbids.

So the answer is a **new, explicitly sanctioned delivery protocol** for the
pre-container case — not a bypass of the existing one. Options to work through:

| | Approach | Question it raises |
| --- | --- | --- |
| a | A device-less writer that shares path resolution with `DeviceFileSystem` and is only reachable before first boot | What guarantees it is *only* used pre-boot, so "never touch a dead local path" still holds afterwards? |
| b | Deliver during the start sequence — the container pulls materialised content on boot | Reintroduces an in-container step; interacts with #935's start-command contract |
| c | Extend the device abstraction with a pre-binding mode the dispatcher can resolve | Largest change, cleanest boundary |

**Blocks:** the delivery half of W5, W6 and W8. W1–W4 and W10/W11 are unaffected
and can proceed while this is settled.

## 4. External confirmations

Owed by other teams. **None of these blocks W1–W6.** X1 and X2 are the long
pole and should be sent now, because their owners are not on this team.

| # | What | Design ref | Gates | Owner |
| --- | --- | --- | --- | --- |
| **X1** | Company git hosting capability: repo-scoped read tokens, archive-by-`ref`+subpath API, refs-resolution API, auth header shape, platform-side reachability | O11 | **W7** | backend + git hosting + business |
| **X2** | teclaw: T1 readiness ordering, T2 convergent re-delivery, T3 how `config/teclaw.json` reaches a *first* instance and when the engine reads it | T1–T3 / O1 | **W8** (teclaw arm); T3 also affects the `engine_config` materialiser in W4 | teclaw + backend |
| **X3** | `cli_tools`: teclaw executable-bit / PATH / sandbox policy (T4), ARCA-family PATH injection point (A2), target architecture (O9) | T4 / A2 / O9 | **W9** | teclaw + engines + business |
| **X4** | Is `desktop` in the v1 manifest surface, and by which delivery path? | O2 | **W1**'s capability table | desktop owner |

X4 has a cheap default if unanswered: mark desktop **unsupported** for manifest
in v1 and widen later. Fail-closed is the design's own rule (§5.1), and widening
a capability is compatible where narrowing one is not.

## 5. Work items

### Wave 1 — foundations (W1, W2, W3, W10, W11 are mutually independent)

---

#### W10 — A service-layer seam apply and the API can share · #1509

**Goal.** The validation and authorisation the public API enforces becomes
callable by something that is not an HTTP request, so apply enforces the same
rules without a second copy of them.

**In scope.** For the categories apply touches (skills, identity, resources,
mcp, engine_config): identify the checks that currently live in the
`openapi_v1` routers — ownership and grant adjudication, package and payload
validation, path-list validation — and re-express them as a declared
dependency/interceptor both entry points consume.

**Out of scope.** Changing *what* any check decides. This is inert on arrival:
the same callers get the same answers.

**Depends on.** — · **Blocked by.** —

**Done when.**

- [ ] Apply can obtain the same verdict the router would, through one declared
      seam, for every category in scope.
- [ ] No check that gates a public-API write is reachable only from a router
      function body.
- [ ] Existing public-API behaviour is unchanged — same status codes, same
      messages, same permission bar — with the existing tests unmodified.
- [ ] The pattern matches the collaborator precedent rather than inventing a
      third shape (`CollaboratorPermissionInterceptor`;
      `specs/2026-08-21-openapi-v1-collaborator-authorization-seam`).

**Notes.** Sequenced before W4 because W4's materialisers are its first consumer.
Doing it after means writing the checks twice and deleting one copy later.

**Size.** Medium; spread across five router groups.

---

#### W11 — Platform-side materialisation and persistence · #1510

**Goal.** Fetched content is stored by the platform as its own durable copy, and
later steps read from that copy rather than re-fetching.

**In scope.** The content store, its addressing (content hash), retention, and
the read path that delivery and audit both use.

**Depends on.** W2 (the fetcher whose output it stores) · **Blocked by.** —

**Done when.**

- [ ] Every fetched object is persisted with enough provenance to answer, later,
      *what exactly did this bot receive, and where did it come from* — source,
      resolved ref/SHA or digest, fetch time, and the bytes.
- [ ] Delivery reads from the store, so a delivery retry never re-fetches and a
      source outage cannot corrupt a delivery already in flight.
- [ ] The store is what `keep_last` reads (§2.7) — one mechanism, not two.
- [ ] Retention is explicit, and stated against the audit requirement rather
      than chosen incidentally.
- [ ] Credentials are never persisted alongside content; provenance records the
      credential **name** only.

**Notes.** A hard requirement (§2.8), and it also decouples fetch from delivery,
which is why W4 depends on it rather than treating it as an add-on.

**Size.** Medium.

---

---

#### W1 — Manifest document: storage, schema v1, capability, API · #1469

**Goal.** A bot can carry a config-manifest document that is stored, validated
and readable, and a caller can ask which categories that bot supports. Nothing
is applied.

**In scope.**

- New module `core/bot_config_manifest/` with a `README.md` carrying the
  Context Boundary block required by `docs/arch/context-boundary-format.md`.
- DDL under `core/bot_config_manifest/sql/`. One row per bot; uniqueness on
  `(avernet_tenant, manifest_key)` where `manifest_key = sha256(env, entity_id,
  bot_id)` — the same InnoDB 3072-byte index budget and the same tenancy
  reasoning as `ac_bot_startup_script`, which documents both.
- Repository contract `core/repository/protocols/bot/config_manifest.py` and
  implementation `core/repository/implementations/bot/config_manifest/`, the
  protocol declared as a base so an omitted member fails at construction.
- Service API contract `api/bot_config_manifest_service.py`, registered in the
  conformance `_PAIRS`.
- Schema v1 parse + validate, covering: `schema_version` (unknown ⇒ refuse);
  top-level `sources`; `manifest.{mcp,resources,skills,engine_config,identity,cli_tools}`;
  `script`.
- Routes `GET`/`PUT`/`DELETE /openapi/v1/bots/{bot_id}/config-manifest` and
  `GET /openapi/v1/bots/{bot_id}/config-manifest/capabilities`, each with its
  `ADMISSION` row, under `PublicAPIRoute`.
- A feature flag gating the surface until W5 lands, since these routes are
  public and apply does not exist yet.

**Out of scope.** Any apply. Any fetching. Credentials. Any change to the
existing `/startup-script` endpoints. Any change to `BaasService`.

**Depends on.** —
**Blocked by.** — (D1 is resolved; see §2.5). **X4** (desktop) affects one row of
the capability table and has a fail-closed default.

**Done when.**

- [ ] A bot that never had a manifest reads as an empty document, not an error —
      the same "absent is not an error" rule `bot_startup_script` established.
- [ ] Validation refuses, each with a message naming the offending entry:
  - two or more of `from` / `source` / `content` / a registry ref on one entry;
  - `from` naming a source not declared in `sources`;
  - `digest` on a git-source entry (commit SHA is the digest — schema §2.2);
  - `auth` on an entry that uses `from` (auth is declared on the named source)
    or on a `content` entry;
  - `apply_once` in any position — a v1 reserved word;
  - an unknown `${...}` placeholder; only `OCB_BOT_ID`, `OCB_ENGINE_TYPE`,
    `OCB_ENV`, `OCB_TENANT` are accepted;
  - a `resources.path` that is absolute or contains `../`;
  - a `resources` entry whose `path` lies under another directory entry's
    `path` (the nesting ban, schema §3.2);
  - an `identity.type` outside the engine's legal set — `VALID_IDENTITY_FILES`
    generally, `CLAUDE.md` only for `claude_code`;
  - any limit in schema §5 checkable at write time (document size, per-category
    entry count, inline `content` size).
- [ ] `PUT` is all-or-nothing: a document with one unsupported category is
      refused whole, with a per-entry reason list, and nothing is written.
- [ ] The capability resolver is **one function** used by both the read and the
      write path, so `GET .../capabilities` can never claim support that `PUT`
      then refuses. Unknown engine ⇒ unsupported.
- [ ] Capability is computed from the bot record alone — `is_teclaw(active_engine)`
      and `bot_type == "desktop"` — with no device-binding lookup and no third
      "unknown" state (§2.5).
- [ ] `DELETE` removes the document. Entities previously materialised from it
      are untouched — this item has none to forget yet; dropping them from the
      apply record lands with that record in W4.
- [ ] Tenancy: two bots sharing a `bot_id` across tenants cannot read or
      overwrite each other's document; the tenant guard is registered.
- [ ] The stored document round-trips byte-exact, including a `script` body
      containing quotes, `$(id)` and `{token}`.

**Notes.** A `PUT` carrying `script` stores it and does nothing else until W4
materialises it. That is why the feature flag exists.

**Size.** Large. The biggest single item after W4.

---

#### W2 — Guarded fetcher and archive pipeline · #1470

**Goal.** One component that fetches a caller-supplied URL safely, and one that
unpacks an archive safely. No manifest concepts.

**In scope.** The fetcher, the unpacker, and a credential-injection Protocol
declared but unbound (W3 binds it).

**Out of scope.** Credential storage. Git sources (W7). Any caller.

**Depends on.** —
**Blocked by.** —

**Done when.**

- [ ] Scheme is restricted to `https`; `http` only against a deployment-level
      explicit allowlist.
- [ ] After DNS resolution the target IP is rejected when it is loopback,
      link-local (including `169.254.169.254`), unique-local, multicast,
      reserved, or RFC1918 — unless deployment-level allowlisted.
- [ ] The connection is made to the **validated** address, so a name that
      re-resolves between check and connect cannot reach a blocked target.
- [ ] Every redirect hop is re-validated by the same rules, and hop count is
      capped.
- [ ] The per-entry byte cap is enforced **while streaming**, not from
      `Content-Length` alone.
- [ ] Per-entry timeout and a per-apply total budget are both enforced;
      concurrency is capped.
- [ ] `sha256` digest verification is available and a mismatch is reported as a
      fetch failure, not a corrupt success.
- [ ] Fetched bytes are only ever written or hashed — never executed, never
      passed to a shell.
- [ ] Unpack supports `zip` and `tar.gz` and rejects: path traversal, absolute
      member paths, symlink and hardlink members that escape the root, and
      device/special members.
- [ ] Unpack enforces a member-count cap and an unpacked-total-size cap, so a
      small archive cannot expand without bound.
- [ ] `strip_components` removes exactly the declared number of leading path
      segments and **never** auto-detects a single top-level directory —
      identical input must behave identically regardless of the archive's
      internal shape (schema §3.2).
- [ ] Permission bits are flattened: nothing unpacked is executable.

**Notes.** `src/engine/.../plugins/resource_materialization.py` is the nearest
precedent in the monorepo and is worth reading first, but it lives in the engine
repo — this is new code on the backend side, which has no SSRF guard today.
Place it inside `core/bot_config_manifest/` while it has one consumer; promote
it only if a second appears.

**Size.** Medium.

---

#### W3 — Source credentials · #1471

**Goal.** A tenant can register a named credential once and reference it from
many manifests, and the platform can present it when fetching — without the
secret ever being readable back, logged, or reaching a container.

**In scope.** Storage, encryption, the prefix authorisation model, the API, and
binding W2's injection port.

**Out of scope.** Any credential type other than `header`. Any use by apply.

**Depends on.** —
**Blocked by.** —

**Done when.**

- [ ] Tenant-level table keyed by `(avernet_tenant, name)`; `name` is a free
      identifier with no derived relationship to the hosts in
      `allowed_prefixes`.
- [ ] The secret is stored **reversibly encrypted** via the existing
      `TokenVault` (`enc:v1:` prefix, AES-GCM through
      `utils/secret_utils.symmetric_encrypt`) with the master key resolved by
      `SecretResolver`. No new crypto is introduced.
- [ ] **The new fail-closed guard:** in a production profile, a credential write
      is **refused** when no master key resolves. `TokenVault` today falls back
      to storing plaintext with no prefix, which is correct for singlebox/CI and
      unacceptable for these secrets — one key-store misconfiguration would
      otherwise leave every tenant's tokens in the clear.
- [ ] `allowed_prefixes` is mandatory, at least one entry, each an absolute
      `https` prefix.
- [ ] Prefix matching is on **path-segment boundaries**: a target must equal the
      prefix or begin with prefix + `/`. `…/team/content` must not authorise
      `…/team/content-secret`. Covering a whole origin requires writing
      `https://host/` explicitly.
- [ ] A target outside every prefix makes the entry **fail** — never
      "continue without the credential".
- [ ] A redirect that leaves the authorised prefix **fails**; the credential is
      never carried across it and never stripped-and-followed.
- [ ] `GET` returns masked metadata only — `has_secret`, `header_name`,
      `allowed_prefixes`, `updated_at` — and never the value, on any path.
- [ ] Reserved types `oss_aksk` and `basic` are refused at write with a message
      saying they are reserved, so the discriminator is real from day one.
- [ ] The value appears in no log, no error message and no apply report; only
      the name does.
- [ ] Rotation is a re-`PUT` of the same name and does not trigger an apply.

**Size.** Medium.

---

### Wave 2 — the apply engine and the first materialisers

---

#### W4 — Apply engine, apply record, and the no-fetch materialisers · #1472

**Goal.** A manifest can be applied on demand, converging the bot's entities
toward the document, with a report of what happened — proven on the three
categories that need no fetching.

**In scope.**

- The apply orchestrator: bot-level serialisation, category ordering, per-entry
  outcome classification, `on_fetch_failure` policy handling.
- The **apply record** — the manifest module's own note of what the last apply
  materialised (§2.3), which exists for un-marking and for `keep_last`/audit and
  stamps no marker on the entities themselves.
- Apply report storage and `GET .../config-manifest/last-apply`, in the shape of
  design §7.
- `POST .../config-manifest/apply`, including `dry_run=true` returning the plan
  without acting.
- Materialisers for the three no-fetch categories: `mcp` (registry reference →
  the existing enable + configure service), `engine_config` (top-level key merge
  via `EngineConfigService.write_bot_config`), and `script` (→
  `BotStartupScriptService`).

**Out of scope.** Fetching. Lifecycle triggers (W8) — explicit apply is the only
entry point in this item.

**Depends on.** W1, W10 (the seam apply calls through), W11 (the store
materialised content lands in).
**Blocked by.** **D2** — the upgrade diff policy must be written before the
convergence logic can be specified. X2/T3 affects the teclaw behaviour of the
`engine_config` materialiser but does not block the item.

**Done when.**

- [ ] **Convergence:** applying the same unchanged document a second time
      reports every entry `unchanged` and performs no writes.
- [ ] Outcomes are classified per entry as `created` / `updated` / `unchanged` /
      `skipped` / `failed`, and the apply result is `SUCCEEDED` / `PARTIAL` /
      `FAILED` accordingly.
- [ ] Category order within one apply is `engine_config → identity → resources →
      skills → mcp`, with `script` materialised last.
- [ ] Two applies against the same bot serialise; the lock follows the existing
      `BotRestartLockRepository` pattern rather than a new mechanism.
- [ ] The §2.7 failure policy holds: a fetch failure against an already-running
      bot changes nothing about it and is reported; a fetch failure on a first
      boot leaves the bot inactive rather than active-and-misconfigured.
- [ ] Apply enforces the same validation and authorisation the public API does
      by calling W10's seam — not a second, hand-written copy of the checks.
- [ ] `engine_config` merges by **top-level key** — declared keys win,
      undeclared keys are untouched, and `engine_ext` is unreachable from the
      manifest on every path.
- [ ] `mcp` refuses a `server_code` the tenant has no permission for, reusing the
      existing permission check rather than a copy.
- [ ] A category present but **empty** (`skills: []`) drops those entries from
      the manifest's record without deleting the assets; they become ordinary
      manual entities.
- [ ] `DELETE` of the manifest does the same for every category and deletes no
      asset — "removing the declaration is not removing the thing".
- [ ] A failure part-way through leaves the record consistent: nothing recorded
      as materialised that was not.
- [ ] `dry_run` performs no write of any kind, including to the report store.
- [ ] The apply report records credential **names** only, never values.

**Notes.** `script` is in this item on purpose: its target service already
exists and is hardened, which makes it the cheapest end-to-end proof that
"materialise through the existing service" works before any fetching is
involved.

**Size.** Large. The other big one alongside W1.

---

#### W5 — `skills` and `identity` from URL sources · #1473

**Goal.** The driving business scenario works: content on a caller's own
service is fetched at apply time and installed as real skills and identity
files.

**In scope.** The two materialisers and `${OCB_*}` substitution in source URLs.

**Out of scope.** Named sources and git (W7). `resources` (W6).

**Depends on.** W2, W3, W4.
**Blocked by.** **D4** for the pre-boot delivery half on the BaaS family;
materialisation and entity creation are unaffected. D3 is resolved (§3.3).

**Done when.**

- [ ] `identity` entries materialise through the existing `IdentityService`
      write path; `type` is validated against the bot's engine at write time,
      not silently skipped at apply time.
- [ ] `identity` supports both `source` and inline `content`; `auth`, `digest`
      and `on_fetch_failure` are refused on an inline entry.
- [ ] `skills` entries materialise through the existing local-skill upload +
      activate path, so an installed skill is indistinguishable from a manually
      uploaded one.
- [ ] **`digest` is mandatory** for non-git skill sources — a skill carries
      code, and an unpinned URL means fetching whatever is there at each apply.
      A skill entry without one is refused at `PUT`.
- [ ] Archive vs. plain-directory is auto-detected by content type / extension,
      with `unpack` accepted only as an explicit override.
- [ ] `${OCB_*}` substitution happens before fetch and before prefix
      authorisation, so a substituted URL cannot escape its credential's
      `allowed_prefixes`.
- [ ] A manifest-installed skill is **registered** through the service (DB row +
      files), never dropped on disk — activation enumerates unregistered
      filesystem content into the pool without creating records (§3.3).
- [ ] A test shows a manifest-installed skill is indistinguishable from the same
      skill uploaded by hand, and survives a skills-pool reconcile.
- [ ] Fetch failure of one entry does not abort the others under the default
      policy, and the bot still starts.

**Size.** Medium-large.

---

### Wave 3 — completing v1

---

#### W6 — `resources`, files and directories · #1474

**Goal.** Workspace resources, including whole directories delivered as
archives.

**In scope.** File entries; directory entries with archive unpacking;
directory-level ownership semantics; teclaw per-file expansion.

**Depends on.** W5 (the fetch-to-entity pattern it follows).
**Blocked by.** **D4** for the pre-boot delivery half on the BaaS family.

**Done when.**

- [ ] A file entry materialises through the existing resource service at a
      workspace-relative logical `path`; physical placement stays the engine's
      decision.
- [ ] A directory entry's **convergence unit is the whole archive**: unchanged
      content ⇒ `unchanged` with no per-file comparison and no writes.
- [ ] Directory-level ownership: on change, the tree under `path` is replaced
      wholesale — files present before and absent from the new archive are
      removed, including manually added ones. Nothing outside `path` is touched.
- [ ] Replacement is atomic: unpack to a temporary location, then rename. No
      apply leaves a half-old, half-new tree, including when it fails mid-way.
- [ ] The nesting ban is enforced at `PUT` (W1) and re-checked at apply.
- [ ] On teclaw the materialised tree expands per-file into `ResourceRef`
      entries; `BotConfigArtifact` is unchanged, and the T5 subtree optimisation
      is **not** taken in v1.
- [ ] The schema §5 limits for archives apply: per-archive size, unpacked size,
      and member count.

**Size.** Medium-large.

---

#### W7 — Named sources and git sources · #1475

**Goal.** One `ref` change upgrades a whole configuration atomically, and
content hosted in the company's git service is a first-class source.

**In scope.** `sources` + `from`; git ref resolution and archive retrieval.

**Depends on.** W5.
**Blocked by.** **X1** — this item cannot start without the git hosting answers.

**Done when.**

- [ ] `sources` declares named sources; `from` references one; `from` and inline
      `source` are mutually exclusive; an unreferenced source is a warning in the
      `PUT` response, not an error.
- [ ] `auth` is declared on the source, not on entries that use `from`.
- [ ] **Atomic upgrade:** changing one `ref` moves every entry referencing that
      source to the same commit within a single apply — no half-upgraded state.
- [ ] A git `ref` is resolved to a commit SHA at each apply point; the apply
      report records both the declared `ref` and the resolved SHA.
- [ ] The same `{git, ref}` is fetched **once** per apply and reused across
      every entry referencing it.
- [ ] Git sources are compiled to an HTTPS archive fetch through the hosting
      service's API and reuse W2's fetcher and unpacker. **No `git clone` runs
      inside the backend process.**
- [ ] A re-pointed tag converges to the new content at the next apply — moving a
      tag changes what the declaration means.
- [ ] Directory entries from a git source need no `unpack` or
      `strip_components`.

**Size.** Medium-large, and the estimate is soft until X1 lands.

---

#### W8 — Lifecycle apply points · #1476

**Goal.** The business ask, delivered: a bot configures itself when it comes up,
with no user action.

**In scope.** Apply at bot creation (ARCA family before the start command is
composed; teclaw before the first artifact is assembled), at publish/republish,
and at rebuild-style restart.

**Depends on.** W4, W5, W6.
**Blocked by.** **D4** — without a pre-boot delivery answer, "the first config
already contains the manifest result" cannot be implemented for the BaaS family
at all. **X2** additionally gates the teclaw arm (T1–T3).

**Done when.**

- [ ] The **first** configuration a bot receives already contains the manifest
      result — there is no "start, then patch it in" window.
- [ ] Scale-out does **not** re-apply; instances stay identical because they
      share one platform state. This is #926's actual requirement.
- [ ] A manifest `PUT` **triggers a restart** (§2.6), so the running bot always
      reflects the manifest last accepted and an unrelated restart is a replay
      rather than a reconfiguration.
- [ ] The §2.7 readiness policy holds, and is the divergence from design §4.3
      most in need of testing on both engine families: a fetch failure on a
      **first** boot leaves the bot **inactive**; the same failure against an
      already-running bot changes nothing about it. Landing points are the start
      command's exit status (BaaS family) and publish-poll (teclaw).
- [ ] Whatever D2 decides about moving refs is enforced here — this is where
      restarts nobody associated with a config change actually happen.
- [ ] `script` runs **after** manifest entities are delivered, so a script may
      assume its declared skills and identity are in place (design §3.4).
- [ ] The no-script start command remains byte-identical to today (design §10.4),
      with #935's existing assertion retained.

**Size.** Large, and the riskiest — it touches `create_flow`, `publish_flow` and
`TeclawProvisionService`.

---

#### W9 — `cli_tools` — deferred · #1477

**Goal.** Command-line tools the model can invoke, installed declaratively.

**Status.** Schema is settled (§3.7); delivery is deferred by business priority
in the design itself. Not scheduled.

**Depends on.** W8.
**Blocked by.** **X3** in full — without the teclaw policy answer the capability
matrix cannot be written, and without the PATH injection points the ARCA arm has
nowhere to land.

**Done when (sketch).** `digest` mandatory and enforced as the convergence key;
static binary and archive forms only; a platform-defined logical tool directory
on the agent process's PATH; teclaw refused at write if X3 comes back negative.

**Size.** Medium, pending X3.

---

## 6. Sequencing

```text
wave 1   W1 ─┐   W2 ─┬─► W11 ─┐   W3 ─┐   W10 ─┐     (mutually independent)
             │       │        │       │        │
wave 2       └───────┴────────┴───────┴────────┴─► W4
                                                    │
                                                    └─► W5
                                                         │
wave 3                                                   ├─► W6 ──┐
                                                         │        ├─► W8
                                                         └─► W7 ──┘
                                                                   │
deferred                                                           └─► W9
```

**Critical path:** W1 → W4 → W5 → W6 → W8.

**Available parallelism:** W2, W3, W10 and W11 all run alongside W1 with no
coordination — W11 needs only W2's output shape. That is five independent
starting points, which is the most this plan will ever offer at once.

**Gating.** D2 must be answered before W4 is specified. **D4 must be answered
before the delivery half of W5, W6 or W8 can be specified at all** — and it is
the largest open question in the feature. X1 gates W7; X2 gates W8's teclaw arm;
X3 gates W9.

**Why lifecycle wiring is last.** Explicit `POST .../apply` exercises the whole
engine from W4 onward, so W8 touches the create and publish flows only after the
thing it triggers is proven. The trade is stated plainly: **nothing before W8
delivers the business ask.** W4's explicit apply is a validation vehicle, not the
product.

## 7. Conventions for each work item

- **SDD.** Each item gets `specs/<yyyy-mm-dd>-<slug>/{spec,plan,tasks}.md`,
  following `specs/2026-08-10-bot-startup-script/` as the model — that feature is
  the nearest precedent in both shape and subject.
- **One PR per item**, titled `<type>(backend): <outcome>` per `AGENTS.md`, with
  the Problem / Solution / Validation sections filled in and the Spec section
  pointing at the item's spec directory.
- **New modules** carry a `README.md` with the Context Boundary block
  (`docs/arch/context-boundary-format.md`).
- **Repositories** split protocol and implementation under
  `core/repository/{protocols,implementations}/bot/`, with the protocol declared
  as a base.
- **Service API contracts** live in `api/` and are registered in the conformance
  `_PAIRS`; core never imports that layer.
- **Every new route** gets its `ADMISSION` row — the authorization scaffold
  refuses an unlisted route.
- **Pre-push** runs lint-only by default; run `OCB_PRE_PUSH_RUN_CI=1` for items
  touching the apply path.

## 8. Traceability

| Item | Design sections it implements |
| --- | --- |
| W1 | design §5.1, §6, §8; schema §1, §2.0, §2.1 (validation only), §3.1–§3.7 shapes, §4, §5 |
| W2 | design §4.1, §4.2, §4.4; schema §3.2 unpack guards, §5 |
| W3 | design §4.5; schema §2.1 |
| W4 | design §3.2, §3.3, §3.4, §4.3, §6, §7, §10.2, §10.3; schema §1 empty-category semantics, §3.1, §3.4, §3.6 |
| W5 | design §4.1, §4.2; schema §3.3, §3.5 |
| W6 | schema §3.2; design §10.1 |
| W7 | design §4.2, §10.5; schema §2.2, §2.3 |
| W8 | design §3.1, §3.4, §4.3, §10.1, §10.4 |
| W9 | schema §3.7; engine-requirements T4, A2, O9 |
| W10 | no design section — arises from §2.9, an implementation constraint the design does not cover |
| W11 | no design section — arises from §2.8, a requirement added after #1031 |

Design decisions this document does **not** re-open: the manifest/script split
(design §2.1), route B (design §2.3), the four rejected alternatives (design
§2.4), platform-side fetch (design §4.1), and the `BotConfigArtifact` schema
(design §5.2 — unchanged, though D4 notes that the backend's teclaw compose
branch is not).

Points where this document **diverges** from the merged design, each argued in
place: §2.3 (the managed marker shrinks to an internal record), §2.5 (capability
scope), §2.6 (`PUT` triggers a restart rather than being lazy — design §3.1),
§2.7 (a first-boot readiness gate, which design §4.3 defers to v2), and §2.8
(platform-side materialisation, which the design does not have). Amending the
Chinese docs to match is a separate change, deliberately not made here.
