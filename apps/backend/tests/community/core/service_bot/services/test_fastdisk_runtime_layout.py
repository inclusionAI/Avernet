"""Trusted config ownership is independent of the terminal execution user."""
import importlib.util
import json
import os
import pwd
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

SOURCE = next(p for p in Path(__file__).resolve().parents if (p / 'src/agentclaw/community/resources').is_dir()) / 'src/agentclaw/community/resources/fastdisk_detection/runtime_layout.py'


@pytest.fixture
def layout_module(monkeypatch):
    spec = importlib.util.spec_from_file_location("layout_under_test", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(pwd, "getpwnam", lambda name: SimpleNamespace(pw_uid=1000))
    return module


def config_file(module, monkeypatch, owner, mode):
    fake = SimpleNamespace(
        exists=lambda: True,
        lstat=lambda: SimpleNamespace(st_uid=owner, st_mode=mode),
        read_text=lambda: json.dumps({"AICODING_ROOT": "/opt/.aicoding"}),
    )
    monkeypatch.setattr(module, "Path", lambda value: fake if value ==
                        "/var/run/agentclaw/persist-runtime.json" else Path(value))


@pytest.mark.parametrize("executor,owner", [(0, 1000), (1000, 0), (0, 0), (1000, 1000)])
def test_trusted_config_does_not_have_to_belong_to_executor(layout_module, monkeypatch, executor, owner):
    monkeypatch.setattr(os, "getuid", lambda: executor)
    config_file(layout_module, monkeypatch, owner, stat.S_IFREG | 0o600)
    assert layout_module.runtime_layout() == {
        "home": "/home/admin/.aicoding", "storage": "/opt/.aicoding"}


@pytest.mark.parametrize("owner,mode", [
    (2000, stat.S_IFREG | 0o600),
    (1000, stat.S_IFREG | 0o620),
    (1000, stat.S_IFREG | 0o602),
    (1000, stat.S_IFLNK | 0o600),
])
def test_unsafe_config_still_rejected(layout_module, monkeypatch, owner, mode):
    monkeypatch.setattr(os, "getuid", lambda: owner)
    config_file(layout_module, monkeypatch, owner, mode)
    with pytest.raises(ValueError, match="unsafe persistence runtime configuration"):
        layout_module.runtime_layout()
