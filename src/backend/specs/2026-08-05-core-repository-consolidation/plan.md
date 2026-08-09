# Plan: Repository Consolidation into `core/repository/`

## Approach

Create two new packages — `core/repository/protocols/` (contracts, grouped one
module per domain) and `core/repository/implementations/` (bodies, one module per
repository) — and move all 43 implementation modules and 46 Protocols into them.
Every Protocol member gains `@abstractmethod`; every implementation declares its
Protocol(s) as base class(es), which is what makes the contract navigable in an
IDE and enforced at construction.

The load-bearing design rule is **directional**: `protocols/` carries *zero
runtime imports* from domain packages (`from __future__ import annotations` plus
a `TYPE_CHECKING` block), while domain services keep importing protocols at
runtime because `injector` resolves constructor annotations through
`typing.get_type_hints()` and needs the real class. That asymmetry is what keeps
the graph acyclic. A survey of all 24 protocol source files confirms it is
achievable with no exceptions: every domain import in them is annotation-only
today.

## Affected Components

- `src/backend/src/agentclaw/community/core/repository/` — **new**; the flat home
- `src/backend/src/agentclaw/community/plugins/` — loses 43 modules, keeps
  `local/`, `community/`, and `http_client.py`
- `src/backend/src/agentclaw/community/core/<21 domains>/` — lose their Protocol
  modules, gain a declared dependency on `core.repository.protocols`
- `src/backend/src/agentclaw/community/di/modules/` — 20 modules re-point imports
- `src/backend/src/agentclaw/community/plugin_api/local_skill_cleanup.py` —
  **deleted**; its Protocol is a repository contract, not a plugin contract
- `src/backend/tests/community/` — 108 test modules re-point; 49 relocate
- `src/backend/tests/community/architecture/` — path-keyed allowlists re-keyed
- `scripts/ci/singlebox_coverage_modules.yaml` — thresholds re-derived from CI

## Data Model Changes

**None.** No DDL, no migration, no `__tablename__` change. Five ORM model classes
relocate between Python modules; their tables and columns are untouched.

```text
plugins/local/sqlite_models.py
  EntityDeviceBinding            → core/devices/repository/models.py      (new)
  DefaultSkillsetMcpExclusion    → core/skill_center/orm.py               (new)
  DefaultSkillsetSkillExclusion  → core/skill_center/orm.py               (new)
plugins/local/system_config_models.py
  AcConfigCategory               → core/system_config/orm.py              (new)
  AcConfigItem                   → core/system_config/orm.py              (new)
```

Both source modules are deleted. They exist only to be imported for their side
effect of registering tables on the shared `Base`, so the SQLite bootstrap must
follow them or local mode starts with missing tables:

```diff
# src/agentclaw/community/plugins/local/database.py:150
-        import agentclaw.community.plugins.local.sqlite_models  # noqa: F401  ac_entity_device_binding
-        import agentclaw.community.plugins.local.system_config_models  # noqa: F401  ac_config_*
+        import agentclaw.community.core.devices.repository.models  # noqa: F401  ac_entity_device_binding
+        import agentclaw.community.core.skill_center.orm  # noqa: F401  ac_default_skillset_*
+        import agentclaw.community.core.system_config.orm  # noqa: F401  ac_config_*
```

`plugins/local/device_lifecycle.py` also imports `EntityDeviceBinding` and
re-points to the new path.

## API / Interface Changes

No HTTP surface changes. Two internal shapes change.

**1. Every Protocol member becomes abstract** (46 Protocols):

```diff
# core/repository/protocols/bot_management.py
+from abc import abstractmethod
+
 @runtime_checkable
 class BotRepository(Protocol):
+    @abstractmethod
     def get_by_id(self, bot_id: str) -> dict | None: ...
```

**2. Every implementation declares its Protocol(s)** (44 classes):

```diff
# core/repository/implementations/bot_repository.py
-class BotRepository:
+class BotRepository(BotRepositoryProtocol):
     @inject
     def __init__(self, database: DatabasePlugin) -> None: ...
```

Two classes take two Protocols; one takes four mixins plus two Protocols:

```python
# core/repository/implementations/skills_pool_layout_repository.py
class SkillsPoolLayoutRepository(
    SkillsPoolCapabilityRepositoryMixin,
    SkillsPoolOperationalRepositoryMixin,
    SkillsPoolPostCutoverRepositoryMixin,
    SkillsPoolQuarantineRepositoryMixin,
    SkillsPoolLayoutRepositoryProtocol,
    QuarantineRepositoryProtocol,
): ...
```

