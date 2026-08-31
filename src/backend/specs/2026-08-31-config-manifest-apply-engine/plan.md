# Plan: the Manifest Apply Engine

## Approach

Backend-only. One new subpackage inside the module that already owns the
document, two new tables, one new Service API contract, one new router file, and
three rows each in `AUTHORIZATION` and `ADMISSION`.

The shape is three ideas, and every acceptance criterion falls out of one of
them:

1. **An ordering table separate from a materialiser registry.** The table names
   all six constructs, their phase and their order — that is the *ordering
   contract*, complete from day one. The registry maps a construct to the code
   that materialises it, and W4 fills in two entries. Walking a complete table
   and finding a sparse registry is what makes "no materialiser yet" an ordinary
   state instead of a branch naming three categories by hand.

2. **Every materialiser is resolve → plan → write, in three separate calls.**
   The split is not decomposition for its own sake; each stage exists because a
   criterion needs a boundary exactly there:
   - `resolve` turns declared entries into materialisable intents. Anything that
     can fail *before* touching the bot fails here — and a failure here aborts
     the category, which is all-or-nothing. W5's fetch lands in this stage and
     nowhere else, which is why the transient-failure criterion is satisfied for
     W5 by construction rather than by W5 remembering it.
   - `plan` reads current state and classifies each intent `created` /
     `updated` / `unchanged`, plus the removals overwrite implies. Read-only.
     `dry_run` is "stop here", so it cannot write by accident — it is missing a
     call, not disciplined about one.
   - `write` executes the plan. It is reached only when `resolve` produced no
     failures. An all-`unchanged` plan with no removals makes it a no-op, which
     is how convergence is proved by *absence of calls* rather than by equal
     output.

3. **The orchestrator does the category-level work exactly once, for every
   category.** Serialization, ordering, phase selection, the abort rule, the
   `skipped` cascade, the outcome tally and the record write all live in the
   orchestrator. A materialiser knows nothing about them. That is the whole
   value W5, W6 and W13 get from this item.

## Module Layout

**New: `core/bot_config_manifest/apply/`** — inside the existing module, because
§2.3 makes the apply record *the manifest module's own* record of what it
materialised, and because the orchestrator's inputs are the parsed document and
the capability resolver, both of which live here already.

| File | Holds |
| --- | --- |
| `apply/__init__.py` | Docstring only. Same reason `bot_config_surface/__init__.py` is empty: `order` is a leaf and `registry` imports the materialisers, so re-exporting either here closes a cycle |
| `apply/outcomes.py` | `EntryOutcome`, `ApplyStatus`, `ApplyPhase`, `EntryResult`, `CategoryResult`, `ApplyReport`. Leaf — imports nothing from the module |
| `apply/order.py` | `APPLY_ORDER`: all six constructs, phase and position. The ordering contract |
| `apply/registry.py` | The `Materialiser` protocol and `MATERIALISERS`, the sparse map. Imports the two materialisers |
| `apply/context.py` | `ApplyContext` — the resolved identity and coordinates one apply runs under |
| `apply/orchestrator.py` | `ApplyOrchestrator`. The only file that knows about aborting, skipping, tallying and recording |
| `apply/materialisers/script.py` | `ScriptMaterialiser` → `BotStartupScriptService` |
| `apply/materialisers/mcp.py` | `McpMaterialiser` → `DirectActivationService` |

**New: persistence**

- `core/bot_config_manifest/repository/apply_models.py` — two ORM models plus
  their records.
- `core/bot_config_manifest/sql/2026_08_31_bot_config_manifest_apply.sql` — DDL
  for both, with the same commented reasoning `2026_08_31_bot_config_manifest.sql`
  carries (index budget, tenancy, why the key is the key).
- `core/repository/protocols/bot/config_manifest_apply.py` — two protocols.
- `core/repository/implementations/bot/config_manifest_apply.py` — one file, both
  implementations.
- `core/schema.py` — the side-effect import so `create_all` emits the tables.

**New: the Service API**

- `core/bot_config_manifest/bot_config_manifest_apply_service_protocol.py`
- `core/bot_config_manifest/services/config_manifest_apply_service.py`
- `api/bot_config_manifest_apply_service.py` — re-export, matching the existing
  manifest contract's shape exactly.

