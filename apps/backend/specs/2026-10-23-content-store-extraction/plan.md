# Plan: Content store as its own component

Issue: https://github.com/inclusionAI/Avernet/issues/3064 (plan approved in the
issue thread on 2026-10-23: edit the DDL in place to 1100, rename the classes).

## Approach

Move `core/bot_config_manifest/content/` to a new core component
`core/content_store/` with `git mv`, so the bodies keep their history and the
diff is imports plus renames. The component also takes the two things the store
needs from the Manifest that are really its own: the `sha256:<hex64>` address
grammar (`DIGEST_RE`) and the acm-tree-v1 blob encoding
(`canonical_tree_bytes` / `decode_tree_bytes`). After the move the component
imports nothing from `bot_config_manifest` at runtime; the Manifest's fetch
package re-exports `DIGEST_RE` from the store so its twelve existing importers
are untouched. One constant, `MAX_AUDIT_ACTOR_CHARS = 1100`, becomes the width of
every audit-identity column the store and the genome write, and the provenance
DDL is edited in place to match (the table is not deployed yet). A new DI module
wires the component at the composition root.

## Affected Components

- `core/content_store/` (new) — the component: service, protocol, models,
  errors, settings, limits, tree encoding, DDL, README.
- `core/bot_config_manifest/` — consumer; loses `content/` and the tree
  functions; `fetch/limits.py` re-exports `DIGEST_RE`.
- `core/bot_genome/` — consumer; imports the store, `DIGEST_RE`, the tree
  encoding and `MAX_AUDIT_ACTOR_CHARS` from the new component.
- `core/repository/protocols/bot/`, `core/repository/implementations/bot/` —
  the renamed repository contract and body (stay in the `bot` domain).
- `di/modules/` — new `content_store_module.py`; bindings removed from
  `bot_management_module.py` and `manifest_fetch_module.py`; `container.py`.
- `core/schema.py` — the ORM side-effect import path.
- Tests under `tests/community/core/content_store/` (new),
  `tests/community/core/bot_config_manifest/`, `tests/community/core/bot_genome/`,
  `tests/community/repository/bot/`, `tests/community/architecture/`,
  `tests/community/framework/flow_coverage.py`, `tests/community/contracts/`.
- Docs: the three module READMEs (context boundaries), `core/repository/README.md`,
  `docs/bot-evolution/01-genome.md` (+ zh-CN).

## Data Model Changes

`ac_manifest_content.modifier` widens from 1024 to 1100. The DDL file moves
with the component and is edited in place (decision in the issue thread: the
table is not deployed to production yet, so no new numbered migration).

```sql
-- core/content_store/sql/2026_08_31_manifest_content.sql  (moved; one line changes)
-  `modifier`      varchar(1024) NOT NULL DEFAULT '' COMMENT '审计：触发拉取的身份',
+  -- 1100 is MAX_AUDIT_ACTOR_CHARS (content_store/limits.py): the width every audit
+  -- identity column the store and the Genome Registry write shares. It holds
+  -- `app:<bigint>:on-behalf-of:<1024-char user id>` (4 + 20 + 14 + 1024 = 1062).
+  `modifier`      varchar(1100) NOT NULL DEFAULT '' COMMENT '审计：触发拉取的身份',
```

The genome DDL (`bot_genome/sql/2026_10_19_bot_genome.sql`) already declares its
four actor columns as `varchar(1100)` and is not edited. The ORM columns on both
sides derive their width from the constant:

```python
# core/content_store/models.py
modifier = Column(String(MAX_AUDIT_ACTOR_CHARS), nullable=False, default="", comment="审计：触发拉取的身份")

# core/bot_genome/repository/models.py  (created_actor, moved_actor ×2, actor_id)
created_actor = Column(String(MAX_AUDIT_ACTOR_CHARS), nullable=False, comment=...)
```

## API / Interface Changes

Renames only; every method signature is unchanged. Import paths move.

