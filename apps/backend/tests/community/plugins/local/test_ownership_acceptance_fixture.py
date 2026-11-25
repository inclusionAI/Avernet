"""Contract of the opt-in acceptance seam, never a production handoff fake."""
from __future__ import annotations

import importlib
import runpy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

BASE = Path(__file__).parents[2] / "acceptance/bot_ownership_sync"
PACKAGE = "tests.community.acceptance.bot_ownership_sync"


@pytest.fixture
def seam():
    assert (BASE / "fixture_module.py").is_file(), "acceptance DI seam is missing"
    return importlib.import_module(f"{PACKAGE}.fixture_module")


@pytest.fixture
def passport(seam):
    module = importlib.import_module(f"{PACKAGE}.fixture_passport")
    return module.FixturePassport(Mock()), module


def test_fixture_scope_and_nonfixture_pass_through(passport):
    plugin, module = passport
    delegate = plugin.delegate
    delegate.owner_handoff_supported.return_value = False
    assert not plugin.owner_handoff_supported(bot_id="ordinary", owner_workno="u", entity_id="u")
    delegate.owner_handoff_supported.assert_called_once()
    assert plugin.owner_handoff_supported(bot_id=module.PREFIX + "bot", owner_workno=module.PREFIX + "old", entity_id=module.PREFIX + "old")
    assert not plugin.owner_handoff_supported(bot_id=module.PREFIX + "bot", owner_workno="ordinary", entity_id="ordinary")
    plugin.update_passport("ordinary", "u")
    delegate.update_passport.assert_called_once_with("ordinary", "u")
    plugin.query_auth_status("ordinary", "u", entity_id="u")
    delegate.query_auth_status.assert_called_once_with("ordinary", "u", entity_id="u")


def test_correlated_generation_is_idempotent_and_fenced(passport):
    plugin, module = passport
    from agentclaw.community.plugin_api.passport import PassportError
    bot, old, new = (module.PREFIX + value for value in ("bot", "old", "new"))
    before = plugin.query_agent_passport(bot, old, entity_id=old)
    args = dict(bot_id=bot, owner_workno=new, entity_id=old, execution_workno=new,
                handoff_operation_id="operation-1", expected_credential_id=before["credential_id"])
    first = plugin.reissue_agent_credentials(**args)
    assert plugin.reissue_agent_credentials(**args) == first
    selected = plugin.query_agent_passport(bot, old, entity_id=old)
    assert selected["credential_id"] != before["credential_id"]
    assert selected["handoff_operation_id"] == "operation-1"
    assert selected["execution_workno"] == new
    selected["credential_id"] = "caller-mutated-copy"
    assert plugin.query_agent_passport(bot, old, entity_id=old)["credential_id"] == first["credential_id"]
    for changes in ({"expected_credential_id": "wrong"}, {"execution_workno": old}, {"handoff_operation_id": "operation-2"}):
        with pytest.raises(PassportError):
            plugin.reissue_agent_credentials(**(args | changes))
    retire = dict(bot_id=bot, owner_workno=new, entity_id=old,
                  keep_credential_id=first["credential_id"], handoff_operation_id="operation-1")
    with pytest.raises(PassportError):
        plugin.retire_agent_credentials(**(retire | {"keep_credential_id": before["credential_id"]}))
    plugin.retire_agent_credentials(**retire)
    plugin.retire_agent_credentials(**retire)
    auth = plugin.query_auth_status(bot, old, entity_id=old)
    assert auth["status"] == "ISSUED" and auth["token"]
    assert plugin.query_agent_passport(bot, old, entity_id=module.PREFIX + "other")["credential_id"] == before["credential_id"]


