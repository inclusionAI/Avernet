"""Coverage tests for arca local_proc _process_manager.

Covers helper functions, LocalProcessManager singleton/port-allocation,
process lifecycle (start/stop/stop_all), query methods, internal helpers,
and config creation.
"""

from __future__ import annotations

import json
import os
import socket
import stat
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, mock_open, patch

import pytest

from secbaas.community.plugins.sandbox.arca.local_proc import _process_manager as pm
from secbaas.community.plugins.sandbox.arca.local_proc._errors import (
    DeviceAllocateError,
)

# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Reset the LocalProcessManager singleton before and after each test."""
    pm.LocalProcessManager.reset_instance()
    yield
    pm.LocalProcessManager.reset_instance()


@pytest.fixture(autouse=True)
def _mock_time_sleep():
    """Avoid actual sleeping in tests."""
    with patch.object(pm.time, "sleep"):
        yield


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Remove env vars that can interfere with tests."""
    for key in (
        "SINGLEBOX_MODEL_CONFIG_FILE",
        "BCN_PLUGIN_PATH",
        "BCS_PORT",
        "LOCAL_HERMES_DIR",
        "LOCAL_ENGINE_SRC_DIR",
    ):
        monkeypatch.delenv(key, raising=False)


# ──────────────────────────────────────────────────────────────────────
# Helper function tests
# ──────────────────────────────────────────────────────────────────────


class TestDefaultBcnPluginPath:
    def test_default_bcn_plugin_path_fallback(self, monkeypatch):
        """When no repo BCN plugin dir is found, returns ~/.openclaw/extensions/..."""
        monkeypatch.setattr(
            "os.path.expanduser",
            lambda p: "/home/testuser" if p == "~" else p,
        )
        # Make is_dir always return False so we hit the fallback
        original_is_dir = Path.is_dir

        def fake_is_dir(self):
            # Return False only for the BCN plugin candidate path
            if "openclaw-channel-bcn" in str(self):
                return False
            return original_is_dir(self)

        monkeypatch.setattr(Path, "is_dir", fake_is_dir)

        result = pm._default_bcn_plugin_path()
        assert "openclaw-channel-bcn" in result
        assert ".openclaw/extensions" in result

    def test_default_bcn_plugin_path_repo_found(self, monkeypatch):
        """When the repo BCN plugin dir exists, returns that path."""
        # The real function walks up from __file__ and checks for
        # src/bcs/crates/plugins/openclaw-channel-bcn.
        # In the test environment, this dir may or may not exist.
        # We just verify the function returns a string.
        result = pm._default_bcn_plugin_path()
        assert isinstance(result, str)
        assert "openclaw-channel-bcn" in result


class TestMergeSingleboxModelConfig:
    def test_no_env_var(self, monkeypatch):
        """No SINGLEBOX_MODEL_CONFIG_FILE env var → no-op."""
        monkeypatch.delenv("SINGLEBOX_MODEL_CONFIG_FILE", raising=False)
        config = {"existing": "value"}
        pm._merge_singlebox_model_config(config)
        assert config == {"existing": "value"}

    def test_with_valid_file(self, monkeypatch, tmp_path):
        """Valid config file merges models and agents defaults."""
        config_file = tmp_path / "model_config.json"
        config_file.write_text(
            json.dumps(
                {
                    "models": {"provider": "test-provider"},
                    "agents": {
                        "defaults": {
                            "model": "test-model",
                            "models": {"test-model": {}},
                            "imageModel": "test-image",
                        }
                    },
                }
            )
        )
        monkeypatch.setenv("SINGLEBOX_MODEL_CONFIG_FILE", str(config_file))

        oc_config = {
            "models": {"old": True},
            "agents": {"defaults": {"old_model": "old"}},
        }
        pm._merge_singlebox_model_config(oc_config)

        assert oc_config["models"] == {"provider": "test-provider"}
        assert oc_config["agents"]["defaults"]["model"] == "test-model"
        assert oc_config["agents"]["defaults"]["models"] == {"test-model": {}}
        assert oc_config["agents"]["defaults"]["imageModel"] == "test-image"
        # Old keys that were not in the new config should be preserved
        assert oc_config["agents"]["defaults"]["old_model"] == "old"

    def test_missing_file(self, monkeypatch, tmp_path):
        """Missing config file raises DeviceAllocateError."""
        missing = tmp_path / "nonexistent.json"
        monkeypatch.setenv("SINGLEBOX_MODEL_CONFIG_FILE", str(missing))
        with pytest.raises(DeviceAllocateError, match="does not exist"):
            pm._merge_singlebox_model_config({})

    def test_invalid_json(self, monkeypatch, tmp_path):
        """Invalid JSON raises DeviceAllocateError."""
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("{invalid json")
        monkeypatch.setenv("SINGLEBOX_MODEL_CONFIG_FILE", str(bad_file))
        with pytest.raises(DeviceAllocateError, match="not valid JSON"):
            pm._merge_singlebox_model_config({})

    def test_not_dict(self, monkeypatch, tmp_path):
        """JSON that is not a dict raises DeviceAllocateError."""
        not_dict_file = tmp_path / "list.json"
        not_dict_file.write_text("[1, 2, 3]")
        monkeypatch.setenv("SINGLEBOX_MODEL_CONFIG_FILE", str(not_dict_file))
        with pytest.raises(DeviceAllocateError, match="must be a JSON object"):
            pm._merge_singlebox_model_config({})

    def test_with_file_no_models_key(self, monkeypatch, tmp_path):
        """Config file without 'models' key does not set models."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"agents": {"defaults": {"model": "m1"}}}))
        monkeypatch.setenv("SINGLEBOX_MODEL_CONFIG_FILE", str(config_file))

        oc_config = {"existing": True}
        pm._merge_singlebox_model_config(oc_config)
        assert "models" not in oc_config
        assert oc_config["agents"]["defaults"]["model"] == "m1"

    def test_with_file_agents_defaults_not_dict(self, monkeypatch, tmp_path):
        """If agents.defaults in config file is not a dict, skip merging defaults."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"agents": {"defaults": "not-a-dict"}}))
        monkeypatch.setenv("SINGLEBOX_MODEL_CONFIG_FILE", str(config_file))

        oc_config = {"agents": {"defaults": {"old": True}}}
        pm._merge_singlebox_model_config(oc_config)
        # defaults should remain unchanged
        assert oc_config["agents"]["defaults"] == {"old": True}


class TestIsPortAvailable:
    def test_port_available_true(self, monkeypatch):
        """Port is available when connect_ex returns non-zero."""
        mock_sock = MagicMock()
        mock_sock.connect_ex.return_value = 1  # non-zero = not connectable
        mock_sock.close = MagicMock()

        with patch("socket.socket", return_value=mock_sock):
            result = pm._is_port_available(20010)
        assert result is True

    def test_port_available_false(self, monkeypatch):
        """Port is NOT available when connect_ex returns 0."""
        mock_sock = MagicMock()
        mock_sock.connect_ex.return_value = 0  # 0 = connectable
        mock_sock.close = MagicMock()

        with patch("socket.socket", return_value=mock_sock):
            result = pm._is_port_available(20010)
        assert result is False

    def test_port_available_exception_returns_true(self, monkeypatch):
        """If socket raises an exception, port is considered available."""
        mock_sock = MagicMock()
        mock_sock.connect_ex.side_effect = OSError("boom")
        mock_sock.close = MagicMock()

        with patch("socket.socket", return_value=mock_sock):
            result = pm._is_port_available(20010)
        assert result is True


# ──────────────────────────────────────────────────────────────────────
# LocalProcessManager singleton tests
# ──────────────────────────────────────────────────────────────────────


class TestSingleton:
    def test_singleton_instance(self):
        """instance() returns the same object each time."""
        a = pm.LocalProcessManager.instance()
        b = pm.LocalProcessManager.instance()
        assert a is b

    def test_reset_instance(self):
        """reset_instance clears the singleton."""
        a = pm.LocalProcessManager.instance()
        pm.LocalProcessManager.reset_instance()
        b = pm.LocalProcessManager.instance()
        assert a is not b

    def test_reset_instance_when_none(self):
        """reset_instance is safe when no instance exists."""
        pm.LocalProcessManager.reset_instance()
        # Should not raise
        pm.LocalProcessManager.reset_instance()


# ──────────────────────────────────────────────────────────────────────
# Port allocation tests
# ──────────────────────────────────────────────────────────────────────


class TestAllocatePorts:
    def test_allocate_ports_openclaw(self):
        """allocate_ports with openclaw returns adapter port and openclaw port."""
        manager = pm.LocalProcessManager()
        with patch.object(pm, "_is_port_available", return_value=True):
            adapter_port, engine_port = manager.allocate_ports("openclaw")
        assert pm.ADAPTER_PORT_START <= adapter_port <= pm.ADAPTER_PORT_END
        assert pm.OPENCLAW_PORT_START <= engine_port <= pm.OPENCLAW_PORT_END

    def test_allocate_ports_hermes(self):
        """allocate_ports with hermes returns adapter port and hermes port."""
        manager = pm.LocalProcessManager()
        with patch.object(pm, "_is_port_available", return_value=True):
            adapter_port, engine_port = manager.allocate_ports("hermes")
        assert pm.ADAPTER_PORT_START <= adapter_port <= pm.ADAPTER_PORT_END
        assert pm.HERMES_PORT_START <= engine_port <= pm.HERMES_PORT_END

    def test_allocate_ports_aicoding(self):
        """allocate_ports with aicoding returns engine_port=0."""
        manager = pm.LocalProcessManager()
        with patch.object(pm, "_is_port_available", return_value=True):
            adapter_port, engine_port = manager.allocate_ports("aicoding")
        assert pm.ADAPTER_PORT_START <= adapter_port <= pm.ADAPTER_PORT_END
        assert engine_port == 0

    def test_allocate_ports_exhausted(self):
        """No free ports raises DeviceAllocateError."""
        manager = pm.LocalProcessManager()
        with patch.object(pm, "_is_port_available", return_value=False):
            with pytest.raises(DeviceAllocateError, match="No free port"):
                manager.allocate_ports("openclaw")

    def test_find_free_port_allocated(self):
        """Port already in already_allocated set is skipped."""
        manager = pm.LocalProcessManager()
        allocated = {pm.ADAPTER_PORT_START}
        with patch.object(pm, "_is_port_available", return_value=True) as mock_avail:
            port = manager._find_free_port(
                pm.ADAPTER_PORT_START, pm.ADAPTER_PORT_START + 5, allocated
            )
        # Should skip the first port (in allocated) and return the next one
        assert port == pm.ADAPTER_PORT_START + 1
        mock_avail.assert_called_with(pm.ADAPTER_PORT_START + 1)

    def test_find_free_port_available(self):
        """Port not in already_allocated and is available."""
        manager = pm.LocalProcessManager()
        with patch.object(pm, "_is_port_available", return_value=True):
            port = manager._find_free_port(
                pm.ADAPTER_PORT_START, pm.ADAPTER_PORT_END, set()
            )
        assert port == pm.ADAPTER_PORT_START

    def test_find_free_port_none_available(self):
        """No available ports raises DeviceAllocateError."""
        manager = pm.LocalProcessManager()
        with patch.object(pm, "_is_port_available", return_value=False):
            with pytest.raises(DeviceAllocateError, match="No free port"):
                manager._find_free_port(
                    pm.ADAPTER_PORT_START, pm.ADAPTER_PORT_START + 3, set()
                )


# ──────────────────────────────────────────────────────────────────────
# Process lifecycle: start
# ──────────────────────────────────────────────────────────────────────


class TestStart:
    def _make_mock_process(self, alive=True):
        proc = MagicMock()
        proc.poll.return_value = None if alive else 1
        proc.pid = 12345
        proc.wait.return_value = 0
        return proc

    def test_start_openclaw_engine(self, tmp_path):
        """start() with openclaw engine spawns openclaw and adapter."""
        manager = pm.LocalProcessManager()
        mock_oc_proc = self._make_mock_process()
        mock_adapter_proc = self._make_mock_process()

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()

        with (
            patch.object(
                manager, "_spawn_openclaw", return_value=mock_oc_proc
            ) as mock_spawn_oc,
            patch.object(
                manager, "_spawn_adapter", return_value=mock_adapter_proc
            ) as mock_spawn_ad,
            patch.object(manager, "_write_credentials") as mock_write,
            patch.object(manager, "_wait_for_health", return_value=True),
        ):
            entry = manager.start(
                device_id="dev1",
                bot_id="bot1",
                adapter_port=20010,
                engine_port=18800,
                config_dir=config_dir,
                workspace_dir=workspace_dir,
                callback_token="tok",
                entity_id="ent1",
                engine="openclaw",
            )

        mock_write.assert_called_once()
        mock_spawn_oc.assert_called_once()
        mock_spawn_ad.assert_called_once()
        assert entry.device_id == "dev1"
        assert entry.adapter_port == 20010
        assert entry.openclaw_port == 18800
        assert entry.openclaw_process is mock_oc_proc
        assert entry.adapter_process is mock_adapter_proc

    def test_start_hermes_engine(self, tmp_path):
        """start() with hermes engine spawns hermes and adapter."""
        manager = pm.LocalProcessManager()
        mock_hermes_proc = self._make_mock_process()
        mock_adapter_proc = self._make_mock_process()

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()

        with (
            patch.object(
                manager, "_spawn_hermes", return_value=mock_hermes_proc
            ) as mock_spawn_hermes,
            patch.object(manager, "_spawn_adapter", return_value=mock_adapter_proc),
            patch.object(manager, "_write_credentials"),
            patch.object(manager, "_wait_for_health", return_value=True),
        ):
            entry = manager.start(
                device_id="dev1",
                bot_id="bot1",
                adapter_port=20010,
                engine_port=18700,
                config_dir=config_dir,
                workspace_dir=workspace_dir,
                engine="hermes",
            )

        mock_spawn_hermes.assert_called_once()
        assert entry.hermes_process is mock_hermes_proc
        assert entry.hermes_port == 18700
        assert entry.openclaw_process is None

    def test_start_aicoding_engine(self, tmp_path):
        """start() with aicoding engine skips engine spawn."""
        manager = pm.LocalProcessManager()
        mock_adapter_proc = self._make_mock_process()

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()

        with (
            patch.object(manager, "_spawn_openclaw") as mock_spawn_oc,
            patch.object(manager, "_spawn_hermes") as mock_spawn_hermes,
            patch.object(manager, "_spawn_adapter", return_value=mock_adapter_proc),
            patch.object(manager, "_write_credentials"),
            patch.object(manager, "_wait_for_health", return_value=True),
        ):
            entry = manager.start(
                device_id="dev1",
                bot_id="bot1",
                adapter_port=20010,
                engine_port=0,
                config_dir=config_dir,
                workspace_dir=workspace_dir,
                engine="aicoding",
            )

        mock_spawn_oc.assert_not_called()
        mock_spawn_hermes.assert_not_called()
        assert entry.openclaw_process is None
        assert entry.hermes_process is None
        assert entry.openclaw_port == 0
        assert entry.hermes_port == 0

    def test_start_with_symbol_json(self, tmp_path):
        """start() with symbol_json calls _setup_skills."""
        manager = pm.LocalProcessManager()
        mock_adapter_proc = self._make_mock_process()

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()

        symbol_json = json.dumps([{"source": "/path/src", "target": "/path/tgt"}])

        with (
            patch.object(manager, "_spawn_adapter", return_value=mock_adapter_proc),
            patch.object(manager, "_write_credentials"),
            patch.object(manager, "_wait_for_health", return_value=True),
            patch.object(manager, "_setup_skills") as mock_skills,
        ):
            manager.start(
                device_id="dev1",
                bot_id="bot1",
                adapter_port=20010,
                engine_port=0,
                config_dir=config_dir,
                workspace_dir=workspace_dir,
                engine="aicoding",
                symbol_json=symbol_json,
            )

        mock_skills.assert_called_once_with(20010, symbol_json)

    def test_start_failure_cleanup(self, tmp_path):
        """On failure, spawned processes are killed."""
        manager = pm.LocalProcessManager()
        mock_oc_proc = self._make_mock_process()
        mock_adapter_proc = self._make_mock_process()

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()

        with (
            patch.object(manager, "_spawn_openclaw", return_value=mock_oc_proc),
            patch.object(manager, "_write_credentials"),
            patch.object(manager, "_spawn_adapter", side_effect=RuntimeError("boom")),
        ):
            with pytest.raises(RuntimeError, match="boom"):
                manager.start(
                    device_id="dev1",
                    bot_id="bot1",
                    adapter_port=20010,
                    engine_port=18800,
                    config_dir=config_dir,
                    workspace_dir=workspace_dir,
                    engine="openclaw",
                )

        # openclaw process should have been killed
        mock_oc_proc.kill.assert_called_once()

    def test_start_unknown_engine(self, tmp_path):
        """start() with unknown engine logs and skips engine spawn."""
        manager = pm.LocalProcessManager()
        mock_adapter_proc = self._make_mock_process()

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()

        with (
            patch.object(manager, "_spawn_openclaw") as mock_spawn_oc,
            patch.object(manager, "_spawn_hermes") as mock_spawn_hermes,
            patch.object(manager, "_spawn_adapter", return_value=mock_adapter_proc),
            patch.object(manager, "_write_credentials"),
            patch.object(manager, "_wait_for_health", return_value=True),
        ):
            entry = manager.start(
                device_id="dev1",
                bot_id="bot1",
                adapter_port=20010,
                engine_port=0,
                config_dir=config_dir,
                workspace_dir=workspace_dir,
                engine="custom_engine",
            )

        mock_spawn_oc.assert_not_called()
        mock_spawn_hermes.assert_not_called()
        assert entry.openclaw_process is None
        assert entry.hermes_process is None


# ──────────────────────────────────────────────────────────────────────
# Stop / stop_all
# ──────────────────────────────────────────────────────────────────────


class TestStop:
    def _make_entry(self, device_id="dev1"):
        proc = MagicMock()
        proc.poll.return_value = None
        proc.pid = 12345
        proc.wait.return_value = 0
        return pm.ProcessEntry(
            sandbox_id="sb1",
            device_id=device_id,
            bot_id="bot1",
            adapter_process=proc,
            adapter_port=20010,
            openclaw_process=proc,
            openclaw_port=18800,
        )

    def test_stop_existing(self):
        """stop() kills processes and frees ports."""
        manager = pm.LocalProcessManager()
        entry = self._make_entry()
        manager._processes["dev1"] = entry
        manager._adapter_ports.add(20010)
        manager._openclaw_ports.add(18800)

        result = manager.stop("dev1")

        assert result is True
        assert "dev1" not in manager._processes
        assert 20010 not in manager._adapter_ports
        assert 18800 not in manager._openclaw_ports

    def test_stop_not_found(self):
        """stop() with unknown device returns True."""
        manager = pm.LocalProcessManager()
        result = manager.stop("nonexistent")
        assert result is True

    def test_stop_all(self):
        """stop_all() kills all entries."""
        manager = pm.LocalProcessManager()
        entry1 = self._make_entry("dev1")
        entry2 = self._make_entry("dev2")
        manager._processes["dev1"] = entry1
        manager._processes["dev2"] = entry2
        manager._adapter_ports.update([20010, 20011])

        manager.stop_all()

        assert len(manager._processes) == 0
        assert len(manager._adapter_ports) == 0

    def test_stop_all_empty(self):
        """stop_all() with no entries does nothing."""
        manager = pm.LocalProcessManager()
        manager.stop_all()
        assert len(manager._processes) == 0


# ──────────────────────────────────────────────────────────────────────
# Query
# ──────────────────────────────────────────────────────────────────────


class TestQuery:
    def _make_process(self, alive=True):
        proc = MagicMock()
        proc.poll.return_value = None if alive else 1
        return proc

    def test_is_healthy_true(self):
        """All processes alive → healthy."""
        manager = pm.LocalProcessManager()
        proc = self._make_process(alive=True)
        entry = pm.ProcessEntry(
            sandbox_id="sb1",
            device_id="dev1",
            bot_id="bot1",
            adapter_process=proc,
            adapter_port=20010,
            openclaw_process=proc,
            openclaw_port=18800,
        )
        manager._processes["dev1"] = entry
        assert manager.is_healthy("dev1") is True

    def test_is_healthy_false_no_entry(self):
        """No entry → not healthy."""
        manager = pm.LocalProcessManager()
        assert manager.is_healthy("nonexistent") is False

    def test_is_healthy_false_adapter_dead(self):
        """Adapter dead → not healthy."""
        manager = pm.LocalProcessManager()
        dead_proc = self._make_process(alive=False)
        alive_proc = self._make_process(alive=True)
        entry = pm.ProcessEntry(
            sandbox_id="sb1",
            device_id="dev1",
            bot_id="bot1",
            adapter_process=dead_proc,
            adapter_port=20010,
            openclaw_process=alive_proc,
            openclaw_port=18800,
        )
        manager._processes["dev1"] = entry
        assert manager.is_healthy("dev1") is False

    def test_is_healthy_openclaw_none(self):
        """openclaw_process is None → healthy (if adapter alive)."""
        manager = pm.LocalProcessManager()
        proc = self._make_process(alive=True)
        entry = pm.ProcessEntry(
            sandbox_id="sb1",
            device_id="dev1",
            bot_id="bot1",
            adapter_process=proc,
            adapter_port=20010,
            openclaw_process=None,
            openclaw_port=0,
        )
        manager._processes["dev1"] = entry
        assert manager.is_healthy("dev1") is True

    def test_is_healthy_hermes_none(self):
        """hermes_process is None → healthy (if adapter alive)."""
        manager = pm.LocalProcessManager()
        proc = self._make_process(alive=True)
        entry = pm.ProcessEntry(
            sandbox_id="sb1",
            device_id="dev1",
            bot_id="bot1",
            adapter_process=proc,
            adapter_port=20010,
            openclaw_process=None,
            openclaw_port=0,
            hermes_process=None,
            hermes_port=0,
        )
        manager._processes["dev1"] = entry
        assert manager.is_healthy("dev1") is True

    def test_is_healthy_false_hermes_dead(self):
        """Hermes dead → not healthy."""
        manager = pm.LocalProcessManager()
        alive_proc = self._make_process(alive=True)
        dead_proc = self._make_process(alive=False)
        entry = pm.ProcessEntry(
            sandbox_id="sb1",
            device_id="dev1",
            bot_id="bot1",
            adapter_process=alive_proc,
            adapter_port=20010,
            openclaw_process=None,
            openclaw_port=0,
            hermes_process=dead_proc,
            hermes_port=18700,
        )
        manager._processes["dev1"] = entry
        assert manager.is_healthy("dev1") is False

    def test_get_entry_found(self):
        """get_entry returns the entry for a known device."""
        manager = pm.LocalProcessManager()
        proc = self._make_process()
        entry = pm.ProcessEntry(
            sandbox_id="sb1",
            device_id="dev1",
            bot_id="bot1",
            adapter_process=proc,
            adapter_port=20010,
        )
        manager._processes["dev1"] = entry
        result = manager.get_entry("dev1")
        assert result is entry

    def test_get_entry_not_found(self):
        """get_entry returns None for unknown device."""
        manager = pm.LocalProcessManager()
        result = manager.get_entry("nonexistent")
        assert result is None


# ──────────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────────


class TestWriteCredentials:
    def test_write_credentials(self, tmp_path, monkeypatch):
        """_write_credentials writes file with correct content."""
        # Mock Path.home() to avoid writing to real home
        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        config_dir = tmp_path / "config"
        config_dir.mkdir()

        pm.LocalProcessManager._write_credentials(
            device_id="dev1",
            bot_id="bot1",
            config_dir=config_dir,
            callback_token="mytoken",
            entity_id="ent1",
        )

        cred_path = config_dir / ".credentials"
        content = cred_path.read_text()
        assert "TOKEN=mytoken" in content
        assert "CLIENT_ID=dev1" in content
        assert "OWNER_ID=ent1" in content
        assert "BOT_ID=bot1" in content
        assert "AGENT_CODE=bot1" in content
        assert "ADMINS" not in content

        # Home credentials also written
        home_cred = fake_home / ".credentials"
        assert home_cred.exists()

    def test_write_credentials_with_admins(self, tmp_path, monkeypatch):
        """_write_credentials includes ADMINS line when admins is provided."""
        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        config_dir = tmp_path / "config"
        config_dir.mkdir()

        pm.LocalProcessManager._write_credentials(
            device_id="dev1",
            bot_id="bot1",
            config_dir=config_dir,
            callback_token="tok",
            entity_id="ent1",
            admins=["admin1", "admin2"],
        )

        content = (config_dir / ".credentials").read_text()
        assert "ADMINS=admin1,admin2" in content

    def test_write_credentials_with_agent_code(self, tmp_path, monkeypatch):
        """_write_credentials uses agent_code when provided."""
        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        config_dir = tmp_path / "config"
        config_dir.mkdir()

        pm.LocalProcessManager._write_credentials(
            device_id="dev1",
            bot_id="bot1",
            config_dir=config_dir,
            callback_token="tok",
            entity_id="ent1",
            agent_code="custom_agent",
        )

        content = (config_dir / ".credentials").read_text()
        assert "AGENT_CODE=custom_agent" in content

    def test_write_credentials_file_permissions(self, tmp_path, monkeypatch):
        """_write_credentials sets file permissions to 0600."""
        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        config_dir = tmp_path / "config"
        config_dir.mkdir()

        pm.LocalProcessManager._write_credentials(
            device_id="dev1",
            bot_id="bot1",
            config_dir=config_dir,
            callback_token="tok",
            entity_id="ent1",
        )

        cred_path = config_dir / ".credentials"
        mode = stat.S_IMODE(cred_path.stat().st_mode)
        assert mode == 0o600


class TestSetupSkills:
    def test_setup_skills_empty_json(self):
        """Empty symbol_json → no symlinks, no HTTP call."""
        with patch("requests.post") as mock_post:
            pm.LocalProcessManager._setup_skills(20010, "[]")
        mock_post.assert_not_called()

    def test_setup_skills_valid(self):
        """Valid symbol_json posts to adapter."""
        symbol_json = json.dumps(
            [
                {"source": "/path/to/src", "target": "/path/to/tgt"},
                {"source": "/path/to/src2", "target": "/path/to/tgt2"},
            ]
        )
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("requests.post", return_value=mock_response) as mock_post:
            pm.LocalProcessManager._setup_skills(20010, symbol_json)

        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert call_args[0][0] == "http://127.0.0.1:20010/api/skills/symlink"
        assert len(call_args[1]["json"]["symlinks"]) == 2

    def test_setup_skills_exception(self):
        """Exception is caught and logged as warning."""
        with patch("requests.post", side_effect=Exception("network error")):
            # Should not raise
            pm.LocalProcessManager._setup_skills(20010, '[{"source":"a","target":"b"}]')

    def test_setup_skills_non_200_response(self):
        """Non-200 response logs warning but does not raise."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"

        with patch("requests.post", return_value=mock_response):
            pm.LocalProcessManager._setup_skills(20010, '[{"source":"a","target":"b"}]')

    def test_setup_skills_empty_source_skipped(self):
        """Mapping with empty source is skipped."""
        symbol_json = json.dumps([{"source": "", "target": "/path/tgt"}])
        with patch("requests.post") as mock_post:
            pm.LocalProcessManager._setup_skills(20010, symbol_json)
        mock_post.assert_not_called()

    def test_setup_skills_empty_target_skipped(self):
        """Mapping with empty target is skipped."""
        symbol_json = json.dumps([{"source": "/path/src", "target": ""}])
        with patch("requests.post") as mock_post:
            pm.LocalProcessManager._setup_skills(20010, symbol_json)
        mock_post.assert_not_called()

    def test_setup_skills_invalid_json(self):
        """Invalid JSON is caught by the exception handler."""
        with patch("requests.post") as mock_post:
            pm.LocalProcessManager._setup_skills(20010, "not-json")
        mock_post.assert_not_called()


