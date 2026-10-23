# Plan: Genome Registry in Backend (RSI-03)

## Approach

Add one core package, `core/bot_genome/`, beside `core/bot_config_manifest/`.
It keeps revisions as the contract's JSON (validated against the RSI-02
schemas, hashed with the contract's canonical JSON) in new tables, and every
byte in the existing manifest content store. Revisions are recorded by
**capturing** the bot through the services the Manifest materialisers and
the compose collector already read through (identity files, capability
state, local skill package storage, resources, MCP, startup script), or by
applying a **patch** to a base revision. The checks apply runs before
writing are reused at record time (`SkillPackageValidator`,
`admission_refusal`, `legal_identity_types`, the MCP permission and override
checks), so a recorded revision is always appliable.

Promotion moves refs by CAS, records the promotion with the compiled
document (a complete, Manifest-shaped desired-state document kept on the
promotion row as canonical JSON, with its digest), and hands the compiled
revision to one narrow `RevisionApplier` seam. This item ships one
implementation of the seam, a placeholder whose preflight refuses every
promotion as `not_appliable` naming RSI-04, so no ref moves until delivery
exists. RSI-04 (Stage 2) replaces it with components that feed the apply
engine directly from the compiled revision; the registry does not change.
No Manifest document is written anywhere.

One additive contract extension rides along: shared-repository skills
(`git://…` memberships) get `origin: "repo"` with `repo_path` in
`genome-revision.schema.json`, so capture can record every bot.

Because apply reports carry no document digest yet, "which revision does
this bot run" joins the bot's last apply to the promotion that started it
(`apply_reference == apply_id`); the promotion row also stores the document
and its digest, so RSI-04 can switch the join without a migration.

## Affected Components

- `src/agentclaw/community/core/bot_genome/` (new) — contract loading,
  canonical JSON, gene paths, patch application, risk, diff, capture,
  compile, the registry and promotion services, repository models and DDL.
- `src/agentclaw/community/core/bot_config_manifest/content/` — gains
  `store_produced` and `is_reachable`.
- `api-contracts/genome/v1/genome-revision.schema.json`, `README.md` §4.2,
  `examples/` — `origin: repo` + `repo_path` on a skill entry (additive).
- `src/agentclaw/community/core/repository/{protocols,implementations}/bot/`
  — genome repository contracts and ORM; the content repository gains
  `has_digest`.
- `src/agentclaw/community/api/` — two new Protocol re-exports.
- `src/agentclaw/community/di/modules/bot_genome_module.py` (new) and
  `di/container.py`.
- `src/agentclaw/community/adapters/http/openapi_v1/` — `bots/genome.py`,
  `bots/schemas_genome.py`, `errors_genome.py`,
  `authorization_rules_genome.py`, `admission_genome_routes.py`, the mount
  in `__init__.py`.
- `src/agentclaw/community/core/schema.py` — model registration.
- `tests/community/contracts/` — the two reference modules move to core;
  `test_bot_genome_schema.py` imports them and uses the registry's compile
  as the `compiled.yaml` oracle (code-owner approval, as for RSI-02).
- `api-contracts/genome/v1/README.md` §7 and
  `manifest-v1-compatibility.md`; `docs/bot-evolution/01-genome.md` (+ zh-CN),
  `work-items.md` (+ zh-CN) — capture replaces document recording; D-6
  closed; RSI-03 status (RSI-04 is already redefined as direct delivery in
  this PR).

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
  `document_digest` char(71) NOT NULL COMMENT 'sha256 of JCS(compiled document)',
  `document_json` mediumtext NOT NULL COMMENT 'the compiled document handed to the delivery seam, canonical JSON',
  `going_back` tinyint(1) NOT NULL,
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
  `ac_manifest_content` for that bot and digest; captured, patched and
  uploaded bytes all file one through `store_produced`. No new table.

## API / Interface Changes

