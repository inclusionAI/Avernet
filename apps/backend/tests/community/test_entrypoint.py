"""Unit tests for the community entrypoint helpers + logger registry.

Covers the corp-free boot helper (``_entry.ensure_stdin_fd``), the profile-driven
logger registry (``log.set_logger_factory`` / ``get_logger``), and the community
entrypoint's profile gate (``main._require_local_profile``) — the logic that
rejects a corp profile and routes every community/local profile to the uvicorn
boot.
"""

from __future__ import annotations

import logging
import os
import runpy
import subprocess
import sys
import types
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import pytest

from agentclaw.community.di.profile import DeployProfile


@pytest.fixture
def restore_logger_state():
    logger_names = (None, "agentclaw.runtime-config", "uvicorn.access")
    saved = {}
    for name in logger_names:
        logger = logging.getLogger(name)
        saved[name] = (logger.handlers[:], logger.level, logger.propagate)
    try:
        yield
    finally:
        for name, (handlers, level, propagate) in saved.items():
            logger = logging.getLogger(name)
            for handler in logger.handlers[:]:
                if handler not in handlers:
                    handler.close()
            logger.handlers[:] = handlers
            logger.setLevel(level)
            logger.propagate = propagate


def test_ensure_stdin_fd_reinitialises_fd0(monkeypatch):
    import agentclaw.community._entry as entry

    calls: dict[str, object] = {}
    monkeypatch.setattr(entry.os, "open", lambda path, flags: 7)
    monkeypatch.setattr(
        entry.os, "dup2", lambda a, b: calls.__setitem__("dup2", (a, b))
    )
    monkeypatch.setattr(entry.os, "close", lambda fd: calls.__setitem__("close", fd))
    monkeypatch.setattr(entry.os, "fdopen", lambda fd, mode: f"stream-{fd}-{mode}")
    # The function reassigns sys.stdin; record the original so monkeypatch restores it.
    monkeypatch.setattr(sys, "stdin", sys.stdin)

    entry.ensure_stdin_fd()

    assert calls["dup2"] == (7, 0)  # duped onto fd 0
    assert calls["close"] == 7  # temp fd closed
    assert sys.stdin == "stream-0-r"  # stdin rebound to the fresh fd 0


def test_logger_factory_default_is_stdlib():
    from agentclaw.community import log

    assert isinstance(log.get_logger("x"), logging.Logger)


def test_set_logger_factory_overrides_default(monkeypatch):
    from agentclaw.community import log

    # Record the original so monkeypatch restores it (avoid cross-test leak).
    monkeypatch.setattr(log, "_factory", log._factory)

    sentinel = object()
    log.set_logger_factory(lambda name: sentinel)
    assert log.get_logger("anything") is sentinel


def test_require_local_profile_accepts_every_community_profile():
    from agentclaw.community import main

    for profile in (
        DeployProfile.SINGLEBOX,
        DeployProfile.TEST,
        DeployProfile.CORP_TEST,
        DeployProfile.COMMUNITY,
    ):
        # Does not raise.
        main._require_local_profile(profile)


def test_require_local_profile_rejects_corp():
    from agentclaw.community import main

    with pytest.raises(RuntimeError, match="agentclaw/corp/main.py"):
        main._require_local_profile(DeployProfile.CORP)


def test_runtime_config_logging_redacts_literal_runtime_credentials():
    from agentclaw.community import main

    source = {
        "user_config": {
            "openapi_bot": {
                "api_key_secret": "test-token",
                "api_key_secret_prod": "test-token",
                "api_key_prefix": "pref0001",
            },
            "bcs_client": {
                "token_secret": "test-token",
                "secret_secret": "test-token",
                "base_url": "http://127.0.0.1:21000",
            },
        }
    }
    redacted = main._redact_runtime_config_for_log(source)

    assert redacted == {
        "user_config": {
            "openapi_bot": {
                "api_key_secret": "<redacted>",
                "api_key_secret_prod": "<redacted>",
                "api_key_prefix": "pref0001",
            },
            "bcs_client": {
                "token_secret": "<redacted>",
                "secret_secret": "<redacted>",
                "base_url": "http://127.0.0.1:21000",
            },
        }
    }
    # The logger must receive a copy, not a mutation of the live config dump.
    assert source["user_config"]["openapi_bot"]["api_key_secret"] == "test-token"
    assert source["user_config"]["openapi_bot"]["api_key_secret_prod"] == "test-token"
    assert source["user_config"]["bcs_client"]["token_secret"] == "test-token"


