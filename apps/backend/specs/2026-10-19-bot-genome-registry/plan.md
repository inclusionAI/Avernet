# Plan: Genome Registry in Backend (RSI-03)

## Approach

Add one core package, `core/bot_genome/`, beside `core/bot_config_manifest/`.
It keeps revisions as the contract's JSON (validated against the RSI-02
schemas, hashed with the contract's canonical JSON) in new tables, keeps every
byte in the existing manifest content store, and reuses the Manifest's own
machinery wherever the contract says apply's behaviour is the rule: the
Manifest validator, `DeclaredSourceResolver` for fetching sources,
`SkillPackageValidator`, `admission_refusal`, `legal_identity_types`, the MCP
permission and override checks, and `placeholders.resolve`. Promotion is a
second small service in the same package that moves refs by CAS, records the
promotion, and hands the compiled document to a narrow `RevisionApplier`
seam whose production implementation stores the document through
`BotConfigManifestService.put` and starts `start_apply`. Everything is wired in
one DI module, exposed through two `api/` Protocols, and mounted as one
`/openapi/v1/bots/{bot_id}/genome` router.

Two consequences of the RSI-04 ordering are designed in rather than worked
around. `compile` emits the contract's proposed RSI-04 source spellings
(`{protocol: content_store, digest}`, `{protocol: center, version}`); the
applier validates the compiled document with the Manifest **before** any ref
moves, so today a promotion is refused as `not_appliable` with the validator's
reasons, and the day RSI-04 lands the same code applies it. And because apply
reports carry no document digest yet, "which revision does this bot run" is
answered by joining the bot's last apply to the promotion that started it
(`apply_reference == apply_id`); the promotion row also stores the compiled
document digest, so RSI-04 can switch the join to the digest without a
migration.

## Affected Components

- `src/agentclaw/community/core/bot_genome/` (new) — the registry: contract
  loading, canonical JSON, gene paths, patch application, risk, diff,
  Manifest recording and capture, compile, the registry and promotion
  services, repository models and DDL.
- `src/agentclaw/community/core/bot_config_manifest/content/` — gains the
  produced-content write path (`store_produced`) and a reachability read.
- `src/agentclaw/community/core/repository/{protocols,implementations}/bot/`
  — genome repository contract and ORM implementation; the content
  repository gains `has_digest`.
- `src/agentclaw/community/api/` — two new Protocol re-exports.
- `src/agentclaw/community/di/modules/bot_genome_module.py` (new) and
  `di/container.py` — wiring.
- `src/agentclaw/community/adapters/http/openapi_v1/` — `bots/genome.py`
  router, `bots/schemas_genome.py`, `errors_genome.py`,
  `authorization_rules_genome.py`, `admission_genome_routes.py`, and the
  mount in `__init__.py`.
- `src/agentclaw/community/core/schema.py` — model registration.
- `tests/community/contracts/` — `genome_canonical_json.py` and
  `genome_rewrite.py` are removed; `test_bot_genome_schema.py` imports the
  core modules and gains the recording/compile oracle tests (code-owner
  approval needed, as for RSI-02).
- `tests/community/core/bot_genome/`, `tests/community/repository/bot/`,
  `tests/community/adapters/http/openapi_v1/`, `tests/community/di/` — tests.
- `docs/bot-evolution/01-genome.md` (+ zh-CN), `work-items.md` (+ zh-CN) —
  D-6 closed, table names fixed, RSI-03 status.

## Data Model Changes

One DDL file, `core/bot_genome/sql/2026_10_19_bot_genome.sql`, same dialect
notes as `bot_config_manifest/sql/2026_08_31_manifest_content.sql`; ORM in
`core/bot_genome/repository/models.py`, registered from `core/schema.py`.
All five tables carry the bot axes `(avernet_tenant, env, entity_id,
bot_id)` with the tenant guard, like `ac_manifest_content`.

```sql
-- core/bot_genome/sql/2026_10_19_bot_genome.sql (sketch; full comments in the file)
CREATE TABLE `ac_genome_revision` (
  `id`             bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `avernet_tenant` varchar(64)  NOT NULL DEFAULT 'teamclaw',
  `env`            varchar(20)  NOT NULL,
  `entity_id`      varchar(256) NOT NULL,
  `bot_id`         varchar(256) NOT NULL,
  `revision_id`    char(71)     NOT NULL COMMENT 'sha256 of JCS({spec, policy})',
  `seq`            int(11)      NOT NULL COMMENT 'per-bot, humans only (r42)',
  `parent_id`      char(71)     DEFAULT NULL,
  `genome_schema`  int(11)      NOT NULL,
  `status`         varchar(16)  NOT NULL COMMENT 'draft/candidate/accepted/rejected/promoted/archived',
  `created_kind`   varchar(16)  NOT NULL COMMENT 'user/strategy_run',
  `created_actor`  varchar(256) NOT NULL,
  `run_id`         varchar(64)  DEFAULT NULL,
  `run_step`       varchar(64)  DEFAULT NULL,
  `patch_digest`   char(71)     DEFAULT NULL,
  `evidence`       text         NOT NULL COMMENT 'JSON list',
  `evaluations`    text         NOT NULL COMMENT 'JSON list',
  `annotations`    text         NOT NULL COMMENT 'JSON object',
  `content`        mediumtext   NOT NULL COMMENT 'JCS({"policy":…,"spec":…}), the hashed bytes',
  `manifest_yaml`  mediumtext   DEFAULT NULL COMMENT 'provenance copy when recorded from a document',
  `created_at`     datetime     NOT NULL,
  `gmt_create`     timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `gmt_modified`   timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_bot_revision` (`avernet_tenant`,`env`,`entity_id`,`bot_id`,`revision_id`) GLOBAL,
  UNIQUE KEY `uk_bot_seq`      (`avernet_tenant`,`env`,`entity_id`,`bot_id`,`seq`) GLOBAL,
  KEY `idx_bot_parent`         (`avernet_tenant`,`env`,`entity_id`,`bot_id`,`parent_id`) GLOBAL
) AUTO_INCREMENT_MODE = 'ORDER' DEFAULT CHARSET = utf8mb4;

