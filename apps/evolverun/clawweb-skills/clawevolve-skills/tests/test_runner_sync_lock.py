"""Exercise the Runner's actual release-sync critical section across processes."""
import fcntl
import os
from pathlib import Path
import select
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts/clawevolve_async_runner.sh"


def sync_script() -> str:
    source = RUNNER.read_text()
    start = source.index('if [[ "$STAGE" == "runtime-cleanup" ]]; then\n  log_line')
    end = source.index("\n# Environment adaptation", start)
    return """set -euo pipefail
STAGE=init
DEBUG_MODE=0
log_line() { :; }
sync_skills() {
  printf 'entered\n'
  if [[ "${HOLD_SYNC:-0}" == 1 ]]; then read -r reply; fi
}
sync_debug_skills() { sync_skills; }
# Reproduce the observed filesystem error only for the retired lock directory.
rm() {
  if [[ "$*" == *".sync.lock.d"* ]]; then
    printf 'Directory not empty\n' >&2
    return 1
  fi
  command rm "$@"
}
""" + source[start:end]


def start_sync(tmp_path: Path, *, hold: bool = False) -> subprocess.Popen:
    return subprocess.Popen(
        ["/bin/bash", "-c", sync_script()],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True,
        env=os.environ | {
            "CLAWEVOLVE_SKILLS_ROOT": str(tmp_path),
            "HOLD_SYNC": "1" if hold else "0",
            "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"],
        },
    )


def entered(process: subprocess.Popen) -> None:
    ready, _, _ = select.select([process.stdout], [], [], 5)
    assert ready, "release synchronization did not enter"
    assert process.stdout.readline() == "entered\n"


def dispose(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.kill()
    process.communicate(timeout=5)


def test_lock_directory_cleanup_cannot_abort_a_healthy_release(tmp_path):
    process = start_sync(tmp_path)
    stdout, stderr = process.communicate(timeout=5)
    assert process.returncode == 0, stderr
    assert stdout == "entered\n"
    assert (tmp_path / ".sync.lock").is_file()
    assert not (tmp_path / ".sync.lock.d").exists()


def test_file_lock_is_held_by_shell_and_serializes_sync(tmp_path):
    first = start_sync(tmp_path, hold=True)
    second = None
    try:
        entered(first)
        lock = tmp_path / ".sync.lock"
        inode = lock.stat().st_ino
        with lock.open("a") as probe:
            with pytest.raises(BlockingIOError):
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        second = start_sync(tmp_path)
        ready, _, _ = select.select([second.stdout], [], [], 0.2)
        assert not ready, "another Runner entered while synchronization held the lock"
        first.stdin.write("release\n")
        first.stdin.flush()
        _, stderr = first.communicate(timeout=5)
        assert first.returncode == 0, stderr
        entered(second)
        _, stderr = second.communicate(timeout=5)
        assert second.returncode == 0, stderr
        assert lock.stat().st_ino == inode, "unlock must not replace or unlink the file"
    finally:
        dispose(first)
        if second is not None:
            dispose(second)


def test_exit_releases_sync_lock_without_removing_shared_state(tmp_path):
    holder = start_sync(tmp_path, hold=True)
    successor = None
    try:
        entered(holder)
        holder.kill()
        holder.communicate(timeout=5)
        successor = start_sync(tmp_path)
        entered(successor)
        _, stderr = successor.communicate(timeout=5)
        assert successor.returncode == 0, stderr
        assert (tmp_path / ".sync.lock").is_file()
    finally:
        dispose(holder)
        if successor is not None:
            dispose(successor)