def _attach_handlers(logger: logging.Logger, handlers: list[logging.Handler]) -> None:
    logger.handlers[:] = handlers
    logger.setLevel(logging.DEBUG)
    logger.propagate = False


def _close_handlers(handlers: list[logging.Handler]) -> None:
    for handler in handlers:
        handler.close()


def test_file_handlers_create_expected_files_and_filter_levels(
    tmp_path, restore_logger_state
):
    from agentclaw.community import main

    log_dir, handlers = main._build_logging_handlers(
        {
            "app_name": "agentclaw",
            "log_config": {"log_dir": str(tmp_path / "logs")},
        }
    )
    assert log_dir == (tmp_path / "logs").resolve()
    assert len(handlers) == 5
    console = handlers[0]
    assert type(console) is logging.StreamHandler
    assert console.level == logging.DEBUG

    files = handlers[1:]
    assert all(isinstance(handler, TimedRotatingFileHandler) for handler in files)
    assert [handler.level for handler in files] == [
        logging.DEBUG,
        logging.ERROR,
        logging.CRITICAL,
        logging.ERROR,
    ]
    assert all(handler.when.lower() == "midnight" for handler in files)
    assert all(handler.backupCount == 7 for handler in files)
    assert all(handler.encoding == "utf-8" for handler in files)

    probe = logging.getLogger("agentclaw.test.log-probe")
    uvicorn_access = logging.getLogger("uvicorn.access")
    _attach_handlers(probe, handlers)
    _attach_handlers(uvicorn_access, handlers)
    try:
        probe.info("probe-info")
        probe.error("probe-error")
        probe.critical("probe-fatal")
        uvicorn_access.info("access-record")
        for handler in handlers:
            handler.flush()
    finally:
        _close_handlers(handlers)
        probe.handlers.clear()
        probe.propagate = True
        uvicorn_access.handlers.clear()
        uvicorn_access.propagate = True

    full_log = (tmp_path / "logs" / "agentclaw.log").read_text(encoding="utf-8")
    error_log = (tmp_path / "logs" / "agentclaw-error.log").read_text(encoding="utf-8")
    fatal_log = (tmp_path / "logs" / "agentclaw-fatal.log").read_text(encoding="utf-8")
    common_error = (tmp_path / "logs" / "common-error.log").read_text(encoding="utf-8")
    assert "probe-info" in full_log
    assert "probe-error" in full_log
    assert "probe-fatal" in full_log
    assert "access-record" in full_log
    assert "probe-info" not in error_log
    assert "probe-error" in error_log
    assert "probe-fatal" in error_log
    assert "probe-error" not in fatal_log
    assert "probe-fatal" in fatal_log
    assert "probe-info" not in common_error
    assert "probe-error" in common_error
    assert "probe-fatal" in common_error


def test_configure_logging_installs_five_handlers_idempotently(tmp_path, monkeypatch):
    from agentclaw.community import main

    calls: list[list[logging.Handler]] = []

    def fake_basic_config(**kwargs):
        calls.append(list(kwargs["handlers"]))

    monkeypatch.setattr(logging, "basicConfig", fake_basic_config)
    config = {
        "app_name": "agentclaw",
        "log_config": {"log_dir": str(tmp_path / "logs")},
    }
    main._configure_logging(config)
    main._configure_logging(config)

    assert len(calls) == 2
    assert all(len(handlers) == 5 for handlers in calls)
    for handlers in calls:
        _close_handlers(handlers)