CREATE TABLE `ac_genome_ref` (  -- one row per (bot, name); moved by CAS
  …bot axes…, `name` varchar(160) NOT NULL, `revision_id` char(71) NOT NULL,
  `version` bigint(20) NOT NULL DEFAULT 1, `moved_at` datetime NOT NULL,
  `moved_kind` varchar(16) NOT NULL, `moved_actor` varchar(256) NOT NULL,
  `run_id` varchar(64) DEFAULT NULL, `reason` varchar(1024) DEFAULT NULL,
  UNIQUE KEY `uk_bot_ref` (…bot axes…, `name`) GLOBAL
);
CREATE TABLE `ac_genome_ref_event` (  -- append-only move log
  …bot axes…, `name`, `from_revision_id` char(71) DEFAULT NULL, `to_revision_id` char(71) NOT NULL,
  `moved_kind`, `moved_actor`, `run_id`, `reason`, `moved_at` datetime NOT NULL,
  KEY `idx_bot_ref_moved` (…bot axes…, `name`, `moved_at`) GLOBAL
);
CREATE TABLE `ac_genome_patch` (  -- content-addressed; digest = candidate id
  …bot axes…, `digest` char(71) NOT NULL, `base_id` char(71) NOT NULL,
  `content` mediumtext NOT NULL COMMENT 'JCS(patch)', `created_at` datetime NOT NULL,
  UNIQUE KEY `uk_bot_patch` (…bot axes…, `digest`) GLOBAL
);
CREATE TABLE `ac_genome_promotion` (
  …bot axes…, `promotion_id` char(32) NOT NULL, `ref` varchar(16) NOT NULL,
  `revision_id` char(71) NOT NULL, `from_revision_id` char(71) DEFAULT NULL,
  `document_digest` char(71) NOT NULL, `going_back` tinyint(1) NOT NULL,
  `actor_kind` varchar(16) NOT NULL, `actor_id` varchar(256) NOT NULL, `reason` varchar(1024) NOT NULL,
  `status` varchar(16) NOT NULL COMMENT 'applying/applied/apply_failed',
  `apply_kind` varchar(16) NOT NULL, `apply_reference` varchar(64) DEFAULT NULL, `apply_detail` varchar(1024) NOT NULL,
  `created_at` datetime NOT NULL,
  UNIQUE KEY `uk_promotion` (`avernet_tenant`,`promotion_id`) GLOBAL,
  KEY `idx_bot_apply` (…bot axes…, `apply_reference`) GLOBAL,
  KEY `idx_bot_created` (…bot axes…, `created_at`) GLOBAL
);
```

- `content` is the exact hashed bytes, so a reader re-hashes it to verify
  the row; `spec`/`policy` are parsed from it on read.
- `seq` is `MAX(seq)+1` for the bot inside the insert transaction; a lost
  race hits `uk_bot_seq` and the insert is retried (bounded). A hit on
  `uk_bot_revision` means the content already exists: the existing row is
  returned (G-6).
- The `active`/`previous` pair moves in one transaction: `UPDATE ac_genome_ref
  … WHERE name='active' AND revision_id=:expected` (rowcount 0 → conflict,
  rollback), then upsert `previous`, then two `ac_genome_ref_event` rows.
- Reachability of a digest for a bot is a provenance row in
  `ac_manifest_content` for that bot and digest (fetched content already
  files one under the bot's scope; produced content files one through
  `store_produced`). No new table; the existing bot-prefixed index serves it.

## API / Interface Changes

### Content store: produced bytes (01-genome.md §9.1 extension 1)

```python
# core/bot_config_manifest/content/service_protocol.py — new members
@abstractmethod
def store_produced(self, data: bytes, *, scope: ContentScope, origin: str,
                   modifier: str = "", category: Optional[str] = None,
                   entry_identity: Optional[str] = None) -> StoredContentRecord:
    """Persist bytes that were produced, not fetched. ``origin`` is a short
    label (``genome/upload``, ``genome/patch``, ``genome/capture``); the
    provenance row records ``produced://<origin>`` as both URLs."""