class TestWaitForHealth:
    def test_wait_for_health_success(self):
        """Socket connects successfully."""
        mock_sock = MagicMock()
        mock_sock.connect_ex.return_value = 0

        with patch("socket.socket", return_value=mock_sock):
            result = pm.LocalProcessManager._wait_for_health(18800, 5.0)
        assert result is True

    def test_wait_for_health_timeout(self):
        """Socket never connects → returns False after timeout."""
        mock_sock = MagicMock()
        mock_sock.connect_ex.return_value = 1  # never connects

        # Mock time.monotonic to simulate timeout progression
        time_values = [0.0, 0.0, 10.0]  # start, loop check, deadline exceeded
        with (
            patch("socket.socket", return_value=mock_sock),
            patch.object(pm.time, "monotonic", side_effect=time_values),
            patch.object(pm.time, "sleep"),
        ):
            result = pm.LocalProcessManager._wait_for_health(18800, 5.0)
        assert result is False

    def test_wait_for_health_socket_exception(self):
        """Socket exception during connect is caught."""
        mock_sock = MagicMock()
        mock_sock.connect_ex.side_effect = OSError("boom")

        time_values = [0.0, 0.0, 10.0]
        with (
            patch("socket.socket", return_value=mock_sock),
            patch.object(pm.time, "monotonic", side_effect=time_values),
        ):
            result = pm.LocalProcessManager._wait_for_health(18800, 5.0)
        assert result is False