def test_log_dir_priority_and_path_validation(tmp_path, monkeypatch):
    from agentclaw.community import main

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("AGENTCLAW_LOG_DIR", raising=False)

    assert (
        main._resolve_log_dir({"app_name": "agentclaw", "log_config": {"log_dir": ""}})
        == (tmp_path / "home" / "logs" / "agentclaw").resolve()
    )
    assert (
        main._resolve_log_dir(
            {"app_name": "agentclaw", "log_config": {"log_dir": "~/custom"}}
        )
        == (tmp_path / "home" / "custom").resolve()
    )
    assert (
        main._resolve_log_dir({"app_name": "agentclaw", "log_config": {"log_dir": 123}})
        == (tmp_path / "home" / "logs" / "agentclaw").resolve()
    )
    assert (
        main._resolve_log_dir({"app_name": "../evil", "log_config": {"log_dir": ""}})
        == (tmp_path / "home" / "logs" / "agentclaw").resolve()
    )

    monkeypatch.setenv("AGENTCLAW_LOG_DIR", str(tmp_path / "override"))
    assert (
        main._resolve_log_dir(
            {"app_name": "agentclaw", "log_config": {"log_dir": "~/custom"}}
        )
        == (tmp_path / "override").resolve()
    )

    monkeypatch.delenv("AGENTCLAW_LOG_DIR", raising=False)
    relative = main._resolve_log_dir(
        {"app_name": "agentclaw", "log_config": {"log_dir": "relative/logs"}}
    )
    assert relative.is_absolute()
    assert relative.name == "logs"


def test_logging_directory_failure_is_fail_fast(tmp_path):
    from agentclaw.community import main

    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x", encoding="utf-8")
    with pytest.raises(OSError):
        main._build_logging_handlers(
            {"app_name": "agentclaw", "log_config": {"log_dir": str(blocker)}}
        )


def test_partial_file_handler_creation_closes_created_handlers(tmp_path, monkeypatch):
    from agentclaw.community import main

    created = []

    class FakeHandler:
        def __init__(self, *args, **kwargs):
            self.closed = False
            created.append(self)

        def setLevel(self, level):
            self.level = level

        def setFormatter(self, formatter):
            self.formatter = formatter

        def close(self):
            self.closed = True

    def fail_on_third_handler(*args, **kwargs):
        if len(created) == 2:
            raise OSError("handler construction failed")
        return FakeHandler()

    monkeypatch.setattr(main, "TimedRotatingFileHandler", fail_on_third_handler)
    with pytest.raises(OSError, match="handler construction failed"):
        main._file_handlers(tmp_path / "logs", "agentclaw")
    assert len(created) == 2
    assert all(handler.closed for handler in created)


def test_runtime_config_is_console_only_and_redacted(
    tmp_path, capsys, restore_logger_state
):
    from agentclaw.community import main

    probe_token = "test-token"
    bcn_admin_field = "provider_admin_" + "token"
    query_url = "https://host/path?" + "token=" + probe_token + "&safe=ok"
    query_keys_url = (
        "https://host/path?"
        + "&".join(
            f"{key}={probe_token}"
            for key in (
                "key",
                "auth",
                "pwd",
                "sig",
                "authorization",
                "apikey",
                "secretkey",
                "accesskey",
            )
        )
        + "&safe=ok"
    )
    config = {
        "app_name": "agentclaw",
        "log_config": {"log_dir": str(tmp_path / "logs")},
        "user_config": {
            "auth_endpoint": "https://auth.example",
            "secret_name": "AUTH_SECRET_NAME",
            "base_url": "https://service.example",
            "database": {
                "url": "mysql+pymysql://user:" + probe_token + "@db.example/app"
            },
            "outbound_rules": {
                "header_rules": [{"header_name": "Authorization", "value": probe_token}]
            },
            "bcn": {bcn_admin_field: probe_token},
            "secret_key": probe_token,
            "token_value": probe_token,
            "password_hash": probe_token,
            "private_key_value": probe_token,
            "access_key_value": probe_token,
            "api_key_value": probe_token,
            "malformed_url": "mysql+pymysql://user:" + probe_token + "@db:abc/app",
            "query_url": query_url,
            "query_keys_url": query_keys_url,
            "userinfo_url": "https://" + probe_token + "@host/path",
            "scheme_less_url": "user:" + probe_token + "@host",
            "fragment_url": "https://host/path#" + "token=" + probe_token,
        },
    }
    log_dir, handlers = main._build_logging_handlers(config)
    probe = logging.getLogger("agentclaw.test.config-probe")
    _attach_handlers(probe, handlers)
    try:
        main._log_runtime_config_console_only(config)
        probe.info("ordinary application record")
        for handler in handlers:
            handler.flush()
    finally:
        _close_handlers(handlers)
        probe.handlers.clear()
        probe.propagate = True

    redacted = main._redact_runtime_config_for_log(config)
    user_config = redacted["user_config"]
    assert user_config["secret_key"] == main._REDACTED_FOR_LOG
    assert user_config["token_value"] == main._REDACTED_FOR_LOG
    assert user_config["password_hash"] == main._REDACTED_FOR_LOG
    assert user_config["private_key_value"] == main._REDACTED_FOR_LOG
    assert user_config["access_key_value"] == main._REDACTED_FOR_LOG
    assert user_config["api_key_value"] == main._REDACTED_FOR_LOG
    assert user_config["malformed_url"] == "<redacted-url>"
    assert user_config["scheme_less_url"] == "<redacted-url>"
    assert user_config["auth_endpoint"] == "https://auth.example"
    assert user_config["secret_name"] == "AUTH_SECRET_NAME"
    assert user_config["base_url"] == "https://service.example"

    captured = capsys.readouterr()
    assert captured.err.count("runtime config (local)") == 1
    assert len(logging.getLogger(main._RUNTIME_CONFIG_LOGGER).handlers) == 1
    assert "<redacted>" in captured.err
    assert "<redacted-url>" in captured.err
    assert "safe=ok" in captured.err
    assert probe_token not in captured.err
    assert "AUTH_SECRET_NAME" in captured.err
    for path in log_dir.glob("*.log"):
        assert probe_token not in path.read_text(encoding="utf-8")


