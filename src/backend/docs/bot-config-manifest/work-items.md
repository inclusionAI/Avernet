# Bot Config Manifest — Implementation Work Items

> **Status: PLANNING.** The design is settled in the four documents beside this
> one (`README.zh-CN.md`, `design.zh-CN.md`, `manifest-schema.zh-CN.md`,
> `examples.zh-CN.md`, `engine-requirements.zh-CN.md`, merged as #1031). This
> document turns that design into a sequence of independently reviewable work
> items. It contains no new design decisions except where a section is
> explicitly labelled as a blocking question.

## 1. How to use this document

Each work item **W1–W12** is scoped to be picked up on its own, by one person or
one session, with no need to re-derive the design from the Chinese docs. An item
states what it delivers, what it deliberately leaves alone, what must land
first, and criteria concrete enough to review against.

Three kinds of entry gate the work and are tracked separately from it:

| Prefix | Meaning |
| --- | --- |
| `W1`–`W12` | Implementation work items — one PR each. W12 is a written contract plus another team's sign-off rather than code |
| `D1`–`D4` | **Design questions**, found while checking the design against the code. **All four are now settled** — D1–D3 resolved, D4 deferred with an interim policy (deliver after start) that unblocks the work |
| `X1`–`X3` | **External confirmations** owed by other teams. None blocks W1–W6; they gate W7 and W9, and W8's teclaw arm |

Where this document diverges from the merged design docs — §2.5 (capability
scope), §2.6 (PUT triggers a restart), §2.7 (first-boot readiness gate), §3.2
(entity-level three-way diff, superseding the wholesale directory replace) — it
says so explicitly. Those docs are not edited here; amending them is a separate
change.

### Trackers

Each entry has a GitHub issue. The issue carries scope and acceptance criteria;
this document remains the source of truth for detail and for how the items fit
together.

| | Issue | | Issue |
| --- | --- | --- | --- |
| **D1** capability model — *resolved* | #1466 | **W4** apply engine | #1472 |
| **D2** upgrade diff policy — *resolved* | #1467 | **W5** skills + identity | #1473 |
| **D3** reconcile verification — *resolved* | #1468 | **W6** resources | #1474 |
| **D4** pre-boot delivery — *deferred* | #1508 | **W7** named + git sources | #1475 |
| **W1** manifest document | #1469 | **W8** lifecycle apply points | #1476 |
| **W2** guarded fetcher | #1470 | **W9** `cli_tools` (deferred) | #1477 |
| **W3** source credentials | #1471 | **W10** service-layer seam | #1509 |
| | | **W11** platform-side materialisation | #1510 |
| | | **W12** cross-engine semantics contract | #1684 |

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
- `bot_type == "desktop"` — a separate axis, and a bot-record field. **Desktop
  is out of scope for this feature**, so this test exists only to refuse it.

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

### 3.2 D2 — manifest-upgrade diff policy · #1467 · **RESOLVED**

The failure half was already settled (§2.7). The diff half is now settled too,
and the answer is **entity-level, three-way**.

#### The convergence unit is the entity

Not a file, not "the whole declared tree". A skill, an identity file, a resource
entry, a `cli_tool` — each is one entity, and a skill's entity spans every file
under its own directory.

#### The rules

Between manifest version **N** and **N+1**:

| | Condition | Action |
| --- | --- | --- |
| 1 | Entity in N, **not** in N+1 | **Delete the entity entirely** |
| 2 | Entity in **both** | Diff its files, per the table below |
| 3 | Entity **only** in N+1 | Create it |

Within an entity present in both versions, each file is decided by a **three-way
comparison** — version N, version N+1, and what is on disk:

| In N | In N+1 | Action |
| --- | --- | --- |
| ✓ | ✗ | **Delete** — the declaration dropped it |
| ✓ | ✓ | **Overwrite** — the declaration owns it |
| ✗ | ✓ | **Write** — newly declared |
| ✗ | ✗ | **Leave untouched** — nobody declared it, so the bot created it |

#### Why this is better than "declaration wins wholesale"

The fourth row is the whole point, and it is what makes the policy safe to run on
every restart: **a file the manifest has never mentioned is never touched.** The
bot's own working files survive an upgrade without needing a warning, an opt-out,
or a list of protected paths.

#### Consequences worth stating

- **Version N's materialised file list becomes required state.** Row 1 and row 4
  are only distinguishable if we know what version N declared — "not in N+1" and
  "in neither" are different actions. This makes **W11 a hard dependency of W4**,
  not a parallel nicety: the store that keeps materialised content is also the
  record of what the previous version contained.
- **Diverges from design §3.2's directory rule**, which replaces a declared tree
  wholesale and deletes agent-added files inside it. Under this policy those
  files are preserved. The design's rule is superseded.
- Rows 1 and 2 are a real delete of user-visible assets, so they are the part of
  W4 that most needs test coverage — including the case where the entity was
  edited by hand between applies.

#### Per-entity conflict policy

The table above says "in both versions → overwrite", which is the right default
but not right for everything. A single file can have **three** versions at apply
time:

1. what manifest **N** materialised,
2. what manifest **N+1** materialises,
3. what is **actually on disk** — which the bot may have modified since.

Blind overwrite discards (3) whenever the file is declared. For some entries that
is exactly right (a persona file, a skill's code). For others the bot's version is
the valuable one.

Two mechanisms, both cheap because W11 already stores what we wrote:

**a. Detect modification instead of assuming it.** Compare the on-disk bytes with
what we materialised for version N. If they match, the bot never touched it and
overwrite is uncontroversial. They differ only when there is a genuine conflict —
so the policy below applies to a small set of real cases, not to every file.

**b. A per-entry `on_conflict` setting**, with a closed set of values rather than
an open knob:

| Value | Behaviour when the on-disk copy differs from version N |
| --- | --- |
| `overwrite` (default) | The declaration wins. Today's rule |
| `preserve` | Keep the bot's version; do not overwrite. Still **created** when absent, so the manifest seeds it once and then leaves it alone |
| `fail` | Report the entry as `failed` and change nothing — for content where silently picking either side is unacceptable |

`preserve` is what an engine-written file like `MEMORY.md` wants if someone
declares it: seed it on first boot, never clobber the accumulated state after. It
is design §3.2's reserved `apply_once`, made explicit and given a reason to exist.

**Not offered:** three-way *merge*. Merging two versions of arbitrary text has no
defined answer and would make behaviour unpredictable, which is the opposite of
what this policy is for.

Note that a file the manifest never declares needs none of this — it is row 4 of
the diff table and is already left alone. `on_conflict` matters only for declared
entries.

#### Moving refs: two modes

A branch ref can still resolve to different content on a restart nobody
associated with a configuration change (§2.6's residual case). Resolved with a
mode switch rather than a blanket rule:

- **Strict mode** — if the resolved SHA differs from the one recorded at the last
  apply, **reject the change**. The bot keeps running what it has.
- **Non-strict mode** — apply the new content and **warn**.

Both modes require recording the resolved SHA per apply, which the report already
carries (design §7).

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

### 3.4 D4 — delivery before a container exists · #1508 · **DEFERRED — no longer blocking**

**Interim policy: deliver after the bot starts.** For any category whose only
delivery API becomes available once the container is up, apply delivers *after*
start rather than before it. Pre-boot delivery is a separate question to answer
later, and W5/W6/W8 proceed without it.

#### The problem, for the record

`design.zh-CN.md` §3.1 promises the first configuration a bot receives already
contains the manifest result. But the services apply must call cannot run before
a container exists, deliberately:

- `IdentityService._device_write` resolves a `DeviceFileSystem` through the
  device binding and **raises if unbound**;
- `EngineConfigService.write_bot_config` does the same, and raises
  `EngineStageNotLiveError` when the named stage has nothing up.

`core/services/identity.py` states the intent: *"a bot with no resolvable device
context is a bug and surfaces as the resolver's error (fail early, never
silently touch a dead local path)."*

#### teclaw is unaffected

The artifact **is** the delivery vehicle, so materialised content becomes
`{store, path}` refs in `BotConfigArtifact` and the first artifact carries them —
genuinely before start. One backend change is required and should be named rather
than discovered: `ConfigComposerInputCollector.identity_files()` currently
returns `[]` for teclaw by design —

```python
if req.engine_type == "teclaw":
    return []   # teclaw owns its identity files in the running container;
                # gathered from the engine at promotion, like resources
```

— so that branch must learn about manifest-materialised identity. The teclaw
*team* still does zero work; `engine-requirements.zh-CN.md` §1's "delivery layer:
zero additions" does not survive, on the backend side only.

#### What the interim policy costs, stated plainly

Deferring is a reasonable call — it unblocks most of the plan for the cost of one
property — but the cost is real and should not be discovered later:

1. **There is a window where the bot is ACTIVE but not yet configured.** This is
   precisely the race `design.zh-CN.md` §2.4 rejected alternative three for: a bot
   already accepting messages while its persona is still landing can answer with
   an unconfigured personality. On a first boot the window is the whole apply.
2. **It interacts with the §2.7 readiness gate.** "A first boot whose fetch fails
   leaves the bot inactive" is harder to honour when delivery happens after the
   bot is already up — the gate has to become "start, apply, and de-activate on
   failure", or the gate moves to a later readiness signal. W8 must resolve this.
3. **Scale-out instances** each need the post-start delivery to have completed
   before they take traffic, or instances diverge — which is #926's original
   complaint.

Mitigation to consider in W8: keep the bot out of the serving path until the
post-start apply reports success, so "started" and "ready" stop being the same
moment.

#### Still to answer, later

For the BaaS family: the workspace path **is** computable without a device
(`get_bot_file_path` is a pure `path_factory` computation, and the composer
already calls `.exists()` on those paths), but *writing* there is exactly what the
service refuses to do unbound, and bypassing it is what §2.1 forbids. So a
pre-boot answer needs a sanctioned protocol, not a bypass:

| | Approach | Question it raises |
| --- | --- | --- |
| a | A device-less writer sharing path resolution with `DeviceFileSystem`, reachable only before first boot | What structurally guarantees it is only used pre-boot? |
| b | Deliver during the start sequence — the container pulls materialised content on boot | Reintroduces an in-container step; interacts with #935's start-command contract |
| c | Extend the device abstraction with a pre-binding mode the dispatcher resolves | Largest change, cleanest boundary |

## 4. External confirmations

Owed by other teams. **None blocks W1–W6.**

> **On the letters.** `T`, `A` and `O` are not work items. They are the
> confirmation lists in `engine-requirements.zh-CN.md`: **T1–T5** are questions
> for the teclaw team (§3), **A1–A2** for the ARCA-family engine owners (§4), and
> **O1–O11** are the design's own open questions (§5). They are cited here only
> so each row can be traced back; the row itself says what is actually being
> asked, so no cross-reading is needed.

| # | What is being asked | Gates | Owner | State |
| --- | --- | --- | --- | --- |
| ~~**X1**~~ | ~~Ant Code credentials~~ | — | — | **Closed: dedicated machine account + `read_repository` 访问令牌**, HTTP Basic. No Deploy Tokens exist; expiry is owner-managed (§4, X1) |
| **X2** | teclaw readiness/convergence — **answered**, see below | **W8** teclaw arm | teclaw + backend | **Answered**; superseded by W12 |
| **X3** | `cli_tools` — one question remains: **O9, is every ARCA bot container the same CPU architecture** (x86_64), or is the fleet mixed? Not about paths or engines — about the machine code a shipped binary is compiled for. **W9 can proceed without it**, see below | **W9** | whoever operates the ARCA fleet | One question open, non-blocking |
| ~~**X4**~~ | ~~desktop in the v1 surface~~ | — | — | **Closed: desktop is out of scope** (§2.5) |

### X1 — Ant Code credential choice

The published Ant Code doc settles most of this, and contains one fact that
overturns a design decision.

**There is no read-only API scope.** Of the 访问令牌 scopes:

- `api` — *"授予对 API 的完整读/写访问权限，包括所有组和仓库"*: full read **and
  write**, across every group and repository the account can see;
- `read_repository` — read-only, but *"使用 Git-over-HTTP（不使用 API）"*: Git
  transport only, **not** the API.

You can have read-only, or API access, not both.

| Option | Scoping | Transport | Verdict |
| --- | --- | --- | --- |
| 访问令牌 + `api` | user-wide, read **and write**, all groups and repos | API | **No.** Write access to everything, for a job that only ever reads |
| 访问令牌 + `read_repository` | user-wide but **read-only** | Git-over-HTTP | **Recommended**, paired with a machine account |
| 私有令牌 | the person's full permissions, no expiry control | API + git | **No.** A personal access token by another name — already ruled out in schema §2.1 |
| SSH 公钥 | *all* of the user's permissions — the doc warns of exactly this | SSH | **No** |
| 仓库部署公钥 | **repo-scoped, read-only** — the best scoping on offer | SSH only | Best security, wrong transport for v1 |

**Recommendation: a dedicated machine account + 访问令牌 scoped `read_repository`.**
The documented usage form `https://git:{token}@code.alipay.com/{group}/{project}.git`
is HTTP Basic, so it stores as `Authorization: Basic base64("git:<token>")` and
injects as a header — **the v1 credential model needs no change**. Scope narrowing
comes from the account's *membership* (only the content repos), which is precisely
the fallback schema §2.1 anticipated for hosts without repo-scoped tokens.
`allowed_prefixes` remains the platform-side check on top of it.

**Consequence: this reverses design §10.5.** That section compiles git sources
into a single HTTPS archive fetch through the hosting API, explicitly avoiding
`git clone` in the backend process. `read_repository` cannot reach the API, so
W7 would do a **shallow single-ref fetch** instead. Taking the API route means
accepting the `api` scope — read/write to everything — which is a far worse thing
to hold in our database than a clone is to run.

**Both follow-up questions are now answered — X1 is closed.** Ant Code offers no
Deploy Tokens, and **token expiry is the credential owner's responsibility**
rather than something the platform rotates.

Owner-managed expiry has one design consequence, and it belongs to W3 and W4: an
expired token is indistinguishable from any other fetch failure unless we make it
distinguishable. Under §2.7 a running bot is untouched by a failed fetch — correct,
but it means **a token can silently expire and nothing visibly breaks until a bot
is next created or restarted**, at which point a first boot stays inactive.

So: an authentication failure (401/403) must be reported in the apply report as
*"credential `<name>` was rejected"*, named and distinct from a generic fetch
error, so the owner knows to rotate rather than hunting a network problem. Cheap
to do, and the only thing that makes owner-managed expiry operable.

Deploy *keys* stay the best security answer on paper — repo-scoped and read-only
beats user-scoped and read-only — but they are SSH: a second transport in a
fetcher built HTTPS-only around SSRF guards, host-key verification, and a
credential model storing a private key rather than a header. Not worth it for v1
now that the header route is settled.

### X2 — answered, and it raised a bigger one

- **T1 (readiness ordering) — yes.** teclaw applies the whole artifact to the bot
  before it reports ready. "Configuration precedes readiness" holds on that side.
- **T2 (convergent re-delivery) — not ours to worry about.** The apply semantics
  inside a teclaw container are owned by teclaw.
- **T3 (engine config on first boot) — removed from scope.** `engine_config` is
  **excluded from the first iteration**, so the question does not arise yet. This
  narrows W4's no-fetch materialisers to `mcp` and `script`.

**But T2 exposes the real issue.** We own convergence semantics for BaaS-family
bots; teclaw owns them for teclaw bots. If the two differ, the same manifest
produces different behaviour on different engines and users have no way to
predict either. **The semantics must be one contract, written once and agreed by
both sides** — tracked as **W12**.

This is not a formality. §3.2's fourth row — *a file declared in neither version
is left untouched because the bot created it* — cannot be computed on our side for
teclaw: we hand over a whole artifact and never see their disk, and the artifact
vocabulary has no way to say "delete this one thing". So row 4 holds on teclaw
**only if their applier implements it**. Their consent is what makes the rule
true there, which is why it needs to be explicit rather than assumed.

### X3 — `cli_tools`, narrowed

**teclaw needs nothing from us on PATH.** The artifact is the delivery vehicle,
so our side of it is a protocol design: how a CLI tool is represented in
`BotConfigArtifact` so teclaw can place and expose it. Executable-bit and PATH
handling inside their container is theirs to decide once the protocol exists.

**ARCA is engine-specific and needs a proposal from us** — where the platform tool
directory should land on each engine's agent-process PATH. Plus a **skill in the
default skill set** so the model knows the tools exist and how to invoke them.

#### Investigation: what exists today (asked for before proceeding)

Findings, checked against the code:

- **There is a working CLI pattern, but only in singlebox.**
  `scripts/modules/bots.sh` starts the openclaw gateway with
  `PATH="$bcs_cli_dir:$PATH"` (line ~954) and separately copies the
  `bcs-coordination` skill into the bot's `workspace/skills/`
  (`bots_dynamic_setup_bcs_skill`, line ~704). That is the "binary on PATH +
  SKILL.md that teaches it" double act the design cites.
- **It does not exist in production.** No PATH injection appears anywhere in the
  production start-command composition (`baas_service.py`, `core/devices/`).
  `Dockerfile.ocb` bakes `bcs-cli` into the all-in-one OSS image at
  `/opt/ocb/src/bcs/target/debug/bcs-cli`, but that is the singlebox image, not
  the ARCA bot container.
- **In production the skill arrives through skill-center**, not a file copy —
  `bcs-coordination` is referenced as `git://default/bcs-coordination` and
  installed via the skill-set mechanism. `SkillSetService` already has an
  engine-type-aware **default skill set** with a selection policy
  (`_default_skill_set_selection`, `DefaultSkillSetSelectionPolicy`), which is the
  natural home for the tools-usage skill.
- **The direction of travel is away from CLIs.** The newer task skills
  deliberately forbid it — `specs/2026-08-09-task-goal-driven-task-runner-bbs/bbs-relay-pickup/SKILL.md`
  says all task APIs go through `exec` + `curl`/`jq` and
  「**禁止引用 bcs-cli 或任何子命令**」 (forbidden to reference bcs-cli or any
  subcommand). `bcs-coordination` itself declares `allowed-tools: [exec]`.

**What is actually outstanding here.** Only **O9**, and it is narrower than its
one-line summary suggests.

#### O9 is about CPU architecture, not paths

`cli_tools` ships a **compiled binary**, and a binary matches its host on **two
independent axes**:

| Axis | Values | State |
| --- | --- | --- |
| OS / libc | linux · darwin · windows | **Answered: Linux** |
| CPU instruction set | `x86_64`/amd64 · `aarch64`/arm64 | **Open** |

Both live in the conventional filename `mycli-linux-amd64`. A Linux **arm64**
host cannot run a `linux-amd64` binary — it fails with `exec format error` inside
the container, at the moment the model tries to use the tool. So knowing the OS
is Linux settles one axis and leaves the other. A manifest entry names **one**
URL:

```yaml
cli_tools:
  - name: mycli
    source: https://my-svc.example.com/tools/mycli-linux-amd64
```

If every ARCA bot container runs on x86_64, that single URL is always right. If
the fleet is mixed, it is wrong for some fraction of bots and the schema needs
per-arch sources — per-arch URLs, or an `${OCB_ARCH}` substitution.

This is **not** the question of where the tool directory sits or whether the PATH
reaches it. That is our design choice, engine-independent, and settled. Nor does
it vary by engine: it varies by the **machine the container is scheduled on**, so
it is a question for whoever operates the ARCA fleet.

**It cannot be answered from the code.** The backend has no notion of container
architecture: images are pinned by name (`arca_image_pin.py`, `sbot_docker_image`)
and placement is ARCA's decision. There is no arch branching anywhere in
`core/service_bot/` or `core/devices/`.

#### W9 does not have to wait for it

Two cheap choices make the answer non-blocking:

- **Reserve `${OCB_ARCH}` in the variable whitelist now.** W1 defines that
  whitelist and it is versioned with `schema_version`; adding the name costs
  nothing today and avoids a schema-version bump if per-arch sources are needed
  later.
- **Validate the binary's architecture at materialisation.** Read the ELF header
  of a fetched binary and refuse it when it does not match the target. A mismatch
  then fails loudly in the apply report, rather than as an `exec format error` the
  model hits mid-task with no explanation.

With those, v1 ships a single URL and a mixed fleet becomes an additive change
rather than a redesign.

**A third option worth checking before assuming anything fleet-wide.** Under
D4's interim policy (§3.4) delivery happens *after* the bot starts, so the
container exists when `cli_tools` is materialised. If the engine can report its
own architecture over an existing channel, `${OCB_ARCH}` could be resolved
**per bot at delivery time** — which answers the question by construction and
stays correct if the fleet ever becomes mixed. It needs an engine-side way to
ask, so W9 should establish feasibility rather than assume it; but it is a better
shape than a global assumption and costs nothing to check.

Everything else below is our own work: the artifact protocol for teclaw (we
design, they implement), the per-engine ARCA PATH proposal, and the tools-usage
skill. teclaw implementing the protocol is a delivery dependency, not a question.

**Is there already a CLI mechanism we would be duplicating? No — and that is the
answer that matters.** Every `bcs-cli` reference in the repository lives in
`scripts/modules/*.sh`, the singlebox orchestration. There is no CLI delivery
mechanism in the platform: no PATH injection in the production start-command
composition, and **no executable-bit handling anywhere in any delivery path**
(resources, skill-center, or the artifact). `cli_tools` is therefore new
machinery, not a second implementation of something that exists.

**The duplication risk is real but points the other way.** Once `cli_tools`
exists, `bcs-cli` should become **its first consumer** rather than staying a
singlebox special case — otherwise we do end up with two mechanisms: the script
that hand-places it locally, and the manifest path that places everything else.
Folding bcs-cli into `cli_tools` also gives the feature a real in-house test case
before any customer uses it. Recorded as an explicit goal of W9 rather than
something to notice afterwards.

**One observation, not an objection.** The newer task skills deliberately avoid
CLIs — `specs/2026-08-09-task-goal-driven-task-runner-bbs/bbs-relay-pickup/SKILL.md`
routes every task API through `exec` + `curl`/`jq` and states
「**禁止引用 bcs-cli 或任何子命令**」 — and `bcs-coordination`'s only declared
capability is `exec`. That is worth knowing when deciding which tools are worth
shipping as binaries, but it does not change that `cli_tools` is wanted.

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

#### W12 — Cross-engine convergence semantics contract · #1684

**Goal.** One written statement of what applying a manifest does to what is
already there, agreed by both sides, so the same manifest behaves the same way on
a BaaS-family bot and a teclaw bot.

**Why it is a work item and not a note.** We own convergence for the BaaS family;
teclaw owns it inside their container. Nothing today makes those agree, and the
asymmetry would be invisible to users until it bit them. Specifically, §3.2's
fourth row — *a file declared in neither version is left untouched* — **cannot be
enforced from our side on teclaw**: we hand over a whole artifact, never see their
disk, and the artifact vocabulary has no "delete this one thing". It holds there
only if their applier implements it.

**In scope.** Write the policy from §3.2 as an engine-facing contract — the entity
diff rules, the three-way file rules including row 4, `on_conflict`, and what
"convergent re-delivery" means. Take it to the teclaw team for review and explicit
agreement. Record the outcome, including anything they decline.

**Depends on.** §3.2 being settled (it is) · **Blocked by.** —

**Done when.**

- [ ] The contract states each rule as a requirement on an applier, not as a
      description of our implementation.
- [ ] Row 4 is called out as the one rule we cannot verify from outside, with its
      consequence if unmet: a bot's own files silently disappearing on upgrade.
- [ ] teclaw has reviewed it and either agreed or named the parts they will not
      implement — the second is a usable answer; silence is not.
- [ ] Any divergence they declare is written into the capability matrix, so the
      difference is documented rather than discovered.

**Size.** Small to write, and the calendar time is the other team's review.

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
**Blocked by.** — D1 is resolved (§2.5) and desktop is out of scope, so the
capability table is fully determined.

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
    `OCB_ENV`, `OCB_TENANT` and the reserved `OCB_ARCH` are accepted. `OCB_ARCH`
    is reserved now because the whitelist is versioned with `schema_version`:
    adding the name costs nothing today and avoids a version bump if `cli_tools`
    later needs per-arch sources (§4, X3/O9);
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
- [ ] An authentication failure (401/403) is reported as *"credential `<name>` was
      rejected"* — named, and distinct from a generic fetch error. Expiry is
      owner-managed (§4, X1), so a rotation reminder that reads as a network
      problem is a real operational failure.
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

**Depends on.** W1, W10 (the seam apply calls through), and **W11 as a hard
dependency** — §3.2's three-way diff cannot distinguish "dropped from the
declaration" from "created by the bot" without version N's materialised file
list, which is what W11 stores.
**Blocked by.** — D2 is resolved (§3.2) and its rules are what this item
implements. X2/T3 affects the teclaw behaviour of the `engine_config`
materialiser but does not block the item.

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
- [ ] `engine_ext` is unreachable from the manifest on every path. (The
      `engine_config` category itself is out of the first iteration; when it
      returns it merges by top-level key.)
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
**Blocked by.** — D3 and D4 are both settled. Delivery on the BaaS family
follows D4's interim policy: deliver after the bot starts (§3.4).

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
- [ ] The §3.2 diff rules are enforced per entity, including the fourth row: a
      file present on disk but declared in neither version N nor N+1 is **left
      untouched**.
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
**Blocked by.** — delivery follows D4's interim post-start policy (§3.4).

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

**In scope.** `sources` + `from`; git ref resolution and content retrieval.

**Depends on.** W5.
**Blocked by.** — **X1 is closed** (§4): a dedicated machine account with a
`read_repository` 访问令牌, injected as HTTP Basic, needing no change to the v1
credential model. Nothing external remains.

**Scope change forced by X1:** `read_repository` cannot reach the API, so this
item does a **shallow single-ref git fetch** rather than the archive-API pull
design §10.5 specifies. That section is superseded. Taking the API route would
require the `api` scope — read/write across every group and repository — a far
worse credential to hold in our database than a clone is to run.

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
- [ ] Git content is retrieved over HTTPS with a **shallow, single-ref** fetch
      (superseding design §10.5's archive-API approach — see §4, X1), reusing
      W2's guarded transport and its size, timeout and concurrency caps.
- [ ] The fetch is read-only and never executes repository-supplied hooks or
      filters.
- [ ] A re-pointed tag converges to the new content at the next apply — moving a
      tag changes what the declaration means.
- [ ] Directory entries from a git source need no `unpack` or
      `strip_components`.

**Size.** Medium-large.

---

#### W8 — Lifecycle apply points · #1476

**Goal.** The business ask, delivered: a bot configures itself when it comes up,
with no user action.

**In scope.** Apply at bot creation (ARCA family before the start command is
composed; teclaw before the first artifact is assembled), at publish/republish,
and at rebuild-style restart.

**Depends on.** W4, W5, W6.
**Blocked by.** **W12** for the teclaw arm — the semantics contract must be
agreed before teclaw delivery can be relied on. X2's own questions are answered
(§4). D4 is deferred rather than blocking, but this item owns its two
consequences: the ACTIVE-but-unconfigured
window, and where the §2.7 readiness gate lands when delivery happens after the
bot has started (§3.4).

**Done when.**

- [ ] On teclaw, the **first** artifact already contains the manifest result.
- [ ] On the BaaS family, post-start delivery (§3.4) completes before the bot
      takes traffic — "started" and "ready" must stop being the same moment, or
      the ACTIVE-but-unconfigured window becomes user-visible.
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
**Blocked by.** **X3**, now narrowed (§4): teclaw needs only an artifact protocol
from us; the ARCA PATH proposal and the default-skill-set skill are ours to
design. The §4 investigation confirmed there is **no existing CLI mechanism to
duplicate** — every `bcs-cli` reference is singlebox orchestration, and no
delivery path handles an executable bit.

**Done when (sketch).** `digest` mandatory and enforced as the convergence key;
static binary and archive forms only; a platform-defined logical tool directory
on the agent process's PATH, with users never seeing a physical path; a
tools-usage skill in the engine-aware **default skill set** (`SkillSetService`
already has one) so the model knows the tools exist and how to call them; and an
artifact representation for teclaw, whose in-container placement is theirs.

**Explicit goal: `bcs-cli` becomes the first consumer**, replacing the singlebox
script that hand-places it. Otherwise this ships alongside the one CLI mechanism
that already exists and we maintain both — and folding it in gives the feature a
real in-house test case before any customer uses it.

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
                                                         │        ├─► W8  ◄── W12
                                                         └─► W7 ──┘
                                                                   │
deferred                                                           └─► W9

         W12 (semantics contract) runs in parallel from now. It gates only
         W8's teclaw arm, and its calendar cost is the other team's review.
```

**Critical path:** W1 → W4 → W5 → W6 → W8.

**Available parallelism:** W2, W3, W10 and W11 all run alongside W1 with no
coordination — W11 needs only W2's output shape. That is five independent
starting points, which is the most this plan will ever offer at once, and
nothing is waiting on a decision to begin.

**Gating.** **No design question blocks the work any more.** D1–D3 are resolved
and D4 is deferred behind an interim policy (deliver after start, §3.4). What
remains is external: X2 gates W8's teclaw arm through W12, and X3's single
question (O9, fleet CPU architecture) is non-blocking for W9, which is deferred
regardless. **X1 and X4 are closed**, so W7 has nothing external left.

Two dependencies the resolutions *tightened*: **W11 is a hard dependency of W4**,
because §3.2's diff needs version N's materialised file list both to tell "the
declaration dropped it" apart from "the bot created it", and to detect whether
the bot modified a declared file at all. And **W12 gates W8's teclaw arm** —
without an agreed semantics contract the same manifest can behave differently on
the two engine families.

**Start W12 now.** It is the only remaining item whose critical path runs through
another team, and it costs us little to write.

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
| W7 | design §4.2; schema §2.2, §2.3. **Design §10.5 superseded** — see §4, X1 |
| W8 | design §3.1, §3.4, §4.3, §10.1, §10.4 |
| W9 | schema §3.7; engine-requirements T4, A2, O9 — narrowed by §4's investigation |
| W10 | no design section — arises from §2.9, an implementation constraint the design does not cover |
| W11 | no design section — arises from §2.8, a requirement added after #1031 |
| W12 | design §3.3 (convergence) and engine-requirements T2, turned into a two-sided contract |

Design decisions this document does **not** re-open: the manifest/script split
(design §2.1), route B (design §2.3), the four rejected alternatives (design
§2.4), platform-side fetch (design §4.1), and the `BotConfigArtifact` schema
(design §5.2 — unchanged, though D4 notes that the backend's teclaw compose
branch is not).

Points where this document **diverges** from the merged design, each argued in
place: §2.3 (the managed marker shrinks to an internal record), §2.5 (capability
scope; desktop out), §2.6 (`PUT` triggers a restart rather than being lazy —
design §3.1), §2.7 (a first-boot readiness gate, which design §4.3 defers to v2),
§2.8 (platform-side materialisation, which the design does not have), §3.2 (an
entity-level three-way diff that preserves bot-created files, superseding design
§3.2's wholesale directory replace), §3.4 (post-start delivery on the BaaS
family, where design §3.1 requires configuration to precede readiness), and §4's
X1 (a shallow git fetch rather than design §10.5's archive-API pull, forced by
Ant Code having no read-only API scope). Amending the Chinese docs to match is a
separate change, deliberately not made here.
