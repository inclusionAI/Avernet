# Dormant Bot Owner-Level Protection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an environment-scoped owner protection list in `ac_common_config` so automatic dormant scans and `external_input` skip every Bot owned by a protected owner, while explicit `recycle-one` operations remain available.

**Architecture:** Extend the existing `CommonWhiteListService` with one bulk owner-list reader that returns a normalized immutable set. `DormantBotService` loads that set once per run, filters internal candidates before `/alive`, and passes the same set into `external_input`; `DormantOpsService` remains outside this check. A small scan-window helper extraction keeps `service.py` below the repository's 1000-line architecture limit.

**Tech Stack:** Python 3.12, FastAPI/Injector, SQLAlchemy, pytest/pytest-asyncio, `ac_common_config`, Ruff, pytest-cov.

## Global Constraints

- Configuration key is exactly `business_code=bot_dormant`, `param_code=protected_owner_ids`, `env=<current_env>`.
- `param_value` is a plain JSON array; runtime values normalize to `frozenset[str]`.
- Existing `CommonWhiteListService.is_bot_feature_enabled()` exact `(owner_id, bot_id)` semantics remain unchanged.
- Existing `ac_bot_dormant_whitelist` exact Bot-level semantics remain unchanged.
- Internal scan and `external_input` enforce owner protection; `DormantOpsService.recycle_one` does not.
- Load the owner list once per run; never query common config once per Bot.
- Missing or disabled config means an empty protection list.
- Malformed enabled config or config-read failure aborts the automatic run before `/alive`, notification creation, container release, status mutation, or passport freeze.
- Do not log the full owner list; log only counts and at most five `bot_id@owner_id` samples.
- Do not commit real protected owner IDs or private spreadsheet paths to the public repository.
- Keep `src/backend/src/agentclaw/community/core/bot_dormant/service.py` at or below 1000 physical lines.
- Change-line coverage must be greater than 90% against `origin/dev`.

---

### Task 1: Add the Generic Bulk Owner-List Reader

**Files:**
- Modify: `src/backend/src/agentclaw/community/core/common_config/whitelist_service.py:16-82`
- Modify: `src/backend/tests/community/core/common_config/test_common_whitelist_service.py:1-213`

**Interfaces:**
- Consumes: `CommonConfigService.get_value(*, business_code, param_code, env, default, only_enabled)`.
- Produces: `CommonWhiteListService.get_owner_ids(*, business_code: str, param_code: str, env: str) -> frozenset[str]`.
- Preserves: `CommonWhiteListService.is_bot_feature_enabled(...) -> bool` without behavioral changes.

- [ ] **Step 1: Write failing normalization and empty-config tests**

Add `pytest` to the imports and append these tests:

```python
import pytest


def test_get_owner_ids_normalizes_deduplicates_and_drops_blanks():
    config = FakeCommonConfigService(
        {
            ("bot_dormant", "protected_owner_ids", "prod"): [
                100001,
                " 100002 ",
                "100001",
                "",
                "   ",
                None,
            ]
        }
    )
    service = CommonWhiteListService(config)

    assert service.get_owner_ids(
        business_code="bot_dormant",
        param_code="protected_owner_ids",
        env="prod",
    ) == frozenset({"100001", "100002"})
    assert config.calls == [
        {
            "business_code": "bot_dormant",
            "param_code": "protected_owner_ids",
            "env": "prod",
            "default": None,
            "only_enabled": True,
        }
    ]


def test_get_owner_ids_returns_empty_set_when_config_is_missing_or_disabled():
    service = CommonWhiteListService(FakeCommonConfigService())

    assert service.get_owner_ids(
        business_code="bot_dormant",
        param_code="protected_owner_ids",
        env="pre",
    ) == frozenset()
```

- [ ] **Step 2: Write failing malformed-value and read-error tests**