def test_directory_only_names_registered_fixture_users(seam):
    from agentclaw.community.core.access.errors import UserNotFoundError
    users, delegate = Mock(), Mock()
    directory = seam.FixtureDirectory(users, delegate)
    known = seam.PREFIX + "registered"
    users.get_user.return_value = SimpleNamespace(user_id=known, status="ACCESS")
    assert directory.get_user_by_work_no(work_no=known).display_name == known
    users.get_user.assert_called_once_with(user_id=known, user_type="NORMAL")
    users.get_user.side_effect = UserNotFoundError("missing", "NORMAL")
    assert directory.get_user_by_work_no(work_no=seam.PREFIX + "unknown").display_name is None
    sentinel = object()
    delegate.get_user_by_work_no.return_value = sentinel
    assert directory.get_user_by_work_no(work_no="ordinary") is sentinel
    directory.get_dept_by_work_no(work_no="ordinary")
    delegate.get_dept_by_work_no.assert_called_once_with(work_no="ordinary")


def test_runtime_defers_bot_service_resolution_and_forwards(seam):
    injector = Mock()
    updater = seam.FixtureRuntime(injector)
    injector.get.assert_not_called()
    assert updater.hot_update_passport_token_to_device(seam.PREFIX + "bot", seam.PREFIX + "old", "synthetic") == {"bindings": [{"device_count": 1}]}
    injector.get.assert_not_called()
    result = updater.hot_update_passport_token_to_device("ordinary", "u", "opaque")
    assert result is injector.get.return_value.hot_update_passport_token_to_device.return_value
    injector.get.return_value.hot_update_passport_token_to_device.assert_called_once_with(bot_id="ordinary", user_id="u", token="opaque")


@pytest.mark.parametrize("profile,coverage,enabled", [("test", "1", "1"), ("corp", "1", "1"), ("singlebox", "0", "1"), ("singlebox", "1", "0")])
def test_bootstrap_guards_do_not_patch_aliases(seam, monkeypatch, profile, coverage, enabled):
    import agentclaw.community.di as di
    import agentclaw.community.di.container as container
    original = di.build_injector
    monkeypatch.setenv("DEPLOY_PROFILE", profile)
    monkeypatch.setenv("SINGLEBOX_COVERAGE", coverage)
    monkeypatch.setenv("SINGLEBOX_ACCEPTANCE_OWNER_HANDOFF", enabled)
    runpy.run_path(str(BASE / "runtime/sitecustomize.py"))
    assert di.build_injector is original
    assert container.build_injector is original


def test_bootstrap_wraps_both_aliases_preserving_extra_modules(seam, monkeypatch):
    import agentclaw.community.di as di
    import agentclaw.community.di.container as container
    from agentclaw.community.di.profile import DeployProfile
    original = Mock(return_value=object())
    monkeypatch.setattr(di, "build_injector", original)
    monkeypatch.setattr(container, "build_injector", original)
    monkeypatch.setenv("DEPLOY_PROFILE", "singlebox")
    monkeypatch.setenv("SINGLEBOX_COVERAGE", "1")
    monkeypatch.setenv("SINGLEBOX_ACCEPTANCE_OWNER_HANDOFF", "1")
    runpy.run_path(str(BASE / "runtime/sitecustomize.py"))
    assert di.build_injector is container.build_injector
    extra = object()
    di.build_injector(profile=DeployProfile.SINGLEBOX, extra_modules=iter([extra]))
    modules = original.call_args.kwargs["extra_modules"]
    assert modules[0] is extra and isinstance(modules[1], seam.OwnerHandoffFixtureModule)
    assert original.call_count == 1
    # Reimporting sitecustomize must not wrap a wrapper.
    wrapped = di.build_injector
    runpy.run_path(str(BASE / "runtime/sitecustomize.py"))
    assert di.build_injector is wrapped
    di.build_injector(profile=DeployProfile.TEST)
    assert original.call_args.kwargs.get("extra_modules") is None