### Content store: produced bytes (01-genome.md §9.1 extension 1)

```python
# core/bot_config_manifest/content/service_protocol.py — new members
@abstractmethod
def store_produced(self, data: bytes, *, scope: ContentScope, origin: str,
                   modifier: str = "", category: Optional[str] = None,
                   entry_identity: Optional[str] = None) -> StoredContentRecord:
    """Persist bytes that were produced, not fetched. ``origin`` is a short label
    (``genome/capture``, ``genome/patch``, ``genome/upload``); the provenance row records
    ``produced://<origin>`` as both URLs."""

@abstractmethod
def is_reachable(self, scope: ContentScope, digest: str) -> bool:
    """A provenance row exists for this bot and digest."""
```

```python
# core/repository/protocols/bot/manifest_content.py — new member
@abstractmethod
def has_digest(self, *, env: str, entity_id: str, bot_id: str, digest: str) -> bool: ...
```

### Contract extension: shared-repository skills

```jsonc
// api-contracts/genome/v1/genome-revision.schema.json — skillEntry gains a third shape (additive, README §1)
{"name": "weather", "origin": "repo", "repo_path": "team/weather"}      // the path after git://; no digest, no version
// compile.py emits, for delivery (proposed spelling, fixed by the delivery item):
// {"name": "weather", "source": {"protocol": "repo", "path": "team/weather"}}
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
    async def capture(self, bot: BotRef, *, created_by: Provenance) -> RecordedRevision: ...
    @abstractmethod
    async def commit_patch(self, bot: BotRef, patch: dict, *, created_by: Provenance,
                           status: RevisionStatus, run: RunScope | None = None) -> RecordedRevision: ...
    @abstractmethod
    def set_policy(self, bot: BotRef, policy: dict, *, created_by: Provenance) -> RecordedRevision: ...
    @abstractmethod
    def set_status(self, bot: BotRef, revision_id: str, status: RevisionStatus,
                   *, actor: Provenance, reason: str) -> None: ...
    @abstractmethod
    def diff(self, bot: BotRef, rev: str, against: str) -> GenomeDiff: ...
    @abstractmethod
    def list_refs(self, bot: BotRef) -> list[GenomeRef]: ...
    @abstractmethod
    def move_ref(self, bot: BotRef, update: RefUpdate, *, actor: Provenance, mover: RefMover) -> GenomeRef: ...
    @abstractmethod
    def read_content(self, bot: BotRef, digest: str) -> bytes: ...
    @abstractmethod
    def put_content(self, bot: BotRef, data: bytes, *, provenance: Provenance) -> ContentReceipt: ...
    @abstractmethod
    def compile(self, bot: BotRef, rev: str) -> CompiledManifest: ...
    @abstractmethod
    def applied_revision(self, bot: BotRef) -> str | None: ...
```

`capture` and `commit_patch` are `async` because the state readers and the
MCP checks are async; the HTTP layer awaits them, the rest run in a
threadpool like the Manifest routes.

