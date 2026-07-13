from __future__ import annotations

import json
import os
from pathlib import Path

from agentclaw.community.plugins.local import process_manager as pm
from agentclaw.community.plugins.local.process_manager import LocalProcessManager


def _repo_bcn_plugin_path() -> Path | None:
    """The in-repo BCN plugin dir, or ``None`` if this tree doesn't ship it.

    The ocb monorepo ships it at ``src/bcs/crates/plugins/openclaw-channel-bcn``; a
    corp-absent staged/extracted community tree does not, so the production code
    falls back to ``~/.openclaw/extensions/...``.
    """
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "src" / "bcs" / "crates" / "plugins" / "openclaw-channel-bcn"
        if candidate.is_dir():
            return candidate
    return None


def test_default_bcn_plugin_path_prefers_ocb_repo_plugin(monkeypatch):
    monkeypatch.delenv("BCN_PLUGIN_PATH", raising=False)

    resolved = Path(pm._default_bcn_plugin_path())
    repo_path = _repo_bcn_plugin_path()
    if repo_path is not None:
        # Monorepo: prefer the in-repo BCN plugin.
        assert resolved == repo_path
    else:
        # Corp-absent staged/extracted tree: no in-repo plugin → the ~/.openclaw
        # extensions fallback (asserts the production fallback branch too).
        assert resolved == Path(
            os.path.expanduser("~/.openclaw/extensions/openclaw-channel-bcn")
        )


def test_create_openclaw_config_uses_engine_root_and_entity_scoped_bcs_bot_id(
    tmp_path,
    monkeypatch,
):
    manager = LocalProcessManager()

    template_path = tmp_path / "template-openclaw.json"
    template_path.write_text(
        json.dumps({"gateway": {}, "agents": {"defaults": {}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(manager, "_resolve_config_template_path", lambda: template_path)

    bcn_plugin_path = tmp_path / "openclaw-channel-bcn"
    bcn_entry_point = bcn_plugin_path / "dist" / "esm" / "index.js"
    bcn_entry_point.parent.mkdir(parents=True)
    bcn_entry_point.write_text("export default {}", encoding="utf-8")
    monkeypatch.setenv("BCN_PLUGIN_PATH", str(bcn_plugin_path))
    monkeypatch.setenv("BCS_PORT", "21001")

    engine_root = tmp_path / "aidesktop_singlebox" / "bolt_data" / "staff_100014" / "bot_v2" / "openclaw"
    workspace_dir = engine_root / "workspace"
    workspace_dir.mkdir(parents=True)

    config_dir = manager.create_openclaw_config(
        bolt_id="bot_v2",
        openclaw_port=18888,
        workspace_dir=workspace_dir,
        entity_id="100014",
    )

    assert config_dir == engine_root
    config = json.loads((engine_root / "openclaw.json").read_text(encoding="utf-8"))
    assert config["gateway"]["port"] == 18888
    assert config["agents"]["defaults"]["workspace"] == str(workspace_dir)
    assert config["channels"]["bcs"]["bcsUrl"] == "ws://127.0.0.1:21001/ws/bot"
    assert config["channels"]["bcs"]["botId"] == "bot_v2:100014"
    assert config["plugins"]["load"]["paths"] == [str(bcn_plugin_path)]