A **second** contract rather than more methods on
`BotConfigManifestServiceProtocol`. Rule 9: that contract's reason to change is
"what a document may be"; this one's is "what applying does". They also have
different bars, different callers (W13 calls this one, and calls it in two
pieces) and different dependency graphs — the document service reaches a
repository and the capability resolver, this one reaches five bot-configuration
services.

**New: the adapter**

- `adapters/http/openapi_v1/bots/config_manifest_apply.py` — the three routes
  (start, poll by id, last).
  Its own file rather than appended to `config_manifest.py`: that file is at 200
  lines and `test_no_oversized_modules.py` exists, but the real reason is that
  the two groups carry different bars and putting them in one file invites
  someone to give a new route the neighbour's row.
- `bots/schemas.py` — `ConfigManifestApplyAccepted` (the 202 body: just the
  `apply_id` and `RUNNING`), `ConfigManifestApplyReport`,
  `ConfigManifestApplyEntry`.
- `openapi_v1/__init__.py` — import and mount, beside `config_manifest_router`.
- `authorization.py`, `admission.py` — three rows each.
- `responses.py` — the new domain errors in the status map and the biz-code map.

**Changed: the `mcp` entry narrows (spec *Decisions* 11)**

- `schema/entries.py` — `CATEGORY_ENTRY_KEYS[MCP]` becomes `{"server_code"}`;
  `validate_mcp_entry` drops its `config` branch. `config` is then refused by the
  existing `unknown_field` path, exactly as retired `entrypoints` is, and pinned
  by a named test.
- `docs/bot-config-manifest/manifest-schema.zh-CN.md` §3.1 — rewritten in the
  same change, per Rule 16. The English and Chinese work-items notes on `mcp`
  follow.
- `core/bot_config_manifest/README.md` — the "known gaps" list gains the
  account-scoped-config finding and its reasoning.

## The Outcome Vocabulary

Every later section speaks these. A leaf module — it imports nothing else from
the feature, which is what lets the materialisers, the orchestrator and the
adapter all depend on it without a cycle.

```python
# apply/outcomes.py

class EntryOutcome(StrEnum):
    """What happened to one **declared** entry.

    ``SKIPPED`` means "not written because its category was aborted" — it no
    longer means "the author allowed this to be missing", because the
    ``on_fetch_failure: skip`` that meant that is gone (§3.2 overwrite).
    """

    CREATED = "created"
    UPDATED = "updated"
    UNCHANGED = "unchanged"
    SKIPPED = "skipped"
    FAILED = "failed"


class ApplyStatus(StrEnum):
    """The report's own state. ``RUNNING`` until the work finishes.

    The three terminal values are **derived** from the entry outcomes and read
    by humans and by W13's poller. Nothing in this module branches on one.
    """

    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


@dataclass(frozen=True)
class EntryResult:
    """One declared entry's outcome."""

    construct: ManifestCategory | ManifestSection
    #: The entry's identity within its category — ``name`` / ``type`` / ``path``
    #: / ``server_code``. Whatever the category keys entries by.
    identity: str
    outcome: EntryOutcome
    #: Why, when the outcome is ``FAILED`` or ``SKIPPED``. Never a credential
    #: value, and never raw exception text that might carry one.
    reason: str | None = None


@dataclass(frozen=True)
class CategoryResult:
    """One category's entries, plus what overwrite removed.

    ``removals`` is its own field rather than a sixth ``EntryOutcome``: the five
    outcomes classify *declared* entries, and a removal has none — ``skills: []``
    deletes everything while declaring nothing. Folding it into the enum would
    either invent a value the acceptance criteria do not list, or leave the
    destructive half of overwrite unaudited.
    """

    construct: ManifestCategory | ManifestSection
    entries: tuple[EntryResult, ...]
    removals: tuple[str, ...] = ()
    #: True when the category was not written at all (all-or-nothing, §3.2).
    aborted: bool = False


@dataclass(frozen=True)
class ApplyReport:
    """Design §7's shape. Apply's only output (§2.7)."""

    apply_id: str
    bot_id: str
    trigger: str                      # explicit | create | republish | restart
    status: ApplyStatus
    started_at: datetime
    finished_at: datetime | None
    categories: tuple[CategoryResult, ...]
    #: Resolved named sources — declared ``ref`` plus resolved SHA. Empty in
    #: this wave (nothing is fetched); W5 fills it. Credential **names** only.
    sources: tuple[SourceResolution, ...] = ()

    def as_payload(self) -> dict[str, Any]:
        """The wire shape, and the one place it is defined.

        Serialises named fields only — never a passthrough of a declared entry —
        so a credential cannot reach the report by being carried along in a dict
        nobody inspected.
        """
```