```diff
# the store Service API
- agentclaw.community.core.bot_config_manifest.content.ManifestContentServiceProtocol
+ agentclaw.community.core.content_store.ContentStoreProtocol
- agentclaw.community.core.bot_config_manifest.content.ManifestContentService
+ agentclaw.community.core.content_store.ContentStoreService

# the repository contract and body
- agentclaw.community.core.repository.protocols.bot.ManifestContentRepositoryProtocol
+ agentclaw.community.core.repository.protocols.bot.ContentStoreRepositoryProtocol
- agentclaw.community.core.repository.implementations.bot.manifest_content.ManifestContentRepository
+ agentclaw.community.core.repository.implementations.bot.content_store.ContentStoreRepository

# unchanged names, new home
- agentclaw.community.core.bot_config_manifest.content.{ContentScope, StoredContentRecord, ManifestContentModel,
-   ContentStoreError, ContentMissingError, ContentStoreFault, ContentIntegrityError,
-   DEFAULT_CONTENT_STORE_DIR, content_store_root_from_config}
+ agentclaw.community.core.content_store.{same names}
- agentclaw.community.core.bot_config_manifest.apply.entry_delivery.{TREE_MAGIC, canonical_tree_bytes, decode_tree_bytes}
+ agentclaw.community.core.content_store.tree.{same names}      # also re-exported from the package
- agentclaw.community.core.bot_genome.limits.MAX_AUDIT_ACTOR_CHARS = 1024   (defined)
+ agentclaw.community.core.content_store.limits.MAX_AUDIT_ACTOR_CHARS = 1100  (bot_genome.limits re-exports it)
+ agentclaw.community.core.content_store.limits.DIGEST_RE                     (bot_config_manifest.fetch.limits re-exports it)
```

`ManifestContentModel` keeps its name: it is the ORM class of `ac_manifest_content`,
and the table name is out of scope. The yaml key
`user_config.bot_config_manifest.content_store_dir` and `cfg.BotConfigManifestConfig`
are unchanged.

## Key Files & Functions

### New component

```text
core/content_store/
├── README.md            # purpose, the two halves, retention, Context Boundary
├── __init__.py          # re-exports (below)
├── limits.py            # DIGEST_RE, MAX_AUDIT_ACTOR_CHARS
├── tree.py              # TREE_MAGIC, canonical_tree_bytes, decode_tree_bytes (moved verbatim from apply/entry_delivery.py:764-870)
├── errors.py            # git mv, unchanged
├── models.py            # git mv; String(MAX_AUDIT_ACTOR_CHARS)
├── settings.py          # git mv, unchanged
├── service_protocol.py  # git mv; class ContentStoreProtocol
├── service.py           # git mv; class ContentStoreService; FetchedObject under TYPE_CHECKING
└── sql/2026_08_31_manifest_content.sql   # git mv; modifier varchar(1100)
```

```python
# core/content_store/limits.py
DIGEST_RE = re.compile(r"\Asha256:[0-9a-f]{64}\Z")   # moved from fetch/limits.py:113 with its comment
MAX_AUDIT_ACTOR_CHARS = 1100
```

```diff
# core/content_store/service.py
-from agentclaw.community.core.bot_config_manifest.fetch.errors import FetchedObject
-from agentclaw.community.core.bot_config_manifest.fetch.limits import DIGEST_RE
+from agentclaw.community.core.content_store.limits import DIGEST_RE, MAX_AUDIT_ACTOR_CHARS
+if TYPE_CHECKING:
+    from agentclaw.community.core.bot_config_manifest.fetch.errors import FetchedObject
-_MODIFIER_MAX = 1024
+_MODIFIER_MAX = MAX_AUDIT_ACTOR_CHARS
-class ManifestContentService(ManifestContentServiceProtocol):
+class ContentStoreService(ContentStoreProtocol):
```

```python
# core/content_store/__init__.py
__all__ = [
    "ContentIntegrityError", "ContentMissingError", "ContentScope", "ContentStoreError", "ContentStoreFault",
    "ContentStoreProtocol", "ContentStoreService", "DEFAULT_CONTENT_STORE_DIR", "DIGEST_RE",
    "MAX_AUDIT_ACTOR_CHARS", "ManifestContentModel", "StoredContentRecord", "TREE_MAGIC",
    "canonical_tree_bytes", "content_store_root_from_config", "decode_tree_bytes",
]
```

### Repository

```diff
# core/repository/protocols/bot/manifest_content.py → content_store.py
-class ManifestContentRepositoryProtocol(Protocol):
+class ContentStoreRepositoryProtocol(Protocol):
# TYPE_CHECKING import of StoredContentRecord moves to core.content_store.models

# core/repository/implementations/bot/manifest_content.py → content_store.py
-class ManifestContentRepository(ManifestContentRepositoryProtocol):
+class ContentStoreRepository(ContentStoreRepositoryProtocol):

# core/repository/protocols/bot/__init__.py:35-37, :82 — import and __all__ entry renamed
# core/repository/README.md:134-135 — the two listed names
```

### Manifest