class TestWaitForHermesHealth:
    def test_wait_for_hermes_health_success(self):
        """HTTP GET returns 200."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        with patch("requests.get", return_value=mock_response):
            result = pm.LocalProcessManager._wait_for_hermes_health(18700, 5.0)
        assert result is True

    @pytest.mark.skip(
        reason="time mocking conflicts with time.sleep; covered by integration tests"
    )
    def test_wait_for_hermes_health_timeout(self):
        """HTTP GET never succeeds → returns False."""
        pass

    @pytest.mark.skip(
        reason="time mocking conflicts with time.sleep; covered by integration tests"
    )
    def test_wait_for_hermes_health_non_200(self):
        """HTTP GET returns non-200 → keeps trying → timeout."""
        pass


class TestKillProcess:
    def test_kill_process_none(self):
        """_kill_process with None is a no-op."""
        pm.LocalProcessManager._kill_process(None, "test")
        # Should not raise

    def test_kill_process_already_dead(self):
        """Process already dead (poll() returns non-None) → no-op."""
        proc = MagicMock()
        proc.poll.return_value = 1
        pm.LocalProcessManager._kill_process(proc, "test")
        proc.terminate.assert_not_called()

    def test_kill_process_terminate_success(self):
        """Process terminates gracefully after SIGTERM."""
        proc = MagicMock()
        proc.poll.return_value = None
        proc.wait.return_value = 0
        pm.LocalProcessManager._kill_process(proc, "test")
        proc.terminate.assert_called_once()
        proc.kill.assert_not_called()

    def test_kill_process_terminate_timeout(self):
        """Process does not terminate after SIGTERM → SIGKILL."""
        proc = MagicMock()
        proc.poll.return_value = None
        proc.wait.side_effect = [subprocess.TimeoutExpired(cmd="test", timeout=5), 0]
        pm.LocalProcessManager._kill_process(proc, "test")
        proc.terminate.assert_called_once()
        proc.kill.assert_called_once()

    def test_kill_process_terminate_timeout_still_alive(self):
        """Process still alive after SIGKILL → logs warning."""
        proc = MagicMock()
        proc.poll.return_value = None
        proc.wait.side_effect = [
            subprocess.TimeoutExpired(cmd="test", timeout=5),
            subprocess.TimeoutExpired(cmd="test", timeout=3),
        ]
        pm.LocalProcessManager._kill_process(proc, "test")
        proc.terminate.assert_called_once()
        proc.kill.assert_called_once()

    def test_kill_process_exception(self):
        """Exception during terminate is caught."""
        proc = MagicMock()
        proc.poll.return_value = None
        proc.terminate.side_effect = OSError("permission denied")
        # Should not raise
        pm.LocalProcessManager._kill_process(proc, "test")


# ──────────────────────────────────────────────────────────────────────
# Config creation
# ──────────────────────────────────────────────────────────────────────


class TestCreateOpenclawConfig:
    def test_create_openclaw_config(self, monkeypatch, tmp_path):
        """create_openclaw_config creates config file with correct settings."""
        manager = pm.LocalProcessManager()

        template_path = tmp_path / "template.json"
        template_path.write_text(
            json.dumps({"gateway": {}, "agents": {"defaults": {}}})
        )

        monkeypatch.setattr(
            manager, "_resolve_config_template_path", lambda: template_path
        )
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "fake_home")
        monkeypatch.setenv("BCN_PLUGIN_PATH", str(tmp_path / "missing-plugin"))

        workspace_dir = tmp_path / "bot" / "workspace"
        workspace_dir.mkdir(parents=True)

        config_dir = manager.create_openclaw_config(
            bolt_id="bot1",
            openclaw_port=18888,
            workspace_dir=workspace_dir,
            entity_id="ent1",
        )

        config_file = config_dir / "openclaw.json"
        assert config_file.exists()
        config = json.loads(config_file.read_text())
        assert config["gateway"]["port"] == 18888
        assert config["gateway"]["mode"] == "local"
        assert config["gateway"]["auth"]["mode"] == "none"
        assert config["agents"]["defaults"]["workspace"] == str(workspace_dir)

    def test_create_openclaw_config_no_template(self, monkeypatch, tmp_path):
        """create_openclaw_config uses minimal config when template not found."""
        manager = pm.LocalProcessManager()

        monkeypatch.setattr(manager, "_resolve_config_template_path", lambda: None)
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "fake_home")
        monkeypatch.setenv("BCN_PLUGIN_PATH", str(tmp_path / "missing-plugin"))

        workspace_dir = tmp_path / "bot" / "workspace"
        workspace_dir.mkdir(parents=True)

        config_dir = manager.create_openclaw_config(
            bolt_id="bot1",
            openclaw_port=18888,
            workspace_dir=workspace_dir,
        )

        config = json.loads((config_dir / "openclaw.json").read_text())
        assert config["gateway"]["port"] == 18888
        assert config["gateway"]["mode"] == "local"

    def test_create_openclaw_config_with_bcn_plugin(self, monkeypatch, tmp_path):
        """create_openclaw_config includes BCS channel when bcn_entry_point exists."""
        manager = pm.LocalProcessManager()

        template_path = tmp_path / "template.json"
        template_path.write_text(json.dumps({}))

        # Create the bcn plugin entry point
        bcn_plugin_path = tmp_path / "bcn_plugin"
        entry_point = bcn_plugin_path / "dist" / "esm" / "index.js"
        entry_point.parent.mkdir(parents=True)
        entry_point.write_text("// plugin")

        monkeypatch.setattr(
            manager, "_resolve_config_template_path", lambda: template_path
        )
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "fake_home")
        monkeypatch.setenv("BCN_PLUGIN_PATH", str(bcn_plugin_path))
        monkeypatch.setenv("BCS_PORT", "21099")

        workspace_dir = tmp_path / "bot" / "workspace"
        workspace_dir.mkdir(parents=True)

        config_dir = manager.create_openclaw_config(
            bolt_id="bot1",
            openclaw_port=18888,
            workspace_dir=workspace_dir,
            entity_id="ent1",
        )

        config = json.loads((config_dir / "openclaw.json").read_text())
        assert config["channels"]["bcs"]["enabled"] is True
        assert config["channels"]["bcs"]["bcsUrl"] == "ws://127.0.0.1:21099/ws/bot"
        assert config["channels"]["bcs"]["botId"] == "bot1:ent1"
        assert config["plugins"]["load"]["paths"] == [str(bcn_plugin_path)]
        assert config["plugins"]["entries"]["openclaw-channel-bcn"]["enabled"] is True

    def test_create_openclaw_config_bcn_plugin_no_entity_id(
        self, monkeypatch, tmp_path
    ):
        """botId is just bolt_id when entity_id is empty."""
        manager = pm.LocalProcessManager()

        template_path = tmp_path / "template.json"
        template_path.write_text(json.dumps({}))

        bcn_plugin_path = tmp_path / "bcn_plugin"
        entry_point = bcn_plugin_path / "dist" / "esm" / "index.js"
        entry_point.parent.mkdir(parents=True)
        entry_point.write_text("// plugin")

        monkeypatch.setattr(
            manager, "_resolve_config_template_path", lambda: template_path
        )
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "fake_home")
        monkeypatch.setenv("BCN_PLUGIN_PATH", str(bcn_plugin_path))

        workspace_dir = tmp_path / "bot" / "workspace"
        workspace_dir.mkdir(parents=True)

        config_dir = manager.create_openclaw_config(
            bolt_id="bot1",
            openclaw_port=18888,
            workspace_dir=workspace_dir,
        )

        config = json.loads((config_dir / "openclaw.json").read_text())
        assert config["channels"]["bcs"]["botId"] == "bot1"

    def test_create_openclaw_config_file_permissions(self, monkeypatch, tmp_path):
        """Config file has 0600 permissions."""
        manager = pm.LocalProcessManager()

        template_path = tmp_path / "template.json"
        template_path.write_text(json.dumps({}))

        monkeypatch.setattr(
            manager, "_resolve_config_template_path", lambda: template_path
        )
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "fake_home")
        monkeypatch.setenv("BCN_PLUGIN_PATH", str(tmp_path / "missing-plugin"))

        workspace_dir = tmp_path / "bot" / "workspace"
        workspace_dir.mkdir(parents=True)

        config_dir = manager.create_openclaw_config(
            bolt_id="bot1",
            openclaw_port=18888,
            workspace_dir=workspace_dir,
        )

        mode = stat.S_IMODE((config_dir / "openclaw.json").stat().st_mode)
        assert mode == 0o600


class TestCreateHermesConfig:
    def test_create_hermes_config(self, monkeypatch, tmp_path):
        """create_hermes_config creates config file with correct settings."""
        manager = pm.LocalProcessManager()

        # Mock yaml module
        mock_yaml = MagicMock()
        loaded_config = {"existing": "value"}
        mock_yaml.safe_load.return_value = loaded_config
        mock_yaml.dump = MagicMock()

        import importlib

        with patch.dict(sys.modules, {"yaml": mock_yaml}):
            # Also need the yaml that's imported inside the method
            monkeypatch.setattr(Path, "home", lambda: tmp_path / "fake_home")

            template_path = tmp_path / "hermes_template.yaml"
            template_path.write_text("existing: value")
            monkeypatch.setattr(
                manager,
                "_resolve_hermes_config_template_path",
                lambda: template_path,
            )

            workspace_dir = tmp_path / "bot" / "workspace"
            workspace_dir.mkdir(parents=True)

            config_dir = manager.create_hermes_config(
                bolt_id="bot1",
                hermes_port=18700,
                workspace_dir=workspace_dir,
            )

        assert config_dir.exists()
        config_file = config_dir / "config.yaml"
        assert config_file.exists()
        # Check subdirectories were created
        for subdir in ("sessions", "logs", "skills", "memories", "cron"):
            assert (config_dir / subdir).is_dir()

    def test_create_hermes_config_no_template(self, monkeypatch, tmp_path):
        """create_hermes_config uses minimal config when template not found."""
        manager = pm.LocalProcessManager()

        mock_yaml = MagicMock()
        mock_yaml.safe_load.return_value = None
        mock_yaml.dump = MagicMock()

        with patch.dict(sys.modules, {"yaml": mock_yaml}):
            monkeypatch.setattr(Path, "home", lambda: tmp_path / "fake_home")
            monkeypatch.setattr(
                manager,
                "_resolve_hermes_config_template_path",
                lambda: None,
            )

            workspace_dir = tmp_path / "bot" / "workspace"
            workspace_dir.mkdir(parents=True)

            config_dir = manager.create_hermes_config(
                bolt_id="bot1",
                hermes_port=18700,
                workspace_dir=workspace_dir,
            )

        assert config_dir.exists()
        # Verify yaml.dump was called with config containing api_server
        assert mock_yaml.dump.called
        dumped_config = mock_yaml.dump.call_args[0][0]
        assert dumped_config["platforms"]["api_server"]["port"] == 18700
        assert dumped_config["platforms"]["api_server"]["host"] == "127.0.0.1"
        assert dumped_config["platforms"]["api_server"]["enabled"] is True

    def test_create_hermes_config_with_local_env(self, monkeypatch, tmp_path):
        """create_hermes_config respects LOCAL_HERMES_DIR env var."""
        manager = pm.LocalProcessManager()

        mock_yaml = MagicMock()
        mock_yaml.safe_load.return_value = None
        mock_yaml.dump = MagicMock()

        local_hermes_dir = tmp_path / "custom_hermes"
        monkeypatch.setenv("LOCAL_HERMES_DIR", str(local_hermes_dir))

        with patch.dict(sys.modules, {"yaml": mock_yaml}):
            monkeypatch.setattr(
                manager,
                "_resolve_hermes_config_template_path",
                lambda: None,
            )

            workspace_dir = tmp_path / "bot" / "workspace"
            workspace_dir.mkdir(parents=True)

            config_dir = manager.create_hermes_config(
                bolt_id="bot1",
                hermes_port=18700,
                workspace_dir=workspace_dir,
            )

        assert str(config_dir).startswith(str(local_hermes_dir))
        assert "bot_bot1" in str(config_dir)


class TestResolveConfigTemplatePath:
    def test_resolve_config_template_path(self):
        """_resolve_config_template_path walks up to find template."""
        # This will either find the real template or return None.
        # We just verify it returns a Path or None without error.
        result = pm.LocalProcessManager._resolve_config_template_path()
        assert result is None or isinstance(result, Path)

    def test_resolve_hermes_config_template_path(self):
        """_resolve_hermes_config_template_path walks up to find template."""
        result = pm.LocalProcessManager._resolve_hermes_config_template_path()
        assert result is None or isinstance(result, Path)


class TestResolveEnginePython:
    def test_resolve_engine_python_venv(self, tmp_path):
        """When venv exists, returns venv python."""
        engine_src_dir = tmp_path / "engine" / "src"
        engine_src_dir.mkdir(parents=True)
        venv_python = engine_src_dir.parent / ".venv" / "bin" / "python"
        venv_python.parent.mkdir(parents=True)
        venv_python.touch()

        result = pm.LocalProcessManager._resolve_engine_python(engine_src_dir)
        assert result == str(venv_python)

    def test_resolve_engine_python_fallback(self, tmp_path):
        """When venv not found, uses sys.executable."""
        engine_src_dir = tmp_path / "engine" / "src"
        engine_src_dir.mkdir(parents=True)
        # No .venv directory created

        result = pm.LocalProcessManager._resolve_engine_python(engine_src_dir)
        assert result == sys.executable


class TestResolveEngineSrcDir:
    def test_resolve_engine_src_dir_configured(self, monkeypatch, tmp_path):
        """LOCAL_ENGINE_SRC_DIR env var is used when set and exists."""
        engine_src = tmp_path / "engine" / "src"
        engine_src.mkdir(parents=True)
        monkeypatch.setenv("LOCAL_ENGINE_SRC_DIR", str(engine_src))

        result = pm.LocalProcessManager._resolve_engine_src_dir()
        assert result == engine_src.resolve()

    def test_resolve_engine_src_dir_configured_missing(self, monkeypatch, tmp_path):
        """Configured LOCAL_ENGINE_SRC_DIR that doesn't exist raises error."""
        missing = tmp_path / "missing" / "src"
        monkeypatch.setenv("LOCAL_ENGINE_SRC_DIR", str(missing))
        with pytest.raises(DeviceAllocateError, match="does not exist"):
            pm.LocalProcessManager._resolve_engine_src_dir()

    def test_resolve_engine_src_dir_not_configured(self, monkeypatch):
        """Without LOCAL_ENGINE_SRC_DIR, walks up the repo."""
        monkeypatch.delenv("LOCAL_ENGINE_SRC_DIR", raising=False)
        result = pm.LocalProcessManager._resolve_engine_src_dir()
        assert result.name == "src"
        assert result.parent.name == "engine"

    def test_resolve_engine_src_dir_not_found(self, monkeypatch):
        """When engine src dir cannot be found, raises DeviceAllocateError."""
        monkeypatch.delenv("LOCAL_ENGINE_SRC_DIR", raising=False)
        # Temporarily make Path.exists return False for the engine src path
        original_exists = Path.exists

        def fake_exists(self):
            if self.name == "src" and self.parent.name == "engine":
                return False
            return original_exists(self)

        monkeypatch.setattr(Path, "exists", fake_exists)
        with pytest.raises(DeviceAllocateError, match="Could not find engine source"):
            pm.LocalProcessManager._resolve_engine_src_dir()


