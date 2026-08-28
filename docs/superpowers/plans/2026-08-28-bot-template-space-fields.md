# Bot Template & Space Fields Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface `template_type`, a security-projected `template_config`, and (on `/bots`) `space` on the two bot listing endpoints `GET /openapi/v1/bots` and `GET /openapi/v1/bots/all`.

**Architecture:** A pure allowlist projection in core strips secrets (`token`, `bot_template_config.ext_config` with its `enc:v1:` `thetaKey`) from `ac_templates.ext` before it reaches any public schema. The inventory service gains a template port and enriches only the **returned page slice** (the fan-out stays `attach_templates=False`). The flat bots listing resolves `space` per distinct `ac_bots.space_id` through the existing `BusinessSpaceContextProtocol` (memoized per page, same `BusinessSpace` schema shape as `/bots/all`). Two-layer model honored throughout: `engine` stays the real engine vocabulary; coding identity comes from `template_type` + projected `template_config`.

**Tech Stack:** Python / FastAPI / Pydantic v2 / injector DI / pytest. Contract file `src/gateway/configs/schemas/bots.openapi.json` edited by hand (never full-regen), mirrored to `src/gateway/tests/fixtures/bots.openapi.json` if the plain diff shows coupling.

**Base branch:** `origin/REL20260828` → dev branch `feat/bot-template-space-fields-REL20260828`.

**Evidence anchors** (read while planning, re-verify before editing — line numbers drift):
- `bot_service.py:_attach_template_configs_to_bots` — `template_config` is `ac_templates.ext`, batch-attached by bot_id, only for rows with `template_type`; `/bots` path default `attach_templates=True`.
- `strategy.py:53-54` — `_THETA_KEY_PATH = ("bot_template_config","ext_config","thetaKey")`, `_ENCRYPTED_VALUE_PREFIX = "enc:v1:"`; outer contract also allows a plain `token: str`.
- `bot_inventory_service.py:_list_cloud_rows` — fan-out passes `attach_templates=False` deliberately (comment at the call site).
- `router.py:_to_bot` (8 call sites), `list_bots`, `_to_inventory_item`; `schemas.py:BusinessSpace / BotInventoryItem / Bot`.

**Non-goals (explicit):**
- No query filter params (`template_type=`/flavor) — wait for a real consumer.
- No raw `template_config` on the wire — allowlist projection only; new keys enter via explicit projection additions plus security review.
- `/bots/{bot_id}` POST-create / restart-outcome responses carry the new fields as `null` except where the row already holds template data (fields are additive and Optional).
- No OpenAPI route additions — schemas only, so the endpoint coverage gate counts do not move.

---

### Task 1: Core template projection function (security seam)

**Files:**
- Create: `src/backend/src/agentclaw/community/core/bot_management/template_public_view.py`
- Test: `src/backend/tests/community/core/bot_management/test_template_public_view.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Allowlist projection of template_config for public responses.

``ac_templates.ext`` legitimately stores engine secrets (``bot_template_config
.ext_config.thetaKey`` is an ``enc:v1:`` blob, and the stable outer contract
allows a plain ``token``). The public listing faces must never echo them, and
engine-owned extensions must default to "not surfaced" rather than "passed
through" — this matrix pins that.
"""

from __future__ import annotations

import pytest

from agentclaw.community.core.bot_management.template_public_view import (
    project_template_config_for_public,
)


def test_none_in_gives_none_out():
    assert project_template_config_for_public(None) is None


def test_empty_dict_gives_none_out():
    assert project_template_config_for_public({}) is None


def test_allowlisted_keys_survive():
    config = {
        "devflow_workflow": "release-notes",
        "yuque_kb_repos": ["team/kb"],
        "code_repos": ["team/svc"],
        "template_key": "normalCC",
        "template_uid": "tpl-1",
    }
    assert project_template_config_for_public(config) == config


@pytest.mark.parametrize(
    "secret_config",
    [
        {"devflow_workflow": "w", "token": "Bearer raw-secret"},
        {
            "devflow_workflow": "w",
            "bot_template_config": {"ext_config": {"thetaKey": "enc:v1:deadbeef"}},
        },
        {"devflow_workflow": "w", "runtime": "codefuse"},
        {"devflow_workflow": "w", "anything_engine_owned": {"nested": "blob"}},
    ],
)
def test_everything_else_is_dropped(secret_config):
    projected = project_template_config_for_public(secret_config)
    assert projected == {"devflow_workflow": "w"}


def test_deep_copy_not_shared_with_input():
    config = {"devflow_workflow": "w", "yuque_kb_repos": ["a"]}
    projected = project_template_config_for_public(config)
    projected["yuque_kb_repos"].append("mutated")
    assert config["yuque_kb_repos"] == ["a"]
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `cd src/backend && python -m pytest tests/community/core/bot_management/test_template_public_view.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named '...template_public_view'`

- [ ] **Step 3: Write the implementation**

```python
"""Public projection of ``ac_templates.ext`` (``template_config``).

``template_config`` stored engine-side legitimately carries secrets: the
aicoding provisioning strategy persists ``bot_template_config.ext_config
.thetaKey`` as an ``enc:v1:`` ciphertext and the stable outer contract allows
a plain ``token``. Listing responses must never echo either — an encrypted
blob is still offline attack material and a replayable oracle.

Rules for this file:
- Allowlist, never denylist: engine-owned extensions surface only when a key
  is added here explicitly (plus security review). Default is "dropped".
- The result is a fresh shallow+container copy: callers may mutate it without
  aliasing the stored snapshot.
"""