```diff
# core/bot_config_manifest/fetch/limits.py:113
-DIGEST_RE = re.compile(r"\Asha256:[0-9a-f]{64}\Z")
+from agentclaw.community.core.content_store.limits import DIGEST_RE  # noqa: F401  the store owns the address grammar

# core/bot_config_manifest/apply/entry_delivery.py
-from agentclaw.community.core.bot_config_manifest.content.errors import (ContentStoreError, ContentStoreFault)
-from agentclaw.community.core.bot_config_manifest.content.service_protocol import ManifestContentServiceProtocol
+from agentclaw.community.core.content_store import ContentStoreError, ContentStoreFault, ContentStoreProtocol
+from agentclaw.community.core.content_store.tree import decode_tree_bytes
# lines 764-870 (TREE_MAGIC, canonical_tree_bytes, decode_tree_bytes) removed; __all__ drops the three names

# apply/fetch_context.py:15, apply/source_fetchers.py:106-109, apply/source_resolver.py:84,
# apply/materialisers/resources.py:85-90 (canonical_tree_bytes from content_store.tree),
# cli_tools/service.py (unchanged: imports EntryFetchError only)
#   → import from agentclaw.community.core.content_store

# core/bot_config_manifest/README.md — §"The content store (W11)" becomes a short pointer to
# core/content_store/README.md; Context Boundary: drop the eight ManifestContent*/Content* entries from
# `provides`, reword the two `consumes` lines, add `agentclaw.community.core.content_store` to
# internal_dependencies.
```

### Genome

```diff
# core/bot_genome/limits.py
-MAX_AUDIT_ACTOR_CHARS = 1024
+from agentclaw.community.core.content_store.limits import MAX_AUDIT_ACTOR_CHARS  # the store's modifier and the actor columns share it

# core/bot_genome/registry.py:24-26
-from agentclaw.community.core.bot_config_manifest.content import ContentScope, ManifestContentServiceProtocol
-from agentclaw.community.core.bot_config_manifest.content.errors import ContentStoreError
-from agentclaw.community.core.bot_config_manifest.fetch.limits import DIGEST_RE
+from agentclaw.community.core.content_store import DIGEST_RE, ContentScope, ContentStoreError, ContentStoreProtocol

# canonical.py:26, capture/capture.py:23, diff.py:20, patch/ops.py:23, spec_checks.py:24
-from agentclaw.community.core.bot_config_manifest.apply.entry_delivery import canonical_tree_bytes, decode_tree_bytes
+from agentclaw.community.core.content_store.tree import canonical_tree_bytes, decode_tree_bytes

# repository/models.py:212,247,266,310 — String(1100) → String(MAX_AUDIT_ACTOR_CHARS)
# models.py:125-130 — docstring: "the content store's modifier column" wording stays; bound is the shared constant
# README.md:109-110,126,130 — consumes + internal_dependencies point at core.content_store
```

### Wiring

```python
# di/modules/content_store_module.py  (new)
class ContentStoreModule(Module):
    def configure(self, binder: Binder) -> None:
        binder.bind(ContentStoreRepositoryProtocol, to=ContentStoreRepository, scope=singleton)

    @singleton
    @provider
    @inject
    def content_store(
        self, repository: ContentStoreRepositoryProtocol, manifest_config: cfg.BotConfigManifestConfig
    ) -> ContentStoreProtocol:
        """Blob root from the typed config cluster; the yaml read stays in ManifestFetchModule."""
        return ContentStoreService(repository, Path(manifest_config.content_store_dir))
```

```diff
# di/container.py:192
         ManifestFetchModule(),
+        ContentStoreModule(),
# di/modules/bot_management_module.py:67,243-245,366-375 — import + ManifestContentRepository binding removed
# di/modules/manifest_fetch_module.py:51-56,163-165,285-296 — ManifestContentService import and the
#   manifest_content_service provider removed; manifest_entry_fetcher takes ContentStoreProtocol;
#   the bot_config_manifest provider imports content_store_root_from_config from the new package
# di/modules/bot_genome_module.py:25 — ContentStoreProtocol
# core/schema.py:55 — import agentclaw.community.core.content_store.models  # noqa: F401  ac_manifest_content
```

### Tests