```python
# core/bot_genome/models.py (frozen dataclasses)
class RevisionStatus(StrEnum): DRAFT, CANDIDATE, ACCEPTED, REJECTED, PROMOTED, ARCHIVED
class ProvenanceKind(StrEnum): USER, STRATEGY_RUN
@dataclass(frozen=True) class BotRef: owner_id: str; bot_id: str; entity_id: str; engine_type: str; bot_type: str; entity_type: str
@dataclass(frozen=True) class Provenance: kind: ProvenanceKind; actor: str; run_id: str | None = None; step: str | None = None
@dataclass(frozen=True) class RunScope: run_id: str; binding_id: str; allowed_genes: tuple[str, ...]
@dataclass(frozen=True) class RevisionMetadata: id, seq, parent, created_by, created_at, status, patch_digest, evidence, evaluations, annotations
@dataclass(frozen=True) class GenomeRevision: genome_schema: int; revision: RevisionMetadata; spec: dict; policy: dict
@dataclass(frozen=True) class RecordedRevision: revision: GenomeRevision; created: bool; risk_tier: str | None; flags: tuple[str, ...]; ref: str | None
@dataclass(frozen=True) class GenomeRef: name: str; revision: str; moved_at: datetime; moved_by: Provenance; reason: str | None
@dataclass(frozen=True) class RefUpdate: name: str; revision: str; expected_revision: str | None; reason: str | None = None
class RefMover(StrEnum): OWNER = "owner"; PROMOTION = "promotion"; RUN = "run"
@dataclass(frozen=True) class GeneChange: gene: str; kind: str; diff: str | None
@dataclass(frozen=True) class GenomeDiff: revision: str; against: str; changes: tuple[GeneChange, ...]; risk_tier: str; rewrite_flags: tuple[str, ...]
@dataclass(frozen=True) class CompiledManifest: revision_id: str; document: dict; document_digest: str
```

```python
# core/bot_genome/errors.py — plain exceptions, mapped by the HTTP group (no DomainError)
class GenomeError(Exception): ...
class RevisionNotFound(GenomeError): ...
class RefConflict(GenomeError): ref: str; expected: str | None; current: str | None
class PatchRejected(GenomeError): reasons: tuple[Reason, ...]      # Reason(code, location, message); does_not_apply, …
class LimitExceeded(GenomeError): ...
class MoverNotAllowed(GenomeError): ...
class ContentNotReachable(GenomeError): ...
class CaptureRefused(GenomeError): reasons: tuple[Reason, ...]      # bad resource path, platform-managed teclaw
class Drifted(GenomeError): active: str; applied: str | None
class NotPromotable(GenomeError): ...
class NotAppliable(GenomeError): violations: tuple[Reason, ...]     # the delivery seam refused the compiled revision
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

# core/bot_genome/promotion/applier.py — the one seam to the delivery path (RSI-04 swaps the implementation)
class RevisionApplier(Protocol):
    @abstractmethod
    def preflight(self, bot: BotRef, compiled: CompiledManifest) -> None:
        """Raise NotAppliable / PromotionUnsupported before any ref moves."""
    @abstractmethod
    def start(self, bot: BotRef, compiled: CompiledManifest, *, actor: Actor, reason: str) -> ApplyResult: ...

class PendingDeliveryApplier(RevisionApplier):
    """This item's only implementation. ``preflight`` raises PromotionUnsupported for a service bot and
    NotAppliable("direct delivery lands with RSI-04") for everything else; ``start`` is never reached here.
    RSI-04 binds its direct-delivery applier in place of this class and nothing else changes."""
```

`promote` order (08-promotion.md §7.1, drift from §5.5): resolve target →
refuse `candidate` revisions that are not `accepted` (`NotPromotable`) →
compile → drift check (`Drifted` for non-owner actors only) →
`applier.preflight` → CAS move of `active` + `previous` in one transaction,
revision status `promoted` → promotion row `applying` with the document →
`applier.start` → row updated with the apply reference, or `apply_failed`
with the detail if starting failed (refs stay moved; the owner goes back by
promoting `previous`).

### HTTP (all under `/openapi/v1/bots/{bot_id}/genome`, `PublicAPIRoute`, `Envelope`)