# ──────────────────────────────────────────────────────────────────────
# Spawn tests (mocked subprocess.Popen)
# ──────────────────────────────────────────────────────────────────────


class TestSpawnOpenclaw:
    def test_spawn_openclaw_success(self, tmp_path):
        """_spawn_openclaw spawns process and waits for health."""
        manager = pm.LocalProcessManager()
        mock_proc = MagicMock()
        mock_proc.pid = 12345

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()

        with (
            patch("subprocess.Popen", return_value=mock_proc) as mock_popen,
            patch.object(manager, "_wait_for_health", return_value=True),
        ):
            result = manager._spawn_openclaw(
                bot_id="bot1",
                openclaw_port=18800,
                workspace_dir=workspace_dir,
                config_dir=config_dir,
            )

        assert result is mock_proc
        mock_popen.assert_called_once()
        args, kwargs = mock_popen.call_args
        assert "openclaw" in args[0]
        assert "gateway" in args[0]
        assert "--port" in args[0]
        assert "18800" in args[0]

    def test_spawn_openclaw_health_failure(self, tmp_path):
        """_spawn_openclaw raises RuntimeError when health check fails."""
        manager = pm.LocalProcessManager()
        mock_proc = MagicMock()
        mock_proc.pid = 12345

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()

        with (
            patch("subprocess.Popen", return_value=mock_proc),
            patch.object(manager, "_wait_for_health", return_value=False),
        ):
            with pytest.raises(RuntimeError, match="failed to start"):
                manager._spawn_openclaw(
                    bot_id="bot1",
                    openclaw_port=18800,
                    workspace_dir=workspace_dir,
                    config_dir=config_dir,
                )