@abstractmethod
def is_reachable(self, scope: ContentScope, digest: str) -> bool:
    """A provenance row exists for this bot and digest."""
```

```python
# core/repository/protocols/bot/manifest_content.py — new member
@abstractmethod
def has_digest(self, *, env: str, entity_id: str, bot_id: str, digest: str) -> bool: ...
```

### Registry service (core Protocol, re-exported by `api/bot_genome_registry_service.py`)

```python
# core/bot_genome/genome_registry_protocol.py
@runtime_checkable
class GenomeRegistryProtocol(Protocol):
    @abstractmethod
    def get_revision(self, bot: BotRef, rev: str) -> GenomeRevision: ...          # id | r<seq> | ref name
    @abstractmethod
    def list_revisions(self, bot: BotRef, *, status: RevisionStatus | None = None,
                       parent: str | None = None, page: int = 1, page_size: int = 20) -> RevisionPage: ...
    @abstractmethod
    async def record_patch(self, bot: BotRef, patch: dict, *, created_by: Provenance,
                           status: RevisionStatus, run: RunScope | None = None) -> RecordedRevision: ...
    @abstractmethod
    async def record_manifest(self, bot: BotRef, document: str, *, created_by: Provenance,
                              base: str | None) -> RecordedRevision: ...
    @abstractmethod
    def set_status(self, bot: BotRef, revision_id: str, status: RevisionStatus,
                   *, actor: Provenance, reason: str) -> None: ...
    @abstractmethod
    def diff(self, bot: BotRef, rev: str, against: str) -> GenomeDiff: ...
    @abstractmethod
    def list_refs(self, bot: BotRef) -> list[GenomeRef]: ...
    @abstractmethod
    def move_ref(self, bot: BotRef, update: RefUpdate, *, actor: Provenance,
                 mover: RefMover) -> GenomeRef: ...
    @abstractmethod
    def read_content(self, bot: BotRef, digest: str) -> bytes: ...
    @abstractmethod
    def put_content(self, bot: BotRef, data: bytes, *, provenance: Provenance) -> ContentReceipt: ...
    @abstractmethod
    def compile(self, bot: BotRef, rev: str) -> CompiledManifest: ...
    @abstractmethod
    def applied_revision(self, bot: BotRef) -> str | None: ...
```

`record_*` are `async` because the recorder awaits the MCP materialiser-style
checks and the current-state readers (identity, resources, local skill
storage are async ports); the HTTP layer awaits them directly, the rest run
in a threadpool like the Manifest routes.

```python
# core/bot_genome/models.py (the values the Protocol passes; frozen dataclasses)
class RevisionStatus(StrEnum): DRAFT, CANDIDATE, ACCEPTED, REJECTED, PROMOTED, ARCHIVED
class ProvenanceKind(StrEnum): USER, STRATEGY_RUN
@dataclass(frozen=True) class BotRef: owner_id: str; bot_id: str; entity_id: str; engine_type: str; bot_type: str; entity_type: str
@dataclass(frozen=True) class Provenance: kind: ProvenanceKind; actor: str; run_id: str | None = None; step: str | None = None
@dataclass(frozen=True) class RunScope: run_id: str; binding_id: str; allowed_genes: tuple[str, ...]
@dataclass(frozen=True) class RevisionMetadata: id, seq, parent, created_by, created_at, status, patch_digest, evidence, evaluations, annotations
@dataclass(frozen=True) class GenomeRevision: genome_schema: int; revision: RevisionMetadata; spec: dict; policy: dict
@dataclass(frozen=True) class RecordedRevision: revision: GenomeRevision; created: bool; risk_tier: str; flags: tuple[str, ...]; ref: str | None
@dataclass(frozen=True) class GenomeRef: name: str; revision: str; moved_at: datetime; moved_by: Provenance; reason: str | None
@dataclass(frozen=True) class RefUpdate: name: str; revision: str; expected_revision: str | None; reason: str | None = None
class RefMover(StrEnum): OWNER = "owner"; PROMOTION = "promotion"; RUN = "run"
@dataclass(frozen=True) class GeneChange: gene: str; kind: str; diff: str | None
@dataclass(frozen=True) class GenomeDiff: revision: str; against: str; changes: tuple[GeneChange, ...]; risk_tier: str; rewrite_flags: tuple[str, ...]
@dataclass(frozen=True) class CompiledManifest: revision_id: str; document: dict; document_yaml: str; document_digest: str
```

`BotRef` carries what every recorder check needs (`entity_id` is the storage
key, `engine_type` and `bot_type` the capability axes); the HTTP layer builds
it from the resolved bot record exactly once (`bot_ref_of(bot, owner_id)`).

```python
# core/bot_genome/errors.py — plain exceptions, mapped by the HTTP group (no DomainError)
class GenomeError(Exception): ...
class RevisionNotFound(GenomeError): ...
class RefConflict(GenomeError): ref: str; expected: str | None; current: str | None
class BaseMismatch(GenomeError): ...
class PatchRejected(GenomeError): reasons: tuple[Reason, ...]      # Reason(code, location, message)
class LimitExceeded(GenomeError): ...
class MoverNotAllowed(GenomeError): ...
class ContentNotReachable(GenomeError): ...
class CaptureRefused(GenomeError): reasons: tuple[Reason, ...]      # e.g. repo skill, non-ASCII resource path
class Drifted(GenomeError): active: str; applied: str | None
class NotPromotable(GenomeError): ...
class NotAppliable(GenomeError): violations: tuple[Reason, ...]     # Manifest refused the compiled document
class PromotionUnsupported(GenomeError): ...                        # service bots, this item
```

### Promotion service (core Protocol, re-exported by `api/bot_genome_promotion_service.py`)

```python
# core/bot_genome/promotion/promotion_service_protocol.py
@runtime_checkable
class GenomePromotionServiceProtocol(Protocol):
    @abstractmethod
    async def promote(self, bot: BotRef, *, revision: str, ref: str, expected_revision: str,
                      actor: Actor, reason: str) -> Promotion: ...
    @abstractmethod
    def get_promotion(self, bot: BotRef, promotion_id: str) -> Promotion: ...
    @abstractmethod
    def list_promotions(self, bot: BotRef, *, page: int = 1, page_size: int = 20) -> PromotionPage: ...

