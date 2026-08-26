"""Package I/O batching contract for :class:`LocalSkillPackageStorage`.

Every package file is one device round trip, so the storage port fans them out
instead of issuing them one at a time. These pin the three properties that make
that substitution safe for the callers built on the old sequential loop:

* files really do overlap (otherwise the fan-out bought nothing),
* the fan-out stays bounded (``asyncio.to_thread``'s default executor is shared
  with the rest of the process),
* a failed batch is fully drained before the error surfaces, and reports the
  same failure the sequential loop would have reported.

The drain matters most: ``LocalSkillUploadService`` reacts to a failed ``write``
by deleting the package directory, so a write still in flight then would land a
file behind the cleanup and leave an orphan.
"""

import asyncio

import pytest

from agentclaw.community.core.skill_center.factories import (
    _PACKAGE_IO_CONCURRENCY,
    LocalSkillPackageStorage,
)

DIRECTORY = "/private/skills-local/pkg"


class _RecordingFilesystem:
    """Records call overlap so concurrency is observable, not just assumed."""

    def __init__(self, *, fail_paths=(), delay=0.01):
        self.files: dict[str, bytes] = {}
        self.completed: list[str] = []
        self.in_flight = 0
        self.peak_in_flight = 0
        self._fail_paths = set(fail_paths)
        self._delay = delay

    async def _tracked(self, path):
        self.in_flight += 1
        self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self._delay)
            if path in self._fail_paths:
                raise OSError(f"device rejected {path}")
        finally:
            self.in_flight -= 1

    async def write_file(self, path, content):
        await self._tracked(path)
        self.files[path] = content
        self.completed.append(path)

    async def read_file(self, path):
        await self._tracked(path)
        self.completed.append(path)
        return self.files.get(path)

    async def list_dir(self, path, *, recursive=False):
        prefix = f"{path}/"
        return [
            {"relative_path": stored[len(prefix):], "is_dir": False}
            for stored in self.files
            if stored.startswith(prefix)
        ] or None


def _package(count):
    return [(f"f{index}.md", f"body-{index}".encode()) for index in range(count)]


@pytest.mark.asyncio
async def test_write_issues_package_files_concurrently():
    filesystem = _RecordingFilesystem()
    storage = LocalSkillPackageStorage(filesystem, DIRECTORY)

    await storage.write(_package(5))

    assert filesystem.peak_in_flight > 1
    assert len(filesystem.files) == 5


@pytest.mark.asyncio
async def test_write_fan_out_stays_bounded():
    """An unbounded gather over a big package would starve the shared executor."""
    filesystem = _RecordingFilesystem()
    storage = LocalSkillPackageStorage(filesystem, DIRECTORY)

    await storage.write(_package(_PACKAGE_IO_CONCURRENCY * 3))

    assert filesystem.peak_in_flight <= _PACKAGE_IO_CONCURRENCY


@pytest.mark.asyncio
async def test_a_failed_write_drains_every_sibling_before_raising():
    """No write may still be in flight when the caller starts its cleanup."""
    files = _package(6)
    filesystem = _RecordingFilesystem(fail_paths={f"{DIRECTORY}/f0.md"})
    storage = LocalSkillPackageStorage(filesystem, DIRECTORY)

    with pytest.raises(OSError):
        await storage.write(files)

    assert filesystem.in_flight == 0
    # the five that did not fail all ran to completion rather than being abandoned
    assert len(filesystem.completed) == 5


@pytest.mark.asyncio
async def test_a_failed_write_reports_the_first_failure_in_file_order():
    """Same error the sequential loop would have surfaced, not a race winner."""
    files = _package(6)
    filesystem = _RecordingFilesystem(
        fail_paths={f"{DIRECTORY}/f1.md", f"{DIRECTORY}/f4.md"}
    )
    storage = LocalSkillPackageStorage(filesystem, DIRECTORY)

    with pytest.raises(OSError, match="f1.md"):
        await storage.write(files)


@pytest.mark.asyncio
async def test_reading_a_package_is_concurrent_and_order_preserving():
    filesystem = _RecordingFilesystem()
    storage = LocalSkillPackageStorage(filesystem, DIRECTORY)
    files = _package(5)
    await storage.write(files)
    filesystem.peak_in_flight = 0

    assert sorted(await storage._read_package_files()) == sorted(files)
    assert filesystem.peak_in_flight > 1


@pytest.mark.asyncio
async def test_an_invalid_listed_path_is_rejected_before_any_read():
    """Validation still gates the whole package, not just the files read so far."""
    filesystem = _RecordingFilesystem()
    filesystem.files[f"{DIRECTORY}/ok.md"] = b"fine"
    filesystem.files[f"{DIRECTORY}/../escape.md"] = b"bad"
    storage = LocalSkillPackageStorage(filesystem, DIRECTORY)

    with pytest.raises(OSError, match="invalid file path"):
        await storage._read_package_files()

    assert filesystem.completed == []


# ── end-to-end over the real baas transport ──────────────────────────────


@pytest.mark.asyncio
async def test_a_package_upload_resolves_http_info_once_for_the_whole_batch():
    """storage → BaasDeviceFileSystem → BaasInvokeTransport, wired for real.

    Writing a 12-file package used to cost 12 binding lookups + 12 BaaS
    ``/http-info`` round trips + 12 uploads, all strictly sequential. It now
    costs one resolution and 12 concurrent uploads.
    """
    from unittest.mock import MagicMock

    import httpx

    from agentclaw.community.core.devices.services.baas_device_filesystem import (
        BaasDeviceFileSystem,
    )
    from agentclaw.community.core.devices.services.baas_invoke_transport import (
        BaasInvokeTransport,
    )

    baas_service = MagicMock()
    baas_service.invoke_http.return_value = httpx.Response(
        status_code=200, request=httpx.Request("POST", "http://fake/")
    )
    transport = BaasInvokeTransport(
        bind_id=42, engine_port=20003, tenant="team_claw",
        baas_service=baas_service,
    )
    storage = LocalSkillPackageStorage(
        BaasDeviceFileSystem(
            transport=transport,
            conn_info={"paas_device_id": "BOT-abc"},
            path_mapper=lambda path: path,
        ),
        DIRECTORY,
    )

    files = _package(12)
    await storage.write(files)

    assert baas_service.get_http_info.call_count == 1
    assert baas_service.invoke_http.call_count == 12
    uploaded = {
        call.kwargs["data"]["target_path"] for call in baas_service.invoke_http.call_args_list
    }
    assert uploaded == {f"{DIRECTORY}/{name}" for name, _ in files}