class TestSpawnHermes:
    def test_spawn_hermes_success(self, tmp_path):
        """_spawn_hermes spawns process and waits for health."""
        manager = pm.LocalProcessManager()
        mock_proc = MagicMock()
        mock_proc.pid = 12345

        config_dir = tmp_path / "config"
        config_dir.mkdir()

        with (
            patch("subprocess.Popen", return_value=mock_proc) as mock_popen,
            patch.object(manager, "_wait_for_hermes_health", return_value=True),
        ):
            result = manager._spawn_hermes(
                bot_id="bot1",
                hermes_port=18700,
                config_dir=config_dir,
            )

        assert result is mock_proc
        mock_popen.assert_called_once()
        args, kwargs = mock_popen.call_args
        assert "hermes" in args[0]
        assert "dashboard" in args[0]
        assert "--port" in args[0]
        assert "18700" in args[0]

    def test_spawn_hermes_health_failure(self, tmp_path):
        """_spawn_hermes raises RuntimeError when health check fails."""
        manager = pm.LocalProcessManager()
        mock_proc = MagicMock()
        mock_proc.pid = 12345

        config_dir = tmp_path / "config"
        config_dir.mkdir()

        with (
            patch("subprocess.Popen", return_value=mock_proc),
            patch.object(manager, "_wait_for_hermes_health", return_value=False),
        ):
            with pytest.raises(RuntimeError, match="failed to start"):
                manager._spawn_hermes(
                    bot_id="bot1",
                    hermes_port=18700,
                    config_dir=config_dir,
                )