## The Ordering Table and the Registry

```python
# apply/order.py

class ApplyPhase(StrEnum):
    PRE_CONTAINER = "pre_container"   # Phase A — no container required
    ON_CONTAINER = "on_container"     # Phase B — container required


@dataclass(frozen=True)
class ApplyStep:
    construct: ManifestCategory | ManifestSection
    phase: ApplyPhase
    position: int


#: Complete, and complete is the point: this is the ordering contract, not a
#: list of what is built. `script` is first and alone in phase A because it is a
#: plain row write needing no container, and on the creation path it must land
#: before `_build_create_bot_payload` composes the start command or the first
#: boot carries no script (§2.12). Everything in phase B resolves a
#: DeviceFileSystem or a device context and raises if unbound (§3.4).
#:
#: This REVERSES design §3.4, which put `script` last. See work-items §2.12.
APPLY_ORDER: tuple[ApplyStep, ...] = (
    ApplyStep(ManifestSection.SCRIPT,           ApplyPhase.PRE_CONTAINER, 0),
    ApplyStep(ManifestCategory.IDENTITY,        ApplyPhase.ON_CONTAINER,  1),
    ApplyStep(ManifestCategory.RESOURCES,       ApplyPhase.ON_CONTAINER,  2),
    ApplyStep(ManifestCategory.SKILLS,          ApplyPhase.ON_CONTAINER,  3),
    ApplyStep(ManifestCategory.MCP,             ApplyPhase.ON_CONTAINER,  4),
    ApplyStep(ManifestCategory.ENGINE_CONFIG,   ApplyPhase.ON_CONTAINER,  5),
    ApplyStep(ManifestCategory.CLI_TOOLS,       ApplyPhase.ON_CONTAINER,  6),
)
```

```python
# apply/registry.py

MATERIALISERS: dict[ManifestCategory | ManifestSection, Materialiser] = {
    ManifestSection.SCRIPT: ScriptMaterialiser(),
    ManifestCategory.MCP:   McpMaterialiser(),
}
```

Two tables, deliberately. A test asserts every `MATERIALISERS` key has an
`APPLY_ORDER` row — a materialiser with no place in the order is unreachable —
and that `APPLY_ORDER` covers every construct the vocabulary defines. The
reverse containment is *not* asserted, because its absence is the sparse state
W5 and W6 close.

Registration is a dict entry. That is the whole of "adding a category", and the
criterion that no branch names the three categories by hand is checked by
grepping the orchestrator for their names in a structural test.

## The Materialiser Contract

```python
@runtime_checkable
class Materialiser(Protocol):
    """Three stages, because three criteria need boundaries between them.

    Every member is ``@abstractmethod`` and each materialiser **inherits** this
    Protocol — the shape ``BotConfigManifestServiceProtocol`` and the repository
    contracts already use here. Omitting a stage then fails at construction
    naming it, rather than as an ``AttributeError`` the first time a category
    reaches that stage — which, for ``write``, would be mid-apply on a real bot.
    """

    #: Which construct this materialises. Read by the registry test that pins
    #: every ``MATERIALISERS`` key to an ``APPLY_ORDER`` row.
    construct: ManifestCategory | ManifestSection

    @abstractmethod
    async def resolve(
        self, ctx: ApplyContext, entries: Sequence[dict[str, Any]]
    ) -> ResolveResult:
        """Declared entries → intents. Everything that can fail before touching
        the bot fails here: substitution, the W10 seam's validators, permission.
        W5's fetch goes here. A single failure aborts the category."""
        ...

    @abstractmethod
    async def plan(self, ctx: ApplyContext, intents: Sequence[Intent]) -> CategoryPlan:
        """Read current state; classify each intent and compute removals.
        Read-only — ``dry_run`` returns after this and cannot have written."""
        ...

    @abstractmethod
    async def write(
        self, ctx: ApplyContext, plan: CategoryPlan
    ) -> Sequence[EntryResult]:
        """Execute. An all-``unchanged`` plan with no removals writes nothing."""
        ...
```