def test_real_community_config_secrets_do_not_reach_log_files(
    tmp_path, monkeypatch, capsys, restore_logger_state
):
    from agentclaw.community import main
    from agentclaw.community.core.config.yaml_provider import YamlConfigProvider
    from agentclaw.community.di.profile import DeployProfile

    monkeypatch.setenv("AGENTCLAW_LOG_DIR", str(tmp_path / "logs"))
    probe_token = "test-token"
    monkeypatch.setenv(
        "DATABASE_URL", "mysql+pymysql://user:" + probe_token + "@db/app"
    )
    monkeypatch.setenv("BCS_URL", "http://bcs.invalid")
    monkeypatch.setenv("BCS_PROVIDER_ID", "provider-visible")
    monkeypatch.setenv("BCS_ADMIN_TOKEN", probe_token)
    monkeypatch.setenv("MODEL_API", "model.invalid")
    monkeypatch.setenv("MODEL_KEY", probe_token)
    monkeypatch.setenv("api_key_secret", probe_token)
    monkeypatch.setenv("api_key_prefix", "public-prefix")

    config = YamlConfigProvider(DeployProfile.COMMUNITY).load()
    config_dump = config.model_dump()
    log_dir, handlers = main._build_logging_handlers(config_dump)
    probe = logging.getLogger("agentclaw.test.real-config-probe")
    _attach_handlers(probe, handlers)
    try:
        main._log_runtime_config_console_only(config_dump)
        probe.info("ordinary application record")
        for handler in handlers:
            handler.flush()
    finally:
        _close_handlers(handlers)
        probe.handlers.clear()
        probe.propagate = True

    captured = capsys.readouterr()
    assert probe_token not in captured.err
    for path in log_dir.glob("*.log"):
        assert probe_token not in path.read_text(encoding="utf-8")
    assert "provider-visible" in captured.err
    assert "public-prefix" in captured.err
    assert "<redacted>" in captured.err