```python
@pytest.mark.parametrize("value", [{"100001": True}, "100001", 100001, True])
def test_get_owner_ids_rejects_non_list_config(value, caplog):
    caplog.set_level("ERROR")
    service = CommonWhiteListService(
        FakeCommonConfigService(
            {("bot_dormant", "protected_owner_ids", "prod"): value}
        )
    )

    with pytest.raises(ValueError, match="protected owner IDs must be a list"):
        service.get_owner_ids(
            business_code="bot_dormant",
            param_code="protected_owner_ids",
            env="prod",
        )
    assert "business_code=bot_dormant" in caplog.text
    assert "param_code=protected_owner_ids" in caplog.text
    assert "env=prod" in caplog.text
    assert repr(value) not in caplog.text


def test_get_owner_ids_propagates_config_read_failure(caplog):
    caplog.set_level("ERROR")
    config = FakeCommonConfigService()
    config.get_value = lambda **_: (_ for _ in ()).throw(RuntimeError("db unavailable"))
    service = CommonWhiteListService(config)

    with pytest.raises(RuntimeError, match="db unavailable"):
        service.get_owner_ids(
            business_code="bot_dormant",
            param_code="protected_owner_ids",
            env="prod",
        )
    assert "business_code=bot_dormant" in caplog.text
    assert "param_code=protected_owner_ids" in caplog.text
    assert "env=prod" in caplog.text
```

- [ ] **Step 3: Run the focused tests and verify they fail**

Run from `src/backend`:

```bash
DEPLOY_PROFILE=test uv run pytest tests/community/core/common_config/test_common_whitelist_service.py -v
```

Expected: the new tests fail with `AttributeError: 'CommonWhiteListService' object has no attribute 'get_owner_ids'`; existing exact Bot whitelist tests continue to pass.

- [ ] **Step 4: Implement the minimal bulk reader**

Import `get_logger`, define `logger = get_logger()`, and add this method to
`CommonWhiteListService` before `_match_bot_whitelist`:

```python
    def get_owner_ids(
        self,
        *,
        business_code: str,
        param_code: str,
        env: str,
    ) -> frozenset[str]:
        """Read and normalize an enabled owner-ID list from common config."""
        try:
            value = self._common_config_service.get_value(
                business_code=business_code,
                param_code=param_code,
                env=env,
                default=None,
                only_enabled=True,
            )
        except Exception:
            logger.exception(
                "[common_whitelist] owner list read failed "
                "business_code=%s param_code=%s env=%s",
                business_code,
                param_code,
                env,
            )
            raise
        if value is None:
            return frozenset()
        if not isinstance(value, list):
            logger.error(
                "[common_whitelist] owner list invalid "
                "business_code=%s param_code=%s env=%s value_type=%s",
                business_code,
                param_code,
                env,
                type(value).__name__,
            )
            raise ValueError(
                "protected owner IDs must be a list: "
                f"business_code={business_code} param_code={param_code} env={env}"
            )
        return frozenset(
            normalized
            for item in value
            if item is not None
            and (normalized := str(item).strip())
        )
```

Log and re-raise read exceptions; callers use the propagated failure as a safety
stop. Never include the raw list or raw invalid value in these logs.

- [ ] **Step 5: Run tests and Ruff**

```bash
DEPLOY_PROFILE=test uv run pytest tests/community/core/common_config/test_common_whitelist_service.py -v
uv run ruff check src/agentclaw/community/core/common_config/whitelist_service.py tests/community/core/common_config/test_common_whitelist_service.py
```

Expected: all common whitelist tests pass and Ruff reports no errors.

- [ ] **Step 6: Commit the generic reader**

```bash
git add src/backend/src/agentclaw/community/core/common_config/whitelist_service.py src/backend/tests/community/core/common_config/test_common_whitelist_service.py
git commit -m "feat(config): support bulk owner whitelist values"
```

---

### Task 2: Extract Scan-Window Resolution to Preserve the Module Size Guard

**Files:**
- Modify: `src/backend/src/agentclaw/community/core/bot_dormant/scan_policy.py:142-152`
- Modify: `src/backend/src/agentclaw/community/core/bot_dormant/service.py:31-36,151-173,752-764`
- Modify: `src/backend/tests/community/core/bot_dormant/test_scan_policy.py:7-10,168-215`
- Test: `src/backend/tests/community/architecture/test_no_oversized_modules.py`

**Interfaces:**
- Consumes: `DormantScanPolicyService.get_policy()` and existing `positive_int_or_default`.
- Produces: `resolve_scan_window(scan_policy, *, default_inactive_threshold_days: int, default_recycle_grace_days: int) -> tuple[int, int]`.
- Preserves: policy-read errors fall back to constructor defaults exactly as today.

- [ ] **Step 1: Replace service-private tests with failing helper tests**

Import `resolve_scan_window` from `scan_policy` and replace the tests that call `_refresh_scan_window_from_policy` with:

```python
def test_resolve_scan_window_uses_policy_values():
    scan_policy = MagicMock()
    scan_policy.get_policy.return_value.inactive_threshold_days = 11
    scan_policy.get_policy.return_value.recycle_grace_days = 4

    assert resolve_scan_window(
        scan_policy,
        default_inactive_threshold_days=7,
        default_recycle_grace_days=3,
    ) == (11, 4)


def test_resolve_scan_window_falls_back_on_policy_error():
    scan_policy = MagicMock()
    scan_policy.get_policy.side_effect = RuntimeError("db unavailable")

    assert resolve_scan_window(
        scan_policy,
        default_inactive_threshold_days=10,
        default_recycle_grace_days=6,
    ) == (10, 6)
```

Keep the existing `positive_int_or_default` edge-case test unchanged.

- [ ] **Step 2: Run the focused tests and verify they fail**

```bash
DEPLOY_PROFILE=test uv run pytest tests/community/core/bot_dormant/test_scan_policy.py -v
```

Expected: collection fails because `resolve_scan_window` is not yet exported.

- [ ] **Step 3: Add the policy-owned helper**

Append to `scan_policy.py` after `DormantScanPolicyService`:

```python
def resolve_scan_window(
    scan_policy: DormantScanPolicyService,
    *,
    default_inactive_threshold_days: int,
    default_recycle_grace_days: int,
) -> tuple[int, int]:
    """Resolve N/M for one run, preserving the legacy default fallback."""
    try:
        policy = scan_policy.get_policy()
    except Exception:
        logger.exception(
            "[dormant.scan_policy] failed to read scan window; "
            "using defaults N=%d M=%d",
            default_inactive_threshold_days,
            default_recycle_grace_days,
        )
        return default_inactive_threshold_days, default_recycle_grace_days
    return (
        positive_int_or_default(
            policy.inactive_threshold_days,
            default_inactive_threshold_days,
        ),
        positive_int_or_default(
            policy.recycle_grace_days,
            default_recycle_grace_days,
        ),
    )
```

- [ ] **Step 4: Replace the service-private method with the helper call**

Import `resolve_scan_window`, remove `_refresh_scan_window_from_policy`, and replace its call in `_process_run_inner` with:

```python
        self._N, self._M = resolve_scan_window(
            self._scan_policy,
            default_inactive_threshold_days=self._default_N,
            default_recycle_grace_days=self._default_M,
        )
```

Do not change dry-run or fallback behavior.

- [ ] **Step 5: Run policy and architecture tests**

```bash
DEPLOY_PROFILE=test uv run pytest tests/community/core/bot_dormant/test_scan_policy.py tests/community/architecture/test_no_oversized_modules.py -v
wc -l src/agentclaw/community/core/bot_dormant/service.py
```

Expected: tests pass and `service.py` is comfortably below 1000 lines before owner protection is added.

- [ ] **Step 6: Commit the focused extraction**

```bash
git add src/backend/src/agentclaw/community/core/bot_dormant/scan_policy.py src/backend/src/agentclaw/community/core/bot_dormant/service.py src/backend/tests/community/core/bot_dormant/test_scan_policy.py
git commit -m "refactor(dormant): move scan window resolution into policy"
```

---

### Task 3: Protect Internal Scan Candidates by Owner

**Files:**
- Modify: `src/backend/src/agentclaw/community/core/bot_dormant/service.py:26-55,106-134,483-485,752-850`
- Modify: `src/backend/tests/community/core/bot_dormant/test_decide.py:29-44,113-138`
- Modify: `src/backend/tests/community/core/bot_dormant/test_external_input.py:13-21,74-88`
- Modify: `src/backend/tests/community/core/bot_dormant/test_scan_policy.py:153-208`

**Interfaces:**
- Consumes: `CommonWhiteListService.get_owner_ids(...) -> frozenset[str]` from Task 1.
- Produces: one immutable protected-owner set per `process_run`, passed into `_process_external_inputs(..., protected_owner_ids)` for Task 4.
- Configuration constants: `OWNER_PROTECTION_BUSINESS_CODE = "bot_dormant"`, `OWNER_PROTECTION_PARAM_CODE = "protected_owner_ids"`.

- [ ] **Step 1: Update test constructors with an empty owner protection dependency**

In `test_decide.py`, import `CommonWhiteListService` and extend `_make_service`:

```python
def _make_service(
    session: Session,
    baas_client: BaasDormantClient | None = None,
    bot_service=None,
    passport_plugin=None,
    protected_owner_ids: frozenset[str] = frozenset(),
    common_whitelist_service=None,
) -> DormantBotService:
    """Build a DormantBotService with all dependencies injected as mocks."""
    if baas_client is None:
        baas_client = AsyncMock(spec=BaasDormantClient)
    if bot_service is None:
        bot_service = MagicMock()
        bot_service.stop_bot = MagicMock(return_value=True)
        bot_service.update_status = MagicMock()
    if passport_plugin is None:
        passport_plugin = MagicMock()
    scan_policy = MagicMock()
    scan_policy.dry_run.return_value = False
    if common_whitelist_service is None:
        common_whitelist_service = MagicMock(spec=CommonWhiteListService)
        common_whitelist_service.get_owner_ids.return_value = protected_owner_ids
    return DormantBotService(
        db=FakeDB(session),
        baas_client=baas_client,
        bot_service=bot_service,
        passport_plugin=passport_plugin,
        scan_policy=scan_policy,
        common_whitelist_service=common_whitelist_service,
        N=N,
        M=M,
    )
```

In `test_external_input.py`, import `CommonWhiteListService` and replace its
helper with the complete protected-owner-aware version:

```python
def _make_service(session, *, dry_run=False, protected_owner_ids=frozenset()):
    baas = AsyncMock(spec=BaasDormantClient)
    baas.check_alive = AsyncMock(
        return_value=AliveResult(result="unknown", last_session_time=None)
    )
    bot_svc = MagicMock()
    bot_svc.stop_bot = MagicMock(return_value=True)
    bot_svc.update_status = MagicMock()
    scan_policy = MagicMock()
    scan_policy.dry_run.return_value = dry_run
    common_whitelist = MagicMock(spec=CommonWhiteListService)
    common_whitelist.get_owner_ids.return_value = frozenset(protected_owner_ids)
    return DormantBotService(
        db=FakeDB(session),
        baas_client=baas,
        bot_service=bot_svc,
        passport_plugin=MagicMock(),
        scan_policy=scan_policy,
        common_whitelist_service=common_whitelist,
        N=N,
        M=M,
    )
```

In each direct `DormantBotService` constructor in `test_scan_policy.py`, import
`CommonWhiteListService` and add this argument:

```python
        common_whitelist_service=MagicMock(spec=CommonWhiteListService),
```

- [ ] **Step 2: Write the failing internal-protection test**

Append to `test_decide.py`:

```python
@pytest.mark.unit
def test_internal_scan_filters_protected_owner_before_alive_check(caplog, monkeypatch):
    caplog.set_level("INFO")
    session = _make_session()
    _insert_bot_record(
        session,
        bot_id="protected_bot",
        owner_id="protected_owner",
        entity_id="100001",
    )
    _insert_bot_record(
        session,
        bot_id="normal_bot",
        owner_id="normal_owner",
        entity_id="100002",
    )
    baas = AsyncMock(spec=BaasDormantClient)
    baas.check_alive = AsyncMock(
        return_value=AliveResult(result="true", last_session_time=None)
    )
    common_whitelist = MagicMock(spec=CommonWhiteListService)
    common_whitelist.get_owner_ids.return_value = frozenset({"protected_owner"})
    monkeypatch.setattr(
        "agentclaw.community.core.bot_dormant.service.get_current_env",
        lambda: "prod",
    )
    service = _make_service(
        session,
        baas_client=baas,
        common_whitelist_service=common_whitelist,
    )

    summary = _run(service.process_run(dry_run=True, run_id="owner-protection-run"))

    assert summary.scanned == 1
    baas.check_alive.assert_awaited_once()
    assert baas.check_alive.await_args.kwargs["bot_id"] == "normal_bot"
    common_whitelist.get_owner_ids.assert_called_once_with(
        business_code="bot_dormant",
        param_code="protected_owner_ids",
        env="prod",
    )
    assert "event=protected_owners_loaded" in caplog.text
    assert "event=protected_owners_filtered" in caplog.text
    assert "protected_bot@protected_owner" in caplog.text
```

- [ ] **Step 3: Write the failing safety-stop test**