`ResolveResult` carries `intents` and `failures`, both keyed by the entry's
identity (`name` / `type` / `path` / `server_code`), so the orchestrator can emit
one `EntryResult` per declared entry without the materialiser knowing what a
report is.

**Removals are reported outside the five-value outcome enum.** `CategoryPlan`
carries `removals: tuple[str, ...]`, and `CategoryResult` surfaces them as their
own field. The reason is precision: the five outcomes classify *declared*
entries, and a removal has no declared entry — `skills: []` deletes everything
while declaring nothing. Folding removals into the enum would either invent a
sixth value the criteria do not list, or leave the destructive half of overwrite
unaudited. A separate field does neither.

## The Orchestrator

```python
async def apply(self, ctx, document, *, phases, dry_run) -> ApplyReport
```

Per construct, in `APPLY_ORDER` position order, filtered to the requested
phases:

1. Not declared in the document → nothing. Not touched, not reported. (Absence
   is not a declaration.)
2. Declared, no `MATERIALISERS` entry → every entry `failed`, reason names the
   construct and that nothing materialises it. Category aborted.
3. Declared → `resolve`. Any failure → those entries `failed`, every other entry
   in the category `skipped`, category aborted, **no `plan` and no `write`**.
4. `plan`. If `dry_run`, record the projected outcomes and stop.
5. `write`. Record the actual outcomes.

An aborted category never affects another: the loop catches per construct and
carries on. `ApplyStatus` is tallied at the end from the entry outcomes — all
`created`/`updated`/`unchanged` ⇒ `SUCCEEDED`; any `failed`/`skipped` with at
least one write ⇒ `PARTIAL`; nothing written ⇒ `FAILED`. It is computed after
every decision has been made, which is the mechanical form of "the summary
decides nothing".

Phase selection is a parameter so W13 can call
`apply(phases={PRE_CONTAINER})` before composing the start command and
`apply(phases={ON_CONTAINER})` after the container is up, and get one report
each. The HTTP route passes both, and the split is invisible.

The lock is acquired once around the whole call, before step 1, and released in
a `finally`. `dry_run` takes it too — it reads current state, and a plan computed
against state another apply is changing is a plan that describes nothing.

## Apply Does Not Block the Request

Applying is device I/O today and network fetching from W5. Holding an HTTP
connection open across it is wrong on its own terms, and it is also the shape
W13 needs: its poll surface reports `APPLYING`, which only exists if apply is
something you start and then ask about.

So the route **starts** the work and answers:

```python
@router.post("/config-manifest/apply", status_code=202)
async def apply_bot_config_manifest(...) -> Envelope[ConfigManifestApplyAccepted]:
    """Start an apply. Returns immediately with the id to poll."""
    bot = bot_service.get_bot(bot_id, owner_id)      # addressed owner/tenant guard
    accepted = apply_service.start_apply(
        entity_id=manifest_target(bot), bot_id=bot_id, actor_id=..., trigger="explicit"
    )
    return envelope(ConfigManifestApplyAccepted(apply_id=accepted.apply_id), request)
```

`start_apply` is synchronous up to the point where it can answer, and no
further:

1. Take the lock. Held ⇒ `ManifestApplyInProgressError` ⇒ 409, **before** an id
   is minted. A caller never gets an id for an apply that did not start.
2. Read and re-validate the stored document. Invalid ⇒ 422 now, not a report
   the caller has to poll for to learn their document was bad.
3. Write the report row with `status=RUNNING` and a fresh `apply_id`.
4. Hand the orchestrator to a background thread and return the id.

The thread is wrapped exactly the way this codebase already wraps them:

```python
threading.Thread(
    target=bind_current_avernet_tenant(self._run_apply, ...), daemon=True
).start()
```