```text
tests/community/core/content_store/__init__.py                    new
tests/community/core/content_store/test_content_store.py          git mv from core/bot_config_manifest/content/; imports + ContentStoreService
tests/community/core/content_store/test_tree.py                   the three acm-tree-v1 tests from apply/test_entry_delivery.py:272-296
tests/community/repository/bot/test_content_store_repository.py   git mv from test_manifest_content_repository.py; + DDL/ORM agreement test
tests/community/core/bot_config_manifest/test_manifest_ddl_contract.py
    _SQL_DIRS += _CORE / "content_store" / "sql"; _APPLICATION_SUPPLIED lookup resolves the file across _SQL_DIRS
tests/community/core/bot_config_manifest/apply/{_fakes,test_entry_delivery,test_resources_materialiser,test_source_resolver}.py
tests/community/core/bot_config_manifest/cli_tools/_fakes.py       (no change: imports BlobDelivery/GitDelivery only)
tests/community/core/bot_genome/{service_fixtures,test_registry_service,test_capture,test_diff,test_patch_ops,test_spec_checks}.py
tests/community/contracts/test_bot_genome_schema.py
tests/community/architecture/test_module_boundaries.py             BOUNDARY_SIGNIFICANT_MODULES += core.content_store
tests/community/framework/flow_coverage.py                         SINGLEBOX_E2E_EXEMPT["content_store"] (+ reason), reason-string path for bot_config_manifest
```

```python
# tests/community/repository/bot/test_content_store_repository.py  (added)
_DDL = Path(content_store_models.__file__).resolve().parent / "sql" / "2026_08_31_manifest_content.sql"

def test_ddl_columns_and_audit_width_match_the_orm() -> None:
    ddl = _ddl_columns()["ac_manifest_content"]            # {column: declaration}, same parser shape as test_genome_repository
    orm = ManifestContentModel.__table__.columns
    assert sorted(ddl) == sorted(c.name for c in orm)
    assert ddl["modifier"].startswith(f"varchar({MAX_AUDIT_ACTOR_CHARS})")
    assert orm["modifier"].type.length == MAX_AUDIT_ACTOR_CHARS
```

```diff
# tests/community/core/bot_genome/test_registry_service.py:531-537
-    """User ids are unbounded at the API boundary; the content store's ``modifier`` column is 1024 wide."""
-    long_user = "u-" + "x" * 1024
+    """User ids are unbounded at the API boundary; the audit columns are MAX_AUDIT_ACTOR_CHARS wide."""
+    long_user = "u-" + "x" * MAX_AUDIT_ACTOR_CHARS
     ...
-    assert Provenance(ProvenanceKind.USER, "u-" + "x" * 1000, recorded_as="app:a:on-behalf-of:u-" + "x" * 1000).audit
+    assert Provenance(ProvenanceKind.USER, "u-" + "x" * 1024, recorded_as="app:a:on-behalf-of:u-" + "x" * 1024).audit
+    # exactly the bound is recorded; one more is refused
```

## Testing Strategy

- Baseline before any move: the affected suites pass on the branch tip.
- After each task: the suites that import what moved. The full set at the end:

```bash
cd apps/backend
DEPLOY_PROFILE=test uv run pytest tests/community/core/content_store tests/community/core/bot_config_manifest \
  tests/community/core/bot_genome tests/community/repository/bot tests/community/architecture \
  tests/community/contracts/test_bot_genome_schema.py tests/community/e2e/test_genome_flows.py -q
./scripts/ci_test.sh
```

- The architecture tests are the extraction's own check: `test_module_boundaries`
  requires the new component's README to declare its boundary and fails if
  `bot_genome` or `bot_config_manifest` import something they do not declare;
  `test_e2e_module_coverage` requires the new core package to be either covered
  or exempt with a reason.
- `grep -rn "bot_config_manifest.content\|bot_config_manifest/content\|ManifestContentService\|ManifestContentRepository" apps/backend` must come back empty except historical specs.

## Risks & Mitigations

- **Import cycle** `content_store` ⇄ `bot_config_manifest.fetch`: the store imports
  `FetchedObject` under `TYPE_CHECKING` only and `fetch/limits.py` imports
  `content_store.limits` (which imports nothing of ours) at runtime. No cycle.
- **DDL contract test keyed by filename in one directory**: the lookup is changed
  to resolve across `_SQL_DIRS`, so the moved file is still covered by the
  DATETIME rule; `test_every_table_in_every_file_is_parsed` keeps
  `ac_manifest_content` in its expected set.
- **Historical specs** (`specs/2026-08-31-w11-content-store/`,
  `specs/2026-10-19-bot-genome-registry/`) keep their old paths: they record what
  was built then. Not edited.
- **`FakeManifestContent`** in `tests/community/core/bot_config_manifest/apply/_fakes.py`
  keeps its name: it is a test double of the Manifest's view of the store.
- **1000-line cap**: no touched file crosses it (`bot_management_module.py` at 996
  loses lines; `entry_delivery.py` loses ~110).
- **Config**: no key moves; a deployment overlay that sets
  `bot_config_manifest.content_store_dir` keeps working.

## Open Questions

None.