| Method, path | Handler → service | Level | Status |
| --- | --- | --- | --- |
| `GET /revisions` | `list_revisions` | MEMBER | 200 `Page[RevisionSummary]` |
| `POST /revisions` | `commit_patch` | ADMIN | 201 `RevisionRecorded`; 200 when it already existed |
| `POST /revisions:capture` | `capture` | ADMIN | 201 `RevisionRecorded`; 200 when unchanged |
| `GET /revisions/{rev:path}/diff?against=` | `diff` | MEMBER | 200 `GenomeDiffPayload` |
| `GET /revisions/{rev:path}` | `get_revision` | MEMBER | 200 `GenomeRevisionPayload` |
| `PUT /policy` | `set_policy` | OWNER | 201 `RevisionRecorded`; 200 when unchanged |
| `GET /refs` | `list_refs` | MEMBER | 200 `{refs: […]}` |
| `PUT /refs/draft` | `move_ref(mover=OWNER)` | ADMIN | 200 `GenomeRefPayload` |
| `GET /content/{digest}` | `read_content` | MEMBER | 200 `application/octet-stream` |
| `PUT /content` | `put_content` | ADMIN | 201 `{digest, size}`; 200 if it existed |
| `POST /promotions` | `promote` | OWNER + EDIT_LOCK | 201 `PromotionPayload` |
| `GET /promotions` | `list_promotions` | MEMBER | 200 `Page[PromotionPayload]` |
| `GET /promotions/{promotion_id}` | `get_promotion` | MEMBER | 200 `PromotionPayload` (with the compiled document) |

The diff route is registered before the get route so `{rev:path}` (needed
for `candidate/<run>/<n>`) cannot swallow `/diff`; `:capture` is a literal
registered before `/revisions/{rev:path}`. Admission for every row is
`GRANT_CHECKED_ADDRESSED_BOT`. Promotion takes apply's own bar
(`test_config_manifest_apply_bars.py`); the policy is OWNER because it
bounds what automation may do to the bot.

```jsonc
// POST /openapi/v1/bots/bot_123/genome/revisions  (from a patch; patch.base is the base)
{"status": "candidate",
 "run": {"run_id": "run_7f3", "binding_id": "bind_01", "allowed_genes": ["persona", "skills"]},
 "patch": {"patch_schema": 1, "base": "sha256:a90b…", "ops": [...], "rationale": "…", "evidence": ["episode:ep_91"]}}
// POST /openapi/v1/bots/bot_123/genome/revisions:capture   (no body; parent = active, or none)
// PUT  /openapi/v1/bots/bot_123/genome/policy
{"mutable_genes": ["memory", "persona", "resources", "skills"], "pins": ["persona/SAFETY.md"], "risk_overrides": {}}
// 201 / 200 for all three
{"id": "sha256:7c1e…", "seq": 42, "status": "candidate", "parent": "sha256:a90b…",
 "patch_digest": "sha256:c41e…", "risk_tier": "T2", "flags": [], "ref": "candidate/run_7f3/1"}
```

Error mapping (`errors_genome.py`, merged into `ENVELOPE_ERRORS` like the
source-credentials rows; `data` carries the reasons or the current ref):

```python
GENOME_ENVELOPE_ERRORS = {
    RevisionNotFound: (404, "Not found"), ContentNotReachable: (404, "Not found"),
    RefConflict: (409, "Ref moved: re-read and decide"),         # data: {ref, expected_revision, current_revision}
    Drifted: (409, "Bot has drifted from its active revision"),
    PatchRejected: (422, "Patch rejected"),                      # data: {reasons: [{code, location, message}]}
    CaptureRefused: (422, "Current state cannot be captured"),
    NotAppliable: (422, "Revision cannot be delivered to this bot yet"),   # data: {reasons: [...]}
    NotPromotable: (422, "Revision is not promotable"),
    PromotionUnsupported: (422, "Promotion is not supported for this bot type"),
    LimitExceeded: (413, "Limit exceeded"), MoverNotAllowed: (403, "Forbidden"),
}
```

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
  compile.py                        # compile_spec(spec, content) → CompiledManifest (RSI-04 spellings), document_digest
  capture/
    __init__.py, capture.py         # BotStateReader Protocol + capture_spec(reader, bot) with normalisation and refusals
    bot_state.py                    # PlatformBotStateReader over IdentityFilePort, capability reader, local skill storage,
                                    # ResourceService/ResourceFileService, DirectActivation + MCPConfig, startup script
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
`SkillPackageValidator.validate_directory` in `spec_checks`, and store
`canonical_tree_bytes`; `resource.add` with a trailing-slash path requires a
`{digest}` of an `acm-tree-v1` blob; `memory.*` decode the items blob, edit
by key, re-encode as `canonicalize(items)`; memory ops on `memory: null`
are `PatchRejected(memory_disabled)`.

