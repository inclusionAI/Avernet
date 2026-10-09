# Dormant Passport Unfreeze One Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an authenticated internal endpoint that synchronously brings one Bot passport online without reading or changing Bot state or starting a container.

**Architecture:** The FastAPI adapter validates and authenticates the request, then delegates to `DormantOpsService`. The core service depends only on the existing `PassportPlugin` contract and calls `unfreeze_agent_passport`; no contract or plugin implementation changes are required.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, Injector, pytest, Ruff, uv.

## Global Constraints

- Endpoint: `POST /api/internal/dormant/unfreeze-passport-one`.
- Authentication: existing `verify_dormant_internal_token` Bearer dependency.
- Required non-blank request fields: `bot_id`, `owner_id`, `reason`.
- Only side effect: `PassportPlugin.unfreeze_agent_passport`.
- Do not query `BotModel`, change Bot status, start a container, or call `ActivateBotService`.
- Do not change `PassportPlugin` or any concrete Passport implementation.
- Do not log Bearer tokens or Passport credentials.

---

### Task 1: Core passport-only operation

**Files:**
- Modify: `src/agentclaw/community/core/bot_dormant/ops_service.py:7-24`
- Modify: `tests/community/core/bot_dormant/test_decide.py:208-337`
- Create: `tests/community/core/bot_dormant/test_ops_service.py`

**Interfaces:**
- Consumes: `PassportPlugin.unfreeze_agent_passport(bot_id: str, owner_workno: str, reason: str) -> None`.
- Produces: `DormantOpsService.unfreeze_passport_one(*, bot_id: str, owner_id: str, reason: str) -> dict`.

- [ ] **Step 1: Write the failing core tests**

Create `test_ops_service.py` with:

```python
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.bot_dormant.ops_service import DormantOpsService
from agentclaw.community.core.bot_dormant.service import DormantBotService
from agentclaw.community.plugin_api.passport import PassportPlugin


def test_unfreeze_passport_one_only_calls_passport() -> None:
    dormant_service = MagicMock(spec=DormantBotService)
    passport = MagicMock(spec=PassportPlugin)
    service = DormantOpsService(dormant_service, passport)

    result = service.unfreeze_passport_one(
        bot_id="default",
        owner_id="37565",
        reason="recover license",
    )

    assert result == {
        "bot_id": "default",
        "owner_id": "37565",
        "status": "passport_online",
    }
    passport.unfreeze_agent_passport.assert_called_once_with(
        bot_id="default",
        owner_workno="37565",
        reason="recover license",
    )
    assert dormant_service.mock_calls == []


def test_unfreeze_passport_one_propagates_passport_error() -> None:
    passport = MagicMock(spec=PassportPlugin)
    passport.unfreeze_agent_passport.side_effect = RuntimeError("passport unavailable")
    service = DormantOpsService(MagicMock(spec=DormantBotService), passport)

    with pytest.raises(RuntimeError, match="passport unavailable"):
        service.unfreeze_passport_one(
            bot_id="default",
            owner_id="37565",
            reason="recover license",
        )
```

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```bash
DEPLOY_PROFILE=test uv run pytest \
  tests/community/core/bot_dormant/test_ops_service.py -q -p no:cacheprovider
```

Expected: FAIL because `DormantOpsService.__init__` does not accept `PassportPlugin` and `unfreeze_passport_one` does not exist.

- [ ] **Step 3: Add the required plugin dependency and minimal method**

Update `ops_service.py` to import `PassportPlugin`, require it in the injected constructor, and add:

```python
def unfreeze_passport_one(
    self,
    *,
    bot_id: str,
    owner_id: str,
    reason: str,
) -> dict:
    logger.info(
        "[dormant.ops.unfreeze_passport_one] event=start "
        "bot_id=%s owner_id=%s reason=%s",
        bot_id,
        owner_id,
        reason,
    )
    try:
        self._passport.unfreeze_agent_passport(
            bot_id=bot_id,
            owner_workno=owner_id,
            reason=reason,
        )
    except Exception:
        logger.exception(
            "[dormant.ops.unfreeze_passport_one] event=failed "
            "bot_id=%s owner_id=%s reason=%s",
            bot_id,
            owner_id,
            reason,
        )
        raise
    logger.info(
        "[dormant.ops.unfreeze_passport_one] event=done "
        "bot_id=%s owner_id=%s reason=%s",
        bot_id,
        owner_id,
        reason,
    )
    return {
        "bot_id": bot_id,
        "owner_id": owner_id,
        "status": "passport_online",
    }
```