def test_actual_di_installs_only_boundary_overrides(seam, monkeypatch):
    from agentclaw.community.di import DeployProfile, build_injector
    from agentclaw.community.di.bot_manager_sync_config import ManagerSyncConfig
    from agentclaw.community.di.config import TaskQueueWorkerConfig
    from agentclaw.community.core.bot_management.owner_handoff.contracts import OwnerHandoffPolicy
    from agentclaw.community.plugin_api.passport import PassportPlugin
    from agentclaw.community.plugin_api.staff_dept import StaffDeptPlugin
    from agentclaw.community.core.execution_identity.protocols import RuntimePassportTokenUpdaterProtocol
    from agentclaw.community.core.task_queue.services.task_queue_service import TaskQueueService
    from agentclaw.community.core.bot_management.owner_handoff.service import OwnerHandoffService
    from agentclaw.community.core.bot_management.owner_handoff.contracts import OwnerHandoffServiceProtocol
    from injector import Module
    class Queue(Module):
        def configure(self, binder):
            binder.bind(TaskQueueWorkerConfig, to=TaskQueueWorkerConfig(enabled=True))
    import agentclaw.community.di.container as container
    monkeypatch.setattr(container, "_app_injector", container._app_injector)
    monkeypatch.setattr(seam, "read_user_config", lambda: {"bot_manager_sync": {"enabled": True, "env": "dev", "bcs_base_url": "http://127.0.0.1:21000"}})
    injector = build_injector(profile=DeployProfile.SINGLEBOX, extra_modules=[Queue(), seam.OwnerHandoffFixtureModule(enable_fault_proxy=False)])
    assert injector.get(OwnerHandoffPolicy).recovery_enabled
    assert injector.get(ManagerSyncConfig).ownership_enabled
    assert isinstance(injector.get(PassportPlugin), seam.FixturePassport)
    assert isinstance(injector.get(StaffDeptPlugin), seam.FixtureDirectory)
    assert isinstance(injector.get(RuntimePassportTokenUpdaterProtocol), seam.FixtureRuntime)
    assert isinstance(injector.get(TaskQueueService), TaskQueueService)
    assert isinstance(injector.get(OwnerHandoffServiceProtocol), OwnerHandoffService)


def test_acceptance_runtime_is_present():
    assert (BASE / "fixture_module.py").is_file(), "acceptance DI seam is missing"
    assert (BASE / "runtime/sitecustomize.py").is_file(), "acceptance boot seam is missing"


def test_singlebox_boot_and_coverage_explicitly_load_fixture():
    root = BASE.parents[5]
    backend = (root / "singlebox/modules/backend.sh").read_text()
    coverage = (root / "singlebox/ci/singlebox_coverage.sh").read_text()
    assert 'bot_ownership_sync/runtime' in backend
    assert 'SINGLEBOX_ACCEPTANCE_OWNER_HANDOFF' in backend
    assert 'SINGLEBOX_ACCEPTANCE_OWNER_HANDOFF="${SINGLEBOX_ACCEPTANCE_OWNER_HANDOFF:-1}"' in coverage


def test_repair_driver_requires_existing_fixture_database(tmp_path):
    path = BASE / "repair_driver.py"
    assert path.exists(), "maintenance driver is missing"
    driver = runpy.run_path(str(path), run_name="test_repair_driver")
    with pytest.raises(ValueError, match="existing"):
        driver["validate_database"]("sqlite:///:memory:", tmp_path)
    with pytest.raises(ValueError, match="existing"):
        driver["validate_database"]("sqlite:///" + str(tmp_path / "missing.db"), tmp_path)


def test_enabled_bootstrap_fails_closed_when_fixture_import_missing(seam, monkeypatch):
    import builtins
    original = builtins.__import__
    def missing(name, *args, **kwargs):
        if name == PACKAGE + ".fixture_module":
            raise ModuleNotFoundError("fixture missing")
        return original(name, *args, **kwargs)
    monkeypatch.setenv("DEPLOY_PROFILE", "singlebox")
    monkeypatch.setenv("SINGLEBOX_COVERAGE", "1")
    monkeypatch.setenv("SINGLEBOX_ACCEPTANCE_OWNER_HANDOFF", "1")
    monkeypatch.setattr(builtins, "__import__", missing)
    with pytest.raises(SystemExit, match="fixture bootstrap failed"):
        runpy.run_path(str(BASE / "runtime/sitecustomize.py"))