A mixin satisfying an abstract member is accepted — verified against Python 3.13.

#### Note: `SkillsPoolLayoutRepository` is one repository in five files

Read at face value that base list looks like a repository inheriting other
repositories. It is not. All four mixins are slices of *this same class* over
*one* table (`ac_bot_skill_layout_state`); none injects `DatabasePlugin`, none is
DI-bound, and none exists independently — which is why the spec classifies them as
non-repositories that relocate without contracts.

The split is a **size workaround, not a design**: the class totals ~1,856 lines and
the main file sits at exactly 1000 — the Rule 9 cap.

The *two Protocols* are legitimate interface segregation — `QuarantineRepositoryProtocol`
exposes 5 members to quarantine consumers, `SkillsPoolLayoutRepositoryProtocol`
exposes 27 — and DI binds the single class to both. The *four mixins* are weaker,
because their file seams cut across the contract seams:

| Method | Implemented in | Declared on |
| --- | --- | --- |
| `quarantine_identity_conflicts` | quarantine mixin | **Layout** protocol |
| `record_runtime_reconciliation` | quarantine mixin | **Layout** protocol |
| `list_states` | operational mixin | **Layout** protocol |
| `release_not_capable_claim` | capability mixin | **Layout** protocol |

Only 5 of the quarantine mixin's 8 public methods belong to the quarantine
contract. Files are cut by size; interfaces by concern; the cuts disagree.

**Carried as-is.** R8 forbids restructuring a repository body in this change, and a
genuine fix (decompose along the contract seam into two classes, or collapse the
size-driven mixins once the file can be shortened) is its own piece of work with
its own review.

Two things this change *does* do about it:

1. **Naming.** In a flat `implementations/` directory, four modules named
   `skills_pool_*_repository.py` that are not repositories is actively misleading.
   They are renamed to sort beside their composite and read as parts of it:

   ```text
   skills_pool_capability_repository.py   → skills_pool_layout_repository_capability.py
   skills_pool_operational_repository.py  → skills_pool_layout_repository_operational.py
   skills_pool_post_cutover_repository.py → skills_pool_layout_repository_post_cutover.py
   skills_pool_quarantine_repository.py   → skills_pool_layout_repository_quarantine.py
   ```

2. **Visibility.** Under R2 the composite must satisfy both Protocols' abstract
   members at construction, so any future mixin edit that drops a declared member
   fails immediately instead of at the call site.

**3. `BotChatDbRepository` becomes DI-bound** (R3b). It is the only wiring change:

```diff
# adapters/http/bot_chat/relation_router.py:61
-    db: DatabasePlugin = Injected(DatabasePlugin),
-):
-    repo = BotChatDbRepository(db)
+    repo: BotChatDbRepositoryProtocol = Injected(BotChatDbRepositoryProtocol),
+):
```

Same for `otel_router.py:361`. `core/bot_chat/service.py:327` takes it as an
injected constructor parameter instead of building one. Behaviourally identical —
the class holds nothing but its `DatabasePlugin`.

## Key Files & Functions

### Target layout and naming rules

```text
core/repository/
├── README.md                  # Context Boundary (Rule 22 + §8 "declared role")
├── protocols/                 # 22 modules, one per domain, 46 Protocols
│   ├── access.py              # PolicyRepository
│   ├── bot_chat.py            # 2 NEW Protocols (R3b)
│   ├── bot_management.py      # BotRepository, BotRestartLock…, Template…, RenderScreen…
│   ├── skill_center.py        # 7, incl. LocalSkillCleanupRepository (from plugin_api/)
│   └── …
└── implementations/           # 43 repository modules + 7 plain modules
    ├── bot_repository.py
    ├── governance_audit_repository.py
    ├── bot_chat_open_repository.py
    ├── skills_pool_capability_repository.py   # mixin, no Protocol
    └── …
```

Naming: implementations keep their current basename where it is already unique
(all 36 plugin-layer modules are). The 7 in-core modules are renamed on the way
in, because their current names only make sense inside their old directory:

```text
core/economy/governance/repositories/audit_repo.py       → governance_audit_repository.py
                                     notify_log_repo.py  → governance_notify_log_repository.py
                                     task_record_repo.py → governance_task_record_repository.py
                                     whitelist_repo.py   → governance_whitelist_repository.py
                                     task_record_query.py→ governance_task_record_query.py   (mixin)
core/bot_chat/repository/open.py                         → bot_chat_open_repository.py
core/bot_chat/repository/product.py                      → bot_chat_db_repository.py
core/common_config/repository/common_config_repository.py→ common_config_repository.py       (unchanged)
```

