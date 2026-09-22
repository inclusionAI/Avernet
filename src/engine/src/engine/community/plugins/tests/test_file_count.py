"""File count behavior: confinement, entry semantics and worker lifetime."""
import os
import asyncio
import sys

import pytest

from engine.community.plugins.openclaw._file import _FilePortMixin


@pytest.mark.asyncio
async def test_count_regular_entries_and_allowed_links(tmp_path, monkeypatch):
    root = tmp_path / "openclaw"
    work = root / "workspace"
    work.mkdir(parents=True)
    (work / ".hidden").write_text("hidden")
    (work / "archive.zip").write_bytes(b"not expanded")
    os.link(work / ".hidden", work / "hardlink")
    (work / "nested").mkdir()
    (work / "nested" / "file").write_text("nested")
    (work / "empty").mkdir()
    (work / "file-link").symlink_to(work / ".hidden")
    (work / "directory-link").symlink_to(tmp_path, target_is_directory=True)
    monkeypatch.setenv("OPENCLAW_WORKSPACE_DIR", str(work))
    port = _FilePortMixin()
    result = await port.count_files("workspace")
    assert result["path"] == "workspace"
    assert result["file_count"] == 5
    assert isinstance(result["elapsed_ms"], int)
    assert result["elapsed_ms"] >= 0


@pytest.mark.asyncio
@pytest.mark.parametrize("path,code", [
    ("", "invalid_path"), ("../outside", "path_forbidden"),
    ("/etc", "path_forbidden"), ("workspace/missing", "path_not_found"),
    ("workspace/file", "not_directory"),
    ("workspace/\x00", "invalid_path"),
])
async def test_count_rejects_invalid_target(tmp_path, monkeypatch, path, code):
    work = tmp_path / "openclaw" / "workspace"
    work.mkdir(parents=True)
    (work / "file").write_text("data")
    (work / "link").symlink_to(tmp_path, target_is_directory=True)
    monkeypatch.setenv("OPENCLAW_WORKSPACE_DIR", str(work))
    with pytest.raises(Exception) as error:
        await _FilePortMixin().count_files(path)
    assert str(error.value) == code


@pytest.mark.asyncio
async def test_absolute_path_and_empty_directory(tmp_path, monkeypatch):
    work = tmp_path / "workspace"
    work.mkdir()
    monkeypatch.setenv("OPENCLAW_WORKSPACE_DIR", str(work))
    result = await _FilePortMixin().count_files(str(work))
    assert result["path"] == str(work)
    assert result["file_count"] == 0


def test_worker_ignores_special_files_and_checks_root_ancestry(tmp_path):
    from engine.community.plugins.file_count_worker import count, ScanError

    root = tmp_path / "root"
    root.mkdir()
    os.mkfifo(root / "pipe")
    (root / "dangling").symlink_to(root / "absent")
    (root / "loop").symlink_to(root)
    (root / "a").write_text("one")
    assert count(str(root), ".") == 1
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    with pytest.raises(ScanError, match="path_forbidden"):
        count(str(alias), ".")
    with pytest.raises(ScanError, match="path_forbidden"):
        count("relative/root", ".")


def test_worker_rejects_directory_swap_without_reading_outside(tmp_path, monkeypatch):
    from engine.community.plugins import file_count_worker as worker

    root = tmp_path / "root"
    root.mkdir()
    nested = root / "nested"
    nested.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_text("not observable")
    original_open = worker.os.open
    seen = []

    def swap(name, flags, **kwargs):
        if name == "nested":
            nested.rmdir()
            nested.symlink_to(outside, target_is_directory=True)
        fd = original_open(name, flags, **kwargs)
        seen.append(os.fstat(fd).st_ino)
        return fd

    monkeypatch.setattr(worker.os, "open", swap)
    with pytest.raises(worker.ScanError, match="path_forbidden"):
        worker.count(str(root), ".")
    assert outside.stat().st_ino not in seen


@pytest.mark.parametrize("failure,expected", [
    (PermissionError(), "permission_denied"),
    (FileNotFoundError(), "directory_changed"),
    (NotADirectoryError(), "directory_changed"),
    (OSError(), "scan_failed"),
])
def test_worker_scan_errors_are_not_partial_success(tmp_path, monkeypatch, failure, expected):
    from engine.community.plugins import file_count_worker as worker

    def fail(_fd):
        raise failure

    monkeypatch.setattr(worker.os, "scandir", fail)
    with pytest.raises(worker.ScanError, match=expected):
        worker.count(str(tmp_path), ".")


