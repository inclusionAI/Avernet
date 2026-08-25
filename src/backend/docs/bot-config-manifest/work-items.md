# Bot Config Manifest — Implementation Work Items

> **Status: PLANNING.** The design is settled in the four documents beside this
> one (`README.zh-CN.md`, `design.zh-CN.md`, `manifest-schema.zh-CN.md`,
> `examples.zh-CN.md`, `engine-requirements.zh-CN.md`, merged as #1031). This
> document turns that design into a sequence of independently reviewable work
> items. It contains no new design decisions except where a section is
> explicitly labelled as a blocking question.

## 1. How to use this document

Each work item **W1–W9** is scoped to be picked up on its own, by one person or
one session, with no need to re-derive the design from the Chinese docs. An item
states what it delivers, what it deliberately leaves alone, what must land
first, and criteria concrete enough to review against.

Three kinds of entry gate the work and are tracked separately from it:

| Prefix | Meaning |
| --- | --- |
| `W1`–`W9` | Implementation work items — one PR each |
| `D1`–`D3` | **Blocking design questions**, found while checking the design against the code. Each gates a specific work item and must be resolved before it starts |
| `X1`–`X4` | **External confirmations** owed by other teams. None blocks W1–W6; they gate W7, W8 and W9 |

Nothing in the design docs is amended here. Where this document disagrees with
them (D1), it says so and leaves the resolution to that question's own
discussion.

### Trackers

Each entry has a GitHub issue. The issue carries scope and acceptance criteria;
this document remains the source of truth for detail and for how the items fit
together.

| | Issue | | Issue |
| --- | --- | --- | --- |
| **D1** capability model | #1466 | **W4** apply engine | #1472 |
| **D2** `keep_last` storage | #1467 | **W5** skills + identity | #1473 |
| **D3** reconcile verification | #1468 | **W6** resources | #1474 |
| **W1** manifest document | #1469 | **W7** named + git sources | #1475 |
| **W2** guarded fetcher | #1470 | **W8** lifecycle apply points | #1476 |
| **W3** source credentials | #1471 | **W9** `cli_tools` (deferred) | #1477 |

Planning PR: #1465.

## 2. Settled decisions

These were decided after #1031 merged and are not open. They are recorded here
because the design docs predate them.

### 2.1 The manifest is the upper layer

The manifest document sits **above** the existing per-category APIs, services
and tables. Apply materialises **downward**: it writes real platform entities
through the same core services the TC Open API already uses, and never writes a
device or filesystem directly. This is design §2.3's "route B", stated as an
ownership rule rather than an implementation note.

```text
         config-manifest document          ← the source of truth users write
                    │
                    │  apply  (materialise downward)
                    ▼
   skills · identity · resources · mcp · engine_config · script
                    │                        ← existing entities, existing services
                    │  existing delivery, unchanged
                    ▼
        BotConfigArtifact (teclaw)  ·  push / NAS (ARCA family)
```

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
  a reversible decision, deferred. Nothing above that table depends on the
  answer. This resolves design §8's open choice.

### 2.3 The manifest owns its own record of what it materialised

The "managed by manifest" marker (design §3.2) is **a record held by the
manifest module**, keyed by `(tenant, bot, category, entity key)` — not a column
added to the five entity stores. The manifest populates those entities; it does
not modify their schemas.

The cost is stated rather than discovered: the owning services do not see the
marker in their own queries, so anything that needs to know "is this entity
manifest-managed" must ask the manifest module. Two consequences are tracked as
work: the UI/API join (W4) and the skills-pool reconcile interaction (**D3**).

### 2.4 `skills` and `identity` lead the fetch-backed categories

They are the driving business scenario ("fetch content → install it"). `resources`
follows in W6 with the directory work rather than riding along in W5.

## 3. Blocking design questions

### D1 — The capability model contradicts the shipped code · gates W1 · #1466

**The contradiction.** design §5.1 requires two things that cannot both hold
today:

1. support must be answered **from the bot record alone**, never by reading the
   live container — because a lookup failure would otherwise become a third
   "we could not find out" state and make an unrelated blip look like a verdict
   about the bot;
2. `ARCA-direct` legacy bots and `LOCAL`/singlebox deployments must be
   **refused at write time** rather than silently not executing
   (`engine-requirements.zh-CN.md` §2, and design §5.1's "堵上 #935 已知的静默坑").

**Why they conflict.** Neither discriminator is on the bot record:

- `device_provider` lives on `EntityDeviceBinding` — the live binding, which is
  exactly what (1) forbids reading;
- `LOCAL` is derived from the BaaS *template's* configured type, which is
  deployment data; `ac_bots` carries no field distinguishing a LOCAL-templated
  install from any other baas-backed one.

This is not an oversight in #935 — `core/bot_startup_script/services/_support.py`
documents both cases at length as the reason it refuses **only** teclaw and
desktop, and records dropping the provider check as a deliberate trade.

**Options.**

| | Approach | Cost |
| --- | --- | --- |
| **a** | v1 keeps #935's verdict surface: teclaw and desktop refused, ARCA-direct and LOCAL left optimistic. Amend the matrix in `engine-requirements.zh-CN.md` | The silent gap #935 documented stays open for `script` |
| **b** | Stamp the provisioning provider / template type onto the bot record at create time, making the question statically answerable | New column, `create_flow` change, and a backfill story for every existing bot — none of it scoped |

**Recommendation: (a) for v1**, with (b) filed as its own work item if the silent
gap matters to the business. (b) is a change to how bots are created, and pulling
it into the manifest's critical path buys nothing the manifest itself needs —
`manifest` is applied platform-side and is unaffected by either discriminator.
Only `script` capability turns on this.

### D2 — `keep_last` has no designed storage · gates W4 · #1467

`keep_last` is the **default** `on_fetch_failure` policy (design §4.3): "reuse
the last successfully materialised version; if there has never been one, record
`skipped`". Nothing in the design gives that state a home — the apply report
(§7) is a record of one apply, not a durable per-entry pointer.

Resolve before W4, because it decides the shape of the ownership record:

- fold it into the ownership record of §2.3, so one row per managed entity
  carries both "the manifest owns this" and "the last source resolution that
  succeeded"; or
- a separate table, if the lifetimes turn out to differ.

One subtlety either way: an entry whose **first** apply fails has no last-good
version, and must record `skipped` rather than fail — so "no previous success"
has to be representable, not merely absent.

### D3 — skills-pool reconcile vs manifest-populated skills · gates W5 · #1468

design §10.2 asks the implementer to "confirm quarantine cleanup has test
coverage for managed entities". That is an assumption, not a verified fact, and
§2.3 sharpens it: because the marker is held by the manifest module,
`skills_pool`'s own reconcile and quarantine queries cannot see it.

Verify against `core/skills_pool/reconcile_service.py` and `quarantine.py`
whether a skill installed by apply — which enters through the same
`LocalSkillUploadService` path a manual upload uses — is indistinguishable from
a manually uploaded one. If it is, there is no problem and the finding closes
with a test. If reconcile keys on something apply does not set, the manifest
must either set it or be taught to reconcile alongside.

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

### Wave 1 — foundations (W1, W2, W3 are mutually independent)

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
**Blocked by.** **D1** (capability model), **X4** (desktop).

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
- [ ] Capability is computed without reading the live container or any device
      binding (subject to D1's resolution).
- [ ] `DELETE` removes the document. Entities previously materialised from it
      are untouched — this item has none to un-mark yet; the un-marking lands
      with the ownership record in W4.
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

#### W4 — Apply engine, ownership record, and the no-fetch materialisers · #1472

**Goal.** A manifest can be applied on demand, converging the bot's entities
toward the document, with a report of what happened — proven on the three
categories that need no fetching.

**In scope.**

- The apply orchestrator: bot-level serialisation, category ordering, per-entry
  outcome classification, `on_fetch_failure` policy handling.
- The **ownership record** of §2.3 — the managed-by-manifest marker — plus
  whatever D2 decides about `keep_last` state.
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

**Depends on.** W1.
**Blocked by.** **D2**. X2/T3 affects the teclaw behaviour of the
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
- [ ] `on_fetch_failure` is honoured: `keep_last` (default) reuses the last good
      version or records `skipped` when there is none; `skip` continues;
      `fail` aborts the apply and records the remainder `skipped`.
- [ ] `engine_config` merges by **top-level key** — declared keys win,
      undeclared keys are untouched, and `engine_ext` is unreachable from the
      manifest on every path.
- [ ] `mcp` refuses a `server_code` the tenant has no permission for, reusing the
      existing permission check rather than a copy.
- [ ] A category present but **empty** (`skills: []`) un-marks previously managed
      entities of that category without deleting the assets.
- [ ] `DELETE` of the manifest un-marks every managed entity and deletes no
      asset — "removing the declaration is not removing the thing".
- [ ] A failure part-way through leaves the ownership record consistent: no
      entity is marked managed that was not actually materialised.
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

**In scope.** The two materialisers, `${OCB_*}` substitution in source URLs, and
the D3 verification.

**Out of scope.** Named sources and git (W7). `resources` (W6).

**Depends on.** W2, W3, W4.
**Blocked by.** **D3**.

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
- [ ] **D3 closed with a test**, not an assumption: a manifest-installed skill
      survives a skills-pool reconcile and is not quarantined as drift.
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
**Blocked by.** —

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
**Blocked by.** **X2** for the teclaw arm. The ARCA arm can proceed alone.

**Done when.**

- [ ] The **first** configuration a bot receives already contains the manifest
      result — there is no "start, then patch it in" window.
- [ ] Scale-out does **not** re-apply; instances stay identical because they
      share one platform state. This is #926's actual requirement.
- [ ] A manifest `PUT` does **not** apply — lazily effective, matching #935.
- [ ] Apply failure does **not** block bot readiness in v1, matching #935's
      semantics, and the failure is fully recorded in the report. Source-site
      flakiness must not become a compose failure — the `keep_last` default is
      the insurance and is tested as such.
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
wave 1   W1 ─┐        W2 ─┐   W3 ─┐        (mutually independent)
             │            │       │
wave 2       └─► W4 ◄─────┴───────┘
                  │
                  └─► W5
                       │
wave 3                 ├─► W6 ──┐
                       │        ├─► W8   (teclaw arm needs X2)
                       └─► W7 ──┘        (needs X1)
                                          │
deferred                                  └─► W9  (needs X3)
```

**Critical path:** W1 → W4 → W5 → W6 → W8.

**Available parallelism:** W2 and W3 can run alongside W1 with no coordination.
W7 can run alongside W6 once X1 lands.

**Why lifecycle wiring is last.** Explicit `POST .../apply` exercises the whole
engine from W4 onward, so W8 touches the create and publish flows only after the
thing it triggers is already proven. The trade is stated plainly: **nothing
before W8 delivers the business ask.** W4's explicit apply is a validation
vehicle, not the product. If the schedule needs the real behaviour sooner, the
teclaw arm of W8 can be pulled forward to directly after W5 — but only once X2
has come back.

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

Design decisions this document does **not** re-open: the manifest/script split
(design §2.1), route B (design §2.3), the four rejected alternatives (design
§2.4), GitOps declaration-wins semantics (design §3.2), platform-side fetch
(design §4.1), and the untouched `BotConfigArtifact` contract (design §5.2).