# core/bot_genome/promotion/applier.py — the one seam to the delivery path
class RevisionApplier(Protocol):
    @abstractmethod
    def preflight(self, bot: BotRef, compiled: CompiledManifest) -> None:
        """Raise NotAppliable / PromotionUnsupported before any ref moves."""
    @abstractmethod
    def start(self, bot: BotRef, compiled: CompiledManifest, *, actor: Actor, reason: str) -> ApplyResult: ...

class ManifestRevisionApplier(RevisionApplier):
    """Personal bots: ``BotConfigManifestService.validate`` in preflight;
    ``put`` + ``start_apply(trigger="genome_promotion")`` in start.
    Service bots: ``preflight`` raises PromotionUnsupported."""
```

`promote` order (08-promotion.md §7.1, drift from §5.5): resolve target →
refuse `candidate` revisions that are not `accepted` (`NotPromotable`; the
gate's decision record does not exist yet, so an owner may promote
`draft`/`accepted`/`promoted`/`archived` revisions) → compile → drift check
(`Drifted` for non-owner actors only) → `applier.preflight` → CAS move of
`active` + `previous` in one transaction, revision status `promoted` →
promotion row `applying` → `applier.start` → row updated with the apply
reference, or `apply_failed` with the detail if starting failed (refs stay
moved, as the design states; the owner goes back by promoting `previous`).

### HTTP (all under `/openapi/v1/bots/{bot_id}/genome`, `PublicAPIRoute`, `Envelope`)

| Method, path | Handler → service | Level | Status |
| --- | --- | --- | --- |
| `GET /revisions` | `list_revisions` | MEMBER | 200 `Page[RevisionSummary]` |
| `POST /revisions` | `record_patch` / `record_manifest` | ADMIN | 201 `RevisionRecorded`; 200 when it already existed |
| `GET /revisions/{rev:path}/diff?against=` | `diff` | MEMBER | 200 `GenomeDiffPayload` |
| `GET /revisions/{rev:path}` | `get_revision` | MEMBER | 200 `GenomeRevisionPayload` |
| `GET /refs` | `list_refs` | MEMBER | 200 `{refs: […]}` |
| `PUT /refs/draft` | `move_ref(mover=OWNER)` | ADMIN | 200 `GenomeRefPayload` |
| `GET /content/{digest}` | `read_content` | MEMBER | 200 `application/octet-stream` |
| `PUT /content` | `put_content` | ADMIN | 201 `{digest, size}`; 200 if it existed |
| `POST /promotions` | `promote` | OWNER + EDIT_LOCK | 201 `PromotionPayload` |
| `GET /promotions` | `list_promotions` | MEMBER | 200 `Page[PromotionPayload]` |
| `GET /promotions/{promotion_id}` | `get_promotion` | MEMBER | 200 `PromotionPayload` |

The diff route is registered before the get route so `{rev:path}` (needed
for `candidate/<run>/<n>`) cannot swallow `/diff`. Admission for every row is
`GRANT_CHECKED_ADDRESSED_BOT`, like the config-manifest group. Promotion takes
apply's own bar (`test_config_manifest_apply_bars.py` reasoning: it rewrites
the bot's whole configuration); the dominance test gains the promotion row.

```jsonc
// POST /openapi/v1/bots/bot_123/genome/revisions  (request, from a patch)
{"base": "sha256:a90b…", "status": "candidate",
 "run": {"run_id": "run_7f3", "binding_id": "bind_01", "allowed_genes": ["persona", "skills"]},
 "patch": {"patch_schema": 1, "base": "sha256:a90b…", "ops": [...], "rationale": "…", "evidence": ["episode:ep_91"]}}
// (request, from a document; `base` optional here: defaults to the `active` revision, or to no parent on a
//  bot with no revisions, whose omitted categories are then captured from current state)
{"base": "sha256:a90b…", "status": "draft", "manifest": {"format": "yaml", "document": "schema_version: 1\n…"}}
// 201 / 200
{"id": "sha256:7c1e…", "seq": 42, "status": "candidate", "parent": "sha256:a90b…",
 "patch_digest": "sha256:c41e…", "risk_tier": "T2", "flags": [], "ref": "candidate/run_7f3/1"}