def test_worker_detects_directory_replacement_and_changed_metadata(tmp_path, monkeypatch):
    from engine.community.plugins import file_count_worker as worker

    root = tmp_path / "root"
    root.mkdir()
    original_scan = worker.os.scandir

    def mutate(fd):
        (root / "new").write_text("changed after before-stat")
        return original_scan(fd)

    monkeypatch.setattr(worker.os, "scandir", mutate)
    with pytest.raises(worker.ScanError, match="directory_changed"):
        worker.count(str(root), ".")


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_worker_timeout_and_cancellation_reap_real_process(tmp_path, monkeypatch, cancel):
    from engine.community.plugins import file_count as scanner
    from engine.community.kernel.file_count import FileCountError

    original_spawn = asyncio.create_subprocess_exec
    spawned = asyncio.Event()
    children = []

    async def slow_spawn(*args, **kwargs):
        process = await original_spawn(sys.executable, "-c", "import time; time.sleep(60)", **kwargs)
        children.append(process)
        spawned.set()
        return process

    with monkeypatch.context() as patch:
        patch.setattr(scanner.asyncio, "create_subprocess_exec", slow_spawn)
        patch.setattr(scanner, "SCAN_TIMEOUT_SECONDS", 0.1)
        task = asyncio.create_task(scanner.count_files(tmp_path, "."))
        await spawned.wait()
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(FileCountError, match="scan_timeout"):
                await task
    assert children[0].returncode is not None
    with pytest.raises(ProcessLookupError):
        os.kill(children[0].pid, 0)
    assert (await scanner.count_files(tmp_path, "."))["file_count"] == 0


@pytest.mark.asyncio
async def test_concurrency_is_bounded_and_slots_recover(tmp_path, monkeypatch):
    from engine.community.plugins import file_count as scanner
    from engine.community.kernel.file_count import FileCountError

    original_spawn = asyncio.create_subprocess_exec
    children = []
    ready = asyncio.Event()

    async def slow_spawn(*args, **kwargs):
        process = await original_spawn(sys.executable, "-c", "import time; time.sleep(60)", **kwargs)
        children.append(process)
        if len(children) == 2:
            ready.set()
        return process

    with monkeypatch.context() as patch:
        patch.setattr(scanner.asyncio, "create_subprocess_exec", slow_spawn)
        tasks = [asyncio.create_task(scanner.count_files(tmp_path, ".")) for _ in range(2)]
        try:
            await ready.wait()
            with pytest.raises(FileCountError, match="busy"):
                await scanner.count_files(tmp_path, ".")
            assert len(children) == 2
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    assert all(child.returncode is not None for child in children)
    assert (await scanner.count_files(tmp_path, "."))["file_count"] == 0


def test_recursive_log_redaction_keeps_business_fields():
    from engine.community.kernel.file_count import redact_fields

    fields = {"path": "workspace", "request_id": "req-1", "nested": [
        {name: "never-print" for name in ("Token", "AUTHORIZATION", "Cookie", "password", "secret", "api_key", "credential", "session")}
    ]}
    safe = redact_fields(fields)
    assert safe["path"] == "workspace"
    assert safe["request_id"] == "req-1"
    assert "never-print" not in str(safe)
    assert fields["nested"][0]["Token"] == "never-print"


@pytest.mark.asyncio
async def test_local_and_claude_implementations_explicitly_unsupported():
    from engine.community.local.openclaw import LocalOpenClawPluginImpl
    from engine.community.local.claude_code import LocalClaudeCodePluginImpl
    from engine.community.core.adapters.openclaw.file import OpenClawFileAdapter
    from engine.community.core.adapters.claude_code.file import ClaudeCodeFileAdapter
    from engine.community.kernel.file_count import FileCountError

    for adapter in (OpenClawFileAdapter(LocalOpenClawPluginImpl()), ClaudeCodeFileAdapter(LocalClaudeCodePluginImpl())):
        with pytest.raises(FileCountError, match="unsupported"):
            await adapter.count_files(".")


def test_direct_worker_success_absolute_nested_and_validation(tmp_path):
    from engine.community.plugins.file_count_worker import count, ScanError

    child = tmp_path / "nested"
    child.mkdir()
    (child / "a").write_text("a")
    assert count(str(tmp_path), ".") == 1
    assert count(str(tmp_path), str(child)) == 1
    (tmp_path / "file").write_text("file")
    for path, error in [("", "invalid_path"), ("x\x00", "invalid_path"),
                        ("../x", "path_forbidden"), ("/etc", "path_forbidden"),
                        ("file", "not_directory"), ("missing", "path_not_found")]:
        with pytest.raises(ScanError, match=error):
            count(str(tmp_path), path)


@pytest.mark.parametrize("swap_before_open", [True, False])
def test_worker_detects_replaced_regular_directory(tmp_path, monkeypatch, swap_before_open):
    from engine.community.plugins import file_count_worker as worker

    root = tmp_path / "root"
    child = root / "child"
    child.mkdir(parents=True)
    original_open = worker.os.open
    original_scan = worker._scan

    def replace():
        child.rename(root / "old")
        child.mkdir()

    def opening(name, flags, **kwargs):
        if name == "child":
            replace()
        return original_open(name, flags, **kwargs)

    def scanning(fd, *args):
        result = original_scan(fd, *args)
        if os.fstat(fd).st_ino == child.stat().st_ino:
            replace()
        return result

    if swap_before_open:
        monkeypatch.setattr(worker.os, "open", opening)
    else:
        monkeypatch.setattr(worker, "_scan", scanning)
    with pytest.raises(worker.ScanError, match="directory_changed"):
        worker.count(str(root), ".")