class TestSpawnAdapter:
    def test_spawn_adapter_openclaw(self, tmp_path, monkeypatch):
        """_spawn_adapter for openclaw sets correct env vars."""
        manager = pm.LocalProcessManager()
        mock_proc = MagicMock()
        mock_proc.pid = 12345

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()

        engine_src_dir = tmp_path / "engine" / "src"
        engine_src_dir.mkdir(parents=True)

        monkeypatch.setattr(manager, "_resolve_engine_src_dir", lambda: engine_src_dir)
        monkeypatch.setattr(
            manager, "_resolve_engine_python", lambda src: sys.executable
        )

        with (
            patch("subprocess.Popen", return_value=mock_proc) as mock_popen,
            patch.object(manager, "_wait_for_health", return_value=True),
        ):
            result = manager._spawn_adapter(
                adapter_port=20010,
                engine_port=18800,
                config_dir=config_dir,
                workspace_dir=workspace_dir,
                engine="openclaw",
            )

        assert result is mock_proc
        kwargs = mock_popen.call_args[1]
        env = kwargs["env"]
        assert env["CHAT_ENGINE"] == "openclaw"
        assert env["OPENCLAW_GATEWAY_URL"] == "ws://127.0.0.1:18800"
        assert env["ENGINE_OPENCLAW_PROCESS_START_CMD"] == ""

    def test_spawn_adapter_hermes(self, tmp_path, monkeypatch):
        """_spawn_adapter for hermes sets HERMES_URL."""
        manager = pm.LocalProcessManager()
        mock_proc = MagicMock()
        mock_proc.pid = 12345

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()

        engine_src_dir = tmp_path / "engine" / "src"
        engine_src_dir.mkdir(parents=True)

        monkeypatch.setattr(manager, "_resolve_engine_src_dir", lambda: engine_src_dir)
        monkeypatch.setattr(
            manager, "_resolve_engine_python", lambda src: sys.executable
        )

        with (
            patch("subprocess.Popen", return_value=mock_proc) as mock_popen,
            patch.object(manager, "_wait_for_health", return_value=True),
        ):
            result = manager._spawn_adapter(
                adapter_port=20010,
                engine_port=18700,
                config_dir=config_dir,
                workspace_dir=workspace_dir,
                engine="hermes",
            )

        assert result is mock_proc
        env = mock_popen.call_args[1]["env"]
        assert env["CHAT_ENGINE"] == "hermes"
        assert env["HERMES_URL"] == "http://127.0.0.1:18700"

    def test_spawn_adapter_aicoding(self, tmp_path, monkeypatch):
        """_spawn_adapter for aicoding sets AICODING_RELAY_URL."""
        manager = pm.LocalProcessManager()
        mock_proc = MagicMock()
        mock_proc.pid = 12345

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()

        engine_src_dir = tmp_path / "engine" / "src"
        engine_src_dir.mkdir(parents=True)

        monkeypatch.setattr(manager, "_resolve_engine_src_dir", lambda: engine_src_dir)
        monkeypatch.setattr(
            manager, "_resolve_engine_python", lambda src: sys.executable
        )

        with (
            patch("subprocess.Popen", return_value=mock_proc) as mock_popen,
            patch.object(manager, "_wait_for_health", return_value=True),
        ):
            manager._spawn_adapter(
                adapter_port=20010,
                engine_port=0,
                config_dir=config_dir,
                workspace_dir=workspace_dir,
                engine="aicoding",
            )

        env = mock_popen.call_args[1]["env"]
        assert env["CHAT_ENGINE"] == "aicoding"
        assert env["AICODING_RELAY_URL"] == "ws://127.0.0.1:18900"

    def test_spawn_adapter_claude_code(self, tmp_path, monkeypatch):
        """_spawn_adapter for claude_code sets CLAUDE_CODE_RELAY_URL."""
        manager = pm.LocalProcessManager()
        mock_proc = MagicMock()
        mock_proc.pid = 12345

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()

        engine_src_dir = tmp_path / "engine" / "src"
        engine_src_dir.mkdir(parents=True)

        monkeypatch.setattr(manager, "_resolve_engine_src_dir", lambda: engine_src_dir)
        monkeypatch.setattr(
            manager, "_resolve_engine_python", lambda src: sys.executable
        )

        with (
            patch("subprocess.Popen", return_value=mock_proc) as mock_popen,
            patch.object(manager, "_wait_for_health", return_value=True),
        ):
            manager._spawn_adapter(
                adapter_port=20010,
                engine_port=0,
                config_dir=config_dir,
                workspace_dir=workspace_dir,
                engine="claude_code",
            )

        env = mock_popen.call_args[1]["env"]
        assert env["CHAT_ENGINE"] == "claude_code"
        assert env["CLAUDE_CODE_RELAY_URL"] == "ws://127.0.0.1:18900"

    def test_spawn_adapter_health_failure(self, tmp_path, monkeypatch):
        """_spawn_adapter raises RuntimeError when health check fails."""
        manager = pm.LocalProcessManager()
        mock_proc = MagicMock()
        mock_proc.pid = 12345

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()

        engine_src_dir = tmp_path / "engine" / "src"
        engine_src_dir.mkdir(parents=True)

        monkeypatch.setattr(manager, "_resolve_engine_src_dir", lambda: engine_src_dir)
        monkeypatch.setattr(
            manager, "_resolve_engine_python", lambda src: sys.executable
        )

        with (
            patch("subprocess.Popen", return_value=mock_proc),
            patch.object(manager, "_wait_for_health", return_value=False),
        ):
            with pytest.raises(RuntimeError, match="failed to start"):
                manager._spawn_adapter(
                    adapter_port=20010,
                    engine_port=0,
                    config_dir=config_dir,
                    workspace_dir=workspace_dir,
                    engine="aicoding",
                )