Update existing `DormantOpsService(...)` constructions in `test_decide.py` to pass a `MagicMock(spec=PassportPlugin)` as the second constructor argument. Do not change recycle assertions.

- [ ] **Step 4: Run core tests and verify GREEN**

Run:

```bash
DEPLOY_PROFILE=test uv run pytest \
  tests/community/core/bot_dormant/test_ops_service.py \
  tests/community/core/bot_dormant/test_decide.py -q -p no:cacheprovider
```

Expected: all selected tests PASS.

- [ ] **Step 5: Commit the core operation**

```bash
git add \
  src/agentclaw/community/core/bot_dormant/ops_service.py \
  tests/community/core/bot_dormant/test_ops_service.py \
  tests/community/core/bot_dormant/test_decide.py
git commit -m "feat: add passport-only dormant operation"
```

### Task 2: Authenticated HTTP endpoint

**Files:**
- Modify: `src/agentclaw/community/adapters/http/bot_dormant/schemas.py:1-72`
- Modify: `src/agentclaw/community/adapters/http/bot_dormant/router.py:84-218`
- Modify: `tests/community/core/bot_dormant/test_internal_endpoints.py:1-340`

**Interfaces:**
- Consumes: `DormantOpsService.unfreeze_passport_one(*, bot_id: str, owner_id: str, reason: str) -> dict` from Task 1.
- Produces: `POST /api/internal/dormant/unfreeze-passport-one` with the success and error semantics in `spec.md`.

- [ ] **Step 1: Write failing endpoint tests**

Add these tests:

```python
def test_unfreeze_passport_one_forwards_audit_reason() -> None:
    ops_svc = MagicMock(spec=DormantOpsService)
    ops_svc.unfreeze_passport_one.return_value = {
        "bot_id": "default",
        "owner_id": "37565",
        "status": "passport_online",
    }
    activate_svc = MagicMock(spec=ActivateBotService)
    client = _build_app(ops_svc=ops_svc, activate_svc=activate_svc)

    response = client.post(
        "/api/internal/dormant/unfreeze-passport-one",
        json={
            "bot_id": "default",
            "owner_id": "37565",
            "reason": "recover license",
        },
        headers={"Authorization": "Bearer test-tok"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "data": {
            "bot_id": "default",
            "owner_id": "37565",
            "status": "passport_online",
        },
    }
    ops_svc.unfreeze_passport_one.assert_called_once_with(
        bot_id="default",
        owner_id="37565",
        reason="recover license",
    )
    activate_svc.activate.assert_not_called()


def test_unfreeze_passport_one_returns_500_for_passport_error() -> None:
    ops_svc = MagicMock(spec=DormantOpsService)
    ops_svc.unfreeze_passport_one.side_effect = RuntimeError("passport unavailable")
    client = _build_app(ops_svc=ops_svc)

    response = client.post(
        "/api/internal/dormant/unfreeze-passport-one",
        json={"bot_id": "default", "owner_id": "37565", "reason": "recover"},
        headers={"Authorization": "Bearer test-tok"},
    )

    assert response.status_code == 500
    assert response.json()["detail"] == "passport unavailable"


@pytest.mark.parametrize("field", ["bot_id", "owner_id", "reason"])
def test_unfreeze_passport_one_rejects_blank_fields(field: str) -> None:
    payload = {"bot_id": "default", "owner_id": "37565", "reason": "recover"}
    payload[field] = "   "
    client = _build_app()

    response = client.post(
        "/api/internal/dormant/unfreeze-passport-one",
        json=payload,
        headers={"Authorization": "Bearer test-tok"},
    )

    assert response.status_code == 422
```

- [ ] **Step 2: Run endpoint tests and verify RED**

Run:

```bash
DEPLOY_PROFILE=test uv run pytest \
  tests/community/core/bot_dormant/test_internal_endpoints.py \
  -q -p no:cacheprovider
```

Expected: FAIL with HTTP 404 for the new route.

