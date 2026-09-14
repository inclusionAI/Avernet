import importlib.util
import json
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "adapt_openclaw_environment.py"
SPEC = importlib.util.spec_from_file_location("adapt_openclaw_environment", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def test_only_modifies_plugins_that_already_exist(tmp_path):
    config = tmp_path / "openclaw.json"
    marker = tmp_path / "marker.json"
    write(config, {"keep": 1, "plugins": {"entries": {"agent-guard": {"enabled": True, "custom": 2}}}})

    result = MODULE.check_and_adapt(config, marker)
    value = json.loads(config.read_text(encoding="utf-8"))
    assert result["status"] == "changed"
    assert value["plugins"]["entries"]["agent-guard"] == {
        "enabled": False,
        "custom": 2,
        "hooks": {"allowConversationAccess": True},
    }
    assert "clawmind" not in value["plugins"]["entries"]
    assert value["keep"] == 1
    assert not marker.exists()


def test_no_existing_target_plugins_does_not_modify_or_restart(tmp_path):
    config = tmp_path / "openclaw.json"
    marker = tmp_path / "marker.json"
    original = {"plugins": {"entries": {"other": {"enabled": True}}}}
    write(config, original)
    result = MODULE.check_and_adapt(config, marker)
    assert result["status"] == "unchanged"
    assert json.loads(config.read_text(encoding="utf-8")) == original
    assert MODULE.marker_is_current(marker, config)


def test_config_mtime_change_causes_recheck(tmp_path):
    config = tmp_path / "openclaw.json"
    marker = tmp_path / "marker.json"
    write(config, {"plugins": {"entries": {}}})
    MODULE.check_and_adapt(config, marker)
    assert MODULE.check_and_adapt(config, marker)["status"] == "cached"
    write(config, {"plugins": {"entries": {}}, "changed": True})
    assert MODULE.check_and_adapt(config, marker)["status"] == "unchanged"
    assert MODULE.marker_is_current(marker, config)


def test_clawmind_rules_and_restore(tmp_path):
    config = tmp_path / "openclaw.json"
    marker = tmp_path / "marker.json"
    original = {"plugins": {"entries": {"clawmind": {"enabled": False, "config": {"keep": 1}}}}}
    write(config, original)
    result = MODULE.check_and_adapt(config, marker)
    entry = json.loads(config.read_text(encoding="utf-8"))["plugins"]["entries"]["clawmind"]
    assert entry["enabled"] is True
    assert entry["config"]["keep"] == 1
    assert entry["config"]["api"]["enabled"] is False
    assert entry["config"]["flowControl"]["enabled"] is False
    assert entry["config"]["contextCompression"]["enabled"] is False
    MODULE.restore(config, Path(result["backup_path"]))
    assert json.loads(config.read_text(encoding="utf-8")) == original


def test_missing_openclaw_config_is_cached_until_it_appears(tmp_path):
    config = tmp_path / "openclaw.json"
    marker = tmp_path / "marker.json"
    assert MODULE.check_and_adapt(config, marker)["engine"] == "other"
    assert MODULE.check_and_adapt(config, marker)["status"] == "cached"
    write(config, {"plugins": {"entries": {}}})
    assert MODULE.check_and_adapt(config, marker)["engine"] == "openclaw"
