from __future__ import annotations

import ctypes
import errno
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest

from engine.community.plugins.skills_pool import layout_atomic


def test_atomic_rename_if_absent_preserves_existing_target(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.write_text("legacy-window", encoding="utf-8")
    target.write_text("pool-write", encoding="utf-8")

    published = layout_atomic.atomic_rename_if_absent(source, target)

    assert published is False
    assert source.read_text(encoding="utf-8") == "legacy-window"
    assert target.read_text(encoding="utf-8") == "pool-write"


def test_atomic_rename_if_absent_rejects_cross_filesystem_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_parent = tmp_path / "source-parent"
    target_parent = tmp_path / "target-parent"
    source_parent.mkdir()
    target_parent.mkdir()
    source = source_parent / "source"
    target = target_parent / "target"
    source.write_text("legacy-window", encoding="utf-8")
    real_stat = Path.stat

    def different_devices(path: Path, *args: object, **kwargs: object):
        if path == source_parent:
            return SimpleNamespace(st_dev=1)
        if path == target_parent:
            return SimpleNamespace(st_dev=2)
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", different_devices)

    with pytest.raises(OSError) as raised:
        layout_atomic.atomic_rename_if_absent(source, target)

    assert raised.value.errno == errno.EXDEV
    assert source.read_text(encoding="utf-8") == "legacy-window"
    assert not target.exists()


def test_atomic_rename_if_absent_keeps_unsupported_filesystem_visible(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeRenameAt2:
        argtypes: ClassVar[list[object]] = []
        restype: object | None = None

        def __call__(self, *_args: object) -> int:
            ctypes.set_errno(errno.ENOTSUP)
            return -1

    class FakeLibc:
        renameat2 = FakeRenameAt2()

    source = tmp_path / "source"
    target = tmp_path / "target"
    source.write_text("legacy-window", encoding="utf-8")
    monkeypatch.setattr(layout_atomic.sys, "platform", "linux")
    monkeypatch.setattr(
        layout_atomic.ctypes,
        "CDLL",
        lambda *_args, **_kwargs: FakeLibc(),
    )

    with pytest.raises(OSError) as raised:
        layout_atomic.atomic_rename_if_absent(source, target)

    assert raised.value.errno == errno.ENOTSUP
    assert source.read_text(encoding="utf-8") == "legacy-window"
    assert not target.exists()