- [ ] **Step 3: Add schema and route implementation**

Use Pydantic v2 constrained strings:

```python
from typing import Annotated

from pydantic import BaseModel, StringConstraints

NonBlankString = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]


class OpsUnfreezePassportOneRequest(BaseModel):
    bot_id: NonBlankString
    owner_id: NonBlankString
    reason: NonBlankString
```

Import the schema in `router.py`, then add:

```python
@internal_router.post("/unfreeze-passport-one")
async def unfreeze_passport_one(
    body: OpsUnfreezePassportOneRequest,
    _: None = Depends(verify_dormant_internal_token),
    service: DormantOpsService = Injected(DormantOpsService),
) -> dict:
    """Ops-only helper: bring one Bot passport online without activation."""
    logger.info(
        "[dormant.ops.unfreeze_passport_one] request "
        "bot_id=%s owner_id=%s reason=%s",
        body.bot_id,
        body.owner_id,
        body.reason,
    )
    try:
        data = service.unfreeze_passport_one(
            bot_id=body.bot_id,
            owner_id=body.owner_id,
            reason=body.reason,
        )
        return {"ok": True, "data": data}
    except Exception as e:
        logger.exception(
            "[dormant.ops.unfreeze_passport_one] failed "
            "bot_id=%s owner_id=%s reason=%s",
            body.bot_id,
            body.owner_id,
            body.reason,
        )
        raise HTTPException(status_code=500, detail=str(e)) from e
```

- [ ] **Step 4: Run endpoint and dormant regression tests**

Run:

```bash
DEPLOY_PROFILE=test uv run pytest \
  tests/community/core/bot_dormant/test_internal_endpoints.py \
  tests/community/core/bot_dormant/test_ops_service.py \
  tests/community/core/bot_dormant/test_decide.py \
  tests/community/core/bot_dormant/test_activate.py \
  -q -p no:cacheprovider
```

Expected: all selected tests PASS.

- [ ] **Step 5: Commit the HTTP endpoint**

```bash
git add \
  src/agentclaw/community/adapters/http/bot_dormant/schemas.py \
  src/agentclaw/community/adapters/http/bot_dormant/router.py \
  tests/community/core/bot_dormant/test_internal_endpoints.py
git commit -m "feat: add dormant passport unfreeze endpoint"
```

### Task 3: Verification and publication

**Files:**
- Verify only; no planned production-file changes.

**Interfaces:**
- Consumes: completed Tasks 1 and 2.
- Produces: verified branch and a draft PR targeting `inclusionAI/Avernet:dev`.

- [ ] **Step 1: Run formatting and lint checks**

```bash
uv run ruff check \
  src/agentclaw/community/core/bot_dormant/ops_service.py \
  src/agentclaw/community/adapters/http/bot_dormant/schemas.py \
  src/agentclaw/community/adapters/http/bot_dormant/router.py \
  tests/community/core/bot_dormant/test_ops_service.py \
  tests/community/core/bot_dormant/test_decide.py \
  tests/community/core/bot_dormant/test_internal_endpoints.py
```

Expected: exit 0.

- [ ] **Step 2: Run the complete dormant test package**

```bash
DEPLOY_PROFILE=test uv run pytest \
  tests/community/core/bot_dormant -q -p no:cacheprovider
```

Expected: all tests PASS.

- [ ] **Step 3: Run the backend CI gate**

```bash
BACKEND_CI_BASE=origin/dev bash scripts/ci_test.sh
```

Expected: pytest, coverage, and changed-line coverage gates exit 0.

- [ ] **Step 4: Audit the final diff**

```bash
git diff --check origin/dev...HEAD
git status -sb
git log --oneline origin/dev..HEAD
```

Expected: clean worktree and only the design, plan, service, schema, router, and related tests in scope.

- [ ] **Step 5: Push and create the PR**

```bash
git push -u origin agent/dormant-unfreeze-passport-one
gh pr create \
  --repo inclusionAI/Avernet \
  --base dev \
  --head agent/dormant-unfreeze-passport-one \
  --draft \
  --title "feat(dormant): add passport-only unfreeze endpoint" \
  --body-file /tmp/avernet-dormant-unfreeze-passport-pr.md
```

Expected: a draft PR URL targeting `inclusionAI/Avernet:dev`.