from __future__ import annotations

from typing import Any, Mapping

#: Keys that are display-safe. Keep alphabetized.
_PUBLIC_TEMPLATE_KEYS = (
    "code_repos",
    "devflow_workflow",
    "template_key",
    "template_uid",
    "yuque_kb_repos",
)


def project_template_config_for_public(
    config: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Project a stored template snapshot onto its public display subset."""
    if not isinstance(config, Mapping) or not config:
        return None
    if not any(key in config for key in _PUBLIC_TEMPLATE_KEYS):
        return None
    projected: dict[str, Any] = {}
    for key in _PUBLIC_TEMPLATE_KEYS:
        if key in config:
            value = config[key]
            if isinstance(value, list):
                projected[key] = list(value)
            elif isinstance(value, dict):
                projected[key] = dict(value)
            else:
                projected[key] = value
    return projected
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `cd src/backend && python -m pytest tests/community/core/bot_management/test_template_public_view.py -v`
Expected: PASS (8-9 tests)

- [ ] **Step 5: Commit**

```bash
git add src/backend/src/agentclaw/community/core/bot_management/template_public_view.py \
        src/backend/tests/community/core/bot_management/test_template_public_view.py
git commit -m "feat(backend): add public projection for template_config"
```

---

### Task 2: Inventory core — item fields, template port, page-slice enrichment

**Files:**
- Modify: `src/backend/src/agentclaw/community/core/bot_inventory/types.py` (append two default fields to frozen `BotInventoryItem`)
- Modify: `src/backend/src/agentclaw/community/core/bot_inventory/protocols.py` (new `BotInventoryTemplatePort`)
- Create: `src/backend/src/agentclaw/community/core/bot_inventory/adapters/template_page.py` (port impl over `TemplateService`)
- Modify: `src/backend/src/agentclaw/community/core/bot_inventory/services/bot_inventory_service.py` (inject port; set `template_type` in `_build_item`; enrich the returned page slice)
- Modify: `src/backend/src/agentclaw/community/di/modules/bot_inventory_module.py` (bind port, pass into service)
- Test: `src/backend/tests/community/core/bot_inventory/services/test_bot_inventory_service.py` (extend)

- [ ] **Step 1: Write the failing service tests**

Add to `test_bot_inventory_service.py` (reuse the file's existing service-construction helpers; if they take keyword args, add `template_port=stub`):

```python
class _StubTemplatePort:
    def __init__(self, ext_by_bot_id: dict[str, dict] | None = None) -> None:
        self.calls: list[list[str]] = []
        self.ext_by_bot_id = ext_by_bot_id or {}

    def list_template_configs_by_bot_ids(self, bot_ids: list[str]) -> dict[str, dict]:
        self.calls.append(list(bot_ids))
        return {
            bot_id: ext
            for bot_id, ext in self.ext_by_bot_id.items()
            if bot_id in set(bot_ids)
        }


def _template_bot_row(bot_id: str, **extra):
    return {
        "bot_id": bot_id,
        "bot_name": f"Bot {bot_id}",
        "bot_type": "personal",
        "status": "ACTIVE",
        "active_engine": "claude_code",
        "template_type": "applicationCoding",
        "owner_id": "u1",
        **extra,
    }


def test_page_slice_attaches_projected_template_config(page_world):
    # Three template-backed bots, page_size=2 -> port sees only page 1's ids.
    stub = _StubTemplatePort(
        {
            "b1": {"devflow_workflow": "w1", "token": "secret"},
            "b2": {"template_key": "normalCC"},
            "b3": {"devflow_workflow": "w3"},
        }
    )
    service = page_world.build_service(template_port=stub)
    items, total = service.list_items(
        owner_id="u1", space=page_world.personal_space(),
        keyword=None, engine=None, deploy_mode=None, page=1, page_size=2,
    )
    assert total == 3
    assert len(items) == 2
    flat = stub.calls
    assert len(flat) == 1
    # Only the returned page's template-backed ids, no more.
    assert set(flat[0]) == {"b1", "b2"}
    assert items[0].template_config == {"devflow_workflow": "w1"}
    assert items[0].template_type == "applicationCoding"


def test_no_template_bots_in_page_means_no_port_call(page_world):
    stub = _StubTemplatePort()
    service = page_world.build_service(template_port=stub)
    items, _ = service.list_items(
        owner_id="u1", space=page_world.personal_space(),
        keyword=None, engine=None, deploy_mode=None, page=1, page_size=20,
    )
    assert items
    assert stub.calls == []


def test_template_config_absent_stays_none(page_world):
    stub = _StubTemplatePort({})  # template row missing for the bot
    service = page_world.build_service(template_port=stub)
    items, _ = service.list_items(
        owner_id="u1", space=page_world.personal_space(),
        keyword=None, engine=None, deploy_mode=None, page=1, page_size=20,
    )
    assert items[0].template_type == "applicationCoding"
    assert items[0].template_config is None
```

(If `page_world` does not exist in the file, build the equivalent from the existing fixtures: an `_InventoryWorld` helper constructing `BotInventoryService` with mocked `BotInventoryBotPort` returning the rows above, `NoopBusinessSpaceContext`, stub desktop/access/lifecycle ports. Follow the file's existing composition style.)

- [ ] **Step 2: Run tests, verify failure**

Run: `cd src/backend && python -m pytest tests/community/core/bot_inventory/services/test_bot_inventory_service.py -v -k template`
Expected: FAIL — `TypeError: ... unexpected keyword 'template_port'` (or AttributeError `template_config`)

- [ ] **Step 3: Implement core changes**

`types.py` — append to frozen `BotInventoryItem` AFTER `internal_status` (defaults keep every existing constructor call valid):

```python
    internal_status: str | None = None
    # Template identity (engine-neutral layer: engine stays engine, coding
    # identity lives in template_type + projected template_config).
    template_type: str | None = None
    template_config: Mapping[str, Any] | None = None
```

`protocols.py` — add next to the other ports:

```python
@runtime_checkable
class BotInventoryTemplatePort(Protocol):
    """Page-slice template-config reader for the inventory read model.

    The fan-out deliberately skips template attachment (cost); this port is
    called with ONLY the returned page's template-backed bot ids.
    """

    def list_template_configs_by_bot_ids(
        self, bot_ids: list[str]
    ) -> dict[str, dict[str, Any]]: ...
```

`adapters/template_page.py`:

```python
"""Bridge the inventory template port to the bot-management TemplateService."""

from __future__ import annotations

from typing import Any

from agentclaw.community.core.bot_management.services.template_service import (
    TemplateService,
)
from agentclaw.community.core.bot_inventory.protocols import (
    BotInventoryTemplatePort,
)


class TemplateServiceInventoryTemplatePort(BotInventoryTemplatePort):
    """Read ``ac_templates.ext`` by bot ids, best-effort like list attach."""

    def __init__(self, template_service: TemplateService) -> None:
        self._templates = template_service

    def list_template_configs_by_bot_ids(
        self, bot_ids: list[str]
    ) -> dict[str, dict[str, Any]]:
        if not bot_ids:
            return {}
        try:
            records = self._templates.list_templates_by_bot_ids(bot_ids)
        except Exception:  # best-effort: template trouble must not break a page
            return {}
        return {
            str(record.get("bot_id")): record.get("ext")
            for record in records
            if record.get("bot_id") is not None
            and isinstance(record.get("ext"), dict)
        }
```

`bot_inventory_service.py` — import `dataclasses.replace` and the projection fn; add `template_port` constructor kwarg; in `_build_item` set `template_type=_optional_str(row.get("template_type"))` among the constructor args; replace the tail of `list_items`:

```python
        total = len(cards)
        start = (page - 1) * page_size
        page_items = cards[start : start + page_size]
        page_items = self._attach_page_templates(page_items)
        return page_items, total

    def _attach_page_templates(
        self, items: list[BotInventoryItem]
    ) -> list[BotInventoryItem]:
        """Project template_config onto the returned page slice only.

        The fan-out intentionally runs with ``attach_templates=False``; this
        is the one place template snapshots enter the read model, and only
        for the page the caller will actually see.
        """
        bot_ids = [i.bot_id for i in items if i.template_type]
        if not bot_ids:
            return items
        ext_by_bot_id = self._templates_port.list_template_configs_by_bot_ids(
            bot_ids
        )
        enriched: list[BotInventoryItem] = []
        for item in items:
            ext = ext_by_bot_id.get(item.bot_id) if item.template_type else None
            projected = project_template_config_for_public(ext)
            if projected is None and item.template_config is None and (
                ext is None
            ):
                enriched.append(item)
                continue
            enriched.append(
                replace(item, template_config=projected)
            )
        return enriched
```

(Import `project_template_config_for_public` from `core/bot_management/template_public_view` — direction bot_inventory→bot_management is already dependency-safe: the DI module wires `BotService` into the inventory the same way.)

`di/modules/bot_inventory_module.py` — provider + constructor arg:

```python
    @singleton
    @provider
    @inject
    def inventory_template_port(
        self, template_service: TemplateService
    ) -> BotInventoryTemplatePort:
        return TemplateServiceInventoryTemplatePort(template_service)
```

and add `template_port: BotInventoryTemplatePort` to the `bot_inventory_service` provider parameters, passing `template_port=template_port` into the constructor. Import `TemplateService` from bot_management (same import shape as the existing `BotService` import in this module) and the new port/impl classes.

- [ ] **Step 4: Run tests, verify pass**

Run: `cd src/backend && python -m pytest tests/community/core/bot_inventory/ -q`
Expected: PASS (whole inventory suite green — no behavior change when no template data)

- [ ] **Step 5: Commit**

```bash
git add src/backend/src/agentclaw/community/core/bot_inventory/ \
        src/backend/src/agentclaw/community/di/modules/bot_inventory_module.py \
        src/backend/tests/community/core/bot_inventory/services/test_bot_inventory_service.py
git commit -m "feat(backend): page-slice template fields on the inventory read model"
```

---

### Task 3: Public schemas + `/bots/all` wire mapping

**Files:**
- Modify: `src/backend/src/agentclaw/community/adapters/http/openapi_v1/bots/schemas.py` (`BotInventoryItem` + 2 fields; `Bot` + 3 fields — Bot part lands here, used by Task 4)
- Modify: `src/backend/src/agentclaw/community/adapters/http/openapi_v1/bots/router.py` (`_to_inventory_item` + 2 fields)
- Test: `src/backend/tests/community/adapters/http/openapi_v1/inventory/test_inventory_handlers.py`

- [ ] **Step 1: Write the failing handler test**

In `test_inventory_handlers.py`, extend the bot row fixture used by the list case with a `template_type` and mock the new port binding. In the app fixture's binder add (stub port):

```python
class _TemplatePortStub:
    def list_template_configs_by_bot_ids(self, bot_ids):
        return {
            "b1": {
                "devflow_workflow": "wf",
                "token": "must-not-leak",
                "bot_template_config": {"ext_config": {"thetaKey": "enc:v1:x"}},
            }
        }
```

and `binder.bind(BotInventoryTemplatePort, to=_TemplatePortStub())`. Test:

```python
def test_list_inventory_carries_template_fields(client):
    data = _ok(client.get("/openapi/v1/bots/all"))
    item = data["items"][0]
    assert item["template_type"] == "applicationCoding"
    assert item["template_config"] == {"devflow_workflow": "wf"}
    assert "token" not in item["template_config"]
    assert "bot_template_config" not in item["template_config"]
```

(The bot row fixture needs `"template_type": "applicationCoding"` added for this to hold.)

- [ ] **Step 2: Run, verify failure**

Run: `cd src/backend && python -m pytest tests/community/adapters/http/openapi_v1/inventory/test_inventory_handlers.py -v`
Expected: FAIL — `KeyError: 'template_type'`

- [ ] **Step 3: Implement schema + mapping**

`schemas.py` — `BotInventoryItem`, after `space` field:

```python
    template_type: str | None = Field(
        default=None,
        description="Template type of the bot, e.g. 'applicationCoding'; "
        "null for bots created without a template.",
    )
    template_config: dict | None = Field(
        default=None,
        description="Server-projected template snapshot (display-safe subset; "
        "secrets never returned). Null without a template.",
    )
```

`schemas.py` — `Bot`, after `owner_entity_id`:

```python
    template_type: str | None = Field(
        default=None,
        description="Template type of the bot, e.g. 'applicationCoding'; "
        "null for bots created without a template.",
    )
    template_config: dict | None = Field(
        default=None,
        description="Server-projected template snapshot (display-safe subset; "
        "secrets never returned). Null without a template.",
    )
    space: BusinessSpace | None = Field(
        default=None,
        description="Business space the bot's record is assigned to "
        "(owner view). Populated on the listing endpoints.",
    )
```

(Update the `Bot` model's `json_schema_extra` example with the three keys' null/simple values to keep the example representative.)

`router.py` — `_to_inventory_item` gains:

```python
        template_type=item.template_type,
        template_config=dict(item.template_config) if item.template_config else None,
```

- [ ] **Step 4: Run handler + endpoint suites, verify pass**

Run: `cd src/backend && python -m pytest tests/community/adapters/http/openapi_v1/inventory/ tests/community/endpoints/test_openapi_bots.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/backend/src/agentclaw/community/adapters/http/openapi_v1/bots/schemas.py \
        src/backend/src/agentclaw/community/adapters/http/openapi_v1/bots/router.py \
        src/backend/tests/community/adapters/http/openapi_v1/inventory/test_inventory_handlers.py
git commit -m "feat(backend): expose template fields on the /bots/all card surface"
```

---

### Task 4: `/bots` — template projection + per-page space resolution

**Files:**
- Modify: `src/backend/src/agentclaw/community/adapters/http/openapi_v1/bots/router.py` (`_to_bot` + `list_bots` + optional single-row helper for `GET /{bot_id}`)
- Test: `src/backend/tests/community/adapters/http/openapi_v1/test_bots_endpoints.py`

- [ ] **Step 1: Write the failing handler tests**

In `test_bots_endpoints.py`, extend the `BOT` fixture dict:

```python
    "template_type": "applicationCoding",
    "template_config": {
        "devflow_workflow": "release-notes",
        "token": "must-not-leak",
        "bot_template_config": {"ext_config": {"thetaKey": "enc:v1:x"}},
        "runtime": "codefuse",
    },
    "space_id": None,
```

Add (the client fixture already binds `BusinessSpaceContextProtocol` to `NoopBusinessSpaceContext`, whose `bot_space` falls back to the synthetic personal space for a NULL `space_id`):

```python
def test_list_bots_carries_template_projection_and_space(client):
    data = _ok(client.get("/openapi/v1/bots"))
    item = data["items"][0]
    assert item["template_type"] == "applicationCoding"
    assert item["template_config"] == {"devflow_workflow": "release-notes"}
    assert "token" not in item["template_config"]
    assert "runtime" not in item["template_config"]
    space = item["space"]
    assert space["kind"] == "personal"
    assert space["space_id"].startswith("personal:")
```

- [ ] **Step 2: Run, verify failure**

Run: `cd src/backend && python -m pytest tests/community/adapters/http/openapi_v1/test_bots_endpoints.py -v -k template`
Expected: FAIL — `KeyError: 'template_type'`

- [ ] **Step 3: Implement**

`router.py` — imports and helpers:

```python
from agentclaw.community.core.bot_management.template_public_view import (
    project_template_config_for_public,
)
```

`_to_bot` gains keyword-only args (existing 7 call sites stay valid — they pass `d` positionally and get `template_type`/`template_config` from the row, `space=None`):

```python
def _to_bot(d: dict[str, Any], *, space: dict[str, Any] | None = None) -> Bot:
    """Adapt an internal bot ``to_dict()`` record to the public ``Bot`` schema.

    ``template_config`` on the row is the stored engine snapshot (may carry
    secrets), projected onto the display-safe subset here; ``space`` is
    resolved by the listing endpoints and passed in by the caller.
    """
    engine = d.get("active_engine") or ""
    return Bot(
        bot_id=d["bot_id"],
        bot_name=d.get("bot_name") or "",
        bot_desc=d.get("bot_desc") or "",
        engine=engine,
        cluster_name=cluster_for_engine(engine),
        bot_type=d.get("bot_type") or "",
        status=d.get("status") or "",
        owner_entity_id=d.get("owner_id") or "",
        template_type=_optional_bot_str(d.get("template_type")),
        template_config=project_template_config_for_public(
            d.get("template_config")
        ),
        space=space,
    )


def _optional_bot_str(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


def _to_public_space(ref: Any) -> dict[str, Any] | None:
    if ref is None:
        return None
    return {"space_id": ref.space_id, "name": ref.name, "kind": ref.kind}


def _resolve_row_spaces(
    space_context: BusinessSpaceContextProtocol,
    rows: list[dict[str, Any]],
    *,
    owner_id: str,
) -> dict[str, dict[str, Any] | None]:
    """Owner-view space summary per distinct ``ac_bots.space_id``.

    Memoized on the raw space_id: one ``bot_space`` resolution per distinct
    space per page (the protocol's ``bot_space`` already folds the synthetic
    ``personal:<user>`` fallback and the member-gated None into one call —
    this is plain memo caching, not policy, which stays in the core port).
    """
    resolved: dict[str, dict[str, Any] | None] = {}
    for row in rows:
        raw = str(row.get("space_id") or "")
        if raw in resolved:
            continue
        resolved[raw] = _to_public_space(
            space_context.bot_space(bot=row, owner_id=owner_id, current_space=None)
        )
    return resolved
```

`list_bots` handler — add the injection and mapping:

```python
    space_context: BusinessSpaceContextProtocol = Injected(
        BusinessSpaceContextProtocol
    ),
```

and replace the tail:

```python
    rows = result["items"]
    row_spaces = _resolve_row_spaces(space_context, rows, owner_id=owner_id)
    items = [
        _to_bot(b, space=row_spaces.get(str(b.get("space_id") or "")))
        for b in rows
    ]
    return page(result["total"], items, request)
```

`BusinessSpaceContextProtocol` import: `from agentclaw.community.core.bot_inventory.protocols import BusinessSpaceContextProtocol` (the inventory handler in the same file already injects it — reuse that import).

- [ ] **Step 4: Run the full bots handler suite + endpoint suite**

Run: `cd src/backend && python -m pytest tests/community/adapters/http/openapi_v1/test_bots_endpoints.py tests/community/endpoints/test_openapi_bots.py -q`
Expected: PASS (all 8 `_to_bot` call sites still construct — new fields default populated from row data)

- [ ] **Step 5: Commit**

```bash
git add src/backend/src/agentclaw/community/adapters/http/openapi_v1/bots/router.py \
        src/backend/tests/community/adapters/http/openapi_v1/test_bots_endpoints.py
git commit -m "feat(backend): template projection and per-page space on GET /openapi/v1/bots"
```

---

### Task 5: Endpoint framework pins (wire-level, real DI graph)

**Files:**
- Modify: `src/backend/tests/community/endpoints/test_openapi_bots.py`

- [ ] **Step 1: Extend the happy-body pins**

`_HAPPY_BODIES` — `GET /openapi/v1/bots` row gains (seeded bot has no template rows, no space_id; the real `SpaceServiceBotSpaceContext` falls back to the synthetic personal space; `batch_query_personal` finds no row):

```python
    ("GET", _BASE_PATH): {
        "data": {
            "total": 1,
            "items": [
                {
                    "bot_id": _BOT_ID,
                    "owner_entity_id": _OWNER,
                    "template_type": None,
                    "template_config": None,
                    "space": {
                        "space_id": f"personal:{_OWNER}",
                        "name": "Personal",
                        "kind": "personal",
                    },
                }
            ],
        }
    },
```

and `GET /openapi/v1/bots/all` row gains `"template_type": None, "template_config": None` inside the pinned item dict.

(If the synthetic personal ref's `name` differs in the noop implementation, pin the observed value — the point is the shape and kind; check `NoopBusinessSpaceContext._personal`.)

- [ ] **Step 2: Run the endpoint suite and the enrollment gate**

Run: `cd src/backend && python -m pytest tests/community/endpoints/test_openapi_bots.py tests/community/endpoints -q -k "openapi_bots or coverage or baseline"`
Expected: PASS with no baseline drift (no route added → counts unchanged; field pins now enforced on the wire)

- [ ] **Step 3: Commit**

```bash
git add src/backend/tests/community/endpoints/test_openapi_bots.py
git commit -m "test(backend): pin template and space fields on the bots wire cases"
```

---

### Task 6: Contract file — `bots.openapi.json` (manual, schema-only)

**Files:**
- Modify: `src/gateway/configs/schemas/bots.openapi.json` (`components.schemas.Bot`, `components.schemas.BotInventoryItem`)
- Modify (if the plain diff shows coupling): `src/gateway/tests/fixtures/bots.openapi.json`

- [ ] **Step 1: Edit `components.schemas.Bot`**

Add to `properties` (match the file's existing `$ref`/field style — copy the surrounding indentation; descriptions mirror the pydantic Fields from Task 3):

```json
"template_type": {
  "anyOf": [
    {"type": "string"},
    {"type": "null"}
  ],
  "description": "Template type of the bot, e.g. 'applicationCoding'; null for bots created without a template."
},
"template_config": {
  "anyOf": [
    {"type": "object", "additionalProperties": true},
    {"type": "null"}
  ],
  "description": "Server-projected template snapshot (display-safe subset; secrets never returned). Null without a template."
},
"space": {
  "anyOf": [
    {"$ref": "#/components/schemas/BusinessSpace"},
    {"type": "null"}
  ],
  "description": "Business space the bot's record is assigned to (owner view). Populated on the listing endpoints."
}
```

- [ ] **Step 2: Edit `components.schemas.BotInventoryItem`**

Add `template_type` and `template_config` with the same shapes (no `space` — already present).

- [ ] **Step 3: Validate json + run any contract tests**

Run:
```bash
python3 -m json.tool src/gateway/configs/schemas/bots.openapi.json > /dev/null && echo OK
cd src/backend && python -m pytest tests/community/contracts -q
```
Expected: `OK`; contracts green. If a schema-snapshot test asserts `Bot`, regenerate **that one snapshot file only** via the mechanism the test itself documents (never a full regen of bots.openapi.json), and if the gateway fixture copy Diff-checks equality, mirror the same two schema edits there.

- [ ] **Step 4: Commit**

```bash
git add src/gateway/configs/schemas/bots.openapi.json \
        src/gateway/tests/fixtures/bots.openapi.json \
        src/backend/tests/community/contracts
git commit -m "feat(gateway): publish template and space fields in bots.openapi.json"
```

---

### Task 7: Full gate + batched final review

- [ ] **Step 1: Coverage gate locally**

Run: `bash src/backend/scripts/ci_test.sh` (per repo memory: changed-line coverage ≥80% is only reproducible here; pre-push is lint-only)
Expected: green, changed-line coverage ≥80%

- [ ] **Step 2: Batched review (per user preference — no per-task reviews)**

One comprehensive quality review at the end: security (projection matrix on the wire), DI wiring across every app that mounts the bots router, contract-file consistency, plan-vs-code drift. Run `/code-review` on the diff before pushing.

- [ ] **Step 3: Push**

```bash
git push -u origin feat/bot-template-space-fields-REL20260828
```

Then (deploy-side, not in this repo's PR): mirror `bots.openapi.json` to the ocb repo gateway configs — per the dual-write convention.

---

## Self-Review notes

- Spec coverage: `/bots/all` template fields (Task 2+3), `/bots` template + space (Task 4), security projection (Task 1, enforced at every mapping site), OpenAPI contract (Task 6), endpoint gate (Task 5 — no baseline movement), coverage (Task 7).
- Type consistency: `BotInventoryTemplatePort.list_template_configs_by_bot_ids(list[str]) -> dict[str, dict]` used in adapter, service, DI, and both test stubs; `project_template_config_for_public(Mapping|None) -> dict|None` referenced in router + service + port-adjacent tests; `BusinessSpace` shape `{space_id,str name,str kind}` identical in `/bots` mapping and `/bots/all` schema.
- No placeholders: every code step carries the actual content; the only adaptive spots (test fixture naming in Task 2, snapshot regen in Task 6) name the exact mechanism to reuse with fallback instructions.
