"""Tests for arca local_proc _process_manager env injection (Task 6).

Spec: docs/superpowers/specs/2026-06-10-engine-per-bot-workspace-design.md §4.3.A3
"""

from __future__ import annotations

import inspect
import json
import stat
from pathlib import Path

import pytest

from secbaas.plugins.sandbox.arca.local_proc import _process_manager as pm


def _repo_bcn_plugin_path() -> Path:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "src" / "plugin" / "packages" / "openclaw-channel-bcn"
        if candidate.is_dir():
            return candidate
    raise AssertionError("repo BCN plugin path not found")


@pytest.mark.skip(
    reason="depends on BCN plugin repo checkout; needs tmp_path fixture to replace ~/.openclaw fallback"
)
def test_default_bcn_plugin_path_prefers_ocb_repo_plugin(monkeypatch):
    monkeypatch.delenv("BCN_PLUGIN_PATH", raising=False)

    assert Path(pm._default_bcn_plugin_path()) == _repo_bcn_plugin_path()


def test_spawn_adapter_source_injects_workspace_dir_env():
    """_spawn_adapter 函数源码里必须包含 env['OPENCLAW_WORKSPACE_DIR'] = str(workspace_dir)"""
    src = inspect.getsource(pm.LocalProcessManager._spawn_adapter)
    assert 'env["OPENCLAW_WORKSPACE_DIR"]' in src, (
        "_spawn_adapter 应该注入 OPENCLAW_WORKSPACE_DIR 给 adapter 进程"
    )
    assert "workspace_dir" in src, "注入值应该是 workspace_dir 参数"


def test_resolve_engine_src_dir_prefers_configured_path(monkeypatch, tmp_path):
    engine_src_dir = tmp_path / "engine" / "src"
    engine_src_dir.mkdir(parents=True)
    monkeypatch.setenv("LOCAL_ENGINE_SRC_DIR", str(engine_src_dir))

    assert pm.LocalProcessManager._resolve_engine_src_dir() == engine_src_dir.resolve()


def test_resolve_engine_src_dir_rejects_missing_configured_path(monkeypatch, tmp_path):
    missing_engine_src_dir = tmp_path / "missing" / "engine" / "src"
    monkeypatch.setenv("LOCAL_ENGINE_SRC_DIR", str(missing_engine_src_dir))

    with pytest.raises(pm.DeviceAllocateError) as exc_info:
        pm.LocalProcessManager._resolve_engine_src_dir()

    assert "Configured LOCAL_ENGINE_SRC_DIR does not exist" in str(exc_info.value)
    assert str(missing_engine_src_dir) in str(exc_info.value)


def test_resolve_engine_src_dir_falls_back_to_repo_layout(monkeypatch):
    monkeypatch.delenv("LOCAL_ENGINE_SRC_DIR", raising=False)

    engine_src_dir = pm.LocalProcessManager._resolve_engine_src_dir()

    assert engine_src_dir.name == "src"
    assert engine_src_dir.parent.name == "engine"
    assert (engine_src_dir / "engine" / "community" / "api" / "app.py").is_file()