@pytest.mark.parametrize("path", [".", "missing"])
def test_worker_wire_output(tmp_path, monkeypatch, capsys, path):
    import json
    from engine.community.plugins import file_count_worker as worker

    monkeypatch.setattr(sys, "argv", ["worker", str(tmp_path), path])
    worker.main()
    output = json.loads(capsys.readouterr().out)
    assert output == ({"file_count": 0} if path == "." else {"error": "path_not_found"})


def test_worker_unexpected_exception_is_sanitized(monkeypatch, capsys):
    from engine.community.plugins import file_count_worker as worker

    monkeypatch.setattr(sys, "argv", ["worker"])
    worker.main()
    assert capsys.readouterr().out.strip() == '{"error": "scan_failed"}'


@pytest.mark.asyncio
@pytest.mark.parametrize("script", [
    "raise SystemExit(1)", "print('not-json')", "print('{}')",
    "print('{\"file_count\": true}')", "print('{\"file_count\": -1}')",
])
async def test_invalid_worker_output_fails_and_releases_slot(tmp_path, monkeypatch, script):
    from engine.community.plugins import file_count as scanner
    from engine.community.kernel.file_count import FileCountError

    original_spawn = asyncio.create_subprocess_exec

    async def spawn(*args, **kwargs):
        return await original_spawn(sys.executable, "-c", script, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(scanner.asyncio, "create_subprocess_exec", spawn)
        with pytest.raises(FileCountError, match="scan_failed"):
            await scanner.count_files(tmp_path, ".")
    assert (await scanner.count_files(tmp_path, "."))["file_count"] == 0


@pytest.mark.asyncio
async def test_spawn_failure_releases_slot(tmp_path, monkeypatch):
    from engine.community.plugins import file_count as scanner
    from engine.community.kernel.file_count import FileCountError

    async def fail(*args, **kwargs):
        raise OSError("sensitive operating-system detail")

    with monkeypatch.context() as patch:
        patch.setattr(scanner.asyncio, "create_subprocess_exec", fail)
        with pytest.raises(FileCountError, match="^scan_failed$"):
            await scanner.count_files(tmp_path, ".")
    assert (await scanner.count_files(tmp_path, "."))["file_count"] == 0


@pytest.mark.asyncio
async def test_worker_ignoring_terminate_is_killed(tmp_path, monkeypatch):
    from engine.community.plugins import file_count as scanner
    from engine.community.kernel.file_count import FileCountError

    original_spawn = asyncio.create_subprocess_exec
    children = []

    async def stubborn(*args, **kwargs):
        script = "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('ready', flush=True); time.sleep(60)"
        process = await original_spawn(sys.executable, "-c", script, **kwargs)
        await process.stdout.readline()
        children.append(process)
        return process

    monkeypatch.setattr(scanner.asyncio, "create_subprocess_exec", stubborn)
    monkeypatch.setattr(scanner, "SCAN_TIMEOUT_SECONDS", 0.15)
    with pytest.raises(FileCountError, match="scan_timeout"):
        await scanner.count_files(tmp_path, ".")
    assert children[0].returncode == -9


@pytest.mark.asyncio
async def test_cancel_during_spawn_and_repeated_cancel_waits_for_cleanup(tmp_path, monkeypatch):
    from engine.community.plugins import file_count as scanner

    original_spawn = asyncio.create_subprocess_exec
    created = asyncio.Event()
    deliver = asyncio.Event()
    children = []

    async def delayed(*args, **kwargs):
        process = await original_spawn(sys.executable, "-c", "import time; time.sleep(60)", **kwargs)
        children.append(process)
        created.set()
        await deliver.wait()
        return process

    monkeypatch.setattr(scanner.asyncio, "create_subprocess_exec", delayed)
    task = asyncio.create_task(scanner.count_files(tmp_path, "."))
    await created.wait()
    task.cancel()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    task.cancel()
    deliver.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert children[0].returncode is not None


@pytest.mark.asyncio
async def test_terminate_exit_race_is_reaped(monkeypatch):
    from engine.community.plugins import file_count as scanner

    process = await asyncio.create_subprocess_exec(sys.executable, "-c", "import time; time.sleep(60)")

    def exit_race():
        os.kill(process.pid, 9)
        raise ProcessLookupError

    monkeypatch.setattr(process, "terminate", exit_race)
    await scanner._stop(process)
    assert process.returncode is not None


@pytest.mark.parametrize("roots", [(), ("relative",), ("/",), ("/safe/../outside",)])
def test_worker_rejects_invalid_explicit_boundaries(tmp_path, roots):
    from pathlib import Path
    from engine.community.plugins.file_count_worker import count, ScanError

    with pytest.raises(ScanError, match="path_forbidden"):
        count(str(tmp_path.resolve()), ".", allowed_roots=tuple(Path(root) for root in roots))