```

Error mapping (`errors_genome.py`, merged into `ENVELOPE_ERRORS` like the
source-credentials rows; `data` carries the reasons or the current ref):

```python
GENOME_ENVELOPE_ERRORS = {
    RevisionNotFound: (404, "Not found"), ContentNotReachable: (404, "Not found"),
    RefConflict: (409, "Ref moved: re-read and decide"),         # data: {ref, expected_revision, current_revision}
    BaseMismatch: (409, "Patch base is not the current parent"),
    Drifted: (409, "Bot has drifted from its active revision"),
    PatchRejected: (422, "Patch rejected"),                      # data: {reasons: [{code, location, message}]}
    CaptureRefused: (422, "Current state cannot be captured"),
    NotAppliable: (422, "Compiled revision is not appliable to this bot"),
    NotPromotable: (422, "Revision is not promotable"),
    PromotionUnsupported: (422, "Promotion is not supported for this bot type"),
    LimitExceeded: (413, "Limit exceeded"), MoverNotAllowed: (403, "Forbidden"),
}
```

### Trigger vocabulary

`start_apply(trigger="genome_promotion")` — a new non-creation trigger
string; `require_phase_matches_trigger` already admits any non-creation
trigger with `phase=None`.

## Key Files & Functions

```text
core/bot_genome/
  README.md                         # Context Boundary (test_module_boundaries) + module story
  __init__.py
  contract/                         # copies of the RSI-02 contract files the registry loads at runtime
    genome-revision.schema.json, genome-patch.schema.json, risk-tiers.json
  contract.py                       # load_schemas() → Draft7Validator pair (referencing Registry), load_risk_tiers()
  canonical.py                      # canonicalize, loads_strict, sha256_digest, revision_id, tree_digest  (from tests/)
  gene_paths.py                     # encode_segment, decode, is_canonical, covers, overlaps, path builders per op/category
  rewrite.py                        # to_lines, strip_common, lcs_length, rewrite_flagged                 (from tests/)
  text_edits.py                     # replace_section, insert_after, apply_unified_diff, description_only
  models.py, errors.py, limits.py
  spec_checks.py                    # README §6.4 predicates over a spec + a ContentReader; SpecCheckContext for MCP
  patch/
    __init__.py, ops.py             # apply_patch(base_spec, patch, content) → PatchOutcome(spec, writes, touched, changed_files)
    policy.py                       # policy + allowed_genes checks → Reason list
    risk.py                         # tier_for_op, apply_raises, apply_overrides, patch_tier, description_only condition
  diff.py                           # diff_specs(a, b, content) → GenomeDiff
  compile.py                        # compile_revision(spec, content) → CompiledManifest (RSI-04 spellings), document_digest
  recorder/
    manifest_recorder.py            # document → spec: validate_document, RecordingContext, DeclaredSourceResolver, fill from parent/capture
    capture.py                      # BotStateReader Protocol + capture_spec(reader) with the AGENTS.md normalisation and refusals
    bot_state.py                    # PlatformBotStateReader over IdentityFilePort, capability reader, local skill storage, ResourceFileService, MCP, script
  genome_registry_protocol.py       # Protocol (abstract members)
  registry.py                       # GenomeRegistryService
  promotion/
    __init__.py, promotion_service_protocol.py, applier.py, service.py
  repository/
    __init__.py, models.py          # ORM + pydantic records for the five tables
  sql/2026_10_19_bot_genome.sql
core/repository/protocols/bot/genome.py           # GenomeRevisionRepositoryProtocol, GenomeRefRepositoryProtocol,
                                                  # GenomePatchRepositoryProtocol, GenomePromotionRepositoryProtocol
core/repository/implementations/bot/genome.py     # one ORM body each (SQLite + OceanBase)
api/bot_genome_registry_service.py, api/bot_genome_promotion_service.py
di/modules/bot_genome_module.py
adapters/http/openapi_v1/bots/genome.py, bots/schemas_genome.py, errors_genome.py,
adapters/http/openapi_v1/authorization_rules_genome.py, admission_genome_routes.py
```

```python
# core/bot_genome/canonical.py — moved verbatim from tests/community/contracts/genome_canonical_json.py
def canonicalize(value: Any) -> bytes: ...
def revision_id(spec: dict, policy: dict) -> str: ...
def tree_digest(files: list[tuple[str, bytes]]) -> str: ...   # canonical_tree_bytes from apply/entry_delivery.py:787
```

```python
# core/bot_genome/patch/ops.py
@dataclass(frozen=True)
class PatchOutcome:
    spec: dict                                   # the new spec, canonical order, policy untouched
    writes: tuple[tuple[str, bytes], ...]        # produced blobs (digest, bytes) to file through store_produced
    touched: tuple[tuple[str, str], ...]         # (op name, gene path) per op, for policy and risk
    changed_files: dict[str, tuple[bytes | None, bytes | None]]   # gene path → (before, after) for the rewrite measure
    replaced: frozenset[str]                     # gene paths removed then re-added (README §6.3 replacement)