def test_real_root_logging_and_uvicorn_access_subprocess(tmp_path):
    backend_root = Path(__file__).resolve().parents[2]
    log_dir = tmp_path / "logs"
    sentinel = "runtime-config-sentinel"
    code = f"""
import logging
import os
from agentclaw.community import main

main._configure_logging({{"app_name": "agentclaw", "log_config": {{"log_dir": {str(log_dir)!r}}}}})
main._configure_logging({{"app_name": "agentclaw", "log_config": {{"log_dir": {str(log_dir)!r}}}}})
root = logging.getLogger()
assert root.level == logging.DEBUG
assert len(root.handlers) == 5
assert sum(type(handler) is logging.StreamHandler for handler in root.handlers) == 1
assert all(handler.formatter._fmt == main._LOG_FORMAT for handler in root.handlers)
logging.getLogger("uvicorn.access").info("uvicorn-access-subprocess")
main._log_runtime_config_console_only(
    {{
        "app_name": "agentclaw",
        "log_config": {{"log_dir": {str(log_dir)!r}}},
        "user_config": {{"bcn": {{"provider_admin_token": {sentinel!r}}}}},
    }}
)
config_logger = logging.getLogger(main._RUNTIME_CONFIG_LOGGER)
assert config_logger.propagate is False
assert len(config_logger.handlers) == 1
for handler in root.handlers:
    handler.flush()
"""
    env = os.environ.copy()
    env.update(
        {
            "AGENTCLAW_LOG_DIR": str(log_dir),
            "DEPLOY_PROFILE": "test",
            "PYTHONPATH": str(backend_root / "src"),
        }
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=backend_root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    full_log = (log_dir / "agentclaw.log").read_text(encoding="utf-8")
    assert "uvicorn-access-subprocess" in full_log
    assert sentinel not in "".join(
        path.read_text(encoding="utf-8") for path in log_dir.glob("*.log")
    )


def test_main_registers_profile_provider_before_reading_sofa_config(
    monkeypatch, tmp_path, restore_logger_state
):
    from agentclaw.community import _entry, local, main
    from agentclaw.community.core.config import provider as config_provider
    from agentclaw.community.core.config.yaml_provider import YamlConfigProvider

    monkeypatch.setenv("DEPLOY_PROFILE", "singlebox")
    monkeypatch.setenv("SERVER_ENV", "prod")
    monkeypatch.setenv("AGENTCLAW_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(sys, "argv", [main.__file__])
    monkeypatch.setattr(_entry, "ensure_stdin_fd", lambda: None)
    monkeypatch.setattr(local, "patch_sofapy_for_local", lambda: None)
    monkeypatch.setattr(config_provider, "_provider", None)
    monkeypatch.setattr(config_provider, "_cached", None)

    events: list[str] = []
    captured_handlers: list[logging.Handler] = []

    def fake_basic_config(**kwargs):
        events.append("logging-configured")
        handlers = kwargs.get("handlers") or []
        if len(handlers) > 1:
            captured_handlers.extend(handlers)

    monkeypatch.setattr(logging, "basicConfig", fake_basic_config)

    class ConfigProbe:
        def model_dump(self):
            provider = config_provider._provider
            assert isinstance(provider, YamlConfigProvider)
            assert provider.overlay_name == "application-singlebox.yaml"
            events.append("config-read")
            return {
                "app_name": "agentclaw",
                "log_config": {"log_dir": ""},
                "user_config": {},
            }

    sofa_module = types.ModuleType("agentclaw.community.core.config.sofa")
    sofa_module.sofa_config = ConfigProbe()
    monkeypatch.setitem(
        sys.modules,
        "agentclaw.community.core.config.sofa",
        sofa_module,
    )

    app_module = types.ModuleType("agentclaw.community.adapters.http.app")
    app_module.app = object()
    monkeypatch.setitem(
        sys.modules,
        "agentclaw.community.adapters.http.app",
        app_module,
    )

    uvicorn_calls: list[dict[str, object]] = []
    uvicorn_module = types.ModuleType("uvicorn")
    uvicorn_module.run = lambda *args, **kwargs: (
        uvicorn_calls.append(kwargs),
        events.append("uvicorn-run"),
    )
    monkeypatch.setitem(sys.modules, "uvicorn", uvicorn_module)

    try:
        runpy.run_path(main.__file__, run_name="__main__")
    finally:
        _close_handlers(captured_handlers)

    assert events[0] == "logging-configured"
    assert events.index("config-read") < events.index("uvicorn-run")
    assert events.count("logging-configured") == 2
    assert len(captured_handlers) == 5
    assert uvicorn_calls[0]["log_config"] is None
