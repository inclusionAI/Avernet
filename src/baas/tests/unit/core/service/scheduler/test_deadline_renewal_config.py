"""Unit tests for DeadlineRenewalScheduler config, lock name, and switch behavior.

Tests TEST-04: config schema validation, enabled derivation, engine selection,
YAML config sync, DI task selection — migrated from enterprise
tests/unit/core/arca_ttl_renewal/test_config.py (imports rewritten to
secbaas.community.*; YAML paths relocated for the community tree).

Adds F4/D-11' coverage: resolved_lock_name() appends the runtime env suffix.

Coverage: Tests 1-5 (Plan 05-01) + Tests 6-12 (Plan 05-05) + F4.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from secbaas.community.bootstrap._configs import _CONFIG_SCHEMAS
from secbaas.community.bootstrap._container import _select_renewal_task
from secbaas.community.core.service.scheduler import (
    DeadlineRenewalScheduler,
    DeadlineRenewalSchedulerConfig,
    RenewalRunReport,
)
from secbaas.community.core.utils.env_utils import get_current_env

# ── Tests 1-5 (Plan 05-01, existing) ───────────────────────────────────


class TestConfigSchema:
    """Tests for RenewalSchedulerConfigSchema auto-registration and validation."""

    def test_default_value_is_legacy(self):
        """Config schema defaults to engine="legacy"."""
        assert "renewal_scheduler" in _CONFIG_SCHEMAS
        schema = _CONFIG_SCHEMAS["renewal_scheduler"]()
        assert schema.engine == "legacy"

    def test_rejects_invalid_engine_values(self):
        """Config schema rejects values other than 'legacy' or 'deadline'."""
        schema_cls = _CONFIG_SCHEMAS["renewal_scheduler"]
        invalid_values = ["invalid_value", "", "LEGACY", "Deadline", "INVALID"]
        for value in invalid_values:
            with pytest.raises(Exception):
                schema_cls(engine=value)

    def test_accepts_deadline_value(self):
        """Config schema accepts engine="deadline"."""
        schema_cls = _CONFIG_SCHEMAS["renewal_scheduler"]
        schema = schema_cls(engine="deadline")
        assert schema.engine == "deadline"


class TestSchedulerConfig:
    """Tests for DeadlineRenewalSchedulerConfig enabled derivation."""

    def test_default_config_is_disabled(self):
        """Default config has enabled=False, engine="legacy"."""
        config = DeadlineRenewalSchedulerConfig()
        assert config.enabled is False
        assert config.engine == "legacy"
        # Verify all other fields retain defaults
        assert config.lock_name == "deadline_renewal_scheduler_lock"
        assert config.lock_expire_seconds == 1800
        assert config.batch_size == 500
        assert config.max_concurrency == 20
        assert config.renew_threshold_hours == 12
        assert config.default_ttl_minutes == 1440
        assert config.retry_delay_minutes == 2
        assert config.max_fail_count == 10
        assert config.ttl_safety_margin_minutes == 1
        assert config.anti_join_verify_interval_cycles == 48
        assert config.env == ""

    def test_deadline_engine_with_enabled(self):
        """When engine="deadline" and enabled=True, scheduler is active."""
        config = DeadlineRenewalSchedulerConfig(
            engine="deadline",
            enabled=True,
        )
        assert config.enabled is True
        assert config.engine == "deadline"
        assert config.env == ""


class TestResolvedLockName:
    """F4/D-11': resolved_lock_name() appends the runtime env suffix.

    pre/prod share one MySQL instance (and one distributed lock table);
    a fixed lock name would make the two environments' schedulers take
    turns. The resolved name is asserted against the actual current
    environment, never a hardcoded 'prod' value (this test runs in any
    environment).
    """

    def test_resolved_lock_name_appends_env_suffix(self):
        config = DeadlineRenewalSchedulerConfig()
        resolved = config.resolved_lock_name()
        assert resolved == f"{config.lock_name}_{get_current_env()}"
        assert resolved.startswith("deadline_renewal_scheduler_lock_")

    def test_resolved_lock_name_never_equals_bare_lock_name(self):
        config = DeadlineRenewalSchedulerConfig()
        assert config.resolved_lock_name() != config.lock_name

    def test_resolved_lock_name_custom_lock_name_gets_env_suffix(self):
        config = DeadlineRenewalSchedulerConfig(lock_name="my_lock")
        assert config.resolved_lock_name() == f"my_lock_{get_current_env()}"

    def test_resolved_lock_name_env_survives_explicit_env_field(self):
        """The `env` dataclass field (DI-injected) is not consulted for the
        lock suffix — resolved_lock_name() always uses get_current_env()."""
        config = DeadlineRenewalSchedulerConfig(env="pre")
        assert config.resolved_lock_name() == f"{config.lock_name}_{get_current_env()}"


class TestSchedulerSkeleton:
    """Tests for DeadlineRenewalScheduler skeleton behavior."""

    @pytest.mark.asyncio
    async def test_run_returns_none_when_disabled(self):
        """run() returns None when config.enabled=False."""
        config = DeadlineRenewalSchedulerConfig(enabled=False)
        scheduler = DeadlineRenewalScheduler(
            config=config,
            lock_service=MagicMock(),
            schedule_repo=MagicMock(),
            paas_facade=MagicMock(),
        )
        result = await scheduler.run()
        assert result is None

    @pytest.mark.asyncio
    async def test_run_returns_report_when_enabled_and_lock_acquired(self):
        """run() returns RenewalRunReport when enabled and lock acquired."""
        config = DeadlineRenewalSchedulerConfig(
            engine="deadline",
            enabled=True,
        )

        # Mock lock context: acquired=True
        lock_ctx = MagicMock()
        lock_ctx.acquired = True
        lock_service = MagicMock()
        lock_service.try_lock.return_value.__enter__ = MagicMock(return_value=lock_ctx)
        lock_service.try_lock.return_value.__exit__ = MagicMock(return_value=None)

        # Mock repo to return empty -- _run_once will early-return
        mock_repo = MagicMock()
        mock_repo.count_active.return_value = 0
        mock_repo.count_hot_arca_devices.return_value = 0
        mock_repo.count_hot_arca_bindings.return_value = 0
        # 86-02 covered-math Step 0 calls these two; explicit values keep
        # MagicMocks out of the gap arithmetic (gap = hot - covered == 0).
        mock_repo.count_hot_covered.return_value = 0
        mock_repo.count_suppressed_terminal.return_value = 0
        mock_repo.list_due_for_renewal.return_value = []

        scheduler = DeadlineRenewalScheduler(
            config=config,
            lock_service=lock_service,
            schedule_repo=mock_repo,
            paas_facade=MagicMock(),
        )
        result = await scheduler.run()
        assert isinstance(result, RenewalRunReport)
        assert result.trigger == "cron"
        assert result.run_uuid is not None

    @pytest.mark.asyncio
    async def test_name_and_interval(self):
        """Scheduler properties match config."""
        config = DeadlineRenewalSchedulerConfig(cron_interval_seconds=900)
        scheduler = DeadlineRenewalScheduler(
            config=config,
            lock_service=MagicMock(),
            schedule_repo=MagicMock(),
            paas_facade=MagicMock(),
        )
        assert scheduler.name == "deadline_renewal_scheduler"
        assert scheduler.interval_seconds == 900


# ── Tests 6-12 (Plan 05-05, new) ───────────────────────────────────────


# Path bases for YAML config lookups. This test file lives at
# ocb-public/src/baas/tests/unit/core/service/scheduler/ inside the
# community submodule; the community application.yaml is resolved relative
# to the submodule's src/baas root so it keeps working in a standalone
# submodule checkout (community CI), while the enterprise YAML path is
# resolved relative to the monorepo root and skipped when absent.
_BAAS_ROOT = Path(__file__).resolve().parents[5]  # ocb-public/src/baas
_REPO_ROOT = Path(__file__).resolve().parents[8]  # monorepo root


class TestYamlConfigValues:
    """Test 6: YAML config values — both community and enterprise."""

    def _read_yaml(self, absolute_path: Path) -> dict:
        if not absolute_path.exists():
            pytest.skip(f"YAML config not found: {absolute_path}")
        with open(absolute_path) as f:
            return yaml.safe_load(f)

    def test_community_yaml_engine_is_legacy(self):
        """Community application.yaml has renewal_scheduler.engine = 'legacy'.

        renewal_scheduler is nested under user_config in application.yaml.
        """
        cfg = self._read_yaml(_BAAS_ROOT / "configs" / "application.yaml")
        rs = cfg.get("user_config", {}).get("renewal_scheduler", {})
        assert rs.get("engine") == "legacy", (
            f"Community YAML engine should be 'legacy', got {rs.get('engine')}"
        )

    def test_enterprise_yaml_engine_is_legacy(self):
        """Enterprise application.yaml has renewal_scheduler.engine = 'legacy'.

        renewal_scheduler is nested under user_config in application.yaml.
        """
        cfg = self._read_yaml(
            _REPO_ROOT / "src" / "baas" / "configs" / "application.yaml"
        )
        rs = cfg.get("user_config", {}).get("renewal_scheduler", {})
        assert rs.get("engine") == "legacy", (
            f"Enterprise YAML engine should be 'legacy', got {rs.get('engine')}"
        )


class TestConfigSchemaExtended:
    """Tests 7-8: Schema validation edge cases."""

    def test_rejects_empty_string_engine(self):
        """Config schema rejects engine='' (empty string)."""
        schema_cls = _CONFIG_SCHEMAS["renewal_scheduler"]
        with pytest.raises(Exception):
            schema_cls(engine="")

    def test_rejects_case_variant_engine_legacy_uppercase(self):
        """Config schema rejects engine='LEGACY' (case-sensitive pattern)."""
        schema_cls = _CONFIG_SCHEMAS["renewal_scheduler"]
        with pytest.raises(Exception):
            schema_cls(engine="LEGACY")

    def test_rejects_case_variant_engine_legacy_titlecase(self):
        """Config schema rejects engine='Legacy' (case-sensitive pattern)."""
        schema_cls = _CONFIG_SCHEMAS["renewal_scheduler"]
        with pytest.raises(Exception):
            schema_cls(engine="Legacy")

    def test_rejects_case_variant_engine_deadline_titlecase(self):
        """Config schema rejects engine='Deadline' (case-sensitive pattern)."""
        schema_cls = _CONFIG_SCHEMAS["renewal_scheduler"]
        with pytest.raises(Exception):
            schema_cls(engine="Deadline")


class TestSelectRenewalTask:
    """Tests 9-10: _select_renewal_task DI task selection."""

    def _make_mock_task(self, name: str) -> MagicMock:
        task = MagicMock()
        task._name = name
        return task

    def test_select_legacy_returns_device_ttl_timer_task(self):
        """_select_renewal_task(engine='legacy', ...) returns legacy_task."""
        legacy_task = self._make_mock_task("device_ttl_timer_task")
        deadline_task = self._make_mock_task("deadline_renewal_scheduler")

        result = _select_renewal_task(
            engine="legacy",
            legacy_task=legacy_task,
            deadline_task=deadline_task,
        )
        assert result is legacy_task, (
            f"Expected legacy_task for engine='legacy', got {result._name}"
        )

    def test_select_deadline_returns_deadline_renewal_scheduler(self):
        """_select_renewal_task(engine='deadline', ...) returns deadline_task."""
        legacy_task = self._make_mock_task("device_ttl_timer_task")
        deadline_task = self._make_mock_task("deadline_renewal_scheduler")

        result = _select_renewal_task(
            engine="deadline",
            legacy_task=legacy_task,
            deadline_task=deadline_task,
        )
        assert result is deadline_task, (
            f"Expected deadline_task for engine='deadline', got {result._name}"
        )


class TestDeadlineEnabledDerivation:
    """Test 11: DeadlineRenewalSchedulerConfig enabled derivation from engine."""

    def test_engine_legacy_defaults_to_disabled(self):
        """engine='legacy' (default) → enabled=False."""
        config = DeadlineRenewalSchedulerConfig()
        assert config.engine == "legacy"
        assert config.enabled is False

    def test_engine_deadline_can_be_explicitly_enabled(self):
        """engine='deadline' with enabled=True — both fields set explicitly."""
        config = DeadlineRenewalSchedulerConfig(
            engine="deadline",
            enabled=True,
            env="pre",
        )
        assert config.engine == "deadline"
        assert config.enabled is True
        assert config.env == "pre"

    def test_engine_deadline_still_respects_explicit_enabled_false(self):
        """Even with engine='deadline', enabled=False means disabled.

        The DI container sets enabled via a Callable(lambda engine: engine=='deadline'),
        but at the dataclass level, enabled is a separate field that defaults to False.
        """
        config = DeadlineRenewalSchedulerConfig(
            engine="deadline",
            enabled=False,
        )
        assert config.engine == "deadline"
        assert config.enabled is False

    def test_engine_deadline_documents_relationship_to_enabled(self):
        """The DeadlineRenewalSchedulerConfig docs mention the engine→enabled link.

        Truth: The DI container derives enabled from engine via:
          enabled = providers.Callable(lambda engine: engine == "deadline")
        From _core_tasks.py ~line 112-116.
        """
        config = DeadlineRenewalSchedulerConfig(engine="deadline", enabled=True)
        # Verify the dataclass stores both independently.
        assert config.engine == "deadline"
        assert config.enabled is True
        # Document the DI-level derivation — it's in _core_tasks.py.
        # This test proves the dataclass supports both states; DI enforces the link.


class TestYamlFileSync:
    """Test 12: Both YAML files in sync (per "配置文件双轨同步" rule)."""

    def _read_renewal_section(self, absolute_path: Path) -> str:
        if not absolute_path.exists():
            pytest.skip(f"YAML config not found: {absolute_path}")
        with open(absolute_path) as f:
            in_section = False
            lines = []
            for line in f:
                if line.startswith("  renewal_scheduler:"):
                    in_section = True
                    lines.append(line.rstrip())
                    continue
                if in_section:
                    if line.startswith("  ") and not line.startswith("    "):
                        break
                    lines.append(line.rstrip())
            return "\n".join(lines)

    def test_both_yaml_renewal_scheduler_sections_identical(self):
        """Community and enterprise YAML have identical renewal_scheduler sections."""
        community_section = self._read_renewal_section(
            _BAAS_ROOT / "configs" / "application.yaml"
        )
        enterprise_section = self._read_renewal_section(
            _REPO_ROOT / "src" / "baas" / "configs" / "application.yaml"
        )

        assert community_section == enterprise_section, (
            "renewal_scheduler YAML sections differ between community and enterprise "
            "config files — must be identical per '配置文件双轨同步' rule.\n"
            "Community:\n" + community_section + "\n"
            "Enterprise:\n" + enterprise_section
        )
        assert 'engine: "legacy"' in community_section, (
            "Community YAML must contain engine: 'legacy'"
        )