```python
# core/bot_genome/patch/risk.py
def op_tier(op: str, gene: str, *, description_only: bool, mode: str | None, rules: list[dict]) -> str
def patch_assessment(outcome: PatchOutcome, policy: dict, tiers: dict) -> tuple[str, tuple[str, ...]]:
    """Highest tier over ops after rewrite raises (measured once per file, base → result, README §6.3; replacement
    counts as rewrite) and policy.risk_overrides by overlap; returns (tier, rewrite-flagged gene paths)."""
```

```python
# core/bot_genome/capture/capture.py
@dataclass(frozen=True) class CapturedSkill: name: str; origin: str; center_version: str | None; repo_path: str | None; files: tuple[tuple[str, bytes], ...]
@dataclass(frozen=True) class CapturedResource: path: str; files: tuple[tuple[str, bytes], ...]   # one member for a file

class BotStateReader(Protocol):                          # narrow; one production implementation, fakes in tests
    async def persona_files(self, bot: BotRef) -> dict[str, str]: ...                 # type → text
    async def skills(self, bot: BotRef) -> tuple[CapturedSkill, ...]: ...
    async def resources(self, bot: BotRef) -> tuple[CapturedResource, ...]: ...
    def mcp_servers(self, bot: BotRef) -> tuple[dict, ...]: ...                        # {server_code, config?}
    def script_body(self, bot: BotRef) -> str | None: ...

async def capture_spec(reader: BotStateReader, bot: BotRef, *, validator: SkillPackageValidator) -> CapturedSpec:
    """README §7 normalisation: AGENTS.md reference block removed (IdentityService._remove_reference_section) and
    rstrip()+"\n"; local skill files through validate_directory; git:// skills → origin repo with repo_path;
    resource paths normalised; CaptureRefused names a resource path the Manifest rule rejects or a
    platform-managed teclaw bot. Returns the spec and the produced blobs (digest, bytes) to file."""
```

```python
# core/bot_genome/capture/bot_state.py — PlatformBotStateReader (device-delivered bots)
# persona:   IdentityFilePort.list_bot_files / read_identity_file   (core/ports/identity_file_port.py)
# skills:    BotCapabilityStateReaderProtocol.active_skill_assets  minus platform Default supply (mirror
#            apply/materialisers/skills.py:320 plan()); center:// → (skill_uuid, sc_version_number);
#            git://<path> → origin repo, repo_path=<path> (skill_set_service.py:1046);
#            local:// → SkillServiceFactory.local_skill_package_storage(...).read_package_files() (factories.py:438, :229)
# resources: ResourceService.list_resources + ResourceFileService.read_file / iter_directory_files
#            (services/resource_file_service.py:416, :450)
# mcp:       DirectActivationServiceProtocol.list_installed_mcps minus platform_default_mcp_codes; config from
#            MCPConfigServiceProtocol.get_bot_override (mcp_config_service_protocol.py:15)
# script:    BotStartupScriptServiceProtocol.get_body
```

```python
# core/bot_genome/compile.py
def compile_spec(spec: dict, content: ContentReader) -> CompiledManifest:
    """identity/skills/resources → {protocol: content_store, digest}; center skills → {protocol: center, version};
    repo skills → {protocol: repo, path}; mcp verbatim; script → {body: <stored body>} or null; memory omitted
    while null; no cli_tools/engine_config. document_digest = sha256_digest(canonicalize(document)).
    The contract's compiled.yaml oracles are compared as parsed values."""
```