`bind_current_avernet_tenant` captures the tenant **at wrap time**, inside the
request thread, and re-establishes it inside the new one. This is the
established pattern — `bot_publish_service.py:1292` (`_do_restart`),
`baas_publish_poller.py:57`, `bot_service.py:1979` — and it is load-bearing
rather than tidy: without it the apply runs under the default tenant and both
substitutes the wrong `${BOT_TENANT}` *and* reads and writes the manifest tables
in the wrong tenant. That is an isolation failure, not a correctness one, and
W13's criteria call it out for the same reason.

**The trap worth naming:** `bind_current_avernet_tenant` looks like a decorator
(it uses `functools.wraps`) but captures at the moment the wrapping expression
is evaluated. Used as `@decorator` on a module-level function it captures at
**import**, when there is no request, and binds the default tenant forever.
Wrap inline at the `threading.Thread(...)` call, as every existing call site
does.

**Terminal states, and not stranding a poller.** The thread updates the row to
the derived `SUCCEEDED` / `PARTIAL` / `FAILED` in a `finally`, so a raising
orchestrator still terminates the report. A process killed mid-apply cannot run
that `finally` — which is what the lock's staleness rule is for: a report still
`RUNNING` whose lock has aged past the TTL reads as `FAILED`. Derived at read
time rather than by a sweeper, so there is no second mechanism to keep alive.

**`dry_run` stays synchronous.** It returns the plan in the response body, takes
no `apply_id`, and writes no row — a preview whose answer arrives later by
polling is not a preview. That is honest only while nothing is fetched; the
moment W5 puts a network call in `resolve`, `dry_run` inherits the same problem
this section solves, and the plan says so rather than letting W5 find out.

## The Service Contract

A second contract, not more methods on `BotConfigManifestServiceProtocol`
(Rule 9: that one's reason to change is "what a document may be", this one's is
"what applying does"). Registered in `test_service_api_conformance.py`'s
`_PAIRS`, and the service **inherits** it so a missing member fails at
construction.

```python
# core/bot_config_manifest/bot_config_manifest_apply_service_protocol.py

@dataclass(frozen=True)
class ApplyAccepted:
    """What starting an apply returns. The id is the caller's handle."""

    apply_id: str
    status: ApplyStatus          # always RUNNING here


@runtime_checkable
class BotConfigManifestApplyServiceProtocol(Protocol):
    """Apply a bot's stored manifest, and read what an apply did."""

    @abstractmethod
    def start_apply(
        self,
        *,
        entity_id: str,
        bot_id: str,
        actor_id: str,
        trigger: str,
        phases: frozenset[ApplyPhase] | None = None,
    ) -> ApplyAccepted:
        """Take the lock, validate, record ``RUNNING``, start the work, return.

        Does **not** wait for the apply. Raises before minting an id when the
        lock is held or the stored document no longer validates, so a caller
        never receives an id for an apply that did not start.

        ``phases`` defaults to both. W13 passes one at a time — Phase A before
        the start command is composed, Phase B once the container is up.

        Raises:
            ManifestApplyInProgressError: Another apply holds this bot's lock.
            ManifestValidationError: The stored document no longer validates for
                this bot — its engine can change after the document was accepted.
        """
        ...

    @abstractmethod
    async def dry_run(
        self, *, entity_id: str, bot_id: str, actor_id: str
    ) -> ApplyReport:
        """Compute the plan and return it. Writes nothing, mints no id.

        Synchronous because a preview whose answer arrives by polling is not a
        preview. Revisit when W5 puts a fetch in ``resolve``.
        """
        ...

    @abstractmethod
    def get_apply(
        self, *, entity_id: str, bot_id: str, apply_id: str
    ) -> Optional[ApplyReport]:
        """One apply's report by id, in progress or finished."""
        ...

    @abstractmethod
    def last_apply(
        self, *, entity_id: str, bot_id: str
    ) -> Optional[ApplyReport]:
        """The newest report for this bot, or ``None`` if never applied.

        ``None``, never an error — the same "absent is not an error" rule the
        manifest's own ``get`` follows.
        """
        ...
```

## Persistence

**`ac_bot_config_manifest_apply`** — one row per apply.

