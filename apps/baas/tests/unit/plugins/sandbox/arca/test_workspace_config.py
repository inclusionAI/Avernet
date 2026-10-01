from pathlib import Path
from types import SimpleNamespace

from secbaas.community.plugins.sandbox.arca.local_proc import _workspace


def test_workspace_folder_comes_from_loaded_profile_config(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCAL_AIDESKTOP_ROOT", str(tmp_path))
    monkeypatch.setenv("SERVER_ENV", "dev")
    config = SimpleNamespace(
        user_config={"workspace": {"env_folder": "aidesktop_singlebox"}}
    )
    monkeypatch.setattr(_workspace, "get_config", lambda: config, raising=False)

    actual = _workspace.resolve_workspace_dir(
        {"entity_id": "owner", "entity_type": "staff", "engine": "openclaw"},
        "bot-1",
    )

    assert actual == (
        Path(tmp_path)
        / "aidesktop_singlebox"
        / "bolt_data"
        / "staff_owner"
        / "bot-1"
        / "openclaw"
        / "workspace"
    )
