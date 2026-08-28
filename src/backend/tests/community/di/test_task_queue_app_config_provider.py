"""Unit tests for ``ConfigModule.task_queue`` — the owning-app config boundary.

The owner is the deployment's own identity, the top-level ``app_name``. It
decides which rows of the shared ``ac_task_queue`` table this deployment enqueues
into and claims from. Both sides read this one value, so the risk is not the two
disagreeing within a process; it is a value the column cannot carry faithfully,
which makes the *stored* name differ from the one the claim filter looks for.
That is silent: work is enqueued and simply never runs.

Hence the provider rejects rather than falls back on a name that is present but
unusable. Falling back is the specific accident worth preventing — the default is
the *other* deployment's name as often as not, so substituting it turns a typo
into one backend claiming another's work. Absent config is the one case that does
fall back: there is nothing to have got wrong.
"""
from __future__ import annotations

import pytest

from agentclaw.community.core.task_queue.types import DEFAULT_APP, MAX_APP_LEN
from agentclaw.community.di.modules import config_module
from agentclaw.community.di.modules.config_module import ConfigModule


@pytest.fixture
def stub_app_name(monkeypatch):
    def _set(app_name: str | None) -> None:
        monkeypatch.setattr(config_module, "_app_name", lambda: app_name)

    return _set


def test_absent_config_keeps_the_column_default(stub_app_name):
    """``None`` is "there is no config to read" — local mode, ad-hoc tests. The
    dataclass default is the table's column default, so a deployment that never
    set ``app_name`` keeps owning exactly the rows it already owned."""
    stub_app_name(None)
    assert ConfigModule().task_queue().app == DEFAULT_APP


def test_app_name_is_used_verbatim(stub_app_name):
    stub_app_name("teclaw")
    assert ConfigModule().task_queue().app == "teclaw"


def test_app_at_the_length_limit_is_accepted(stub_app_name):
    """The bound is inclusive — the boundary value must not be rejected."""
    name = "a" * MAX_APP_LEN
    stub_app_name(name)
    assert ConfigModule().task_queue().app == name


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_blank_app_name_is_rejected(stub_app_name, blank):
    """Distinct from ``None``: a config that is present and names the app
    nothing is a misconfiguration, not an absence."""
    stub_app_name(blank)
    with pytest.raises(ValueError, match="app_name must name"):
        ConfigModule().task_queue()


@pytest.mark.parametrize("padded", ["claw ", " claw", "claw\t"])
def test_padded_app_name_is_rejected(stub_app_name, padded):
    """Not tidiness: MySQL/OceanBase compare with a PAD SPACE collation, so
    ``"claw "`` and ``"claw"`` are one app there and two on SQLite — a
    divergence no test running on SQLite could observe."""
    stub_app_name(padded)
    with pytest.raises(ValueError, match="leading or trailing whitespace"):
        ConfigModule().task_queue()


def test_over_length_app_name_is_rejected(stub_app_name):
    """A non-strict server truncates it, so rows are filed under a name the
    claim filter never matches and none of this deployment's work ever runs."""
    stub_app_name("a" * (MAX_APP_LEN + 1))
    with pytest.raises(ValueError, match="exceeds"):
        ConfigModule().task_queue()


def test_rejection_is_not_a_silent_fallback_to_the_default(stub_app_name):
    """The behaviour this whole boundary exists for. Were a bad value to fall
    back, a deployment named ``teclaw`` with a typo would boot as ``agentclaw``
    and start claiming the other backend's tasks."""
    stub_app_name("teclaw " * 20)
    with pytest.raises(ValueError):
        ConfigModule().task_queue()


def test_absent_config_source_reads_as_none_not_as_a_failure():
    """The legitimate absence: no composition root has registered a provider,
    so there is genuinely nothing to read and the caller may default."""
    from agentclaw.community.core.config import provider as config_provider

    saved_provider, saved_cached = config_provider._provider, config_provider._cached
    config_provider._provider = None
    config_provider._cached = None
    try:
        assert config_module._app_name() is None
    finally:
        config_provider._provider = saved_provider
        config_provider._cached = saved_cached


def test_a_failing_config_source_propagates_instead_of_defaulting():
    """The case this must never treat as absence.

    A registered provider that cannot load — a missing or malformed overlay —
    used to be swallowed into ``None``, which ``task_queue`` reads as "no
    config" and answers with the shipped default. For a deployment whose
    ``app_name`` is *not* that default, booting on it means stamping and
    claiming the other fleet's queue rows: exactly the corruption ``app``
    scoping exists to prevent. The failure has to reach the boot."""
    from agentclaw.community.core.config import provider as config_provider

    class _Broken:
        def load(self):
            raise FileNotFoundError("application-whatever.yaml is missing")

    saved_provider, saved_cached = config_provider._provider, config_provider._cached
    config_provider._provider = _Broken()
    config_provider._cached = None
    try:
        with pytest.raises(FileNotFoundError):
            config_module._app_name()
        with pytest.raises(FileNotFoundError):
            ConfigModule().task_queue()
    finally:
        config_provider._provider = saved_provider
        config_provider._cached = saved_cached


def test_the_owner_is_read_off_the_real_top_level_app_name(stub_app_name):
    """``_app_name`` is stubbed everywhere above, so pin once that it reads the
    top-level key rather than a ``user_config`` block — the reason this provider
    has no config surface of its own to get out of step with ``app_name``."""
    from agentclaw.community.core.config.provider import AppConfig, set_config_provider
    from agentclaw.community.core.config import provider as config_provider

    class _Static:
        def load(self):
            return AppConfig(
                user_config={}, raw={}, app_name="teclaw", delegate=None
            )

    saved_provider, saved_cached = config_provider._provider, config_provider._cached
    set_config_provider(_Static())
    try:
        assert config_module._app_name() == "teclaw"
    finally:
        config_provider._provider = saved_provider
        config_provider._cached = saved_cached