The `document_digest` rule (digest of the canonical JSON of the compiled
document) is what RSI-04's apply report must record; stated in the README
and the compatibility doc so the two sides agree.

```python
# core/bot_genome/registry.py — GenomeRegistryService(GenomeRegistryProtocol)
def __init__(self, *, revisions, refs, patches, promotions, content: ManifestContentServiceProtocol,
             state_reader: Callable[[], BotStateReader], checks: SpecChecks, validator: SkillPackageValidator,
             applies: BotConfigManifestApplyServiceProtocol) -> None: ...

async def capture(self, bot, *, created_by) -> RecordedRevision:
    # capture_spec → policy of active (or the contract default) → spec_checks → store_produced each blob →
    # revision row (idempotent; parent = active or None) → RecordedRevision(created, risk_tier=None)

async def commit_patch(self, bot, patch, *, created_by, status, run=None) -> RecordedRevision:
    # schema → patch.base resolves (RevisionNotFound) → gene paths canonical → policy/allowed (PatchRejected) → apply_patch →
    # limits → spec_checks → risk → store_produced writes → patch row → revision row (idempotent) →
    # candidate ref for a run (next n, idempotent on (run, revision)) → RecordedRevision

def set_policy(self, bot, policy, *, created_by) -> RecordedRevision:
    # schema + canonical gene paths → revision with active's spec and the new policy, parent = active

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
    @singleton @provider
    def revision_applier(self) -> RevisionApplier:
        return PendingDeliveryApplier()          # RSI-04 rebinds this provider
    # repositories bound with binder.bind(Protocol, to=Impl, scope=singleton); the state reader is a thunk
    # (Callable[[], BotStateReader]) for the boot-walk reason manifest_delivery_module documents.
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
+# _compile is replaced by core/bot_genome/compile.compile_spec over the example blobs; the hand-written _record
+# oracle stays as the documented Manifest v1 → revision field mapping (manifest-v1-compatibility.md)
```

## Dependencies

None new. `jsonschema>=4.20` is already a runtime dependency
(`pyproject.toml:102`), and `referencing` ships with it; `pyyaml` is present.

## Risks & Mitigations

- **Risk:** Nothing can be delivered until RSI-04, so the item's
  acceptance story stops at the delivery hand-off.
  **Mitigation:** the seam refuses before any ref moves, so nothing is
  left half-promoted; the seam, the compiled document and the
  `document_digest` rule are fixed here so RSI-04 only binds its applier.
  Stated in the spec.
- **Risk:** Capture reads live device state (identity files, local skill
  packages, resource files) through async services; a wrong read records a
  wrong revision, and it needs the bot's device reachable.
  **Mitigation:** one narrow `BotStateReader` with a fake for every unit
  test; the production reader reuses the services the compose collector
  and materialisers already read through; capture refuses rather than
  guesses and names what it refused; an unreachable device is an ordinary
  error, not a partial capture.
- **Risk:** `seq` races and double-recording under concurrent submissions.
  **Mitigation:** unique keys on `(bot, revision_id)` and `(bot, seq)`;
  content identity makes the duplicate path return the existing row; the
  seq collision retries inside the repository.
- **Risk:** The 1,000-line cap. `responses.py` (954), `authorization_rules.py`
  (944) and `admission_routes.py` (916) are near it.
  **Mitigation:** genome rows live in their own files merged into the
  tables.
- **Risk:** The `origin: repo` extension touches the RSI-02 contract files.
  **Mitigation:** additive by README §1 (existing records unchanged); one
  example revision and the schema copy test cover it; owner sign-off on
  the PR.
- **Risk:** Contract copies in the package drift from `api-contracts/`.
  **Mitigation:** a test asserts byte equality of the three files.
- **Risk:** Reading a 100 MiB upload into memory on `PUT …/content`.
  **Mitigation:** the body is capped at the Manifest's per-entry fetch
  limit before it is read fully; the store already holds whole blobs.