def apply_patch(base_spec: dict, patch: dict, content: ContentReader) -> PatchOutcome:
    """Ops in order, each on the result of the previous; raises PatchRejected(reasons) on the first op that
    does not apply exactly (README §6.1/§6.2)."""
```

Per op: `file.edit` resolves the target to (category, entry, member path),
reads the file (persona/resource blob, or a tree member via
`decode_tree_bytes`), applies `text_edits` in order, and re-encodes the tree
when the file is a member; `skill.add/update` build the member list, run
`SkillPackageValidator.validate_directory` in `spec_checks` (not here), and
store `canonical_tree_bytes`; `resource.add` with a trailing-slash path
requires a `{digest}` of an `acm-tree-v1` blob; `memory.*` decode the items
blob, edit by key, re-encode as `canonicalize(items)`; `memory.*` on
`memory: null` is `PatchRejected(memory_disabled)`.

```python
# core/bot_genome/patch/risk.py
def op_tier(op: str, gene: str, *, description_only: bool, mode: str | None, rules: list[dict]) -> str
def patch_assessment(outcome: PatchOutcome, policy: dict, tiers: dict) -> tuple[str, tuple[str, ...]]:
    """Highest tier over ops after rewrite raises (measured once per file, base → result, README §6.3; replacement
    counts as rewrite) and policy.risk_overrides by overlap; returns (tier, rewrite-flagged gene paths)."""
```

```python
# core/bot_genome/recorder/manifest_recorder.py
@dataclass
class RecordingContext:        # satisfies apply/fetch_context.FetchContext (nine attributes, apply_id None)
    bot_id: str; entity_id: str; env: str; tenant: str; engine_type: str; actor_id: str
    apply_id: None = None; budget: ApplyFetchBudget | None = None; source_session: SourceSession | None = None

class ManifestRecorder:
    def __init__(self, *, manifests: BotConfigManifestServiceProtocol, resolver: DeclaredSourceResolver,
                 git_client: Callable[[], GitSourceClient], validator: SkillPackageValidator) -> None: ...
    async def build_spec(self, bot: BotRef, document: str, *, parent: dict | None,
                         capture: Callable[[], Awaitable[dict]]) -> tuple[dict, dict]:
        """validate_document(document, capabilities_for_bot) → for each declared entry resolve() → bytes;
        identity: single() bytes; skills: validate_directory(members) → tree digest; resources: single() or
        tree of members(unpack, strip_components); mcp: config normalised (README §4.2); script: placeholders.resolve;
        cli_tools ignored; omitted categories from `parent` or `await capture()`; returns (spec, parsed_document)."""
```

The `SourceSession` is built as the apply service builds it
(`config_manifest_apply_service.py:564`: `sources` from the parsed document,
`baselines={}`, `git=self._git_client_provider()`) and closed in `finally`.
Receipts are filed under the bot's scope with `category`/`entry_identity`
set and no `apply_id`, which is what the content table's nullable linkage
means (`2026_08_31_manifest_content.sql`).

```python
# core/bot_genome/recorder/capture.py
class BotStateReader(Protocol):                          # narrow; one production implementation, fakes in tests
    async def persona_files(self, bot: BotRef) -> dict[str, str]: ...                 # type → text
    async def skills(self, bot: BotRef) -> tuple[CapturedSkill, ...]: ...            # name, origin, center_version | files
    async def resources(self, bot: BotRef) -> tuple[CapturedResource, ...]: ...      # path, files (one for a file)
    def mcp_servers(self, bot: BotRef) -> tuple[dict, ...]: ...                        # {server_code, config?}
    def script_body(self, bot: BotRef) -> str | None: ...

async def capture_spec(reader: BotStateReader, bot: BotRef, *, validator: SkillPackageValidator) -> CapturedState:
    """README §7 capture: AGENTS.md reference block removed and rstrip()+"\n"; a resource whose path fails the
    Manifest path rule, or a skill that is neither local nor Center (git:// repo), raises CaptureRefused naming it."""
```

```python
# core/bot_genome/recorder/bot_state.py — PlatformBotStateReader (device-delivered bots)
# persona:   IdentityFilePort.list_bot_files / read_identity_file   (core/ports/identity_file_port.py)
# skills:    BotCapabilityStateReaderProtocol.active_skill_assets  minus platform Default supply (mirror
#            apply/materialisers/skills.py:320 plan()); center:// → (skill_uuid, sc_version_number);
#            local:// → SkillServiceFactory.local_skill_package_storage(...).read_package_files() (factories.py:438, :229)
# resources: ResourceService.list_resources + ResourceFileService.read_file / iter_directory_files (services/resource_file_service.py:416, :450)
# mcp:       DirectActivationServiceProtocol.list_installed_mcps minus platform_default_mcp_codes; config from
#            MCPConfigServiceProtocol.get_bot_override (mcp_config_service_protocol.py:15)
# script:    BotStartupScriptServiceProtocol.get_body
```

```python
# core/bot_genome/compile.py
def compile_spec(spec: dict, content: ContentReader) -> CompiledManifest:
    """identity/skills/resources → {protocol: content_store, digest}; center skills → {protocol: center, version};
    mcp verbatim; script → {body: <stored body>} or null; memory omitted while null; no cli_tools/engine_config.
    document_digest = sha256_digest(canonicalize(document)); document_yaml = yaml.safe_dump(document, sort_keys=False)."""
