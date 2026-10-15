"""Simulator shared-storage addresses use the existing OpenClaw file port."""

from pathlib import Path

import pytest

from engine.community.plugins.openclaw import _file
from engine.community.plugins.openclaw.plugin_impl import OpenClawPluginImpl
from engine.community.plugins.skills_pool.center_content import (
    MountedCenterContentAdapter,
)


@pytest.mark.asyncio
async def test_read_simulator_config_from_engine_home(tmp_path, monkeypatch):
    monkeypatch.setattr(_file, "_PATH_PREFIX_NEW", str(tmp_path) + "/")
    (tmp_path / "openclaw.json").write_bytes(b'{"gateway": {}}')
    impl = OpenClawPluginImpl(center_content_adapter=MountedCenterContentAdapter())

    content = await impl.read(
        "/home/admin/.aidesktop/aidesktop_dev/bolt_data/"
        "staff_42/bot-a/openclaw/openclaw.json"
    )

    assert content == b'{"gateway": {}}'


@pytest.mark.parametrize("relative", [
    "openclaw.json",
    "workspace/skills/skills-local/example/SKILL.md",
    "workspace/skills-pool/skills-local/example/SKILL.md",
])
def test_simulator_preserves_config_and_both_skill_layouts(relative):
    address = (
        "/home/admin/.aidesktop/aidesktop_dev/bolt_data/"
        "staff_42/bot-a/openclaw/" + relative
    )
    assert _file._convert_path(address) == Path("/home/admin/.openclaw") / relative


@pytest.mark.parametrize("address", [
    "/home/admin/.aidesktop/aidesktop_pre/bolt_data/staff_42/bot-a/openclaw/a",
    "/home/admin/.aidesktop/aidesktop_prod/bolt_data/staff_42/bot-a/openclaw/a",
    "/home/admin/.aidesktop/aidesktop_dev2/bolt_data/staff_42/bot-a/openclaw/a",
    "/home/admin/.aidesktop/aidesktop_dev/bolt_data/staff_42/bot-a/hermes/a",
    "/home/other/.aidesktop/aidesktop_dev/bolt_data/staff_42/bot-a/openclaw/a",
    "/aidesktop/aidesktop_dev/bolt_data/staff_42/bot-a/openclaw/a",
    "/home/admin/.openclaw/openclaw.json",
])
def test_other_address_forms_keep_existing_passthrough(address):
    assert _file._convert_path(address) == Path(address)


@pytest.mark.asyncio
async def test_simulator_file_operations_share_one_engine_location(tmp_path, monkeypatch):
    monkeypatch.setattr(_file, "_PATH_PREFIX_NEW", str(tmp_path) + "/")
    impl = OpenClawPluginImpl(center_content_adapter=MountedCenterContentAdapter())
    root = "/home/admin/.aidesktop/aidesktop_dev/bolt_data/staff_42/bot-a/openclaw/"
    directory = root + "workspace/qa/"
    address = directory + "proof.txt"

    result = await impl.upload(address, b"first")
    assert result["target_path"] == str(tmp_path / "workspace/qa/proof.txt")
    assert await impl.read(address) == b"first"
    result = await impl.upload(address, b"updated")
    assert result["overwritten"] is True
    assert await impl.read(address) == b"updated"
    listing = await impl.list_dir(directory)
    assert [item["name"] for item in listing["files"]] == ["proof.txt"]
    await impl.remove(address)
    with pytest.raises(FileNotFoundError):
        await impl.read(address)
    await impl.upload(address, b"again")
    await impl.rmtree(directory)
    with pytest.raises(FileNotFoundError):
        await impl.list_dir(directory)