```python
@pytest.mark.unit
def test_owner_config_error_aborts_before_downstream_calls():
    session = _make_session()
    _insert_bot_record(session, bot_id="bot1", owner_id="owner1")
    baas = AsyncMock(spec=BaasDormantClient)
    common_whitelist = MagicMock(spec=CommonWhiteListService)
    common_whitelist.get_owner_ids.side_effect = RuntimeError("db unavailable")
    bot_service = MagicMock()
    service = _make_service(
        session,
        baas_client=baas,
        bot_service=bot_service,
        common_whitelist_service=common_whitelist,
    )

    with pytest.raises(RuntimeError, match="db unavailable"):
        _run(service.process_run(dry_run=False))

    baas.check_alive.assert_not_awaited()
    bot_service.stop_bot.assert_not_called()
    assert session.query(DormantCheckAudit).count() == 0
    assert session.query(DormantNotifyLog).count() == 0
```

- [ ] **Step 4: Run the tests and verify they fail**

```bash
DEPLOY_PROFILE=test uv run pytest tests/community/core/bot_dormant/test_decide.py -k "protected_owner or owner_config_error" -v
```

Expected: constructor or behavior failures because `DormantBotService` does not yet consume `CommonWhiteListService`.

- [ ] **Step 5: Inject and load the owner set once per run**

In `service.py`:

```python
from agentclaw.community.core.common_config import CommonWhiteListService
from agentclaw.community.utils.env_utils import get_current_env


OWNER_PROTECTION_BUSINESS_CODE = "bot_dormant"
OWNER_PROTECTION_PARAM_CODE = "protected_owner_ids"
```

Add `common_whitelist_service: CommonWhiteListService` to `__init__` and assign
`self._common_whitelist_service = common_whitelist_service`.

Immediately after resolving N/M in `_process_run_inner`, load the set:

```python
        env = get_current_env()
        protected_owner_ids = self._common_whitelist_service.get_owner_ids(
            business_code=OWNER_PROTECTION_BUSINESS_CODE,
            param_code=OWNER_PROTECTION_PARAM_CODE,
            env=env,
        )
        logger.info(
            "[dormant.run=%s] event=protected_owners_loaded env=%s owner_count=%d",
            run_id,
            env,
            len(protected_owner_ids),
        )
```

After `filter_candidates`, partition before assigning `summary.scanned`:

```python
        protected_candidates = [
            candidate
            for candidate in candidates
            if str(candidate.owner_id) in protected_owner_ids
        ]
        candidates = [
            candidate
            for candidate in candidates
            if str(candidate.owner_id) not in protected_owner_ids
        ]
        logger.info(
            "[dormant.run=%s] event=protected_owners_filtered skipped=%d sample=%s",
            run_id,
            len(protected_candidates),
            [f"{c.bot_id}@{c.owner_id}" for c in protected_candidates[:5]],
        )
        summary.scanned = len(candidates)
```

Pass `protected_owner_ids` to `_process_external_inputs` and extend that private
method's signature to accept `protected_owner_ids: frozenset[str]`; Task 4 will
apply the external decision.

- [ ] **Step 6: Run constructor, decision, and architecture tests**

```bash
DEPLOY_PROFILE=test uv run pytest tests/community/core/bot_dormant/test_scan_policy.py tests/community/core/bot_dormant/test_decide.py tests/community/core/bot_dormant/test_external_input.py -v
DEPLOY_PROFILE=test uv run pytest tests/community/architecture/test_no_oversized_modules.py -v
wc -l src/agentclaw/community/core/bot_dormant/service.py
```

Expected: all tests pass, only the unprotected Bot reaches `/alive`, and
`service.py` remains at or below 1000 lines.

- [ ] **Step 7: Commit internal scan protection**

```bash
git add src/backend/src/agentclaw/community/core/bot_dormant/service.py src/backend/tests/community/core/bot_dormant/test_decide.py src/backend/tests/community/core/bot_dormant/test_external_input.py src/backend/tests/community/core/bot_dormant/test_scan_policy.py
git commit -m "feat(dormant): protect configured owners from scans"
```

---

### Task 4: Protect External Input and Prove Manual Ops Override

**Files:**
- Modify: `src/backend/src/agentclaw/community/core/bot_dormant/service.py:483-541`
- Modify: `src/backend/tests/community/core/bot_dormant/test_external_input.py:74-88,400-420`
- Modify: `src/backend/tests/community/core/bot_dormant/test_decide.py:207-255`