No class is renamed. `DeviceRepository` keeps its name despite implementing
`DeviceBindingRepository`.

### The cycle-avoidance rule, applied

```python
# core/repository/protocols/system_config.py
from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:                        # never imported at runtime
    from agentclaw.community.core.system_config.models import ConfigItemRecord
```

**The loop is closed by package initialization, not by the model.** A model class
is inert — it has no dependency on its domain's services. But importing *any*
submodule executes the parent package's `__init__.py` first, and that is where the
cycle lives:

```text
protocols/system_config.py
  imports core.system_config.models
    → Python executes core/system_config/__init__.py first
      → `from agentclaw.community.core.system_config.service import SystemConfigService`
        → service.py:17 `from …repository import ConfigRepositoryProtocol`
          → i.e. core.repository.protocols.system_config — the module still mid-import
            → ImportError: cannot import name from partially initialized module
```

Services must import Protocols at runtime (`injector` resolves constructor
annotations via `get_type_hints()`), so that leg cannot be made lazy. The
tractable leg is the other one.

Five domains have both halves today — an eager `__init__` *and* a Protocol that
imports from them: `bot_public`, `service_bot`, `session_resources`, `skills_pool`,
`system_config`. `bot_management` has neither (its `__init__.py` is a bare
docstring), so it would not cycle.

**The rule is nevertheless applied to all 22 protocol modules, not just the five.**
Auditing which domains cycle produces an answer that silently expires the next time
somebody adds an import to a domain `__init__.py` — the failure would then surface
as an ImportError at boot, far from the edit that caused it. A blanket "no runtime
domain imports in `protocols/`" costs nothing (all 24 protocol source files are
already annotation-only) and is checkable by a guard, which Task 13 adds.

The hazard predates this change: `core/channel/__init__.py` already carries a
`__getattr__` lazy-import hack commented *"Lazy import to avoid circular
dependencies at module load time."*

### Six co-located types that must be separated first

Protocol source files also define domain records and errors with 4–12 external
importers each. They are not contracts and must not enter `protocols/`, or they
drag runtime imports back out of the contract package:

```text
QualityTaskRecord               core/quality/repositories.py         → core/quality/models.py        (new)
ChannelRecord                   core/channel/services/repositories.py→ core/channel/models.py        (new)
CallerIdentityLockMismatchError core/caller_identity/repository.py   → core/caller_identity/contracts.py
CallerIdentityEngineChangedError            "                        → core/caller_identity/contracts.py
ActiveSkillSetReferenceError    core/skill_center/services/repositories.py → core/skill_center/errors.py
BotLookupAmbiguousError         core/bot_management/repository/protocol.py → core/bot_management/errors.py
```

Three targets already exist (`contracts.py`, `skill_center/errors.py`,
`bot_management/errors.py`); two are new.

### Non-repositories

```text
plugins/skills_pool_{capability,operational,post_cutover,quarantine}_repository.py
plugins/skills_pool_{layout_persistence,cutover_diagnostics}.py
core/economy/governance/repositories/task_record_query.py
        → core/repository/implementations/   (plain modules, no Protocol)

plugins/skills_pool_runtime.py  → core/skills_pool/runtime.py
        (transport client; its SkillsPoolRuntimeProtocol already lives in core/skills_pool/ports.py)

core/economy/governance/repositories/orm.py → core/economy/governance/orm.py
        (4 Base subclasses imported by 3 domain/ modules — domain-owned, not repository code)

plugins/http_client.py          → unchanged, genuine paired plugin
```

### Guard updates

```diff
# tests/community/architecture/test_no_oversized_modules.py:116
-    "plugins/skill_repository.py":
+    "core/repository/implementations/skill_repository.py":
         "~2423 lines — skill CRUD + market + install + parameters + members.",
```