| Column | Why |
| --- | --- |
| `apply_id` | `uuid4().hex`. The report's public identity, returned by `start_apply` and polled on; also what a later per-entry table would join on |
| `status` | `RUNNING` on insert, terminal on completion. Denormalised so "show me failed applies" is a query, and so a poll is one indexed read |
| `env`, `entity_id`, `bot_id`, `avernet_tenant` | The same logical key `ac_bot_config_manifest` carries, and the same 256-char widths for the same index-budget reason |
| `trigger` | `explicit` in W4. W8 and W13 add `republish` / `restart` / `create` without a migration |
| `started_at`, `finished_at` | §7's shape. `finished_at` is null exactly while `status` is `RUNNING` |
| `actor` | Bounded the way `MAX_MODIFIER_CHARS` bounds the manifest's `modifier`, and for the same reason: an application actor composes a prefix onto a 1024-char user id |
| `report` | `mediumtext`, the per-entry detail as JSON. Written twice: an empty shell on insert, the full report on completion |

No `dry_run` column: `dry_run` mints no id and writes no row, so there is
nothing for it to mark. A test asserts the table is untouched by a dry run —
the honest form of that criterion, rather than a flag that could be set.

Two indexes, one per read:

- `(avernet_tenant, env, entity_id, bot_id, id DESC)` — `last-apply`, "newest
  row for this bot".
- `(avernet_tenant, env, entity_id, bot_id, apply_id)` — the poll by id. It
  carries the bot key rather than being a bare `apply_id` lookup so that a
  guessed id from another bot cannot be read; the id is not the authorization.

**`ac_bot_config_manifest_apply_lock`** — the serialization lock. Columns and
behaviour mirror `ac_bot_restart_lock`: `UNIQUE(avernet_tenant, env, entity_id,
bot_id)` *is* the lock, `acquire` inserts and lets the database arbitrate,
`IntegrityError` means held, `release` compares `lock_token` before deleting, and
`get_if_stale` judges the TTL on the **database clock** on both sides.

A separate table from the restart lock, not a separate row in it: applying a
manifest and restarting a bot are different operations, and coupling them would
mean a restart blocking an apply as an accident of storage rather than a
decision. The *pattern* is reused verbatim, which is what work-items §5 asks for.

**Why per-entry detail is JSON rather than a second table.** §2.7 makes the
per-entry records *the* report and apply's only output; nothing queries across
entries. `keep_last` reads materialised **content**, which is W11's
content-addressed store and a different question. A per-entry table would be a
join with no query behind it. Recorded here so that if W11 later wants to join
provenance per entry, `apply_id` is already the key it would join on.

## The Two Materialisers

### `script` → `BotStartupScriptService`

- `resolve`: substitute `${BOT_*}` via
  `schema/placeholders.py::resolve(text, engine_type, env, tenant)`. Refuse
  through the capability resolver if `script` is unsupported for this bot
  (teclaw, desktop) — the same verdict `PUT` used, re-asked because a bot's
  engine can change between write and apply.
- `plan`: `get_body(entity_id, bot_id)` and compare against the **substituted**
  body. Equal ⇒ `unchanged`. Absent ⇒ `created`. Different ⇒ `updated`.
  Declared-absent while a row exists ⇒ a removal.
- `write`: `put(...)` or `delete(...)`. Nothing else.

Comparing the substituted body is the whole of *Decisions* 7 — comparing the raw
document text would report `updated` on every apply of any document using a
placeholder.

**When the row actually executes, confirmed against the baas path.** The
materialiser writes a row and stops there, which raises the fair question of
whether a script written by a *later* manifest ever runs. It does:

- `BaasService._build_create_bot_payload` calls `_resolve_startup_script(entity_id,
  bot_id)` on **every payload it composes** (`baas_service.py:710`), then
  `_compose_start_command` appends it to the composer's boot chain and the
  result becomes `after_create_cmd_hook`.
- That function is reached from **`create_bot` and `upgrade_bot`** — the latter
  being `POST /api/v1/bots/{bot_uuid}/update`, used for republish and in-place
  restart.
- `BotService.restart_bot` is documented as *"Restart a bot by releasing current
  device and allocating a new one"* — a new device means a fresh payload, and
  the row is re-read at that moment.