def test_create_openclaw_config_merges_singlebox_model_config(monkeypatch, tmp_path):
    manager = pm.LocalProcessManager()

    template_path = tmp_path / "template-openclaw.json"
    template_path.write_text(
        json.dumps(
            {
                "models": {
                    "mode": "merge",
                    "providers": {
                        "antchat": {
                            "baseUrl": "${OPEN_CLAW_BASE_URL}",
                            "apiKey": "${OPEN_CLAW_API_KEY}",
                        }
                    },
                },
                "agents": {"defaults": {"model": {"primary": "antchat/Kimi-K2.5"}}},
                "gateway": {},
            }
        ),
        encoding="utf-8",
    )
    runtime_model_config = tmp_path / "singlebox-model-config.json"
    runtime_model_config.write_text(
        json.dumps(
            {
                "models": {
                    "mode": "merge",
                    "providers": {
                        "manual-provider": {
                            "baseUrl": "https://model.example.test/v1",
                            "apiKey": "sk-test",
                            "api": "openai-completions",
                            "models": [{"id": "model-a", "name": "Model A"}],
                        }
                    },
                },
                "agents": {
                    "defaults": {
                        "model": {"primary": "manual-provider/model-a"},
                        "models": {"manual-provider/model-a": {"alias": "Model A"}},
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(manager, "_resolve_config_template_path", lambda: template_path)
    monkeypatch.setenv("SINGLEBOX_MODEL_CONFIG_FILE", str(runtime_model_config))
    monkeypatch.setenv("BCN_PLUGIN_PATH", str(tmp_path / "missing-plugin"))

    workspace_dir = tmp_path / "bot" / "openclaw" / "workspace"
    workspace_dir.mkdir(parents=True)

    config_dir = manager.create_openclaw_config(
        bolt_id="default",
        openclaw_port=18888,
        workspace_dir=workspace_dir,
        entity_id="mock-user",
    )

    config = json.loads((config_dir / "openclaw.json").read_text(encoding="utf-8"))
    assert "antchat" not in config["models"]["providers"]
    assert config["models"]["providers"]["manual-provider"]["apiKey"] == "sk-test"
    assert config["agents"]["defaults"]["model"]["primary"] == "manual-provider/model-a"
    assert config["gateway"]["port"] == 18888
    assert stat.S_IMODE((config_dir / "openclaw.json").stat().st_mode) == 0o600


def test_create_openclaw_config_removes_template_model_fields_for_mock_config(
    monkeypatch, tmp_path
):
    manager = pm.LocalProcessManager()

    template_path = tmp_path / "template-openclaw.json"
    template_path.write_text(
        json.dumps(
            {
                "models": {
                    "mode": "merge",
                    "providers": {
                        "antchat": {
                            "baseUrl": "${OPEN_CLAW_BASE_URL}",
                            "apiKey": "${OPEN_CLAW_API_KEY}",
                        }
                    },
                },
                "agents": {
                    "defaults": {
                        "model": {"primary": "antchat/Kimi-K2.5"},
                        "models": {"antchat/Kimi-K2.5": {}},
                        "imageModel": {"primary": "antchat/Kimi-K2.5"},
                    }
                },
                "gateway": {},
            }
        ),
        encoding="utf-8",
    )
    runtime_model_config = tmp_path / "mock-model-config.json"
    runtime_model_config.write_text(
        json.dumps(
            {
                "models": {"mode": "merge", "providers": {}},
                "agents": {"defaults": {"models": {}}},
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(manager, "_resolve_config_template_path", lambda: template_path)
    monkeypatch.setenv("SINGLEBOX_MODEL_CONFIG_FILE", str(runtime_model_config))
    monkeypatch.setenv("BCN_PLUGIN_PATH", str(tmp_path / "missing-plugin"))

    workspace_dir = tmp_path / "bot" / "openclaw" / "workspace"
    workspace_dir.mkdir(parents=True)

    config_dir = manager.create_openclaw_config(
        bolt_id="default",
        openclaw_port=18888,
        workspace_dir=workspace_dir,
        entity_id="mock-user",
    )

    defaults = json.loads((config_dir / "openclaw.json").read_text(encoding="utf-8"))[
        "agents"
    ]["defaults"]
    assert "model" not in defaults
    assert defaults["models"] == {}
    assert "imageModel" not in defaults


def test_create_openclaw_config_rejects_non_object_singlebox_model_config(
    monkeypatch, tmp_path
):
    manager = pm.LocalProcessManager()

    template_path = tmp_path / "template-openclaw.json"
    template_path.write_text(
        json.dumps({"models": {"mode": "merge", "providers": {}}, "gateway": {}}),
        encoding="utf-8",
    )
    runtime_model_config = tmp_path / "list-model-config.json"
    runtime_model_config.write_text("[]", encoding="utf-8")

    monkeypatch.setattr(manager, "_resolve_config_template_path", lambda: template_path)
    monkeypatch.setenv("SINGLEBOX_MODEL_CONFIG_FILE", str(runtime_model_config))
    monkeypatch.setenv("BCN_PLUGIN_PATH", str(tmp_path / "missing-plugin"))

    workspace_dir = tmp_path / "bot" / "openclaw" / "workspace"
    workspace_dir.mkdir(parents=True)

    with pytest.raises(pm.DeviceAllocateError, match="must be a JSON object"):
        manager.create_openclaw_config(
            bolt_id="default",
            openclaw_port=18888,
            workspace_dir=workspace_dir,
            entity_id="mock-user",
        )


def test_merge_singlebox_model_config_noops_without_configured_file(monkeypatch):
    config = {"models": {"mode": "merge", "providers": {"existing": {}}}}
    monkeypatch.delenv("SINGLEBOX_MODEL_CONFIG_FILE", raising=False)

    pm._merge_singlebox_model_config(config)

    assert config["models"]["providers"] == {"existing": {}}


def test_merge_singlebox_model_config_rejects_missing_file(monkeypatch, tmp_path):
    missing_config = tmp_path / "missing-openclaw.json"
    monkeypatch.setenv("SINGLEBOX_MODEL_CONFIG_FILE", str(missing_config))

    with pytest.raises(pm.DeviceAllocateError, match="does not exist"):
        pm._merge_singlebox_model_config({})


def test_merge_singlebox_model_config_rejects_invalid_json(monkeypatch, tmp_path):
    invalid_config = tmp_path / "invalid-openclaw.json"
    invalid_config.write_text("{", encoding="utf-8")
    monkeypatch.setenv("SINGLEBOX_MODEL_CONFIG_FILE", str(invalid_config))

    with pytest.raises(pm.DeviceAllocateError, match="is not valid JSON"):
        pm._merge_singlebox_model_config({})