**Interfaces:**
- Consumes: `protected_owner_ids: frozenset[str]` passed once from Task 3.
- Produces: protected `external_input` rows audit as `whitelisted/skipped` and remain `processed=0`.
- Preserves: `DormantOpsService.recycle_one` bypasses automatic owner protection and retains `source="manual_ops"`.

- [ ] **Step 1: Write the failing external-input protection test**

```python
@pytest.mark.unit
def test_external_input_skips_protected_owner_and_leaves_row_unprocessed():
    session = _make_session()
    _insert_bot(session, bot_id="bot1", owner_id="protected_owner")
    row_id = _insert_external(
        session,
        bot_id="bot1",
        owner_id="protected_owner",
        dt_str=(date.today() - timedelta(days=M)).strftime("%Y%m%d"),
    )
    service = _make_service(
        session,
        protected_owner_ids=frozenset({"protected_owner"}),
    )

    _run(service.process_run(dry_run=False))

    row = session.query(DormantExternalInput).filter_by(id=row_id).one()
    audit = session.query(DormantCheckAudit).filter_by(
        source="external_input",
        bot_id="bot1",
        owner_id="protected_owner",
    ).one()
    assert row.processed == 0
    assert audit.check_result == "whitelisted"
    assert audit.action_taken == "skipped"
    assert session.query(DormantNotifyLog).count() == 0
    service._bot_service.stop_bot.assert_not_called()
```

- [ ] **Step 2: Strengthen the existing manual recycle test**

Change `test_manual_recycle_one_reuses_recycle_side_effects_and_writes_audit`
to build the service with the same owner protected:

```python
    service = _make_service(
        session,
        bot_service=bot_service,
        passport_plugin=passport,
        protected_owner_ids=frozenset({"owner1"}),
    )
    ops_service = DormantOpsService(service)

    result = ops_service.recycle_one(
        bot_id="ops_bot",
        owner_id="owner1",
        dry_run=False,
        reason="explicit protected-owner override",
    )

    assert result["status"] == "recycled"
    service._common_whitelist_service.get_owner_ids.assert_not_called()
```

Keep the existing stop/status/freeze and `source="manual_ops"` assertions.

- [ ] **Step 3: Run tests and verify the external test fails**

```bash
DEPLOY_PROFILE=test uv run pytest tests/community/core/bot_dormant/test_external_input.py -k "protected_owner" -v
DEPLOY_PROFILE=test uv run pytest tests/community/core/bot_dormant/test_decide.py -k "manual_recycle_one_reuses" -v
```

Expected: external protection fails because the set is accepted but not yet
checked; manual override already passes and proves it is decoupled from scan
configuration.

- [ ] **Step 4: Merge owner-level and exact Bot-level external protection**

Replace the existing exact-whitelist branch condition with:

```python
                is_owner_protected = str(row.owner_id) in protected_owner_ids
                is_bot_whitelisted = (row.bot_id, row.owner_id) in whitelist
                if is_owner_protected or is_bot_whitelisted:
                    reason = (
                        "protected_owner" if is_owner_protected else "whitelisted"
                    )
                    logger.info(
                        "[dormant.run=%s] event=external_skip reason=%s "
                        "bot_id=%s owner_id=%s",
                        run_id,
                        reason,
                        row.bot_id,
                        row.owner_id,
                    )
                    self._write_audit(
                        session,
                        run_id=run_id,
                        bot_id=row.bot_id,
                        owner_id=row.owner_id,
                        check_result="whitelisted",
                        action_taken="skipped",
                        dry_run=dry_run,
                        source="external_input",
                    )
                    continue
```

Do not mark the external row processed and do not increment warned/recycled.

- [ ] **Step 5: Run external, manual-ops, and architecture tests**

```bash
DEPLOY_PROFILE=test uv run pytest tests/community/core/bot_dormant/test_external_input.py tests/community/core/bot_dormant/test_decide.py -v
DEPLOY_PROFILE=test uv run pytest tests/community/architecture/test_no_oversized_modules.py -v
wc -l src/agentclaw/community/core/bot_dormant/service.py
```

Expected: protected automatic paths skip, manual ops still recycles, all
existing exact Bot whitelist tests pass, and the module remains within the cap.

- [ ] **Step 6: Commit external protection and override coverage**

```bash
git add src/backend/src/agentclaw/community/core/bot_dormant/service.py src/backend/tests/community/core/bot_dormant/test_external_input.py src/backend/tests/community/core/bot_dormant/test_decide.py
git commit -m "feat(dormant): protect owners in external governance"
```

