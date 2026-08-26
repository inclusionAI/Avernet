"""Device-I/O batching contract for the legacy Skills API surface.

``SkillService`` backs the legacy addresses — ``POST /skills/upload`` and
``GET /skills/active/list`` — and both used to address the device one file at a
time, so a request cost ``file_count × round_trip``. These pin the two
substitutions that removed that, and the properties that make each safe.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.core.devices.device_io_batch import DEVICE_IO_CONCURRENCY
from agentclaw.community.core.skill_center.services.skill_service import SkillService


class _RecordingDeviceFilesystem:
    """Records overlap and completion order so both are observable, not assumed."""

    def __init__(self, *, fail_paths=(), delay=0.01):
        self.files: dict[str, bytes] = {}
        self.events: list[str] = []
        self.in_flight = 0
        self.peak_in_flight = 0
        self._fail_paths = set(fail_paths)
        self._delay = delay

    async def write_file(self, path, content):
        self.in_flight += 1
        self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self._delay)
            if path in self._fail_paths:
                raise OSError(f"device rejected {path}")
            self.files[path] = content
            self.events.append(f"write:{path}")
        finally:
            self.in_flight -= 1

    async def read_file(self, path, **_kwargs):
        self.in_flight += 1
        self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self._delay)
            self.events.append(f"read:{path}")
            return self.files.get(path)
        finally:
            self.in_flight -= 1

    async def list_dir(self, path, *, recursive=False):
        return None

    async def delete_tree(self, path):
        self.events.append(f"delete_tree:{path}")
        return True

    async def exists(self, path):
        raise AssertionError(f"the read answers existence; do not probe {path}")


def _repo():
    repo = MagicMock()
    repo.list_skills.return_value = []
    repo.list_skill_set_references.return_value = []
    repo.get_bot_local_by_name.return_value = None
    repo.get_by_name_global.return_value = None
    repo.create.side_effect = lambda data: {"id": "1", **data}
    return repo


def _service(tmp_path, device_fs, *, active_dir=None):
    return SkillService(
        skill_repo=_repo(),
        skill_repo_sync=MagicMock(),
        market_cache=MagicMock(),
        category_repo=MagicMock(),
        active_dir=active_dir or (tmp_path / "skills"),
        repo_dir=tmp_path / "skills-repo",
        local_dir=tmp_path / "skills-local",
        device_fs_factory=lambda bolt_id, user_id: device_fs,
        git_sync_service_factory=MagicMock(),
    )


_MANIFEST = b"---\nname: test-skill\ndescription: test\n---\n# Test"


def _upload_files(count):
    """A SKILL.md plus ``count`` sibling files — the shape of a real package."""
    files = [
        {"filename": "SKILL.md", "content": _MANIFEST, "relative_path": "SKILL.md"}
    ]
    files += [
        {
            "filename": f"asset{index}.txt",
            "content": f"body-{index}".encode(),
            "relative_path": f"assets/asset{index}.txt",
        }
        for index in range(count)
    ]
    return files


# ── POST /skills/upload ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_upload_writes_package_files_concurrently(tmp_path):
    device_fs = _RecordingDeviceFilesystem()
    service = _service(tmp_path, device_fs)

    await service.upload_skill(_upload_files(5), user_id="user1", bolt_id="bot1")

    assert device_fs.peak_in_flight > 1
    assert len(device_fs.files) == 6


@pytest.mark.asyncio
async def test_upload_fan_out_stays_bounded(tmp_path):
    """The blocking device transport shares one ``to_thread`` executor process-wide."""
    device_fs = _RecordingDeviceFilesystem()
    service = _service(tmp_path, device_fs)

    await service.upload_skill(
        _upload_files(DEVICE_IO_CONCURRENCY * 3), user_id="user1", bolt_id="bot1"
    )

    assert device_fs.peak_in_flight <= DEVICE_IO_CONCURRENCY


@pytest.mark.asyncio
async def test_failed_upload_is_drained_before_the_rollback_deletes_the_tree(tmp_path):
    """A write still in flight during the rollback would outlive it as an orphan.

    ``upload_skill`` compensates a failure by deleting the whole skill directory,
    so every write must have finished — succeeded or failed — before that delete
    is issued. The fan-out drains for exactly this reason.
    """
    files = _upload_files(7)
    device_fs = _RecordingDeviceFilesystem(delay=0)
    # Fail the first file in order; the rest keep running and must all land first.
    service = _service(tmp_path, device_fs)
    failing = str(
        service._local_skill_path_adapter(
            str(service.local_dir / "test-skill")
        )
    ) + "/SKILL.md"
    device_fs._fail_paths = {failing}

    with pytest.raises(ValueError):
        await service.upload_skill(files, user_id="user1", bolt_id="bot1")

    deletes = [i for i, e in enumerate(device_fs.events) if e.startswith("delete_tree:")]
    writes = [i for i, e in enumerate(device_fs.events) if e.startswith("write:")]
    # events[0] is the pre-write delete_tree; the rollback delete is the last one.
    assert len(deletes) == 2
    assert device_fs.in_flight == 0
    assert writes, "the surviving files should still have been attempted"
    assert max(writes) < deletes[-1]


# ── GET /skills/active/list ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_active_list_reads_every_entry_concurrently(tmp_path):
    active_root = tmp_path / "skills"
    device_fs = _RecordingDeviceFilesystem()
    names = [f"skill-{index}" for index in range(5)]
    for name in names:
        device_fs.files[str(active_root / name / "SKILL.md")] = (
            f"---\nname: {name}\n---\n".encode()
        )
    device_fs.list_dir = AsyncMock(
        return_value=[
            {"name": name, "path": str(active_root / name), "is_dir": True}
            for name in names
        ]
    )
    service = _service(tmp_path, device_fs, active_dir=active_root)
    service.get_skill_by_link_name = MagicMock(return_value=None)

    skills = await service.get_active_skills_from_device(
        bot_id="bot1", owner_id="user1"
    )

    assert sorted(skill.id for skill in skills) == sorted(names)
    assert device_fs.peak_in_flight > 1
    # One read per entry: ``exists`` is itself a read on the baas backend, so the
    # probe it replaces was a second full download of every SKILL.md.
    assert len([e for e in device_fs.events if e.startswith("read:")]) == len(names)


@pytest.mark.asyncio
async def test_active_list_skips_entries_whose_manifest_is_absent(tmp_path):
    """A missing SKILL.md reads back as ``None`` — that is the whole check."""
    active_root = tmp_path / "skills"
    device_fs = _RecordingDeviceFilesystem()
    device_fs.files[str(active_root / "real" / "SKILL.md")] = b"---\nname: real\n---\n"
    device_fs.list_dir = AsyncMock(
        return_value=[
            {"name": "dangling", "path": str(active_root / "dangling"), "is_dir": True},
            {"name": "real", "path": str(active_root / "real"), "is_dir": True},
        ]
    )
    service = _service(tmp_path, device_fs, active_dir=active_root)
    service.get_skill_by_link_name = MagicMock(return_value=None)

    skills = await service.get_active_skills_from_device(
        bot_id="bot1", owner_id="user1"
    )

    assert [skill.id for skill in skills] == ["real"]


# ── activate / deactivate batches ─────────────────────────────────────
#
# These already ran concurrently, but through a raw ``asyncio.gather`` — every
# activation blocks a worker in the ``asyncio.to_thread`` executor, which is
# shared process-wide (``min(32, cpu_count + 4)`` threads), so a large market
# selection or skill set starved every other caller until it drained.

@pytest.mark.asyncio
async def test_activate_batch_stays_bounded(tmp_path):
    state = {"in_flight": 0, "peak": 0}

    async def _activate(skill_path, **_kwargs):
        state["in_flight"] += 1
        state["peak"] = max(state["peak"], state["in_flight"])
        try:
            await asyncio.sleep(0.005)
            return True
        finally:
            state["in_flight"] -= 1

    service = _service(tmp_path, _RecordingDeviceFilesystem())
    service.activate_skill = _activate

    paths = [f"git://biz/skill-{index}" for index in range(DEVICE_IO_CONCURRENCY * 3)]
    results = await service.activate_skills_batch(paths, user_id="u1", bolt_id="bot1")

    assert len(results["success"]) == len(paths)
    assert state["peak"] > 1
    assert state["peak"] <= DEVICE_IO_CONCURRENCY


@pytest.mark.asyncio
async def test_activate_batch_still_reports_each_failure_against_its_path(tmp_path):
    async def _activate(skill_path, **_kwargs):
        if skill_path.endswith("-1"):
            raise RuntimeError("device refused")
        return not skill_path.endswith("-2")

    service = _service(tmp_path, _RecordingDeviceFilesystem())
    service.activate_skill = _activate

    paths = [f"git://biz/skill-{index}" for index in range(4)]
    results = await service.activate_skills_batch(paths, user_id="u1", bolt_id="bot1")

    assert [entry["path"] for entry in results["success"]] == [
        "git://biz/skill-0",
        "git://biz/skill-3",
    ]
    assert [(f["path"], f["error"]) for f in results["failed"]] == [
        ("git://biz/skill-1", "device refused"),
        ("git://biz/skill-2", "Failed to activate skill"),
    ]


@pytest.mark.asyncio
async def test_deactivate_all_removes_entries_concurrently(tmp_path):
    state = {"in_flight": 0, "peak": 0}

    async def _deactivate(skill_id, **_kwargs):
        state["in_flight"] += 1
        state["peak"] = max(state["peak"], state["in_flight"])
        try:
            await asyncio.sleep(0.005)
            return skill_id != "skill-2"
        finally:
            state["in_flight"] -= 1

    service = _service(tmp_path, _RecordingDeviceFilesystem())
    active = [MagicMock(id=f"skill-{index}") for index in range(5)]
    service.get_active_skills = MagicMock(return_value=active)
    service.deactivate_skill = _deactivate

    results = await service.deactivate_all_skills()

    assert results["success"] == ["skill-0", "skill-1", "skill-3", "skill-4"]
    assert results["failed"] == ["skill-2"]
    assert state["peak"] > 1