- **Risk:** `tests/community/contracts/` changes need code-owner approval.
  **Mitigation:** called out in the PR description, as RSI-02 did.

## Alternatives Considered

- **Record a revision from a Manifest document** (the RSI-02 contract's
  §7, the first draft of this plan). Dropped after review: it needed a
  second recorder over the fetch pipeline, a `base` to fill omitted
  categories, and a provenance YAML copy, and it tied revisions to the
  Manifest for no gain; an owner who manages a bot through Manifest
  documents applies the document as today and captures the result.
- **Deliver through a generated Manifest document in this item** (save it
  as the bot's Manifest, or apply it through a new entry point). Dropped
  after review: RSI-04 is now direct delivery, so the Manifest grammar
  never gains the content-store, Center or repo sources and that path
  would never run; a placeholder applier that refuses is the honest
  Stage 1.
- **Refuse shared-repository skills at capture.** Rejected: many bots have
  them, and refusing would block their first revision; `origin: repo` is
  an additive extension of the contract.
- **A new delivery pipeline for Stage 2.** Rejected in favour of new
  components that feed the apply engine directly from a revision: the
  engine families, convergence, locking, the task queue and reports stay
  shared, which keeps ADR 0019's "no second delivery path".
- **A git repository per bot (D-6).** Rejected per `01-genome.md` §9.2;
  D-6 is closed by this plan as DB + content store.
- **Typed dataclasses for `spec` and `policy`.** Rejected for v1: the
  contract is JSON and the schema already validates it.
- **Reading the contract JSON from `apps/backend/api-contracts/` at
  runtime.** Rejected: the wheel packages `src/` only.
- **Service-bot publish in this item.** Rejected: publish composes from
  the bot's state after apply, so it needs RSI-04 as much as the personal
  path does; refused as unsupported behind the same seam.

## Rollout

New tables (`create_all` in local/test; the DDL file for OceanBase), new
endpoints, an additive contract extension, no change to existing
behaviour: a bot with no revisions answers empty lists and 404s. The
promotion endpoint refuses with `422 not_appliable` until RSI-04 (direct
delivery) lands. No flag.

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
# tests/community/core/bot_genome/test_capture.py                        — a fake reader seeded from the example revisions' blobs captures
#                                                                           exactly those specs; repo skills; AGENTS.md block, rstrip rule, refusals
# tests/community/core/bot_genome/test_bot_state.py                      — PlatformBotStateReader over fakes of each service, per category
# tests/community/core/bot_genome/test_compile.py                        — compiled.yaml cases; document_digest stable under YAML formatting
# tests/community/core/bot_genome/test_registry_service.py               — in-memory SQLite repos: idempotent record, seq, refs CAS,
#                                                                           candidate numbering, set_policy, reachability, applied_revision join
# tests/community/core/bot_genome/test_promotion_service.py              — order of steps with a fake applier; NotAppliable leaves refs;
#                                                                           drift rules per actor; apply_failed recording; document on the row;
#                                                                           PendingDeliveryApplier refuses every promotion naming RSI-04
# tests/community/repository/bot/test_genome_repository.py               — uniqueness, CAS rowcount semantics, seq retry
# tests/community/core/bot_config_manifest/content/test_content_store.py — store_produced + is_reachable
# tests/community/adapters/http/openapi_v1/test_genome_routes.py         — bars, admission, error envelopes, {rev:path} and :capture routing,
#                                                                           octet-stream
# tests/community/adapters/http/openapi_v1/test_config_manifest_apply_bars.py — promotion row dominates category writes
# tests/community/di/test_bot_genome_module_wiring.py                    — the graph resolves both Protocols
# tests/community/contracts/test_bot_genome_schema.py                    — imports the core modules; compile oracle uses compile_spec;
#                                                                           contract copies equal api-contracts/genome/v1
# tests/community/architecture/test_service_api_conformance.py           — the two (Protocol, service) pairs registered
```