```

The `document_digest` rule (digest of the canonical JSON of the parsed
document, not of the YAML bytes) is what RSI-04's apply report must record;
stated in the README and the compatibility doc so the two sides agree.

```python
# core/bot_genome/registry.py — GenomeRegistryService(GenomeRegistryProtocol)
def __init__(self, *, revisions: GenomeRevisionRepositoryProtocol, refs: GenomeRefRepositoryProtocol,
             patches: GenomePatchRepositoryProtocol, content: ManifestContentServiceProtocol,
             recorder: ManifestRecorder, state_reader: BotStateReader, checks: SpecChecks,
             applies: BotConfigManifestApplyServiceProtocol, promotions: GenomePromotionRepositoryProtocol) -> None: ...

async def record_patch(self, bot, patch, *, created_by, status, run=None) -> RecordedRevision:
    # schema → base (BaseMismatch) → gene paths canonical → policy/allowed (PatchRejected) → apply_patch →
    # limits → spec_checks (await) → risk → store writes (store_produced) → patch row → revision row (idempotent)
    # → candidate ref for a run (next n, idempotent on (run, revision)) → RecordedRevision

def applied_revision(self, bot) -> str | None:
    # applies.last_apply(entity_id, bot_id) → promotions.by_apply_reference(bot, report.apply_id) → revision_id
```

```python
# di/modules/bot_genome_module.py
class BotGenomeModule(Module):
    @singleton @provider @inject
    def genome_registry(self, …) -> GenomeRegistryProtocol: ...
    @singleton @provider @inject
    def genome_promotion(self, registry: GenomeRegistryProtocol, applier: RevisionApplier, …) -> GenomePromotionServiceProtocol: ...
    @singleton @provider @inject
    def revision_applier(self, manifests: BotConfigManifestServiceProtocol,
                         applies: BotConfigManifestApplyServiceProtocol) -> RevisionApplier: ...
    # repositories bound with binder.bind(Protocol, to=Impl, scope=singleton); the state reader is a thunk
    # (Callable[[], BotStateReader]) for the same boot-walk reason manifest_delivery_module documents.
```

```diff
# di/container.py:179
         ManifestDeliveryModule(),
+        BotGenomeModule(),
```

```diff
# core/schema.py:57
     import agentclaw.community.core.bot_config_manifest.repository.apply_models  # noqa: F401
+    import agentclaw.community.core.bot_genome.repository.models  # noqa: F401  ac_genome_*
```

```diff
# adapters/http/openapi_v1/__init__.py:188, :391
 from .bots.config_manifest_apply import router as config_manifest_apply_router
+from .bots.genome import router as genome_router
 …
     config_manifest_apply_router,
+    genome_router,
```

```diff
# adapters/http/openapi_v1/responses.py:628
     **SOURCE_CREDENTIALS_ENVELOPE_ERRORS,