def test_concurrent_reissue_replays_one_generation_without_logging_token(passport, caplog):
    caplog.set_level("DEBUG")
    from concurrent.futures import ThreadPoolExecutor
    plugin, module = passport
    bot, old, new = (module.PREFIX + x for x in ("bot", "old", "new"))
    args = dict(bot_id=bot, owner_workno=new, entity_id=old, execution_workno=new,
                handoff_operation_id="parallel-operation", expected_credential_id="fixture-generation-0")
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: plugin.reissue_agent_credentials(**args), range(8)))
    assert all(result == results[0] for result in results)
    token = plugin.query_auth_status(bot, old, entity_id=old)["token"]
    assert token not in caplog.text


def test_nonfixture_passport_reissue_metadata_retirement_are_forwarded(passport):
    plugin, _ = passport
    args = dict(bot_id="ordinary", owner_workno="u", entity_id="stable",
                execution_workno="executor", handoff_operation_id="op", expected_credential_id="old")
    plugin.reissue_agent_credentials(**args)
    plugin.delegate.reissue_agent_credentials.assert_called_once_with(**args)
    plugin.query_agent_passport("ordinary", "u", entity_id="stable")
    plugin.delegate.query_agent_passport.assert_called_once_with("ordinary", "u", entity_id="stable")
    retire = dict(bot_id="ordinary", owner_workno="u", entity_id="stable",
                  keep_credential_id="selected", handoff_operation_id="op")
    plugin.retire_agent_credentials(**retire)
    plugin.delegate.retire_agent_credentials.assert_called_once_with(**retire)


def test_directory_propagates_storage_errors_and_rejects_refused_user(seam):
    users = Mock()
    directory = seam.FixtureDirectory(users, Mock())
    user = seam.PREFIX + "user"
    users.get_user.return_value = SimpleNamespace(user_id=user, status="REFUSE")
    assert directory.get_user_by_work_no(work_no=user).display_name is None
    users.get_user.side_effect = RuntimeError("storage unavailable")
    with pytest.raises(RuntimeError, match="storage unavailable"):
        directory.get_user_by_work_no(work_no=user)


def test_repair_database_preflight_accepts_only_existing_file_under_root(tmp_path):
    import sqlite3
    driver = runpy.run_path(str(BASE / "repair_driver.py"), run_name="test_repair_driver")
    path = tmp_path / "fixture.db"
    with sqlite3.connect(path):
        pass
    assert driver["validate_database"](f"sqlite:///{path}", tmp_path) == path
    with pytest.raises(ValueError, match="existing"):
        driver["validate_database"](f"sqlite:///{path}", tmp_path / "other")