- `deploy_config_composer.py`'s own docstring states the contract: `BaasService`
  appends the per-bot script *"so a bot's stored script runs on **every**
  deployment"*, and `_resolve_startup_script` repeats it — *"the published
  contract says a stored script runs on **every** start the platform composes."*

So the effect is deferred, not lost: the row takes effect at the next **device
provisioning** — create, restart, or republish. What never happens is
re-execution inside a container that is already up, which is why §2.7's boundary
holds (apply records delivery, not execution) and why the response says
*delivered now, executes at next provisioning* rather than the looser "next
start". This is also why **the phase split is not optional**: on the creation
path the row must exist *before* `_build_create_bot_payload` reads it, and phase
A being separately callable is what lets W13 arrange that without bypassing the
orchestrator.

### `mcp` → `DirectActivationService`

- `resolve`: the tenant permission check for each `server_code`, reusing the
  existing check rather than a copy. A server the tenant may not enable is a
  `failed` entry, which aborts the category — a partially-activated set is a
  deactivation of the rest.
- `plan`: `list_installed_mcps(bot_id, owner_id, actor_id)` is the current set.
  Declared − current ⇒ `created`; declared ∩ current ⇒ `unchanged`;
  current − declared ⇒ removals.
- `write`: `activate_mcp` per addition, `deactivate_mcp` per removal.

Nothing in this materialiser can reach `update_user_unified_config`,
`write_unified_config` or `sync_mcp_detail_to_all_bots`, and a structural test
asserts it — that is the account-scoped write whose fan-out spec *Decisions* 11
removed from the schema.

## Authorization

Two rows in each table, per W10's *Apply Declares Its Own Bars*:

| Operation | `ADMISSION` | `AUTHORIZATION` |
| --- | --- | --- |
| `POST …/config-manifest/apply` | `GRANT_CHECKED_ADDRESSED_BOT` | `Check(PermissionLevel.OWNER, EDIT_LOCK)` |
| `GET …/config-manifest/last-apply` | `GRANT_CHECKED_ADDRESSED_BOT` | `Check(PermissionLevel.MEMBER)` |
| `GET …/config-manifest/applies/{apply_id}` | `GRANT_CHECKED_ADDRESSED_BOT` | `Check(PermissionLevel.MEMBER)` |

The two reads sit at `MEMBER` beside `GET …/config-manifest`, which the same
argument already covers: reading how a bot is configured is part of working on
it. They carry no secret to protect — credentials appear as names only, and in
W4 the report has no source section at all.

**The `apply_id` is not an authorization token.** The by-id read is bar-checked
and bot-scoped exactly like every other route in the group, and its query
carries the bot key alongside the id, so an id guessed or leaked from another
bot resolves to nothing. An unguessable id is not a substitute for a check.

**The dominance test.**

```python
def test_apply_bar_dominates_every_category_it_can_materialise():
    """Apply must never be a route around an owner-only category endpoint."""
```

For every construct in `MATERIALISERS`, it collects that category's own write
operations from `AUTHORIZATION` and asserts apply's level is ≥ theirs, treating
`OWNER_SCOPED` as `OWNER`. It compares the **admitted set**, not raw enum
ordering, because `Check(OWNER)` + `GRANT_CHECKED_ADDRESSED_BOT` and
`OWNER_SCOPED` + `GRANT_CHECKED_OWN_BOT` admit the same people: `OWNER` is
unreachable by a collaborator (the vocabulary is admin/member), so the caller
must *be* the addressed owner. Spec *Decisions* 8 records that reasoning; the
test carries it as a comment so the next reader does not re-derive it.

The test iterates `MATERIALISERS`, so W5 registering `skills` brings `skills`
into it automatically — which is the property that makes it a safety net rather
than a snapshot.

## Errors and Status Codes

| Error | Status | Note |
| --- | --- | --- |
| `ManifestApplyInProgressError` | 409 | The lock is held. Retryable, and the message says so |
| `ManifestValidationError` | 422 | Existing. Re-validating the stored document at apply is not paranoia: capabilities are resolved from the bot's engine, which can change after the document was accepted |

Everything else — a failed entry, an aborted category — is a **200 with a report
saying so**, not an error status. A category that could not be materialised is
the operation working correctly and reporting what happened; there is no failure
of the apply *request*.

## Testing