---

### Task 5: Run the Final Gate and Prepare the Private Deployment Data

**Files:**
- Verify: all files modified in Tasks 1-4.
- Generate outside Git: environment-specific `ac_common_config` upsert SQL containing the user-provided owner IDs.
- Do not create any committed file containing real owner IDs or private source paths.

**Interfaces:**
- Consumes: the user-provided private owner list and the public config contract.
- Produces: verified code commits plus private `pre` and `prod` upsert statements for operator review.

- [ ] **Step 1: Run focused unit and architecture suites**

From `src/backend`:

```bash
DEPLOY_PROFILE=test uv run pytest tests/community/core/common_config/test_common_whitelist_service.py tests/community/core/bot_dormant tests/community/architecture/test_no_oversized_modules.py -v
```

Expected: all selected tests pass with zero failures.

- [ ] **Step 2: Run Ruff on every changed Python file**

```bash
uv run ruff check src/agentclaw/community/core/common_config/whitelist_service.py src/agentclaw/community/core/bot_dormant/scan_policy.py src/agentclaw/community/core/bot_dormant/service.py tests/community/core/common_config/test_common_whitelist_service.py tests/community/core/bot_dormant/test_scan_policy.py tests/community/core/bot_dormant/test_decide.py tests/community/core/bot_dormant/test_external_input.py
```

Expected: `All checks passed!`.

- [ ] **Step 3: Run the complete community backend CI gate**

From the repository root:

```bash
src/backend/scripts/ci_test.sh --base origin/dev --head HEAD
```

Expected: all community tests pass, case pass rate is 100%, baseline line
coverage passes, and the built-in change-line gate passes.

- [ ] **Step 4: Recheck change-line coverage at the project requirement of 90%**

From the repository root, using the reports produced by Step 3:

```bash
python scripts/ci/report_check.py --junit src/backend/pytest_report/TEST-junit.xml --coverage src/backend/pytest_report/TEST-cov.xml --source-root src/backend/src --min-case-pass-rate 100 --min-line-coverage 75 --base origin/dev --head HEAD --min-change-line-coverage 90
```

Expected: `change line coverage` is greater than 90% and the command exits 0.

- [ ] **Step 5: Verify no sensitive deployment identifiers entered Git**

```bash
git diff origin/dev...HEAD --check
git grep -n -E 'Downloads/|protected_owner_ids.*\[[[:space:]]*[0-9]{4,}' -- . ':!docs/superpowers/specs' ':!docs/superpowers/plans'
git status --short
```

Expected: diff check passes, grep returns no real owner-list payload, and the
worktree is clean after committed code changes.

- [ ] **Step 6: Generate private pre/prod upsert SQL outside the repository**

Use the private spreadsheet tooling to normalize the source column into a
sorted, deduplicated string array. Build two statements with the same JSON
array and different `env` values:

```sql
INSERT INTO ac_common_config (
  business_code,
  business_name,
  param_code,
  param_name,
  param_value,
  enable,
  ext_info,
  env,
  gmt_create,
  gmt_modified
) VALUES (
  'bot_dormant',
  'Dormant Bot Governance',
  'protected_owner_ids',
  'Dormant protected owner IDs',
  :normalized_owner_ids_json,
  '1',
  :deployment_metadata_json,
  :target_env,
  NOW(),
  NOW()
)
ON DUPLICATE KEY UPDATE
  param_value = VALUES(param_value),
  enable = VALUES(enable),
  ext_info = VALUES(ext_info),
  gmt_modified = NOW();
```

Render concrete statements for `target_env='pre'` and `target_env='prod'` in a
private, untracked output file. Never place the concrete values in this public
worktree.

- [ ] **Step 7: Reconcile private deployment data before handoff**

Verify all four conditions against the source workbook:

```text
configured_count == source_unique_count
configured_count == configured_unique_count
source_ids - configured_ids == empty set
configured_ids - source_ids == empty set
```

Expected: all conditions are true. Hand the two concrete SQL statements and
the reconciliation counts to the user separately from the public PR.

- [ ] **Step 8: Record the final verification commit if test-only adjustments were needed**

If verification required test or formatting changes, commit only those changes:

```bash
git add src/backend/src src/backend/tests
git commit -m "test(dormant): complete owner protection coverage"
```

If the worktree was already clean after Step 5, do not create an empty commit.
