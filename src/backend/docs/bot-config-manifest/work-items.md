# Bot Config Manifest — Implementation Work Items

> **Status: PLANNING.** The design is settled in the four documents beside this
> one (`README.zh-CN.md`, `design.zh-CN.md`, `manifest-schema.zh-CN.md`,
> `examples.zh-CN.md`, `engine-requirements.zh-CN.md`, merged as #1031). This
> document turns that design into a sequence of independently reviewable work
> items. It contains no new design decisions except where a section is
> explicitly labelled as a blocking question.

## 1. How to use this document

Each work item **W1–W13** is scoped to be picked up on its own, by one person or
one session, with no need to re-derive the design from the Chinese docs. An item
states what it delivers, what it deliberately leaves alone, what must land
first, and criteria concrete enough to review against.

Three kinds of entry gate the work and are tracked separately from it:

| Prefix | Meaning |
| --- | --- |
| `W1`–`W13` | Implementation work items — one PR each. W12 is a written contract plus another team's sign-off rather than code |
| `D1`–`D4` | **Design questions**, found while checking the design against the code. **All four are now settled** — D1–D3 resolved, D4 deferred with an interim policy (deliver after start) that unblocks the work |
| `X1`–`X4` | **External confirmations** owed by other teams. **All four are now closed.** |

Where this document diverges from the merged design docs — §2.5 (capability
scope), §2.6 (`PUT` takes effect immediately), §2.7 (apply records per-entry
delivery and nothing else), §3.2
(category-scoped overwrite with a reserved-name list) — it
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
| **W2** guarded fetcher | #1470 | **W9** `cli_tools` (deferred — *artifact shape landed*) | #1477 |
| **W3** source credentials | #1471 | **W10** service-layer seam — *merged* | #1509 |
| | | **W11** platform-side materialisation | #1510 |
| | | **W12** cross-engine semantics contract — *done* | #1684 |
| | | **W13** create a bot from a manifest | #1696 |

Planning PR: #1465. **All thirteen items are assigned in §7**, and every item carries an **Owner** line with its day budget and calendar day.

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
  endpoints keep working, unchanged, because their table is still the
  materialisation target — **and they do not know the manifest exists.** An
  earlier revision of this plan made them an alias view (write-through) onto
  the manifest's `script`; W8's review (inclusionAI/Avernet#1836) withdrew
  that: the manifest is the upper layer, and the startup script is one of the
  entities it materialises into, not a view of it. Consequence, stated so it
  is not mistaken for a bug: on a bot whose manifest declares `script`, a
  legacy `PUT` writes the row and the next apply writes the declared body
  back over it — the manifest is the source of truth for what it declares,
  and the place to change it is the manifest.
- Whether to later collapse `ac_bot_startup_script` into the manifest module is
  a reversible decision, deferred. This resolves design §8's open choice.
- Apply's *only* action for this category is that one DB write; the platform
  composes the script into the start command by itself. See §2.12 for what that
  mechanism confirms and for the ordering restriction it forces on iteration 1.

### 2.3 A manifest-materialised entity is indistinguishable from a manual one

Because the manifest sits one layer above the existing services, an entity it
creates is stored **exactly** as the same entity created by hand: same service,
same table, same shape. Nothing downstream — reconcile, compose, the UI, the
engine — can tell them apart, and nothing downstream needs to.

The manifest therefore does **not** stamp a marker on the entities themselves.
It keeps its own record of *what the last apply materialised*, which now exists
for **one** job: **`keep_last` and audit** (§2.8), which need the last
successfully materialised content anyway.

It used to have a second job — "un-marking", knowing what to stop managing when a
category emptied. §3.2's move to category overwrite removed it: `skills: []` is a
declaration that the set is empty, so the area is emptied without needing to know
what was previously declared.

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
  `provider_resolver.resolve_device_provider` itself keys on (`teclaw` or else the `baas`
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

### 2.6 A manifest `PUT` takes effect immediately, without a restart

**Divergence from design §3.1**, which makes `PUT` lazy (no apply; effective at
the next restart for unrelated reasons). Instead: writing a new manifest version
to an **existing** bot makes it effective there and then, so the running bot
always reflects the manifest that was last accepted.

**The verb is the §3.2 overwrite, and it needs no restart on either engine
family.** An earlier revision of this section specified a per-family *restart*;
that is withdrawn — restarting was never what made the configuration effective,
it was only how the payload got rebuilt.

| | How a declared category is overwritten | Restart? |
| --- | --- | --- |
| **BaaS / ARCA** | `identity` and `resources` are file writes through `DeviceFileSystem`; `skills` are written into the active skill set and reconciled by the existing full symlink sync, reachable on a live bot via `DeviceSyncDispatcher` / `sync_symlinks` | **No** |
| **teclaw** | Per-file writes through `TeclawDeviceFileSystem`, which *"forwards every read/write per-file to the engine, so it needs neither OSS nor the whole-artifact device-sync redeliver"*; the whole-artifact redeliver (`TeclawDeviceSyncPlugin.sync_symlinks([])`, as `ChannelService` already uses it) remains available where a category needs it | **No** |

**`script` is the one exception, and it is not a restart requirement so much as a
deferral.** It is materialised by writing `ac_bot_startup_script` (§2.12), which
`_build_create_bot_payload` reads when it composes the start command — so a
changed script is *delivered* immediately and *takes effect* at the bot's next
start. That is consistent rather than special: apply records delivery, not
execution (§2.7).

**Never route a teclaw manifest change through `BotService.restart_bot`.** It
raises `BotOperationNotAllowedError("teclaw 类型的 Bot 不支持重启")`, and
`bot_publish_service.py` records why: a teclaw restart destroys the container,
fails reallocation, and strands the bot with no binding and its in-container
files lost. Under this section nothing needs a restart anyway, so the rule is
easy to keep — it is recorded because an implementer reaching for a "make it
effective" verb might otherwise reach for that one.

**Dropping the restart removes a lifecycle-state problem too.**
`BotService.restart_bot` accepts only `ACTIVE`, `FAILED` and `PENDING`;
`REACTIVATING` is a no-op and every other state raises
`BotInvalidLifecycleStateError`. Had `PUT` been specified as "always restarts", a
caller would have got a 4xx for a perfectly valid manifest because of where the
bot happened to sit in its lifecycle. Overwriting files and reconciling skills
has no such gate.

The rule:

1. **Persist and validate always.** Accepting the document never depends on the
   bot's runtime state.
2. **Overwrite the declared categories** (§3.2). No restart.
3. **`script`, if declared, is written now and runs at the next start** — and the
   response says so, so a caller is never left guessing.

A manifest supplied to the **creation** API (§2.11, W13) is the same operation
run as part of creation, so the bot's first container already carries it.

One residual case survives and belongs to **D2**: a manifest whose source is a
**moving ref** (a branch rather than a tag or SHA) can resolve to different
content on a restart nobody associated with a configuration change.

### 2.7 Apply records, per entry, whether the configuration was delivered

**Apply has no notion of "first boot".** Whether a bot is coming up for the first
time or has been running for a month, apply does exactly the same thing, and
nothing in it branches on which. Earlier revisions of this section built a policy
on that distinction; it is withdrawn.

**Its whole job: for every entry in the manifest, was that entry's configuration
delivered? Record each entry individually.** Those per-entry records *are* the
report, and they are the only thing apply produces.

Everything this section used to say follows from that, rather than needing to be
stated as policy:

- **There is no bot-level failure policy, because there is no bot-level
  decision.** "Do not disturb a running bot when a fetch fails" is not a rule —
  an entry that failed is an entry that was not delivered, and not delivering is
  not disturbing. The bot keeps running whatever it already had because nothing
  wrote over it.
- **Nothing is ever written to the bot record.** No status change, no
  de-activation, no readiness gate. This also removes the need for a status the
  lifecycle does not have — there is no `INACTIVE`, so a gate would have landed
  on `FAILED` and made "the container never came up" indistinguishable from "the
  container is fine, its manifest is not". **This restores design §4.3**, which
  put apply failure outside the readiness gate in the first place.
- **Recording is per entry; the write decision is per category.** These are two
  different levels and conflating them is how the previous revision of this
  bullet became wrong. Every entry's outcome is recorded individually, always.
  What gets *decided* from those outcomes is one thing only: whether that
  entry's **category** is written at all (all-or-nothing, below).
- **The apply-wide aggregate decides nothing.** `SUCCEEDED` / `PARTIAL` /
  `FAILED` is a summary derived from the entries for a caller's convenience.
  Nothing reads it and then acts — least of all on the bot record.
- **`on_fetch_failure` is per entry** (`keep_last` / `fail`), which is where
  "what happens when *this one* fails" belongs. `keep_last` means "reuse what we
  materialised for this entry last time", and its storage is the same store §2.8
  requires. **`skip` was removed** when §3.2 became overwrite — see below.
- **A category is overwritten all-or-nothing.** If any entry in a declared
  category cannot be materialised, that category is **not overwritten at all**;
  nothing about it changes, and every entry's outcome is still recorded. Under
  overwrite a partial set is a *destructive* set: writing `{A}` when the
  declaration was `{A, B}` deletes B. So a category is only ever written from a
  complete desired state.

  This is the **only** aggregate that drives a decision, and its scope is
  deliberately the category: one category's failure never withholds another's.
  It also settles the `keep_last` edge case by construction — an entry whose
  source fails on the **first** apply has no previously materialised copy to fall
  back on, so the set cannot be completed and the category is not written. A
  first boot with a flaky source therefore delivers nothing for that category
  rather than something partial, which is the safe end of the trade.
- **Iteration 1 records; it does not push.** The per-entry records are the
  deliverable, and the user pulls them when they want to know. There is no
  notification, no alert, no proactive message.

**Apply records delivery, not execution.** This is the boundary that keeps the
responsibility as narrow as stated above, and it dissolves two cases an earlier
revision wrongly carried as exceptions:

| | Where apply's record ends | What happens after is a different layer |
| --- | --- | --- |
| `script` | The `ac_bot_startup_script` row is written | The script runs in the container at boot, on the start command's single exit status (#935). Apply neither sees nor records that |
| **teclaw** | The artifact is handed over, or the per-file write lands | The engine applies it — in full, before reporting ready (X2/T1). That is the engine's contract (W12), not apply's record |

So these were never exceptions to a rule about bot status. They are simply
outside apply's scope: apply answers "did we deliver it", and the container and
the engine answer "did it work".

**What carries the signal**, since the bot record does not:

- `GET .../config-manifest/last-apply` is the authoritative answer to "is this
  bot's manifest applied", and W13's poll reports the same per-entry detail as a
  terminal state without touching the bot.
- **Any surface that shows a bot as healthy must be able to show that an entry of
  its manifest is not.** That is a UI/API requirement rather than a status-column
  one, and W8 owns making the signal reachable — a failure recorded where nobody
  looks is the failure mode this trades for.

**On notification, for the record.** An earlier revision of this section required
"a surfaced notification". That was not a requirement of the merged design —
none of the five design docs mentions notification at all — and it is withdrawn
rather than carried as an unimplemented promise. Whether the platform should ever
push a config failure at an owner is a product decision nobody has asked for.

If it is ever wanted, the reuse point is named here so it is not re-investigated:
`core/work_orders` already models a pure notice
(`WorkOrderNotificationService`, `NotificationCategory.NOTICE` alongside
`APPROVAL`), and would need a new `WorkOrderBizType` plus an event→message
mapping. `core/notify` is **not** it, despite the name — it lists bots eligible
for *engine* notification polling, which is bots pushing messages to users.

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

### 2.9 Substitution variables are named `BOT_*`, not `OCB_*`

`design.zh-CN.md` §4 and `manifest-schema.zh-CN.md` name the substitution
variables `OCB_BOT_ID`, `OCB_ENGINE_TYPE`, `OCB_ENV`, `OCB_TENANT`. **`OCB` is an
internal codename and nothing a user writing a manifest should need to know.** In
the code it appears only on internal machinery — `__OCB_RC`, a private shell
variable inside the start-command wrapper, and `OCB_AGENT_LOG_PRICE_*`, backend
config. It has never been a user-facing namespace.

They are renamed to **`BOT_ENGINE_TYPE`, `BOT_ENV`, `BOT_TENANT`,
`BOT_ARCH`**: self-explanatory (the user is configuring a bot), consistent with
the container environment's existing `BOT_DATA_DIR`, and still prefixed — which
matters because these are injected as environment variables into `script`, where
an unprefixed `${ENV}` would collide with the author's own variables.

`OCB_BOT_ID` has no `BOT_ID` counterpart: it is **dropped**, not renamed (W1
review). The four that remain are all properties of the *fleet*, which is what
lets one document be reused across bots. A bot id is not: `generate_bot_id()`
mints it at creation time (a date plus eight random characters), the caller
cannot choose it, and an author preparing content in a git repository has no way
to know it. Anything genuinely per-bot belongs in that bot's own manifest,
written literally.

### 2.10 Validation and authorisation move to a reusable seam

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

### 2.11 A bot can be created *from* a manifest

The manifest is not only something attached to an existing bot. There is a
public API that **creates a bot from a manifest**, taking the manifest alongside
the ordinary creation parameters (engine, name, description, …). It is
asynchronous — creation is slow — and it rides the existing two-phase Passport
flow, so the caller polls for the outcome.

This resolves what would otherwise be a hole in design §3.1's promise that "the
first configuration a bot receives already contains the manifest result". Through
`PUT` alone that promise is unreachable for a genuinely new bot: you cannot write
a manifest to `/bots/{bot_id}/config-manifest` before the bot exists, so its first
boot would always be bare. Creation-from-manifest closes it — the manifest is in
hand before the bot record is written, so apply runs inside creation.

**How it lands on the existing flow** (`core/bot_management/create_flow.py`):

| | What happens | Bot record |
| --- | --- | --- |
| Phase 1 `create_bot_with_authorization` | `bot_id` allocated platform-side (`generate_bot_id`, called in the router); preflight; Passport applied; `iframe_url` returned | **Does not exist** — the code's own comment: *"No token yet → authorization pending; nothing is created."* |
| User clicks the authorization link | | |
| Phase 2 `complete_bot_authorization` (polled) | Passport status queried; on `ISSUED`, `bot_service.create_bot(...)` runs | **Written here** |

**The manifest is persisted in phase 1**, keyed by the already-allocated
`bot_id`. This needs **no schema change**: the manifest table's key is
`(avernet_tenant, sha256(env, entity_id, bot_id))` and all three parts are known
in phase 1.

The alternative — having the caller re-send the manifest on every poll, which is
what the existing flow does with `spec` — was rejected: it means **the manifest
that was validated is not necessarily the manifest that gets applied**, since a
caller can send a different one on the poll. That is a latent issue for `spec`;
for a manifest, which installs skills, identity files, MCP config and a shell
script, it is not acceptable.

The cost is orphan rows when a user never clicks the link or Passport rejects.
Still accepted for v1, with cleanup deferred to #1698 — but the justification has
to be stated honestly, because *"an orphan manifest occupies no runtime resource"*
is true and answers the wrong question. **Nothing bounds these rows.** No bot
record is ever written, so ordinary bot deletion cannot reach them; and phase 1
allocates a `bot_id` **without consuming bot quota**, so the per-tenant ceiling
that bounds every other creation path does not apply here either. Innocent
retries — a user starting creation three times and finishing none — grow the
table with no ceiling and no reclaim path.

**Superseded by PR #1791 — there is no feature flag, and #1698 no longer gates
this endpoint.** The reasoning above was right about the hazard and wrong about
the only available answer. The creation job carries a configurable wall-clock
deadline (default 10 minutes), which gives every creation a terminal moment; the
job deletes the manifest **and any startup-script row phase A wrote** whenever a
creation ends without a bot — declined or expired alike. So the rows this endpoint
creates are bounded by their own jobs, which is what the flag was standing in for.
#1698's general sweeper remains worth having for rows no job can account for, but
it is no longer a precondition for shipping this.

**A fixed constraint, not a design choice: creation always requires a human.**
The Passport authorization link is an AgentPass limitation, outside our control.
So "create a bot" is inherently a one-at-a-time, human-in-the-loop operation, and
no feature may be planned that assumes otherwise.

Its reach is narrower than it first appears, and the distinction matters:

| | Needs an authorization click? |
| --- | --- |
| **Creating** a bot from a manifest (W13) | **Yes, one per bot.** Inherent |
| `PUT`-ing a manifest to an **existing** bot (§2.6) | No |
| Applying at republish / restart (W8) | No |
| Scale-out of an existing bot | No — instances share one platform state (#926) |

So design §9's **O6 (a template-level manifest, one declaration for many bots)**
survives for the case it was actually written for — *applying* one declaration
across many **existing** bots involves no authorization at all. What is not
viable is a "create N bots from one manifest" batch feature: that is N
authorization clicks, and no amount of platform work removes them. Nothing in
v1 depends on it; this is recorded so it is not proposed later.

Tracked as **W13**.

### 2.12 In iteration 1 the `script` may not depend on anything the manifest declares

**The mechanism, confirmed against the code.** Apply materialises `script` by
writing one row into `ac_bot_startup_script` and doing nothing else. That table
*is* the script's meta configuration, in the same sense that `mcp` is a registry
reference: a plain tenant-scoped DB write, needing no device binding. This is why
`script` is the one category that can be materialised before a container exists.
The platform composes it into the start command by itself:

- `BaasService._build_create_bot_payload` resolves the stored script
  (`_resolve_startup_script`) while building the payload, and `_get_start_cmd`
  bakes it into `after_create_cmd_hook`. No production caller passes a script in
  — resolving centrally is what makes every path deliver it.
- So every path that rebuilds a payload re-reads the row: create, service-bot
  release, `upgrade_bot`, and both device services. So a rewritten row is picked
  up by whatever next rebuilds the payload, with no extra machinery — which is
  why §2.6 does not restart for it: `script` is **delivered** immediately and
  **effective** at the next start.

**The consequence.** Because the script is baked into the start command at
payload-build time, it executes at container start — while `identity`, `skills`,
`resources` and `engine_config` can only be delivered *after* the container is up
(§3.4). On a **first boot**, the script therefore runs before any of them exist.

**Iteration 1 states this as a rule rather than engineering around it: a
manifest's `script` may not depend on anything else that manifest declares.** It
belongs in the API docs, in the manifest reference, and in a W8 test. No extra
restart is inserted to close the window: suppressing the script on the first boot
and restarting would add a full boot to every creation, and *not* suppressing it
would run the script twice — which is exactly what design §2.4 rejected its
alternative two for.

**Iteration 2 lifts it.** Once every category can be delivered into the container
*before* start (#1508 — D4's option (a) or (c)), the ordering inverts on its own:
entities land first, the script runs after, and the restriction is deleted. It is
temporary by construction, and every place it is written must say so, so that it
can be removed rather than re-argued.

**This corrects design §3.4.** Its fixed apply order
(`engine_config → identity → resources → skills → mcp`, script last) and its
promise that 「script 可以依赖 manifest 声明的实体已经就位」 hold only once
iteration 2 lands. Until then, on the BaaS family's first boot, `script` is
effectively **first**. teclaw is unaffected twice over: its artifact carries
everything before start, and teclaw does not support `script` at all
(`bot_startup_script/services/_support.py`).

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

### 3.2 D2 — manifest-upgrade policy · #1467 · **RESOLVED (revised: overwrite)**

The failure half was already settled (§2.7). The convergence half is settled as
**category-scoped overwrite**, aligned with how teclaw already takes an artifact.

**This revises an earlier resolution of D2** — an entity-level three-way diff
(N, N+1, disk) with a per-entry `on_conflict` policy. That is withdrawn in favour
of the simpler rule below. What the earlier version was protecting is now
protected by a named reserved set instead of by a diff.

#### The rule

**Making a manifest effective overwrites each declared category so that it equals
the declaration.** A category the manifest does not declare is not touched at
all.

| | Behaviour |
| --- | --- |
| Category declared | Its area becomes **exactly** what the manifest says. Anything else in that area is removed |
| Category not declared | **Untouched.** The manifest expresses no opinion, so nothing happens |
| `skills: []` | The empty set *is* a declaration: **every skill is removed.** This reverses the earlier reading of `[]` as "stop managing without deleting" |
| Reserved names (below) | Never written, never removed — outside every overwrite |

#### "The area" is defined per category, not globally

Overwrite needs a scope, and the scope is not the same shape for every category.
Getting this wrong is how a rule meant to converge a skill list ends up deleting
a bot's working directory:

| Category | The area that is overwritten |
| --- | --- |
| `skills` | The **active skill set**. It equals the declaration; skills not listed are removed |
| `identity` | The **identity file set**, minus the reserved names below |
| `resources` | **Only the declared `path` subtrees.** Nothing outside a declared `path` is touched — the workspace is the bot's working area, not the manifest's |
| `mcp` | The **set of enabled servers** |

`resources` is the one that must not be read as "the category area is the
workspace". W6 already states the narrower rule (a declared `path`'s tree is
replaced wholesale, nothing outside it is touched); this table is that rule made
general, not a new one.

#### A category is written all-or-nothing

If any entry in a declared category cannot be materialised, **the category is not
overwritten** — nothing about it changes and the per-entry failures are recorded
(§2.7). Overwrite makes a partial set destructive: writing `{A}` when the
declaration was `{A, B}` removes B, so a transient fetch failure would delete a
working entity. A category is only ever written from a complete desired state.

**The guarantee is scoped to before delivery starts, and that scope is
deliberate.** Every entry in a category is **materialised first** — fetched,
verified, unpacked, stored (W11) — and only then is any delivery write issued
for that category. So the all-or-nothing decision is made when the whole desired
state is in hand, and the failure mode it removes is the common one: a network
fetch failing.

What it does **not** cover: a delivery write failing part-way through a category
whose entries all materialised. The third identity file failing to write leaves
the first two written and, under overwrite, some old entries already removed.
v1 does **not** roll that back — there is no category-wide staging or
transaction across the services apply writes through, and inventing one would
mean a distributed transaction over `IdentityService`, `SkillSetService` and the
resource service.

Stated rather than hidden, because the two failure classes are very different in
likelihood: fetching from a caller's server across the internet fails routinely;
a local service write failing mid-category does not. The residual case is
recorded per entry like any other, and the apply report is what shows a category
in a mixed state. Closing it properly would need a staging-and-commit protocol
across the materialisers — worth its own item if it ever proves necessary, not
worth pre-building.

This is also why **`on_fetch_failure` lost its `skip` value**. Under the
withdrawn per-entry diff, `skip` meant "proceed without this one" and left the
existing entity alone. Under overwrite it would mean "delete this one" — the
opposite of what the name says. `keep_last` (complete the set from the stored
copy) and `fail` (do not write the category) remain, and they cover the cases
`skip` was reached for.

#### Reserved names — the one exception, and it is a list, not a rule

Two files are engine-generated runtime state that happens to live inside the
identity area on both engine families (ARCA: flat in the workspace; teclaw: under
`/identity`):

```
MEMORY.md
IDENTITY.md
```

They are **never written and never removed by apply**, whether or not a manifest
declares them. `kernel/bot_config/artifact.py` already names exactly these two as
engine-generated; today nothing enforces it, and this makes it enforced.

That this is a finite, enumerated list is what makes the policy negotiable with
another engine team. The previous policy's protection — "preserve every file
declared in neither version" — is an *unbounded* set, which we could neither
compute for teclaw nor ask them to honour (that was W12's hardest clause). Two
names, both sides, is a contract that can actually be agreed.

#### What this accepts, deliberately

- **A skill installed through the UI is removed** when a manifest that declares
  `skills` is applied. For a declared category the manifest is the sole owner —
  the manifest and the UI are mutually exclusive there. Accepted by decision.
- **Bot-created files inside a declared category are removed**, unless they are a
  reserved name. This is the cost of dropping the three-way diff, and it is why
  the reserved list exists at all.

#### What it buys

- **One semantics across both engine families**, which was the reason to revisit
  D2 at all. teclaw takes a whole artifact and replaces; ARCA now does the same
  thing to the declared categories.
- **No version-N file list is needed to converge.** The earlier policy needed it
  to tell "the declaration dropped it" from "the bot created it"; overwrite needs
  no such distinction. **W11 stops being a hard dependency of W4** — it remains
  required for §2.8's audit and for `keep_last`, but it no longer gates the apply
  engine.
- **No `on_conflict` knob.** `overwrite` / `preserve` / `fail` all disappear; the
  category rule plus the reserved list covers what they were for.
- **W12 shrinks from a negotiation to a statement.** We no longer ask teclaw to
  implement a preservation rule we cannot verify. See W12.

#### Relationship to design §3.2

This moves **back toward** the merged design, which replaces a declared tree
wholesale. The earlier revision of this section superseded that rule; this one
largely restores it, with the reserved list as the single documented refinement.

#### On ARCA this is mostly existing machinery

Two findings that make the ARCA side smaller than it looks:

- **Skills already reconcile full-state from the DB.**
  `skill_symlink_listener` performs a *"full skill-symlink sync … refreshed from
  the DB's current active skill sets"*, and the same reconcile is reachable on a
  live bot through `DeviceSyncDispatcher` / `sync_symlinks`. Apply writes the
  declared set into the active skill set and triggers the existing reconcile; the
  deletion half is already implemented.
- **Identity is plain file writes.** Nothing in `core/services/identity.py`
  involves a restart.

#### Moving refs: two modes

A branch ref can still resolve to different content on a restart nobody
associated with a configuration change (§2.6's residual case). Resolved with a
mode switch rather than a blanket rule:

- **Strict mode** — if the resolved SHA differs from the one recorded at the last
  apply, **reject the change**. The bot keeps running what it has.
- **Non-strict mode** — apply the new content and **warn**.

Both modes require recording the resolved SHA per apply, which the report already
carries (design §7).

**Where the mode is set, and its default.** An earlier revision left this to
W4's spec, which is not good enough: W8's criterion *"whatever D2 decides about
moving refs is enforced here"* is unimplementable while the selector does not
exist, and two implementers would guess opposite behaviours for the same
document. Settled here:

- **The field is `mode` on the source, with values `strict` and `non_strict`.**
  Named here so the prose and the acceptance criteria that implement it (W1's
  refusal list, W7's `Done when`) cannot drift apart.
- **Per source, on the source declaration** — not per bot and not per manifest.
  The property being described is *"is this ref allowed to move under me"*, which
  belongs to the thing that has the ref. A manifest mixing a pinned vendor
  dependency with a fast-moving internal repo is the normal case, and a per-bot
  switch cannot express it.
- **Default: non-strict.** Someone writing `ref: main` instead of a SHA is asking
  for the moving behaviour; making the default reject it would turn the common
  case into a surprise. Strict is for a caller who wants pinning semantics
  without writing the SHA out.
- **The warning surfaces in the apply report**, on the entry, naming the previous
  and new SHA. Iteration 1 is pull-only by decision (§2.7), so the report is the
  only place it can surface — there is no push channel to put it in, and inventing
  one here would contradict that decision.
- **A SHA ref ignores the mode entirely.** It cannot move, so neither branch of
  the switch can fire; setting the mode on one is accepted and inert rather than
  an error.

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

**#1508 is iteration 2's item, and it carries a second payload.** Closing it does
not only move delivery earlier; it is also what lifts §2.12's rule that a
manifest's `script` may not depend on anything the manifest declares. Whoever
picks it up owns deleting that restriction from the docs, the manifest reference
and W8's test — not just the delivery change.

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
2. ~~**It interacts with the §2.7 readiness gate.**~~ **Resolved, and this is
   what resolved it.** Gating the bot's activation on a post-start apply would
   mean "start, apply, then de-activate on failure" — which is why §2.7 now keeps
   apply failure at the manifest level and leaves the bot record alone.
3. **Scale-out instances** started *during* that window see partially delivered
   content. (Instances do not diverge afterwards: they share one NAS `bot-data`
   mount, so a delivered file is visible to all of them. This is the same window
   as cost 1, not a separate one.)

**Costs 1 and 3 are accepted for iteration 1, not mitigated.** An earlier
revision proposed keeping the bot out of the serving path until the post-start
apply reports success — that is exactly the gating §2.7 rejects, and it is
withdrawn. **#1508 closes the window properly** by delivering every category
before start, at which point there is no ACTIVE-but-unconfigured moment to gate.

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

> **All four rows are now closed or answered.** Nothing in this plan is waiting on
> another team for a *decision*. What remains across engine boundaries is
> execution: teclaw agreeing the semantics contract (**W12**) and implementing the
> `cli_tools` artifact protocol once we design it.

| # | What is being asked | Gates | Owner | State |
| --- | --- | --- | --- | --- |
| ~~**X1**~~ | ~~Ant Code credentials~~ | — | — | **Closed: dedicated machine account + `read_repository` 访问令牌**, HTTP Basic. No Deploy Tokens exist; expiry is owner-managed (§4, X1) |
| **X2** | teclaw readiness/convergence — **answered**, see below | **W8** teclaw arm | teclaw + backend | **Answered**; superseded by W12 |
| ~~**X3**~~ | ~~`cli_tools` target architecture (O9)~~ | — | — | **Closed: `linux/amd64`** — `uname -m` on an ARCA container returns `x86_64` (§4, X3) |
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
distinguishable. Under §2.7 a failed entry is simply an entry that was not
delivered, so a running bot is untouched — correct,
but it means **a token can silently expire and nothing visibly breaks until a bot
is next created or restarted**, at which point the failure is reported on the
manifest rather than the bot (§2.7).

So: an authentication failure (401/403) must be reported in the apply report as
*"credential `<name>` was rejected"*, named and distinct from a generic fetch
error, so the owner knows to rotate rather than hunting a network problem. Cheap
to do, and the only thing that makes owner-managed expiry operable.

Deploy *keys* stay the best security answer on paper — repo-scoped and read-only
beats user-scoped and read-only — but they are SSH: a second transport in a
fetcher built HTTPS-only around SSRF guards, host-key verification, and a
credential model storing a private key rather than a header. Not worth it for v1
now that the header route is settled.

### X2 — fully answered · **CLOSED**

- **T1 (readiness ordering) — yes.** teclaw applies the whole artifact to the bot
  before it reports ready. "Configuration precedes readiness" holds on that side.
- **T2 (convergent re-delivery) — CONFIRMED: teclaw's re-delivery is a full
  overwrite** (teclaw owner, 2026-08-30). This started as "not ours to worry
  about — the semantics inside a teclaw container are teclaw's", which was a
  fine answer while our own policy did not depend on it. It stopped being fine
  when §3.2 was revised to **adopt** overwrite precisely because teclaw already
  does it: an assumption load-bearing for our design has to be confirmed, not
  inherited. It now is.
- **T3 (engine config on first boot) — removed from scope.** `engine_config` is
  **excluded from the first iteration**, so the question does not arise yet. This
  narrows W4's no-fetch materialisers to `mcp` and `script`.

**T2's answer is what §3.2 rests on.** We own convergence semantics for
BaaS-family bots; teclaw owns them for teclaw bots. Rather than reconciling two
different policies, §3.2 adopted theirs — a declared category is overwritten to
equal the declaration — so the same manifest now behaves the same way on both
engine families by construction rather than by negotiation.

**What survives as W12 is a write-up, not a negotiation.** The contract states
what both sides do, plus the one thing an applier must never touch: the reserved
names `MEMORY.md` and `IDENTITY.md`. That replaces the withdrawn clause asking
teclaw to preserve every file declared in neither version — an unbounded set we
could neither compute for them nor verify. Two names is a contract that can be
agreed and checked.

**The delivery contract itself is unchanged.** The manifest feature adds nothing
to how an artifact is handed over. The only addition on the teclaw side is
`cli_tools`, specified in `teclaw-cli-contract.zh-CN.md` — that document is what
goes to the teclaw owner for implementation.

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

#### Answered: `linux/amd64`

`uname -m` inside an ARCA bot container returns **`x86_64`**, so v1 ships a single
`*-linux-amd64` URL per tool and needs no per-arch sources.

Two observations recorded from the same check:

- The image is **RPM-based**, not Debian (`dpkg` is absent). Immaterial to
  `cli_tools`, which ships static binaries and archives, but worth noting because
  schema §3.7 names `apt` as an example of the package-manager installs that
  belong in `script` — on this fleet that would be `yum`/`dnf`.
- The evidence is **one sampled container**. That is strong but not a proof of
  fleet uniformity across clusters or regions. It does not need to be: the ELF
  validation below turns a wrong assumption into a loud apply-time failure rather
  than a silent one, and `${BOT_ARCH}` is implemented as a constant, so a future mixed
  fleet changes only where that value comes from.

The reasoning behind the question is kept below, because the distinction it turns
on is easy to lose.

#### Why it was about CPU architecture, not paths

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
per-arch sources — per-arch URLs, or an `${BOT_ARCH}` substitution.

This is **not** the question of where the tool directory sits or whether the PATH
reaches it. That is our design choice, engine-independent, and settled. Nor does
it vary by engine: it varies by the **machine the container is scheduled on**, so
it is a question for whoever operates the ARCA fleet.

**It cannot be answered from the code.** The backend has no notion of container
architecture: images are pinned by name (`arca_image_pin.py`, `sbot_docker_image`)
and placement is ARCA's decision. There is no arch branching anywhere in
`core/service_bot/` or `core/devices/`.

#### W9 does not have to wait for it

**Owner.** `totalfrank` · 0.25 d · day 4 · design (§7)


Two cheap choices make the answer non-blocking:

- **Implement `${BOT_ARCH}` now, resolving to the constant `amd64`.** About one
  line. Users can write `mycli-linux-${BOT_ARCH}` immediately and it works. If the
  fleet ever stops being uniform we change only *where the value comes from* — a
  per-bot lookup instead of a constant — with **no schema change, no manifest
  change, no version bump, and nothing for users to rewrite**. Merely *reserving*
  the name would reject anyone who used it, which helps nobody; the whitelist is
  versioned with `schema_version`, so the point is to avoid amending a released
  contract later, and implementing it achieves that and more.
- **Validate the binary's architecture at materialisation.** Read the ELF header
  of a fetched binary and refuse it when it does not match the target. A mismatch
  then fails loudly in the apply report, rather than as an `exec format error` the
  model hits mid-task with no explanation.

With the answer in hand, the first makes a future mixed fleet **invisible to
users**, and the second is worth keeping regardless of architecture: `digest`
answers *"are these the bytes you asked for"*, ELF validation answers *"can this
machine run them"*, and a digest of the **wrong** binary is still a valid digest.
The failures it actually catches are usually user error — a darwin build
published to the URL, amd64 and arm64 paths swapped, or a 404 HTML page the user
computed a digest over. All three pass a digest check.

**A third option, no longer needed but recorded.** Under D4's interim policy
(§3.4) delivery happens *after* the bot starts, so the container exists when
`cli_tools` is materialised. If the engine can report its own architecture,
`${BOT_ARCH}` could be resolved **per bot at delivery time**, answering the
question by construction. Unnecessary for a uniform `amd64` fleet; the natural
answer if that ever stops being true.

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

**Owner.** `totalfrank` · 0.25 d · day 1 · design (§7)



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

**Owner.** `lucas-xzp` · 0.5 d · day 2 · build (§7)


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

**Owner.** `totalfrank` · 0.25 d · day 2 · design (§7)



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

**In scope.** Write the policy from §3.2 as an engine-facing contract and take it
to the teclaw team. **The revision to §3.2 turned this from a negotiation into
largely a statement plus one question**, and the item shrank accordingly:

- **State:** a declared category is overwritten to equal the declaration; an
  undeclared category is untouched; `MEMORY.md` and `IDENTITY.md` are never
  written and never removed. Two reserved names is a finite list an engine team
  can actually agree to — unlike the withdrawn "preserve every file declared in
  neither version", which was an unbounded set we could neither compute for them
  nor verify.
- ~~**Ask:** what does an artifact re-delivery actually do on their side?~~
  **ANSWERED — it is a full overwrite.** Confirmed with the teclaw owner
  (2026-08-30). X2/T2 is closed, and §3.2's central assumption is now a
  confirmed fact rather than an inherited one: our overwrite semantics and
  theirs are the same operation.

Record the outcome, including anything they decline.

**What the confirmation settles, beyond unblocking this item:**

- **§3.2 rests on verified ground.** Category overwrite was adopted *because*
  teclaw already replaces wholesale; had that been wrong, the whole revision
  would have needed reversing.
- **The delivery contract to teclaw is unchanged.** Nothing about how we hand
  over an artifact changes for the manifest feature. The only addition on their
  side is `cli_tools` — specified separately in
  `teclaw-cli-contract.zh-CN.md`, which is the document to share with them.
- **This item is now a write-up, not an open question.** It states what both
  sides do, and its remaining calendar cost is their review of the CLI addition
  rather than a semantics negotiation.

**Deliverable.** `engine-convergence-contract.zh-CN.md` — this section's rules
written as requirements on the applier (R1–R9), the per-category region table,
the split of responsibilities, and a self-check list. Together with
`teclaw-cli-contract.zh-CN.md` it is the complete package handed to teclaw: the
former is the convergence semantics for every category, the latter the one
addition they have to implement, `cli_tools`.

> **✅ Done (2026-08-31).** All four acceptance criteria are met: the contract
> states requirements on the applier, the reserved-names clause is in, teclaw
> reviewed and agreed point by point, and the conclusions are recorded in the
> capability matrix (`engine-requirements.zh-CN.md` §2 and T1/T2/T4 in §3).
>
> **The review caught a substantive error, recorded here so it is not repeated.**
> The first draft stated "declared categories overwrite, undeclared are
> untouched, an empty set is a declaration, absence is not `[]`" as **requirements
> on teclaw**. That was wrong: `ConfigComposer` assembles the artifact by reading
> **platform entities** for their full current state, not by reading the manifest,
> so **an artifact is always a complete snapshot, never a diff** — the
> declared/undeclared distinction dissolves before the artifact exists and never
> reaches the engine. The contract is now split into **A1–A5 (requirements on the
> applier)** and **P1–P4 (platform-side rules, for context only)**.
>
> Two other decisions landed with it: **`md5` is a change test, not an integrity
> gate** (an unchanged md5 means skip the re-download and replace), and
> **`schema_version` is not bumped** — `cli_tools` rides into v4 and relies on
> A5's ignore-unknown-fields rule. Placement, PATH approach and sandbox policy are
> teclaw's own business; the platform does not ask.

**Depends on.** §3.2 being settled (it is) · **Blocked by.** —

**Done when.**

- [ ] The contract states each rule as a requirement on an applier, not as a
      description of our implementation.
- [ ] The **reserved names** (`MEMORY.md`, `IDENTITY.md`) are stated as the one
      thing an applier must never write or remove. This replaces the withdrawn
      "row 4" clause, which asked for an unbounded set we could not verify.
- [ ] teclaw has reviewed it and either agreed or named the parts they will not
      implement — the second is a usable answer; silence is not. **The
      convergence semantics are already confirmed (full overwrite); what remains
      for their review is the `cli_tools` addition.**
- [ ] Any divergence they declare is written into the capability matrix, so the
      difference is documented rather than discovered.

**Size.** Small — smaller now that the semantics question is answered. The
calendar time is the other team's review of the CLI addition.

---

#### W13 — Create a bot from a manifest · #1696

**Owner.** `totalfrank` · 0.5 d · day 3 · design (§7)

> **Design settled on PR #1791** — see
> `specs/2026-09-01-create-bot-with-manifest/`. Four things below are superseded
> by it and are corrected in place: the endpoint is **ARCA-only** (teclaw creation
> is W8's, see there); a **task-queue job** carries the creation rather than the
> caller's polling; **applying itself became a task** on every path, replacing
> W4's daemon thread; and the terminal states name *what* failed. There is no
> feature flag.

**Goal.** A public, asynchronous API that creates a bot from a manifest plus the
ordinary creation parameters, so the bot's **first** container already carries its
configuration (§2.11).

**Depends on.** W1 (document storage, schema, capability resolver) and W4 (the
apply engine it invokes) — **plus a materialiser for every category this endpoint
accepts**. W4 alone materialises only `mcp` and `script`, so with W5/W6 absent a
creation request declaring `identity`, `skills` or `resources` would pass
preflight, take the user through Passport authorization, provision a bot, and
*then* fail apply — the worst place to discover it, because the bot now exists.
Either W5 and W6 are dependencies too, or this endpoint's accepted vocabulary is
gated to what has landed (W1's gating rule). Do not let it accept a category whose
materialiser is not there. **Settled: the gate is derived from the materialiser
registry**, so W5/W6 widen the endpoint by landing rather than by an edit here ·
**Blocked by.** —

**Engine scope: ARCA only.** This item's whole pre/post-container split exists
because `BaasService._build_create_bot_payload` reads the startup-script row while
composing a start command. teclaw has no analogue — `TeclawProvisionService`
composes a config artifact at provision time — so teclaw creation is **W8's**,
which already claims it in scope ("apply at bot creation … teclaw before the first
artifact is assembled") and whose first acceptance criterion is the first-artifact
guarantee. This endpoint refuses a teclaw engine; **W8 owns lifting that refusal**
along with the teclaw creation mechanism.

**Why it is its own item rather than part of W1.** W1 is deliberately scoped to
never touch `create_flow`; that coupling is the one this plan most wants to
avoid. This item is where the coupling belongs, and it is substantial on its own:
a new public endpoint, an asynchronous status surface, manifest storage before a
bot record exists, capability validation from parameters rather than a record,
and integration with the two-phase Passport flow.

**In scope.** The creation endpoint (manifest + engine, name, description, …);
phase-1 manifest persistence keyed by the allocated `bot_id`; the poll/status
endpoint and its states; invoking apply as part of creation.

**Out of scope.** #1698's general orphan sweeper (still worth having, but **no
longer a gate on this endpoint** — the creation job's deadline bounds the rows it
writes, and the job deletes its own manifest and startup-script rows when a
creation ends without a bot) and creation idempotency (#1697 — a pre-existing gap: `generate_bot_id`
mints the id platform-side with no idempotency key, so a retried create makes a
second bot regardless of manifests).

**Poll states.** Addressed by `bot_id` alone — the poll never takes the manifest
or the creation parameters, and makes no external call.

```text
AWAITING_AUTHORIZATION   waiting for the user to follow the Passport link
        │
        ├──► AUTHORIZATION_REJECTED   terminal — the user declined
        ├──► AUTHORIZATION_EXPIRED    terminal — Passport expired, or the job's
        │                             deadline elapsed unclicked
        ▼
CREATING                 authorized; bot record written, container provisioning
        ├──► CREATE_FAILED   terminal — no usable bot. Nothing to do with the
        │                    manifest
        ▼
APPLYING                 the post-container apply is running
        ├──► READY         terminal — bot up, manifest landed
        └──► APPLY_FAILED  terminal — bot up, part of the manifest did not land
```

- **`PARTIAL` means some category was not overwritten** (§3.2 all-or-nothing), so
  it reports as a failure, not `READY`. An earlier revision mapped `PARTIAL` to
  `READY` on the grounds that the skips were author-sanctioned; that rested on
  `on_fetch_failure: skip`, which no longer exists.
- **Three failure modes, three answers, none needing prose to tell apart:** an
  invalid manifest is a `422` at submission with no bot and no state; a bot that
  could not be created or never came up is `CREATE_FAILED`; a running bot with an
  incomplete manifest is `APPLY_FAILED`. A single `FAILED` covering the last two
  was the real source of the "did I get a bot or not?" ambiguity.
- `APPLY_FAILED` is a **manifest-level** terminal state (§2.7). The bot record is
  not touched, and the name says so, so the response does not have to argue it.
  The apply record itself still reads `PARTIAL` — only this poll's one-word
  summary maps it, and `POST …/config-manifest/apply` on a running bot is
  unaffected.
- **`APPLYING` turns D4's interim cost into a visible state.** Post-start delivery
  (§3.4) leaves a window where the bot is ACTIVE but unconfigured; a caller that
  waits for `READY` never observes it. The window stops being an invisible trap.
- **A `bot_id` this endpoint did not create is a `404`, not a state.** A bot made
  the ordinary way and given a manifest by `PUT` has a bot record and an apply
  record and no creation job — which is exactly the shape of `CREATING`, so
  without the job the poll would report a creation that never happened. The job
  is found by an idempotency key derived from `(tenant, entity_id, bot_id)`,
  which also keeps one owner's `bot_id` from finding another's pending
  authorization URL.

**Operational precondition (PR #1791).** Applying — and therefore creating a bot
with a manifest — only progresses where `task_queue_worker.enabled=true` and
`ac_task_queue` is provisioned. That flag gated an optimisation before this item;
it now gates the feature, and the failure mode is not slowness. With the worker
off, submission still answers `202` and the poll sits at
`AWAITING_AUTHORIZATION` until the deadline retires it as
`AUTHORIZATION_EXPIRED`; an explicit apply still answers `202` and its report
stays `RUNNING` until the lock's TTL expires. Neither ever completes.

**Done when.**

- [ ] The manifest is **validated before Passport is applied** — inside the
      preflight stage of `create_bot_with_authorization`, alongside the quota,
      name and engine checks. A user must never complete an authorization only to
      be told their manifest was invalid; that wastes their time and burns a
      Passport application.
- [ ] Capability is validated from the **request parameters** (engine, bot type),
      not from a bot record, since no record exists in phase 1. W1's resolver
      grows this entry point.
- [ ] The manifest is persisted in phase 1 and read in phase 2 — **the manifest
      that was validated is the manifest that is applied**. It is never re-sent
      by the caller on a poll.
- [ ] Storage needs **no schema change**: the existing
      `(avernet_tenant, sha256(env, entity_id, bot_id))` key works, all three
      parts being known in phase 1.
- [ ] **Tenant context survives to wherever apply actually runs**, by the right
      mechanism for how it is scheduled (see the note below). Tested, not left to
      memory: a wrong tenant here silently substitutes the wrong `${BOT_TENANT}`
      **and reads and writes the manifest table under the wrong tenant** — an
      isolation failure, not merely a correctness one.
- [ ] Polling reports the states above, and both terminal states carry the apply
      report.
- [ ] The pre-existing `PUT` path is unchanged: a bot created any other way can
      still be given a manifest afterwards and have it **take effect
      immediately, by the same no-restart path as any other existing bot**
      (§2.6). The two paths coexist; neither leaves a bot on stale
      configuration until its next lifecycle event.
- [ ] **Apply is invoked in two phases** (W4), and this item is the reason the
      orchestrator has that shape: **phase A** (`script`) runs before
      `_build_create_bot_payload` composes the start command, **phase B**
      (everything else) after the container is up.
- [ ] **Settled: phase A runs before the bot record is created at all**, not via a
      hook inside `create_bot`. It needs nothing from the record — the
      startup-script row is keyed by `(entity_id, bot_id)`, both known at
      submission, and the placeholder whitelist is exactly `BOT_ENGINE_TYPE`,
      `BOT_ENV`, `BOT_TENANT`, `BOT_ARCH` (no `BOT_ID`, no `BOT_NAME`). Ordering
      it ahead of creation makes the guarantee true by construction, and leaves
      `bot_service.py` untouched.
- [ ] **Settled: the creation is carried by a durable task-queue job**, not by the
      caller's polling, with a configurable deadline (default 10 minutes). The job
      waits for authorization, runs phase A, creates the bot, waits for the
      container, starts phase B and finishes — it does **not** wait for phase B,
      because nothing in the platform is blocked on it. A caller who stops polling
      still gets a configured bot; a caller who never authorizes gets a bounded,
      terminal expiry.
- [ ] **`script` is written before the create payload is built**, not with the
      rest of apply. It is the one category that needs no container (§2.12), and
      `_build_create_bot_payload` reads the row while composing the start
      command — so the row must exist by then for the first boot to carry the
      script at all. Everything else is delivered post-start, which is why
      iteration 1 forbids a script from depending on it.

**Note — how tenant context reaches apply depends on how apply is scheduled.**
Two mechanisms are available and they behave differently; only one is already
solved:

| Mechanism | Does tenant survive? |
| --- | --- |
| A thread spawned during the request | **Yes — already solved.** `threading.Thread(target=bind_current_avernet_tenant(fn), daemon=True)` is the established pattern: `bot_publish_service.py:1267` (`_do_restart`, itself an apply point), `baas_publish_poller.py:57` (`_poll`, the natural home of `CREATING → APPLYING`), `bot_service.py:1979`. The wrapper reads the tenant at **bind** time, inside the request thread, and re-establishes it inside the new thread. Follow the pattern and there is nothing to do |
| The task queue | **No, and this is now the live case.** `core/task_queue`'s model carries `env`, `app` and `idempotency_key` but nothing tenant-shaped, and the module has no tenant handling at all. A task is enqueued now and run later by a worker, so no request context remains to capture and `bind_current_avernet_tenant` cannot help. The tenant rides in the task `payload` and the handler opens `avernet_tenant_scope(...)` around its body. **This gets a test, not a comment**, because the failure is silent: `get_current_avernet_tenant()` is a *total* function that returns the **default** tenant outside a request rather than raising, so a handler that drops it does not crash — it quietly reads and writes the manifest tables under the wrong tenant |

**Superseded by PR #1791: every apply path now uses the task queue.** When this
note was written no apply path did, and W13 was told it need not solve the tenant
question. It does: W13 puts the creation on a job *and* moves apply execution
itself off W4's daemon thread onto a task, for all three cases — the
pre-container phase, the post-container phase, and the explicit apply on a running
bot. The queue's own README names the daemon-thread pattern as the one it exists
to replace ("loses work on restart and double-runs across pods"), and creation
*depends* on an apply completing, so a thread that dies would boot a bot without
its script. `skills_pool` was the only adopter before this (`skills_pool.reconcile`,
`skills_pool.quarantine.cleanup`).

Two consequences to carry forward. **The worker becomes load-bearing**: applying,
and therefore bot creation, only progresses where `task_queue_worker.enabled=true`
and `ac_task_queue` is provisioned — with the worker off, creations do not run
slower, they never complete. And **re-runs are safe by convergence, never by
"retry is off"**: at-least-once is structural, since a crashed worker's task is
re-claimed once its lease expires whether or not a handler ever asks for a retry;
safety comes from apply re-planning against current state, plus the apply lock.

A smaller footgun worth knowing: `bind_current_avernet_tenant` *looks* like a
decorator (it uses `functools.wraps`) but captures the tenant when the wrapping
expression is evaluated. Used as a real `@decorator` on a module-level function
it would capture at **import** time, when no request exists, and bind the default
tenant permanently. Every current call site wraps inline at the
`threading.Thread(...)` construction, which is correct.

**Size.** Large.

---

#### W1 — Manifest document: storage, schema v1, capability, API · #1469

**Owner.** `totalfrank` · 0.75 d · day 1 · design (§7)



**Goal.** A bot can carry a config-manifest document that is stored, validated
and readable, and a caller can ask which categories that bot supports. Nothing
is applied.

**In scope.**

- New module `core/bot_config_manifest/` with a `README.md` carrying the
  Context Boundary block required by `docs/arch/context-boundary-format.md`.
- DDL under `core/bot_config_manifest/sql/`. One row per bot; uniqueness on the
  logical key itself, `(avernet_tenant, env, entity_id, bot_id)`, with no
  surrogate column. InnoDB's 3072-byte index budget therefore constrains the
  column widths: `entity_id` is `varchar(256)` rather than `ac_bots`' 1024 (which
  would be 4096 bytes on its own and over the cap), putting the four columns at
  2384 bytes. `ac_bot_startup_script` hashes the same logical key into a
  surrogate; it did not have to, and this table does not copy it. Same tenancy
  reasoning as that table.
- Repository contract `core/repository/protocols/bot/config_manifest.py` and
  implementation `core/repository/implementations/bot/config_manifest.py`, the
  protocol declared as a base so an omitted member fails at construction.
- Service API contract `api/bot_config_manifest_service.py`, registered in the
  conformance `_PAIRS`.
- Schema v1 parse + validate, covering: `schema_version` (unknown ⇒ refuse);
  top-level `sources`; `manifest.{mcp,resources,skills,engine_config,identity,cli_tools}`;
  `script`.
- Routes `GET`/`PUT`/`DELETE /openapi/v1/bots/{bot_id}/config-manifest` and
  `GET /openapi/v1/bots/{bot_id}/config-manifest/capabilities`, each with its
  `ADMISSION` row, under `PublicAPIRoute`.
- A feature flag gating the surface. **It lifts when W8 lands, not W5.** These
  routes are public, and until W8 wires `PUT` to immediate apply (§2.6) an
  accepted manifest would sit unapplied; until W6 lands, a document declaring
  `resources` would be accepted with no materialiser behind it. Either the flag
  stays on through W6 and W8, or each incomplete category and trigger is gated
  independently — one flag through W8 is the simpler of the two.
- **The rule: the surface accepts nothing it cannot apply.** Anything the
  document can express and no landed code can act on is reported `unsupported`
  and refused at `PUT`. The flag lifting at W8 is not enough on its own, because
  W1 parses the **whole vocabulary** while iteration 1 implements only part of
  it, and the gap is not confined to categories — a source *form* with no
  resolver has exactly the same failure mode. The capability resolver therefore
  answers **per accepted construct**, not per bot and not only per category.

  This rule has now been arrived at three times, each time as a new instance
  (`cli_tools`, then `engine_config`, then the source forms below). It is written
  as one rule with one table so there is no fourth. As of iteration 1:

  | Construct | Why nothing can apply it | Refused until |
  | --- | --- | --- |
  | category `cli_tools` | W9 is deferred and unscheduled — no materialiser, no PATH delivery, no artifact field | W9 lands |
  | category `engine_config` | Removed from iteration 1 by the X2/T3 decision (§4), so W4's no-fetch materialisers are `mcp` and `script` only | its materialiser returns |
  | a `from` reference to a **named source** | Named sources are resolved by W7, which may be cut from v1; W5 excludes them explicitly | W7 lands |
  | a **git** source | Same — the git resolver is W7's | W7 lands |

  An implementer adding anything to the vocabulary must add a row or the code
  that applies it. **Leaving the surface accepting something no code applies is
  never the third option** — that is the shape of every entry in this table.

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
  - a `source` URL carrying **userinfo** — `https://user:token@host/path`. The
    schema requires every private-source secret to go through a credential
    reference (W3), and an inline token would be stored in the document, read
    back byte-exact by `GET`, and recorded by W11 as provenance — three places
    the encrypted, never-readable credential store exists to keep it out of;
  - `digest` on a git-source entry (commit SHA is the digest — schema §2.2);
  - `auth` on an entry that uses `from` (auth is declared on the named source)
    or on a `content` entry;
  - `apply_once` in any position — a v1 reserved word;
  - an unknown `mode` value on a source: only `strict` and `non_strict` are
    accepted, defaulting to `non_strict` (§3.2's moving-ref rule). Same shape as
    the `on_fetch_failure` enum check, and refused here for the same reason — a
    typo'd mode would otherwise silently take the default and pin nothing;
  - an unknown `${...}` placeholder; only `BOT_ENGINE_TYPE`, `BOT_ENV`,
    `BOT_TENANT` and `BOT_ARCH` are accepted (there is no `BOT_ID` — see §2.9). `BOT_ARCH` resolves to
    the constant `amd64` today (§4, X3): implementing it now rather than merely
    reserving the name means a future mixed fleet changes only where the value
    comes from, with no schema change and nothing for users to rewrite;
  - a `resources.path` that is absolute or contains `../`;
  - a `subpath` that is absolute or contains a `..` segment, in **any** category
    — the source-side path is checked on write, not only when a fetch resolves
    it. `cli_tools` makes this load-bearing: after flattening, `subpath` is what
    selects the file that gets made executable and put on PATH;
  - two `cli_tools` entries with the **same `name`**, or a `name` carrying a path
    separator. Since the category was flattened to one entry = one command = one
    file (schema §3.7), `name` *is* the exposed command name, so duplicates
    cannot both be callable — one would shadow the other and the winner would
    depend on installation order. v1 has no alias field: the collision is
    refused instead. This replaces the withdrawn entrypoint-basename rule, which
    existed only because a single entry could expose several commands;
  - a `resources` entry whose `path` lies under another directory entry's
    `path` (the nesting ban, schema §3.2);
  - an `identity.type` outside the engine's legal set — `VALID_IDENTITY_FILES`
    generally, `CLAUDE.md` only for `claude_code`;
  - an `identity.type` naming a **reserved** file (`MEMORY.md`, `IDENTITY.md`).
    Both are in `VALID_IDENTITY_FILES`, so the check above accepts them, but
    §3.2 guarantees apply never writes or removes them — a document declaring
    one could be accepted and then never converge. Refusing at `PUT` (and so at
    W13's phase-1 preflight) is what keeps "accepted" and "appliable" the same
    set;
  - any limit in schema §5 checkable at write time (document size, per-category
    entry count, inline `content` size).
- [ ] `PUT` is all-or-nothing: a document with one unsupported category is
      refused whole, with a per-entry reason list, and nothing is written.
- [ ] The capability resolver is **one function** used by both the read and the
      write path, so `GET .../capabilities` can never claim support that `PUT`
      then refuses. Unknown engine ⇒ unsupported.
- [ ] That function can also answer from **engine type and bot type alone**, with
      no bot record — W13 validates a manifest in phase 1, before any record
      exists. One function, two entry points; never two implementations.
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
- [ ] **The rule: wherever this item's validator diverges from
      `manifest-schema.zh-CN.md`, the schema doc is amended in the same PR.**
      Unlike the other divergences in §9 — internal planning decisions — the
      schema doc is a **user-facing contract**, so a divergence there means a
      client that follows the published contract gets rejected. The two documents
      cannot disagree. It lands here rather than earlier because the schema doc
      describes an implemented contract, and until this item there is nothing to
      describe.

      **The amendments owed as of iteration 1 have landed** — the schema doc now
      states `BOT_*` variables (§4), `on_fetch_failure` without `skip` (§2), the
      source-level `mode` selector (§2.3), category-level overwrite and what
      `skills: []` versus a `DELETE` means (§1), the `MEMORY.md` / `IDENTITY.md`
      reserved list (§3.5), the first-boot `script` ordering (§3.6), the narrowed
      directory-replacement guarantee (§3.2), the shallow single-ref git fetch
      (§2.2), and a §7 listing the constructs the first phase rejects at `PUT`.
      **The rule stays**: this item's validator is what the schema doc must
      describe, so any further divergence is a schema edit in the same PR, not a
      note somewhere else.

      Stated as a rule because the divergences arrive one at a time and each one
      had so far been noticed only after it shipped.

**Notes.** A `PUT` carrying `script` stores it and does nothing else until W4
materialises it. That is why the feature flag exists.

**Size.** Large. The biggest single item after W4.

---

#### W2 — Guarded fetcher and archive pipeline · #1470

**Owner.** `lucas-xzp` · 0.75 d · day 1 · build (§7)



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
- [ ] **A URL carrying userinfo is refused at fetch time too**, not only at
      `PUT` (W1). Two gates because they catch different things: `PUT` catches
      what a user wrote, this one catches what a redirect or a `${BOT_*}`
      substitution produced. A credential belongs in a header from W3's store,
      never in the URL.
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
- [ ] Permission bits are flattened: nothing unpacked is executable. **This is
      the right default for every category that ships data, and `cli_tools` by
      definition ships programs.** Object storage does not preserve POSIX bits
      anyway, so the original mode is gone before any engine sees the file —
      which is why the executable bit is set by whoever pulls the file down, not
      carried through. Since `cli_tools` was flattened to one file per entry
      (W9), that exception is narrow and needs nothing from this rule: the engine
      marks the one delivered file executable. Do not widen the flattening here.

**Notes.** `src/engine/.../plugins/resource_materialization.py` is the nearest
precedent in the monorepo and is worth reading first, but it lives in the engine
repo — this is new code on the backend side, which has no SSRF guard today.
Place it inside `core/bot_config_manifest/` while it has one consumer; promote
it only if a second appears.

**Size.** Medium.

---

#### W3 — Source credentials · #1471

**Owner.** `lucas-xzp` · 0.75 d · days 1–2 · build (§7)



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
- [ ] **Both sides are canonicalised before that comparison, structurally.** A
      raw `startswith` is not enough: `https://host/team/content/../admin` begins
      with the prefix as a string but resolves outside it, and the client would
      send the secret header to the resolved path. Parse the URL, compare the
      whole **origin** — scheme and host case-insensitively **and the effective
      port**, with the default folded in so `https://host` and `https://host:443`
      are the same origin — then resolve percent-encoding (including encoded
      separators such as `%2F` and `%2E`), collapse dot segments, and **only
      then** compare paths. Applied identically to the initial target and to
      every redirect hop, since a redirect is where a hostile server would put
      it.
- [ ] **The port is part of the origin, not a detail.** `https://host:8443` is a
      different service from `https://host` — very often an internal one on the
      same box — so a credential scoped to the latter must never be attached to
      the former. Comparing scheme and host alone would authorise it.
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

**Owner.** `totalfrank` · 0.75 d · day 2 · design (§7)


**Goal.** A manifest can be applied on demand, converging the bot's entities
toward the document, with a report of what happened — proven on the three
categories that need no fetching.

**In scope.**

- The apply orchestrator: bot-level serialisation, category ordering, per-entry
  outcome classification, `on_fetch_failure` policy handling.
- The **apply record** — the manifest module's own note of what the last apply
  materialised (§2.3), which exists for `keep_last` and audit and stamps no
  marker on the entities themselves. (It used to also serve "un-marking";
  §3.2's move to overwrite removed that job.)
- Apply report storage and `GET .../config-manifest/last-apply`, in the shape of
  design §7.
- `POST .../config-manifest/apply`, including `dry_run=true` returning the plan
  without acting.
- Materialisers for the two no-fetch categories in iteration 1: `mcp` (registry
  reference → the existing **per-bot** activation service,
  `DirectActivationService`, converging the enabled-server set that §3.2 names as
  this category's area) and `script` (→ `BotStartupScriptService`).
  **`mcp[].config` is removed from schema v1** (W4 review; see manifest-schema
  §3.1). It was defined as per-bot configuration "the same shape as the existing
  MCP config API", and those two halves cannot both be true: that API writes
  `ac_user_mcp_config`, keyed `(user_id, server_code)`, and its write calls
  `sync_mcp_detail_to_all_bots` — **fanning out to every bot the owner has**. Its
  payload is also `api_key` and `custom_headers`, which design §4.5 keeps out of
  a manifest. Account-scoped configuration stays on the existing
  `/openapi/v1/bots/mcp/servers/{server_code}/config`.

**Out of scope.** Fetching. Lifecycle triggers (W8) — explicit apply is the only
entry point in this item. **`engine_config` is out of iteration 1** by the X2/T3
decision (§4); when it returns, its materialiser is a top-level key merge via
`EngineConfigService.write_bot_config` and belongs here.

**Depends on.** W1 and W10 (the seam apply calls through). **W11 is no longer a
hard dependency** — the withdrawn three-way diff needed version N's materialised
file list to tell "the declaration dropped it" from "the bot created it";
category overwrite needs no such distinction. W11 is still required for §2.8's
audit and for `keep_last`, but it does not gate this item.
**Blocked by.** — D2 is resolved (§3.2) and its rules are what this item
implements. X2/T3 removed `engine_config` from iteration 1 altogether, so its
teclaw behaviour is no longer this item's problem.

**Done when.**

- [ ] **Convergence:** applying the same unchanged document a second time
      reports every entry `unchanged` and performs no writes.
- [ ] Outcomes are classified per entry as `created` / `updated` / `unchanged` /
      `skipped` / `failed`, and the apply result is `SUCCEEDED` / `PARTIAL` /
      `FAILED` accordingly. `skipped` now means "not written because its category
      was aborted" (§3.2 all-or-nothing) — it no longer comes from an
      `on_fetch_failure: skip` the author asked for, because that value is gone.
- [ ] **Apply is two-phase, not one ordered pass.** The orchestrator's shape has
      to carry this or W13 is forced to bypass it:
      - **Phase A — no container required.** `script` only. It is a plain write
        to `ac_bot_startup_script` (§2.12), and on the creation path it must land
        *before* `_build_create_bot_payload` composes the start command, or the
        first boot carries no script at all.
      - **Phase B — container required.** `identity → resources → skills → mcp`,
        in that order, delivered after the bot is up (§3.4).

      On an already-running bot the two phases run back to back and the split is
      invisible. On the creation path they are separated by the whole of
      container provisioning. This **reverses design §3.4's order**, which put
      `script` last — see §2.12.
- [ ] Two applies against the same bot serialise; the lock follows the existing
      `BotRestartLockRepository` pattern rather than a new mechanism.
- [ ] §2.7 holds: apply does not branch on whether this is a first boot, records
      each entry's delivery outcome individually, and writes nothing to the bot
      record. The aggregate result is derived from the entries, never an input to
      a decision.
- [ ] Apply enforces the same validation and authorisation the public API does
      by calling W10's seam — not a second, hand-written copy of the checks.
- [ ] `engine_ext` is unreachable from the manifest on every path. (The
      `engine_config` category itself is out of the first iteration; when it
      returns it merges by top-level key.)
- [ ] `mcp` refuses a `server_code` the tenant has no permission for, reusing the
      existing permission check rather than a copy.
- [ ] A category present but **empty** (`skills: []`) is a declaration that the
      set is empty: every skill is **removed** (§3.2). This is the reverse of an
      earlier rule that read `[]` as "stop managing without deleting".
- [ ] **`DELETE` of the manifest deletes nothing.** This follows from §3.2 rather
      than being a separate rule: `[]` is a declaration ("the set is empty"),
      absence is not a declaration ("no opinion, do not touch"). Removing the
      document leaves no declared categories, so nothing is overwritten. The two
      behaviours look opposite and are the same rule.
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

**Owner.** `lucas-xzp` · 1.0 d · day 3 · build (§7)


**Goal.** The driving business scenario works: content on a caller's own
service is fetched at apply time and installed as real skills and identity
files.

**In scope.** The two materialisers and `${BOT_*}` substitution in source URLs.

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
- [ ] `${BOT_*}` substitution happens before fetch and before prefix
      authorisation, so a substituted URL cannot escape its credential's
      `allowed_prefixes`.
- [ ] §3.2's overwrite is enforced per declared category: after apply the area
      equals the declaration, and `skills: []` removes every skill. A skill
      installed through the UI is removed too — accepted by decision.
- [ ] **The area is scoped per category** (§3.2), not globally. In particular
      `resources` overwrites only the declared `path` subtrees — a test must pin
      that a file the bot created elsewhere in the workspace survives.
- [ ] **A category is written all-or-nothing.** If any declared entry cannot be
      materialised the category is not overwritten at all. The test that matters:
      declaration `{A, B}` with B's fetch failing leaves B's existing content
      intact — a transient failure must never delete a working entity.
- [ ] `on_fetch_failure` accepts `keep_last` and `fail` only. **`skip` is
      rejected at `PUT`** — under overwrite it would mean "delete this entry",
      the opposite of its name.
- [ ] `MEMORY.md` and `IDENTITY.md` are never written and never removed, whether
      or not the manifest declares them. This is the single exception to
      overwrite and it needs a test of its own.
- [ ] A manifest-installed skill is **registered** through the service (DB row +
      files), never dropped on disk — activation enumerates unregistered
      filesystem content into the pool without creating records (§3.3).
- [ ] A test shows a manifest-installed skill is indistinguishable from the same
      skill uploaded by hand, and survives a skills-pool reconcile.
- [ ] Fetch failure of one entry does **not** abort the other categories, but
      does abort its own (§3.2 all-or-nothing): that category is left exactly as
      it was, the failure is recorded per entry, and the bot still starts.

**Size.** Medium-large.

---

### Wave 3 — completing v1

---

#### W6 — `resources`, files and directories · #1474

**Owner.** `lucas-xzp` · 0.75 d · day 4 · build (§7)


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
- [ ] A directory entry's **convergence unit is the whole archive**, but
      "unchanged" is judged against **what was delivered, not against the source
      alone**. An unchanged archive plus a drifted tree still needs the write:
      if someone added or edited a file under `path` after the last apply,
      reporting `unchanged` from source content alone would skip every write and
      leave that drift in place — directly defeating the ownership rule below
      and §3.2's guarantee that making a manifest effective overwrites the
      declared area. **There is no cheap way out of this, and an earlier revision
      of this bullet claimed there was.** W11's record holds only the bytes we
      *intended* to deliver; drift by definition lives on the bot, so no
      comparison confined to our own record can see it — same-size edits least of
      all. Two honest options:

      1. **Always replace on apply** (recommended for v1) — the atomic
         unpack-and-rename below runs every time, and §3.2's guarantee holds by
         construction. No read of the bot, no drift question.
      2. **Read and hash the deployed subtree** through `DeviceFileSystem` and
         compare it against W11's record, replacing on any difference. A real
         optimisation only if reading the tree is cheaper than rewriting it.

      Take (1) unless apply cost measurably becomes a problem; (2) is where to go
      if it does. What is *not* available is skipping on unchanged source alone.
- [ ] Directory-level ownership: on change, the tree under `path` is replaced
      wholesale — files present before and absent from the new archive are
      removed, including manually added ones. Nothing outside `path` is touched.
- [ ] **Replacement is as atomic as the transport allows, and the transport
      cannot do rename.** `core/devices/services/device_filesystem.py` exposes
      `read_file`, `write_file`, `delete_file`, `delete_tree`, `list_dir` and
      `exists` — there is no rename, move or swap anywhere in `core/devices/`,
      and neither the BaaS nor the teclaw transport offers one. So "unpack to a
      temporary location, then rename" is **not implementable today**, and an
      earlier revision of this bullet promised it anyway.

      What is achievable through the existing contract is `delete_tree` followed
      by per-file writes, which has a real window in which the tree under `path`
      is missing or half-written, and leaves it that way if delivery fails
      mid-way. Two options, and this item must pick one rather than inherit the
      promise:

      1. **Narrow the guarantee** (recommended for v1) — document the window and
         report the entry `failed` when delivery stops mid-tree, so the apply
         report says the tree is in an unknown state. This is already the level
         §3.2 settled at: all-or-nothing is scoped to **materialisation**, and
         v1 explicitly does not roll back a mid-category delivery failure. The
         rename promise contradicted that decision as well as the transport.
      2. **Add an atomic subtree swap to the device contract** — a new
         `DeviceFileSystem` operation plus an implementation in each engine
         transport. Real cross-team work, and it is what a genuine atomicity
         guarantee costs.

      Unpacking to a temporary location on the **platform** side stays either
      way: it keeps a failed fetch or a bad archive from ever reaching the bot,
      which is the half of the guarantee we can keep by ourselves.
- [ ] The nesting ban is enforced at `PUT` (W1) and re-checked at apply.
- [ ] On teclaw the materialised tree expands per-file into `ResourceRef`
      entries; `BotConfigArtifact` is unchanged, and the T5 subtree optimisation
      is **not** taken in v1.
- [ ] The schema §5 limits for archives apply: per-archive size, unpacked size,
      and member count.

**Size.** Medium-large.

---

#### W7 — Named sources and git sources · #1475

**Owner.** `lucas-xzp` · 0.25 d · day 4 · build (§7)

> **✅ Delivered (2026-09-02).** Named sources and git sources are in:
> subprocess git CLI shallow single-ref fetch (W2's refusal semantics and
> caps, containment checks before W11), `from`/`sources` resolution in
> `EntryFetcher.fetch_declared`, the per-apply `SourceSession` (one
> `{git, ref}` fetch reused), `SourceResolution` in the apply report, and
> strict baselines read back from the last apply's report. v1 narrowing:
> git sources take no `digest` (pin by writing the commit SHA as the
> source's ref); `resources` entries still accept URL sources only —
> wiring W6's materialiser to the git road is follow-up work (W6 merged
> before W7 and carries no git consumption).
>
> **🔧 Review fixes (2026-09-02, `fix/w7-review-fixes`).** A whole-diff review
> of #1829 found the delivery above gated off and carried five latent
> defects, all fixed here: the admission rows for `SourceForm.GIT`/`NAMED`
> were never flipped, leaving the entire runtime unreachable through PUT
> (now flipped, with the resources narrowing moved to a per-entry schema
> refusal so it stays precise when the blanket gate opens); strict-mode
> resolutions are now adopted only after the strict gate and read back
> through a bounded report-history walk, so a refusal no longer poisons the
> next baseline once and a failed fetch no longer disarms strict/keep_last;
> git failure text is report-safe (step + exit only — stderr with its URL
> echoes is dropped, matching W2's contract); tree byte caps and the category
> entry cap are enforced on `ls-tree -l` declared sizes **before** checkout
> or any read, and the tree's bytes charge the apply ledger once per `(url,
> ref)`; quotepath-escaped member names are unquoted (non-ASCII filenames
> work; non-UTF-8 names are refused by their quoted form); git receipts
> carry `credential_name` like URL receipts; the subprocess env is read at
> the composition root with ambient `GIT_*` dropped, and credentials ride
> `GIT_CONFIG_*` env (owner-only readable) instead of the ps-visible argv.


**Goal.** One `ref` change resolves a whole configuration to one commit, and
content hosted in the company's git service is a first-class source.

**In scope.** `sources` + `from`; git ref resolution and content retrieval.

**Depends on.** W5.
**Blocked by.** — **X1 is closed** (§4): a dedicated machine account with a
`read_repository` 访问令牌, injected as HTTP Basic, needing no change to the v1
credential model. Nothing external remains.

**What this item builds is host-agnostic.** The contract is **git over HTTPS
with a credential injected as a request header** — the same `header` credential
type W3 already stores, with no dependency on any hosting provider's API. Any
git service reachable over HTTPS satisfies it, and a public-local checkout needs
no company-only service to exercise this code path.

**Scope change forced by X1:** the archive-API pull that design §10.5 specifies
is replaced by a **shallow single-ref git fetch**. That section is superseded.
The reason is recorded in §4 (X1) and is a *deployment* fact rather than a design
dependency: our git host offers no read-only **API** scope, only a read-only
**Git-over-HTTP** one, so the API route would require a read/write-everything
credential in our database. Choosing the git-transport route made the resulting
contract *more* portable, not less — §4's specifics are one deployment's
instance of it, not a requirement of this item.

**Done when.**

- [ ] `sources` declares named sources; `from` references one; `from` and inline
      `source` are mutually exclusive; an unreferenced source is a warning in the
      `PUT` response, not an error.
- [ ] `auth` is declared on the source, not on entries that use `from`.
- [ ] **Atomic *resolution*, not atomic delivery.** Changing one `ref` resolves
      every entry referencing that source to the **same commit** within a single
      apply, and that SHA is fetched once and reused. This is the guarantee, and
      it is deliberately narrower than "no half-upgraded state": §2.7 requires
      one category's failure never to withhold another's, so `identity` can land
      while `skills` aborts, leaving categories at different versions. Delivery
      atomicity across categories would need apply-wide staging and rollback,
      which v1 does not have. Per-category all-or-nothing (§3.2) is the level at
      which delivery *is* atomic.
- [ ] A git `ref` is resolved to a commit SHA at each apply point; the apply
      report records both the declared `ref` and the resolved SHA.
- [ ] The same `{git, ref}` is fetched **once** per apply and reused across
      every entry referencing it.
- [ ] Git content is retrieved over HTTPS with a **shallow, single-ref** fetch
      (superseding design §10.5's archive-API approach — see §4, X1), reusing
      W2's guarded transport and its size, timeout and concurrency caps.
- [ ] The fetch is read-only and never executes repository-supplied hooks or
      filters.
- [ ] **W2's containment guards apply to git content too.** A git source is not
      unpacked, so it bypasses the archive-member checks by construction — yet a
      repository can contain exactly the same hazards: a symlink such as
      `payload -> /etc`, a gitlink/submodule entry, or a device/special entry. A
      materialiser traversing the requested subtree would read outside the
      checkout or deliver an escaping link to the bot. Apply the same canonical
      containment and special-entry rejection **before git content enters W11's
      store or any delivery service**.
- [ ] **The expanded checkout is bounded, not just the transfer.** W2's streaming
      byte cap governs bytes on the wire; a small pack can expand into an
      enormous tree, and `--depth` bounds *history*, not the selected commit's
      blobs or trees. Enforce caps on expanded total bytes, object/file count and
      individual file size, and **clean up the temporary checkout on failure** —
      otherwise an authenticated manifest author can exhaust backend disk.
- [ ] A re-pointed tag converges to the new content at the next apply — moving a
      tag changes what the declaration means.
- [ ] Directory entries from a git source need no `unpack` or
      `strip_components`.
- [ ] **The source schema carries the moving-ref `mode`** (§3.2): `strict` or
      `non_strict`, declared on the source because that is what holds the `ref`,
      defaulting to `non_strict`. Declaring it on a SHA ref is accepted and inert
      — a SHA cannot move, so neither branch can fire.
- [ ] **Strict mode is enforced where the ref resolves**: when the resolved SHA
      differs from the one recorded at the last apply, the entry **fails** and
      the bot keeps running what it has. Non-strict applies the new content and
      records a warning on the entry in the apply report, naming the previous and
      new SHA — the report is the only surface for it, since iteration 1 is
      pull-only (§2.7). W8's *"whatever D2 decides about moving refs is enforced
      here"* is apply-time enforcement and depends on this field existing.

**Size.** Medium-large.

---

#### W8 — Lifecycle apply points · #1476

**Owner.** `totalfrank` · 0.25 d · day 4 · design (§7)


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
window. (§2.7's readiness gate is withdrawn — a failed apply is recorded at the
manifest level, so there is no de-activation for W8 to place.)

**Done when.**

- [ ] On teclaw, the **first** artifact already contains the manifest result.
- [ ] **teclaw creation belongs to this item, including lifting W13's refusal.**
      W13 shipped **ARCA-only** (PR #1791): its pre/post-container split exists
      because `_build_create_bot_payload` reads the startup-script row while
      composing a start command, and teclaw has no analogue — it composes a config
      artifact at provision time, which is this item's first criterion above. So
      W13's creation endpoint refuses a teclaw engine, and **this item owns
      removing that refusal** and giving the creation job a teclaw-shaped
      sequence, alongside the artifact work. The endpoint, the job and the poll
      all exist by then; what is missing is the engine gate and a different step
      order. Without this bullet the piece falls between the two items, because
      the criterion below assumes W13 covered creation for both engine families.
      *Worth checking early:* the teclaw artifact is produced as a snapshot of
      platform state, so materialising a manifest into platform state **before**
      `provision()` would make the first artifact carry it with no artifact-side
      work — but today's `ON_CONTAINER` materialisers resolve a device and raise if
      unbound, so that may land on W5/W6's materialiser design rather than here.
- [ ] Bots created through W13 — **on ARCA** — get their manifest applied inside
      creation, so
      this item covers the *other* apply points — republish and rebuild-restart —
      plus making a `PUT` effective per §2.6.
- [ ] **The ACTIVE-but-unconfigured window is accepted, not gated.** An earlier
      version of this criterion required post-start delivery to complete before
      the bot takes traffic. That is withdrawn: §3.4 accepts the window for
      iteration 1 and §2.7 withdrew the readiness gate, so requiring a traffic
      gate here would contradict both — and no ready/serving state separate from
      `ACTIVE` exists to hang one on (`bot_chat` has no bot-status routing gate).
      What replaces it is **narrower than an earlier revision claimed**, and the
      difference is per apply point:
      - **Creation (W13)** — the caller polls, so `APPLYING` is a state they
        wait through and the window is genuinely observable. That is W13's
        mechanism, not this item's.
      - **Republish and rebuild-restart** — this item's actual apply points — have
        **no poll loop at all**. So for iteration 1 the window is **not
        observable in real time on these paths**; what exists is
        `GET .../config-manifest/last-apply` after the fact, which is consistent
        with §2.7's pull-only decision. Say this rather than imply a state that
        only creation has.

      #1508 closes the window properly in iteration 2 by delivering before
      start, on every path at once.
- [ ] Scale-out does **not** re-apply; instances stay identical because they
      share one platform state. This is #926's actual requirement.
- [ ] A manifest `PUT` **takes effect immediately and without a restart**
      (§2.6), so the running bot always reflects the manifest last accepted.
      - **BaaS / ARCA** — `identity` and `resources` are `DeviceFileSystem`
        writes; `skills` go into the active skill set and are reconciled by the
        existing full symlink sync, reachable on a live bot through
        `DeviceSyncDispatcher` / `sync_symlinks`.
      - **teclaw** — per-file writes through `TeclawDeviceFileSystem`, which
        forwards straight to the engine. `TeclawDeviceSyncPlugin.sync_symlinks([])`
        (the one-call whole-artifact redeliver `ChannelService` already uses)
        stays available; **establish which categories actually need it** rather
        than reaching for it by default.
      - **Never `BotService.restart_bot` on teclaw** — it raises and would
        strand the bot (§2.6). Nothing here needs it on either family.
- [ ] **`script` is delivered now and effective at the next start**, and the
      response says so. It is baked into the start command at payload-build time
      (§2.12), so it is the one category whose effect is deferred — consistent
      with §2.7's delivery-not-execution boundary, not an exception to it.
- [x] **The legacy `/startup-script` endpoints are untouched** (§2.2, as
      revised in review). They read and write their own row and inject nothing
      from the manifest layer; a manifest that declares `script` materialises
      into that row on apply. Their existing tests pass unedited.
- [ ] **§2.7 holds on both engine families: apply writes nothing to the bot
      record, and does not branch on first boot.** Apply's record ends at
      delivery — the `ac_bot_startup_script` row written, the artifact handed
      over, the per-file write landed. What the container's start command or the
      engine then does with it is a different layer and is not apply's outcome.
- [ ] **The per-entry records are readable on demand.** Iteration 1 is
      pull-only by decision (§2.7) — no notification. `last-apply` and W13's
      creation poll must return enough per-entry detail that someone asking "did
      my manifest apply" gets a complete answer without further digging.
- [ ] Whatever D2 decides about moving refs is enforced here — this is where
      restarts nobody associated with a config change actually happen.
- [ ] `script` is materialised by writing `ac_bot_startup_script` and nothing
      else; the platform composes it into the start command as it already does
      (§2.12). A rewritten row is picked up by the next payload build, which is
      what §2.6's BaaS-family verb triggers. teclaw does not support `script` at
      all, so this criterion has no teclaw arm.
- [ ] **Iteration 1 asserts the opposite of design §3.4**: on a first boot the
      script runs *before* the manifest's other categories, because it is baked
      into the start command while they are delivered post-start. A test pins
      this, and the docs state the rule — a manifest's `script` may not depend on
      anything that manifest declares. Both are deleted by #1508 in iteration 2.
- [ ] The no-script start command remains byte-identical to today (design §10.4),
      with #935's existing assertion retained.

**Size.** Large, and the riskiest — it touches `create_flow`, `publish_flow` and
`TeclawProvisionService`.

---

**Progress (landed, PR inclusionAI/Avernet#1836).** Spec revision 3
(`specs/2026-09-02-manifest-lifecycle-apply-points/`) narrowed the item to
`PUT` and teclaw creation and added the delivery seam; review (revision 5)
made ownership follow the operation and withdrew the startup-script alias.
Per criterion:

- **The first teclaw artifact carries the manifest** — yes, on the
  platform-managed path: the creation job records the bot, runs the single
  pre-container phase against the record (every construct writes platform
  state — the store-backed ports write bytes to the bot-data store under a
  key layout that is the record, no index table; activation records without
  projecting), then provisions; the composer lists the store and emits the
  `ownership` map (engine contract §9). **Ownership follows the operation**:
  a manifest apply's redeliver and a manifest bot's first artifact are the
  platform's for every category; a skill upload, a resource upload, an MCP
  edit or a publish build is the engine's for every category (`mcp` always
  the platform's).
- **teclaw creation, the refusal lifted** — done; the poll walks
  `AWAITING_AUTHORIZATION → CREATING → APPLYING → CREATING → READY`.
- **`PUT` takes effect** — done: `PUT` stores, then starts an apply under
  trigger `put`; the response's `apply` field reports it; the not-ACTIVE and
  script notes ride in `warnings`. No restart on either family (pinned).
- **The alias view** — withdrawn in review: the legacy `/startup-script`
  routes are byte-for-byte what they were before W8.
- **The seam** — `DeliveryStrategy` (`apply/delivery.py`): `ArcaDelivery` is
  today's behaviour; `TeclawDelivery` runs every construct pre-container over
  store-backed ports with one closing whole-artifact redeliver when the switch
  is on, and the pre-W8 per-file shape when it is off.
- **The switch** — `user_config.bot_config_manifest.teclaw_platform_managed`,
  default **off**; flip once the teclaw engine implements `ownership`
  (R-O1/R-O2/R-O3), after explicitly applying each existing teclaw bot's
  manifest so the store is populated.
- **Deferred** (spec D-1 and *Follow-ups*): restart and republish as apply
  points; the publish gather for platform-managed teclaw files; a health
  surface for a failed closing redeliver beyond the report's `notes`; an ARCA
  pre-binding port.

#### W9 — `cli_tools` — deferred · #1477

**Goal.** Command-line tools the model can invoke, installed declaratively.

**Status.** Schema is settled (§3.7); delivery is deferred by business priority
in the design itself. Not scheduled.

> **Progress: the artifact shape landed early, nothing else has started** (PR
> #1734, the same PR as W12 — the schema was aligned there while delivering
> W12, a **deliberate exception** rather than a new precedent against §8's
> one-PR-per-item rule). Whoever picks this up, start here:
>
> | | State |
> | --- | --- |
> | `cliToolRef` + optional `cli_tools` in `artifact.schema.json` | ✅ merged |
> | `CliToolRef` in `artifact.py`; `to_dict` omits the key when undeclared, `from_dict` never reads an absence as `[]` | ✅ merged, pinned by tests |
> | The three "artifact schema unchanged" statements in `README.zh-CN.md` | ✅ reconciled |
> | `SCHEMA_VERSION` 4 → 5 | ➖ **decided against** (2026-08-31) — `cli_tools` rides into v4, compatibility via ignore-unknown-fields |
> | Manifest-side `cli_tools` (§3.7): storage, validation, materialiser | ❌ not started |
> | Fetch / `sha256` enforcement / unpack / select `subpath` / compute `md5` / write to store | ❌ not started |
> | ELF header check, `${BOT_ARCH}` → `amd64` | ❌ not started |
> | ARCA-side PATH proposal + the usage skill in the default skill set | ❌ not started |
> | `bcs-cli` adopted as the first consumer | ❌ not started |
>
> **`SCHEMA_VERSION` is not being bumped — that is settled, not pending.**
> Decided with the teclaw owner on 2026-08-31: `cli_tools` rides into v4
> artifacts as a new field and compatibility rests on the engine-side
> "ignore unknown fields rather than reject" rule
> (`engine-convergence-contract.zh-CN.md` A5, agreed). **The cost, stated:
> `schema_version` no longer tracks this contract's evolution** — to know
> whether an artifact carries `cli_tools`, probe for the key, never the version.
> A test in `tests/community/kernel/test_bot_config_artifact.py` guards against
> it drifting upward.
>
> **Note that issue #1477's body still describes the pre-flattening shape**
> (`entrypoints`, "the engine receives an unpacked directory"). This document
> and `teclaw-cli-contract.zh-CN.md` are authoritative.

**Depends on.** W8. (The part that landed is the exception: it only declares a
shape and produces no content, so it does not depend on W8.)

**The artifact contract genuinely changes, and `artifact.schema.json` is part of
it.** `kernel/bot_config/artifact.py` and its language-neutral
`artifact.schema.json` are the published contract; that schema set top-level
`"additionalProperties": false`, so a `cli_tools` field was **rejected** until
the schema file was amended. **That part is done** (see the progress table
above), along with reconciling the "artifact schema unchanged" statements in
`README.zh-CN.md` and §9 — true for every other category, no longer true for
this one. **`SCHEMA_VERSION` stays put**: see "not being bumped — that is
settled, not pending" above.

> **`cli_tools` is currently off the wire.** Nothing populates it, so `to_dict`
> omits the key and today's artifacts are byte-identical to those built before
> the field existed. **This is transitional, not a semantic**: an artifact is a
> full snapshot of platform state, so once the composer fills the field it is
> always present and always complete like every other category, and `[]` simply
> means the bot has no platform-delivered tools.

**The teclaw half is written and ready to hand over.**
`teclaw-cli-contract.zh-CN.md` is the engine-facing specification: the delivery
contract is unchanged, `cli_tools` is the only addition, and the platform does
the fetch, digest check, unpack and file selection so the engine receives **one
executable file per entry** (`{name, store, path, md5, version}`). What teclaw
implements is placement, the `md5` check, the executable bit, PATH, and the same
full-overwrite semantics every other category already has. It carries six worked
use cases and an acceptance checklist. **`schema_version` is not bumped**
(decided 2026-08-31).

**Blocked by.** — **X3 is closed** (§4): the ARCA fleet is `linux/amd64`, so a
single URL per tool suffices. teclaw needs only an artifact protocol from us; the
ARCA PATH proposal and the default-skill-set skill are ours to design. The §4
investigation confirmed there is **no existing CLI mechanism to duplicate** —
every `bcs-cli` reference is singlebox orchestration, and no delivery path
handles an executable bit. This item stays deferred by business priority, not by
a missing answer.

**The category is flattened: one entry = one command = one file.** `entrypoints`
is gone from both the manifest schema (§3.7) and the artifact contract. An entry
names one executable file; an archive is a *transport form* from which `subpath`
selects that one file, and the rest of the package is **not delivered**. The
artifact entry is `{name, store, path → the file, md5, version}` and the exposed
command name is `name`.

**This closes the private-executable-helper question rather than deferring it,
and the cost is explicit.** The withdrawn shape — a directory plus a list of
entrypoints — left no way to say *"this file must be executable but must not
become a command"*, so a packaged CLI whose `bin/tk` execs a bundled
`libexec/helper` would fail at runtime with `EACCES` and could not be fixed from
the manifest. The candidate answers were a second non-PATH `executables` list, or
carrying POSIX mode through the platform's fetch/unpack/store path (which nothing
does today, and which re-opens trusting an archive's own bits). Flattening
removes the shape that produced the question: **v1 ships self-contained single
executables**, so tools needing bundled helpers or a sibling `lib/` are out of
scope and must be built as static binaries. Say that in the schema, the manual
and the teclaw contract — a limitation stated is a scoping decision; the same
limitation unstated is a bug report from the first user who packages a wrapper.

What flattening also deletes, and what should not be reintroduced by accident:
the containment rules that existed **only** to constrain entrypoint values
(in-package traversal, symlink escape, basename collisions across the bot's
tools). Uniqueness is now `name` uniqueness (W1).

**Done when (sketch).** **The content-dependent `subpath` checks live here, not
in W1** — once an archive is unpacked, the selected `subpath` must exist, be a
**regular file**, and still resolve inside the unpacked tree after symlink
resolution. W1 rejects only the syntactic half (absolute paths, `..` segments,
duplicate `name`s) because it does no fetching and has no tree to inspect at
`PUT`. Then: the platform computes the **`md5`** of the finally selected file and
carries it in the artifact entry, so the engine can verify the bytes it pulls
from the store (it is our own hash, never the store's ETag — a multipart ETag is
not a content MD5). Then: targets `linux/amd64` with a single URL per tool (§4,
X3), while `${BOT_ARCH}` resolves to `amd64` in W1's whitelist and a fetched binary's
ELF header is validated — so a wrong-architecture binary fails in the apply report
rather than as an `exec format error` the model meets mid-task. Then: `digest`
mandatory, and the convergence key is the **whole delivery-relevant
declaration — `digest` *and* `subpath`** — not the digest alone. The same package
with `subpath` changed from `bin/old` to `bin/new` is a real change: keying on
digest would report `unchanged`, deliver nothing, and leave the old file exposed
while the declaration names the new one;
static binary and archive-plus-`subpath` forms only; a platform-defined logical tool directory
on the agent process's PATH, with users never seeing a physical path; a
tools-usage skill in the engine-aware **default skill set** (`SkillSetService`
already has one) so the model knows the tools exist and how to call them; and an
artifact representation for teclaw, whose in-container placement is theirs.

**Explicit goal: `bcs-cli` becomes the first consumer**, replacing the singlebox
script that hand-places it. Otherwise this ships alongside the one CLI mechanism
that already exists and we maintain both — and folding it in gives the feature a
real in-house test case before any customer uses it.

**Size.** Medium.

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
                                                         ├─► W7 ──┘
                                                         └─► W13
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

**§3.2's revision to overwrite loosened the plan's tightest dependency.** W11 was
a hard dependency of W4 while the three-way diff needed version N's file list;
category overwrite does not, so W11 and W4 are now independent. W11 is still
required — §2.8's audit and `keep_last` both need it — but it no longer gates the
apply engine.

**W12 still gates W8's teclaw arm**, and is still the only item whose critical
path runs through another team — but it is now mostly a statement of what we do
plus one confirmation (does teclaw's re-delivery overwrite, and is it
convergent?), rather than asking them to implement a preservation rule we cannot
verify.

**W13 is what actually delivers "a bot comes up configured on its very first
boot."** W8 covers every *other* apply point; W13 covers creation, which is the
one the business asked for. It needs W1 and W4 and nothing external. **One scope
qualifier (PR #1791): W13 covers ARCA creation only; teclaw creation goes through
artifact assembly and belongs to W8.**

**Why lifecycle wiring is last.** Explicit `POST .../apply` exercises the whole
engine from W4 onward, so W8 touches the create and publish flows only after the
thing it triggers is proven. The trade is stated plainly: **nothing before W8
delivers the business ask.** W4's explicit apply is a validation vehicle, not the
product.

## 7. Assignment — all 13 items, split by the nature of the work

**Team:** `totalfrank` (3 days) · `lucas-xzp` (4 days). **All thirteen items are
allocated and all must land inside the seven days.**

### The split rule: `totalfrank` owns the design

`totalfrank` is the author of this plan's design, so **every item whose
deliverable is a contract belongs there** — a schema, a public API, an
orchestrator's shape, an engine-facing agreement, a lifecycle hook point.
`lucas-xzp` takes **every item whose deliverable is machinery built against a
contract that is already settled.**

| | `totalfrank` — the design | `lucas-xzp` — the machinery |
| --- | --- | --- |
| | **W1** manifest schema v1, the public API surface, the capability model | **W2** the guarded fetcher and unpacker |
| | **W4** the apply orchestrator's shape, the apply record, the report | **W3** credential storage, encryption, prefix authorisation |
| | **W10** the seam pattern both entry points consume | **W11** the content store and its addressing |
| | **W12** the engine-facing semantics contract | **W5** the `skills` + `identity` materialisers |
| | **W13** the creation API and its poll states | **W6** the `resources` materialiser |
| | **W8** where apply hooks into the lifecycle | **W7** named sources and the git fetch |
| | **W9** the `cli_tools` artifact protocol and the ARCA PATH proposal | |

### What this costs, and why it is worth paying

The previous revision put the whole critical chain
(**W1 → W4 → W5 → W6 → W8 → W9**, six links, strictly sequential) inside one
person's continuous thread, precisely to avoid handovers. Splitting by nature of
work breaks that thread in two places:

- **W4 → W5** — `totalfrank` finishes the apply engine, `lucas-xzp` builds the
  first materialisers on top of it (end of day 2);
- **W6 → W8** — `lucas-xzp` finishes `resources`, `totalfrank` wires apply into
  the lifecycle (day 4).

Two handovers on the critical chain is the price of keeping design with its
author, and it is the right price: a contract designed by one person and
implemented by another is the normal shape, whereas a contract *invented* by
whoever happened to have hours free is not.

The handover points are named here so they are prepared for rather than
discovered — each is a moment where the receiving developer needs the interface,
not the reasoning.

### `totalfrank` — 3 person-days, spread across the four calendar days

| Day | Item | | Budget | Deliverable is a contract for |
| --- | --- | --- | --- | --- |
| 1 | **W10** service-layer seam | #1509 | 0.25 | W4 — apply and the API share one verdict |
| 1 | **W1** manifest document | #1469 | 0.75 | Everything — schema, API, capability |
| 2 | **W4** apply engine | #1472 | 0.75 | W5, W6, W7, W13 — the orchestrator they plug into |
| 2 | **W12** semantics contract | #1684 | 0.25 | teclaw — and W8's teclaw arm |
| 3 | **W13** create a bot from a manifest | #1696 | 0.5 | The business ask |
| 4 | **W8** lifecycle apply points | #1476 | 0.25 | The other apply points |
| 4 | **W9** `cli_tools` | #1477 | 0.25 | teclaw's artifact protocol; ARCA PATH |

**Total 3.0**, deliberately spread over four calendar days rather than three
consecutive ones. The design is **front-loaded** — W1 and W4 must land on days 1
and 2 or `lucas-xzp` has nothing to build against — and the late items (W8, W9)
are gated behind work that only completes on day 4.

### `lucas-xzp` — 4 person-days, days 1–4

| Day | Item | | Budget | Built against |
| --- | --- | --- | --- | --- |
| 1 | **W2** guarded fetcher | #1470 | 0.75 | — settled security rules |
| 1–2 | **W3** source credentials | #1471 | 0.75 | — settled credential model (§4, X1) |
| 2 | **W11** platform-side materialisation | #1510 | 0.5 | W2's output shape |
| 3 | **W5** `skills` + `identity` | #1473 | 1.0 | W4 (handover, end of day 2) |
| 4 | **W6** `resources` | #1474 | 0.75 | W5 |
| 4 | **W7** named + git sources | #1475 | 0.25 | W5 |

**Total 4.0.** Days 1–2 are entirely dependency-free, so the wait for W4 costs
nothing.

### The four-day calendar

```text
        │ day 1          │ day 2            │ day 3      │ day 4
────────┼────────────────┼──────────────────┼────────────┼─────────────────────
frank   │ W10 · W1       │ W4 ──────┐ · W12 │ W13        │ W8 · W9
 design │                │          │       │            │  ▲
        │                │          ▼       │            │  │
lucas   │ W2 · W3────────┼──► W3 · W11      │ W5 ────────┼──┴ W6 · W7
 build  │                │                  │            │
────────┴────────────────┴──────────────────┴────────────┴─────────────────────
 handover 1: W4 → W5, end of day 2      handover 2: W6 → W8, within day 4
```

**Day 4 is the tightest point in the plan**: `lucas-xzp`'s W6 must land before
`totalfrank` starts W8, and W9 follows W8 — three items serialised inside one
day, across two people. If any day slips, this is the one that breaks.

### What the numbers are

They are **budgets, not estimates.** An earlier revision estimated the plan at
roughly 24 person-days from each item's own **Size** line; the budget is 7, so
every item is allocated a quarter to a full day against earlier figures of 1.5–3
days. That is a ~3.4× compression, recorded once here rather than hidden inside
the tables: **what each item actually delivers will be narrower than its
acceptance criteria describe.** The criteria stay as written because they define
*done*, and knowing which of them a round did not reach is more useful than
quietly deleting them.

### What to cut first if the seven days do not hold

Stated in advance so the decision is not made under pressure at the end:

1. **W9** (`cli_tools`) — deferred by product priority in the design itself, and
   its teclaw half is already written and hand-over-ready.
2. **W7** (named + git sources) — v1 works with URL sources alone.
3. **W6** (`resources`) — `skills` + `identity` are the driving scenario (§2.4).

**W1, W4, W5, W8 and W13 must not be cut**: without them there is no manifest, no
apply, no delivery, and no way to create a bot from a manifest — which is the
business ask. Note that cutting W6 also relieves day 4's handover, so it buys
more schedule than its budget suggests.

## 8. Conventions for each work item

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

## 9. Traceability

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
| W10 | no design section — arises from §2.10, an implementation constraint the design does not cover |
| W11 | no design section — arises from §2.8, a requirement added after #1031 |
| W12 | design §3.3 (convergence) and engine-requirements T2, turned into a two-sided contract |
| W13 | design §3.1's create apply point — reachable only through the creation API this item adds; no design section describes that API |

Design decisions this document does **not** re-open: the manifest/script split
(design §2.1), route B (design §2.3), the four rejected alternatives (design
§2.4), platform-side fetch (design §4.1), and the `BotConfigArtifact` schema
(design §5.2 — unchanged, though D4 notes that the backend's teclaw compose
branch is not).

Points where this document **diverges** from the merged design, each argued in
place: §2.3 (the managed marker shrinks to an internal record), §2.5 (capability
scope; desktop out), §2.6 (`PUT` takes effect immediately rather than being lazy —
design §3.1), §2.8 (platform-side materialisation, which the design does not have), §2.9 (substitution
variables renamed from `OCB_*` to `BOT_*`, since `OCB` is an internal codename),
§3.4 (post-start delivery on the BaaS
family, where design §3.1 requires configuration to precede readiness), and §4's
X1 (a shallow git fetch rather than design §10.5's archive-API pull, forced by
Ant Code having no read-only API scope). Amending the Chinese docs to match is a
separate change, deliberately not made here.