def test_cold_sitecustomize_defers_application_imports_until_coverage_starts():
    import os
    import subprocess
    import sys
    env = os.environ.copy()
    backend = BASE.parents[3]
    env.update({"DEPLOY_PROFILE": "singlebox", "SINGLEBOX_COVERAGE": "1",
                "SINGLEBOX_ACCEPTANCE_OWNER_HANDOFF": "1",
                "PYTHONPATH": f"{BASE / 'runtime'}:{backend / 'src'}:{backend}"})
    result = subprocess.run([sys.executable, "-c", '''
import sys
assert "agentclaw.community.di" not in sys.modules, "fixture imported Core before coverage"
import agentclaw.community.di as di
import agentclaw.community.di.container as container
assert di.build_injector is container.build_injector
assert di.build_injector._owner_acceptance_installed is True
'''], env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_fixture_staff_ids_fit_bcs_registration_grammar(seam):
    # /me/ensure-human rejects any non-ASCII-alphanumeric staff number.
    assert seam.PREFIX.isascii() and seam.PREFIX.isalnum()
    from tests.community.acceptance.bot_ownership_sync.live_case import fixture_staff_id
    for role in ("old", "new", "member"):
        value = fixture_staff_id(role, "012345abcdef")
        assert value.isascii() and value.isalnum() and value.startswith(seam.PREFIX)


def test_live_case_close_does_not_use_forbidden_tc_leave_as_deletion():
    from tests.community.acceptance.bot_ownership_sync.live_case import OwnershipCase
    case = OwnershipCase.__new__(OwnershipCase)
    case.backend, case.bcs = Mock(), Mock()
    case._registered_bot, case.operation = True, ""
    case.target, case.old_owner = "fixture:old", "fixtureold"
    case.bcs.delete.return_value.status_code = 403
    case.close()
    case.bcs.delete.assert_not_called()
    case.bcs.close.assert_called_once()
    case.backend.close.assert_called_once()


def test_transient_proxy_forwards_real_http_and_drops_exactly_once(caplog):
    caplog.set_level("DEBUG")
    import asyncio
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    import httpx
    path = BASE / "fixture_proxy.py"
    assert path.exists(), "bounded acceptance fault proxy is missing"
    proxy_module = importlib.import_module(PACKAGE + ".fixture_proxy")
    requests = []
    class Upstream(BaseHTTPRequestHandler):
        def do_PUT(self):
            requests.append((self.path, self.headers.get("X-Avernet-Principal"), self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"upstream":true}')
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    proxy = proxy_module.OwnershipFaultProxy(f"http://127.0.0.1:{server.server_port}")
    asyncio.run(proxy.bootstrap())
    try:
        with httpx.Client(base_url=proxy.origin, trust_env=False, timeout=5) as client:
            headers = {"X-Avernet-Principal": "synthetic-unit-principal"}
            target = f"/api/v1/collaboration/bots/{proxy_module.PREFIX}transienttest%3A{proxy_module.PREFIX}old/ownership"
            with pytest.raises(httpx.RemoteProtocolError):
                client.put(target, content=b'{"owner_user_id":"new"}', headers=headers)
            assert requests == []
            for _ in range(2):
                assert client.put(target, content=b'{"owner_user_id":"new"}', headers=headers).json() == {"upstream": True}
            assert client.put("/api/v1/collaboration/bots/ordinary%3Astable/ownership", content=b'{}', headers=headers).status_code == 200
            assert client.put(target.replace("/ownership", "/managers"), content=b'{}', headers=headers).status_code == 200
        assert len(requests) == 4
        assert all(principal == "synthetic-unit-principal" for _, principal, _ in requests)
        assert "synthetic-unit-principal" not in caplog.text
        assert requests[0][2] == b'{"owner_user_id":"new"}'
    finally:
        asyncio.run(proxy.teardown())
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert not any(t.name == "ownership-acceptance-proxy" for t in threading.enumerate())


@pytest.mark.parametrize("origin", ["https://example.test", "http://example.test", "http://127.0.0.1/path", "http://user:pass@127.0.0.1"])
def test_fault_proxy_rejects_nonlocal_or_nonorigin_targets(origin):
    assert (BASE / "fixture_proxy.py").exists(), "bounded acceptance fault proxy is missing"
    module = importlib.import_module(PACKAGE + ".fixture_proxy")
    with pytest.raises(ValueError, match="loopback"):
        module.OwnershipFaultProxy(origin)


def test_manual_driver_cannot_inherit_coverage_autostart(monkeypatch, tmp_path):
    module = importlib.import_module(PACKAGE + ".live_case")
    assert hasattr(module, "maintenance_environment"), "maintenance coverage isolation helper missing"
    for key in ("COVERAGE_PROCESS_START", "COVERAGE_FILE", "COV_CORE_SOURCE", "COV_CORE_CONFIG", "COV_CORE_DATAFILE"):
        monkeypatch.setenv(key, "must-not-reach-manual-driver")
    env = module.maintenance_environment(tmp_path / "backend.db")
    assert not any(key.startswith("COV_CORE_") or key.startswith("COVERAGE_") for key in env)
    assert env["DATABASE_URL"] == f"sqlite:///{tmp_path / 'backend.db'}"
    assert "bot_ownership_sync/runtime" not in env["PYTHONPATH"]