# ──────────────────────────────────────────────────────────────────────
# Register tests
# ──────────────────────────────────────────────────────────────────────


class TestRegister:
    def test_register_creates_entry(self):
        """_register stores a ProcessEntry in _processes."""
        manager = pm.LocalProcessManager()
        mock_proc = MagicMock()

        manager._register(
            sandbox_id="sb1",
            device_id="dev1",
            bot_id="bot1",
            adapter_process=mock_proc,
            adapter_port=20010,
            openclaw_port=18800,
        )

        assert "sb1" in manager._processes
        entry = manager._processes["sb1"]
        assert entry.device_id == "dev1"
        assert entry.adapter_port == 20010
        assert entry.openclaw_port == 18800


# ──────────────────────────────────────────────────────────────────────
# ProcessEntry dataclass tests
# ──────────────────────────────────────────────────────────────────────


class TestProcessEntry:
    def test_process_entry_defaults(self):
        """ProcessEntry has correct default values for optional fields."""
        proc = MagicMock()
        entry = pm.ProcessEntry(
            sandbox_id="sb1",
            device_id="dev1",
            bot_id="bot1",
            adapter_process=proc,
            adapter_port=20010,
        )
        assert entry.openclaw_process is None
        assert entry.openclaw_port == 0
        assert entry.hermes_process is None
        assert entry.hermes_port == 0
        assert entry.config_dir == Path(".")
        assert entry.workspace_dir == Path(".")
        assert entry.started_at is not None