`_ALLOWLIST` in `test_core_no_concrete_plugin_imports.py` stays empty — R7
removes the violations rather than suppressing them. 27 `ZDAS` docstring mentions
are rewritten to capability language ("the relational store", "prod OceanBase +
local SQLite") with no guard change.

```diff
# tests/community/architecture/test_module_boundaries.py:60
     "agentclaw.community.plugin_api",
+    "agentclaw.community.core.repository",
```

Each of the 21 affected domain READMEs gains one `internal_dependencies` entry:

```yaml
internal_dependencies:
  - agentclaw.community.core.repository.protocols
```

## Dependencies

**None.** `src/backend/pyproject.toml` is not modified. No new packages, no
version bumps.

## Risks & Mitigations

- **Risk:** an import cycle appears at runtime despite the TYPE_CHECKING rule
  (e.g. an implementation imports a domain package whose `__init__` reaches back).
  **Mitigation:** a guard test that imports every module under `core/repository/`
  in isolation, plus `build_injector()` on all four profiles. Both fail loudly.

- **Risk:** the `>1000`-line guard trips. `skills_pool_layout_repository.py` is at
  **exactly 1000** and `bot_repository.py` at 954.
  **Mitigation:** the new imports are net-neutral or shorter (sibling imports
  become package-absolute but replace equally long ones). Measure both files
  before commit; if either grows, shorten the import block — do not allowlist.

- **Risk:** singlebox `core_min_percent` breaches once Protocol and implementation
  lines leave the per-domain `core_paths`.
  **Mitigation:** land the move, read the CI figures, re-pin only what moved, each
  with a justification comment matching the existing `harness` 41.59→41.30→40.86
  precedent. Never trim `core_paths`.

- **Risk:** `SkillRepository` will not construct until R3 is resolved, and the
  drifted set grows on `dev`.
  **Mitigation:** re-derive the set at implementation time with the AST diff, not
  from the spec's list.

- **Risk:** the one-commit constraint plus ~200 files makes review hard and
  bisection coarse.
  **Mitigation:** accepted per R9 — `corp/ocb` imports these paths. The path map
  (R6) is the compensating control.

## Alternatives Considered

- **Per-domain subdirectories** (`core/repository/bot_management/…`) — closer to
  `arch.rules.md` §8's recommendation. Rejected: the spec's C2 records the flat
  layout as a deliberate accepted decision; §8 is Policy and says names are
  repository-specific.
- **One Protocol per module** (46 files) instead of one per domain (22).
  Rejected: it would split groups that are read together
  (`CollaboratorRepositoryProtocol` + its log and lock siblings) for no gain.
- **Allowlisting the `plugins/local/` ORM imports** instead of relocating the
  models. Rejected by R7 — the allowlist entries would encode the exact debt this
  change exists to remove.
- **Leaving Protocols in their domains and moving only implementations.** Rejected:
  it fixes IDE navigation but leaves the nine competing conventions in place, which
  is cost #3.
- **Adding `@abstractmethod` to `plugin_api/` Protocols too.** Out of scope — the
  user scoped this to repositories. Recorded here because the survey found the
  plugin layer has base classes without abstract members, and two impls
  (`SelfIssuedPassportPlugin`, `MockObjectStoragePlugin`) silently inherit `...`
  no-ops as a result.

## Rollout

One commit, no flag, no migration. Sequence within the commit:

```bash
# 1. separate co-located types + relocate ORM models (unblocks the layering guards)
# 2. create protocols/ with @abstractmethod; delete plugin_api/local_skill_cleanup.py
# 3. move implementations, add Protocol bases; resolve R3 drift
# 4. re-point DI, importers, guards, module READMEs; move tests
# 5. generate path-map.md
cd src/backend && .venv/bin/python -m pytest tests/community/architecture/ -q   # expect 120 passed
cd src/backend && .venv/bin/python -m pytest tests/community -q -x
```

The `corp/ocb` side is updated from `path-map.md` in lockstep; the broken window
is this one commit.

## Test Strategy

```python
# tests/community/architecture/test_repository_contracts.py  (new)
def test_every_repository_protocol_member_is_abstract():
    """Walk core/repository/protocols/; every public member carries @abstractmethod."""

def test_every_implementation_declares_its_protocol():
    """Walk core/repository/implementations/; each repository class has a Protocol base."""

def test_incomplete_implementation_fails_at_construction():
    """The teeth. A subclass omitting one member raises TypeError naming it."""
    class Incomplete(TaskQueueRepositoryProtocol):
        pass
    with pytest.raises(TypeError, match="abstract method"):
        Incomplete()

def test_protocols_have_no_runtime_domain_imports():
    """AST: every agentclaw import in protocols/ sits under `if TYPE_CHECKING:`."""
```

Existing suites carry the behaviour guarantee unchanged — 108 test modules move or
re-point but no case is rewritten:

```bash
pytest tests/community/architecture/          # 120 passed, no new allowlist entry
pytest tests/community/repository/            # relocated from tests/community/plugins/
pytest tests/community/core/economy/governance/  # in-core repos, unchanged assertions
pytest tests/community/di/                    # build_injector on every profile
```

R8 verification: `git show --stat` plus a body-level diff review confirming every
moved implementation differs only in its `import` block, its `class` line, and
neutralized vendor docstrings.