- **Convergence, by absence of writes.** Apply twice against an unchanged
  document; assert every entry is `unchanged` *and* that the startup-script and
  activation services were not called on the second pass. Equal output is not
  the criterion.
- **All-or-nothing.** Declare `{A, B}` in `mcp` where B's permission check
  fails; assert A is still active with its original state, B reports `failed`, A
  reports `skipped`, and `activate_mcp`/`deactivate_mcp` were never called.
- **The transient-failure test that matters**, stated in #1472: the same setup
  proves a momentary failure does not delete a working entity.
- **`[]` and `DELETE`, in one test.** `skills: []` empties; deleting the
  manifest empties nothing. Two behaviours, one rule, one test that would fail
  if either were implemented as its own special case.
- **Per-category area.** A declared `mcp` category leaves the bot's skills,
  identity files and workspace untouched. (The `resources` subtree criterion is
  written here as a placeholder assertion against the *area* rule, and becomes
  real in W6 — the plan does not pretend to test a materialiser that does not
  exist.)
- **Reserved identity files.** `MEMORY.md` / `IDENTITY.md` are unreachable from
  apply: refused at `PUT` today, and asserted here to have no path through the
  orchestrator either, so the guarantee does not rest on one layer.
- **Two phases.** Phase A alone applies `script` and nothing else; phase B alone
  applies `mcp` and not `script`; both together preserve the order. This is
  W13's call pattern, tested before W13 exists — the same discipline W10 used
  for `from_spec`.
- **No materialiser.** A document declaring `skills` and `script` delivers the
  script, fails every `skills` entry, and reports `PARTIAL`.
- **`dry_run` writes nothing**, including no report row. Asserted by counting
  rows in both tables before and after, not by trusting the code path.
- **Serialization.** Two concurrent applies: one proceeds, one gets 409.
- **The dominance test** above.
- **The account-scoped-write guard**: the `mcp` materialiser's module cannot
  reach the user-config write functions.
- **The record never over-claims.** Force a failure between two categories and
  assert nothing is recorded as materialised that was not.
- **`mcp[].config` is refused by name**, mirroring the retired-`entrypoints`
  test at `test_manifest_schema.py:596`.
- **Unmodified:** every existing manifest, startup-script and MCP test. This
  change adds operations; it does not alter one. The one deliberate exception is
  a new `mcp`-entry test for the narrowed key set — recorded rather than
  absorbed, per the spec.
- `OCB_PRE_PUSH_RUN_CI=1` on push, per work-items §8.

## Risks

- **Scope.** This is the largest item in the plan and the acceptance list is
  long. If something is cut it must not be the all-or-nothing test or the
  dominance test — those are the two that protect against silent data loss and
  silent privilege widening respectively. The realistic cut is the `dry_run`
  row-count assertion, and even that is cheap.
- **`DirectActivationService` is async and legal only when no Set or platform
  Default policy governs the capability.** A `server_code` governed by a skill
  set is a case the materialiser must resolve into a `failed` entry with a
  readable reason, not an exception escaping the orchestrator. Its behaviour
  there is checked before the materialiser is written, not assumed.
- **The orchestrator must not become where category logic accretes.** Its
  README section says so, and the "no category named in the orchestrator"
  structural test is what enforces it. Without that, W5 and W6 will each add
  "just one" special case and the registry stops meaning anything.
- **Deactivating MCP servers a user activated through the UI is destructive and
  intended** (§3.2, accepted by decision). It is called out in the route
  docstring, because the first person surprised by it will be a real user.
- **Re-validating at apply can refuse a stored document** whose bot changed
  engine after the write. That is correct and it is a behaviour a caller can
  hit; the 422 names the construct and why.

## Follow-ups (named, not done here)

- **`PUT`'s bar versus apply's** — spec *Follow-ups*. W8 cannot land without it.
- **Purging manifest and apply state on bot deletion** — W1 named this item as
  the home; it stays a follow-up because bot deletion is a soft update with no
  cascade, and wiring one is its own change with its own blast radius.
- **Apply-record retention**, to be decided with W11's explicit retention policy
  rather than invented here.
- **`call_type` in the `mcp` entry**, additive, once the lock-epoch and
  irreversibility semantics of an idempotent re-apply are answered.
