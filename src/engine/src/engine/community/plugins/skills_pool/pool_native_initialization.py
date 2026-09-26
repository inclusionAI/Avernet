"""Initialize or repair the OpenClaw Pool steady-state root contract."""

from __future__ import annotations

import argparse
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from engine.community.core.skills.layout_planner import (
    LAYOUT_CONTRACT_VERSION,
    LayoutIdentity,
    RuntimeLayoutContext,
    resolve_filesystem_skill_layout,
)
from engine.community.plugins.skills_pool.active_marker_validation import (
    startup_active_marker_valid,
)


class PoolNativeInitializationError(RuntimeError):
    """The trusted Pool startup declaration conflicts with filesystem facts."""


@dataclass(frozen=True, slots=True)
class PoolNativeInitializationEvidence:
    actual_engine: str
    actual_layout: str
    layout_contract_version: str
    roots_initialized: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "actual_engine": self.actual_engine,
            "actual_layout": self.actual_layout,
            "layout_contract_version": self.layout_contract_version,
            "roots_initialized": self.roots_initialized,
        }


def _require_directory(path: Path, *, create: bool) -> None:
    try:
        path_stat = path.lstat()
    except FileNotFoundError:
        if not create:
            raise PoolNativeInitializationError(f"required root is absent: {path}")
        try:
            path.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            # Another startup may have created this root after our lstat().
            # Re-read the winner instead of treating a valid directory as a
            # failed initialization.
            pass
        try:
            path_stat = path.lstat()
        except FileNotFoundError as error:
            raise PoolNativeInitializationError(
                f"required root disappeared during initialization: {path}"
            ) from error
    if stat.S_ISLNK(path_stat.st_mode) or not stat.S_ISDIR(path_stat.st_mode):
        raise PoolNativeInitializationError(f"required root is not a directory: {path}")


def _read_active_marker(path: Path) -> dict[str, object] | None:
    try:
        marker_stat = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise PoolNativeInitializationError("Pool active marker is unreadable") from error
    if not stat.S_ISREG(marker_stat.st_mode):
        raise PoolNativeInitializationError("Pool active marker is not a regular file")
    try:
        marker = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PoolNativeInitializationError("Pool active marker is invalid") from error
    if not isinstance(marker, dict):
        raise PoolNativeInitializationError("Pool active marker is invalid")
    return marker


def _validate_active_marker(marker: dict[str, object], *, engine: str) -> None:
    if not startup_active_marker_valid(
        marker,
        engine=engine,
        expected_contract_version=LAYOUT_CONTRACT_VERSION,
    ):
        raise PoolNativeInitializationError("Pool active marker conflicts with startup")


def _path_present(path: Path, *, description: str) -> bool:
    try:
        path.lstat()
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError as error:
        raise PoolNativeInitializationError(
            f"{description} could not be inspected: {path}"
        ) from error
    return True


def _atomic_create_active_marker(path: Path, marker: dict[str, str]) -> bool:
    payload = json.dumps(
        marker,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    temporary = path.parent / f".pool-active.tmp-{uuid4()}"
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            return False
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return True
    finally:
        temporary.unlink(missing_ok=True)


def initialize_pool_native(
    *,
    engine: str,
    home: Path = Path("/home/admin"),
    steady_state: bool = False,
) -> PoolNativeInitializationEvidence:
    """Establish the minimal Pool roots without Legacy preparation or copying.

    This capability is intentionally OpenClaw-only.  Migration startup keeps
    using its preparation/finalizing contract, and other engines retain their
    current rollout boundaries. ``steady_state`` must come from the trusted
    persisted layout state; filesystem evidence alone must not infer it.  It
    allows an already-completed Pool restart to repair a missing active marker
    while preserving historical migration preparation evidence.
    """

    if engine != "openclaw":
        raise PoolNativeInitializationError(
            f"Pool-native initialization is unsupported for engine={engine}"
        )
    layout = resolve_filesystem_skill_layout(
        LayoutIdentity(engine, LAYOUT_CONTRACT_VERSION),
        RuntimeLayoutContext(home=home),
    )
    marker = _read_active_marker(layout.active_marker)
    if marker is not None:
        _validate_active_marker(marker, engine=engine)
    elif not steady_state and _path_present(
        layout.ready_marker,
        description="Pool migration preparation marker",
    ):
        raise PoolNativeInitializationError(
            "Pool migration preparation requires recovery before native startup"
        )

    for legacy_entry in (layout.legacy_local, layout.legacy_repo):
        if _path_present(legacy_entry, description="Legacy entry"):
            raise PoolNativeInitializationError(
                f"legacy entry conflicts with Pool-native startup: {legacy_entry}"
            )

    create_roots = marker is None and not steady_state
    _require_directory(layout.active_root, create=create_roots)
    _require_directory(layout.pool_root, create=create_roots)
    _require_directory(layout.pool_local, create=create_roots)

    if marker is None:
        created = _atomic_create_active_marker(
            layout.active_marker,
            {
                "engine": engine,
                "layout_contract_version": LAYOUT_CONTRACT_VERSION,
                "activation_state": "active",
            },
        )
        if not created:
            concurrent_marker = _read_active_marker(layout.active_marker)
            if concurrent_marker is None:
                raise PoolNativeInitializationError(
                    "Pool active marker disappeared during initialization"
                )
            _validate_active_marker(concurrent_marker, engine=engine)

    return PoolNativeInitializationEvidence(
        actual_engine=engine,
        actual_layout="pool",
        layout_contract_version=LAYOUT_CONTRACT_VERSION,
        roots_initialized=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Initialize the OpenClaw Pool steady-state roots."
    )
    parser.add_argument("--engine", required=True)
    parser.add_argument("--home", type=Path, default=Path("/home/admin"))
    parser.add_argument(
        "--steady-state",
        action="store_true",
        help=(
            "trusted persisted layout is already Pool-active; permit marker "
            "repair while preserving historical migration preparation"
        ),
    )
    args = parser.parse_args(argv)
    evidence = initialize_pool_native(
        engine=args.engine,
        home=args.home,
        steady_state=args.steady_state,
    )
    print(json.dumps(evidence.to_dict(), sort_keys=True, separators=(",", ":")))
    return 0


__all__ = [
    "PoolNativeInitializationError",
    "PoolNativeInitializationEvidence",
    "initialize_pool_native",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