+    **GENOME_ENVELOPE_ERRORS,
```

```diff
# tests/community/contracts/test_bot_genome_schema.py:33
-from tests.community.contracts.genome_canonical_json import (...)
-from tests.community.contracts.genome_rewrite import (...)
+from agentclaw.community.core.bot_genome.canonical import (...)
+from agentclaw.community.core.bot_genome.rewrite import (...)
+# the hand-written _record / _compile oracles are replaced by the registry's recorder and compile over the
+# fixture sources (served by FakeObjectStore and a scripted git client), so the examples test the real code
```

## Dependencies

None new. `jsonschema>=4.20` is already a runtime dependency
(`pyproject.toml:102`), and `referencing` ships with it; `pyyaml` is present.

## Risks & Mitigations

- **Risk:** The compiled document cannot be applied until RSI-04, so the
  item's acceptance story cannot complete end to end here.
  **Mitigation:** the applier validates before any ref moves
  (`NotAppliable`), so nothing is left half-promoted; the seam, the source
  spellings and the `document_digest` rule are fixed here so RSI-04 only
  teaches the Manifest the sources. Stated in the spec and at the gate.
- **Risk:** First-revision capture touches live device state (identity
  files, local skill packages, resource files) through async services with
  many knobs; a wrong read records a wrong baseline.
  **Mitigation:** one narrow `BotStateReader` Protocol with a fake for every
  unit test; the production reader reuses the exact services the compose
  collector and the materialisers already read through; capture refuses
  rather than guesses (repo skills, non-ASCII paths, platform-managed
  teclaw) and names what it refused.
- **Risk:** `seq` races and double-recording under concurrent submissions.
  **Mitigation:** unique keys on `(bot, revision_id)` and `(bot, seq)`;
  content identity makes the duplicate path return the existing row; the seq
  collision retries inside the repository.
- **Risk:** The 1,000-line cap. `responses.py` (954), `authorization_rules.py`
  (944) and `admission_routes.py` (916) are near it.
  **Mitigation:** genome rows live in their own files merged into the
  tables, following the task/source-credentials precedents; the registry is
  split by responsibility from the start.
- **Risk:** Contract files copied into the package drift from
  `api-contracts/genome/v1/`.
  **Mitigation:** a test asserts byte equality of the three files.
- **Risk:** Reading a 100 MiB upload into memory on `PUT …/content`.
  **Mitigation:** the body is capped at the Manifest's per-entry fetch
  limit before it is read fully; the store already holds whole blobs.
- **Risk:** `tests/community/contracts/` changes need code-owner approval.
  **Mitigation:** called out in the PR description, as RSI-02 did.

## Alternatives Considered

- **Implement the Manifest content-store source in this item** so promotion
  works today. Rejected: it is RSI-04 item 1 by the work-items split, the
  registry PR is already large, and RSI-04 also needs the Center source,
  #2162 and #2862 before a compiled document applies faithfully; the seam
  makes the hand-off clean instead.
- **Compile to today's Manifest (inline `content`) for small bots.**
  Rejected: skills and resources cannot be inlined, and it would make the
  compiled document, and so the digest the apply report will carry, depend
  on the bot's size.
- **A git repository per bot (D-6).** Rejected per `01-genome.md` §9.2:
  tenancy, ACL and cross-bot queries; D-6 is closed by this plan as DB +
  content store, table names as above.
- **Typed dataclasses for `spec` and `policy`.** Rejected for v1: the
  contract is JSON and the schema already validates it; a second typed model
  would be a second definition to drift. Light accessors in `models.py`
  cover what the code reads.
- **Reading the contract JSON from `apps/backend/api-contracts/` at
  runtime.** Rejected: the wheel packages `src/` only; copies plus an
  equality test keep one source of truth checkable.
- **Letting the service bot path publish in this item.** Rejected: publish
  composes from the bot's state after apply, so it needs RSI-04 as much as
  the personal path does; refused as unsupported behind the same seam.

## Rollout

New tables (`create_all` in local/test; the DDL file for OceanBase), new
endpoints, no change to existing behaviour: a bot with no revisions answers
empty lists and 404s. The promotion endpoint refuses with `422 not_appliable`
until RSI-04 lands. No flag.

```bash
# order: DDL applied out of band on OceanBase before the deploy that mounts the router
cd apps/backend && DEPLOY_PROFILE=test uv run pytest tests/community/core/bot_genome tests/community/contracts/test_bot_genome_schema.py -q
```

## Test Strategy

```python
# tests/community/core/bot_genome/test_canonical.py, test_rewrite.py     — the contract vectors (moved from the contract test)
# tests/community/core/bot_genome/test_gene_paths.py                     — encode/decode, covers/overlaps, canonical refusals
# tests/community/core/bot_genome/test_text_edits.py                     — replace_section, insert_after, strict unified diff, description_only
# tests/community/core/bot_genome/test_patch_ops.py                      — every examples/patches/*.json against its base + blobs;
#                                                                           00-mixed gives 02-support-bot-candidate.json; each refusal code
# tests/community/core/bot_genome/test_policy_and_risk.py                — mutable/pins/allowed_genes, tier table, raises, overrides, replacement
# tests/community/core/bot_genome/test_spec_checks.py                    — examples/invalid cases that name a §6.4 predicate; MCP checks with fakes
# tests/community/core/bot_genome/test_diff.py                           — added/removed/modified, text diffs, tree members, tier
# tests/community/core/bot_genome/test_manifest_recorder.py              — examples/manifest-v1 cases through the real resolver over
#                                                                           FakeObjectStore + a scripted git client; refusals (01, 04)
# tests/community/core/bot_genome/test_capture.py                        — AGENTS.md block, rstrip rule, refusals, fill from parent
# tests/community/core/bot_genome/test_compile.py                        — compiled.yaml cases; document_digest stable under YAML formatting
# tests/community/core/bot_genome/test_registry_service.py               — in-memory SQLite repos: idempotent record, seq, refs CAS,
#                                                                           candidate numbering, reachability, applied_revision join
# tests/community/core/bot_genome/test_promotion_service.py              — order of steps with a fake applier; NotAppliable leaves refs;
#                                                                           drift rules per actor; apply_failed recording
# tests/community/repository/bot/test_genome_repository.py               — uniqueness, CAS rowcount semantics, seq retry
# tests/community/core/bot_config_manifest/content/test_content_store.py — store_produced + is_reachable
# tests/community/adapters/http/openapi_v1/test_genome_routes.py         — bars, admission, error envelopes, {rev:path} routing, octet-stream
# tests/community/adapters/http/openapi_v1/test_config_manifest_apply_bars.py — promotion row dominates category writes
# tests/community/di/test_bot_genome_module_wiring.py                    — the graph resolves both Protocols
# tests/community/contracts/test_bot_genome_schema.py                    — imports the core modules; recording/compile oracles use them;
#                                                                           contract copies equal api-contracts/genome/v1
# tests/community/architecture/test_service_api_conformance.py           — the two (Protocol, service) pairs registered
```
