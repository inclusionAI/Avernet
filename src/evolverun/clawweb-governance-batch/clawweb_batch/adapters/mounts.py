"""Reuse the realtime runner's trusted deployment NFS mounts, always read-only."""
from __future__ import annotations

import subprocess
from pathlib import Path


def prepare_readonly_mounts(nas: dict, expected_roots: tuple[Path, ...]) -> None:
    mounts = nas.get("mounts")
    if not isinstance(mounts, list) or not mounts:
        raise ValueError("--prepare-nas requires existing deployment nas.mounts")
    for item in mounts:
        target = Path(item["target"])
        source = item.get("source")
        if not target.is_absolute() or ".." in target.parts or not any(p.is_relative_to(target) for p in expected_roots):
            raise ValueError("NAS target must contain an explicitly configured evidence root")
        if not isinstance(source, str) or ":/" not in source or "\n" in source:
            raise ValueError("invalid deployment NFS source")
        if subprocess.run(["mountpoint", "-q", str(target)], check=False, timeout=10).returncode:
            target.mkdir(parents=True, exist_ok=True)
            subprocess.run(["mount", "-t", "nfs", "-o", "ro,vers=3,nolock,noatime,proto=tcp,noresvport",
                            "--", source, str(target)], check=True, timeout=180, capture_output=True)
        result = subprocess.run(["findmnt", "-n", "-o", "OPTIONS", "--target", str(target)],
                                check=True, capture_output=True, text=True, timeout=10)
        if "ro" not in result.stdout.strip().split(","):
            raise ValueError("refusing writable NAS mount")
    if any(not p.is_dir() for p in expected_roots):
        raise ValueError("configured NAS evidence root unavailable after preparation")


def verify_readonly_roots(expected_roots: tuple[Path, ...]) -> None:
    """Verify every configured evidence root is mounted read-only before reads."""
    if not expected_roots:
        raise ValueError("at least one NAS evidence root is required")
    for root in expected_roots:
        if not root.is_absolute() or ".." in root.parts:
            raise ValueError("invalid configured NAS evidence root")
        if not root.is_dir():
            raise ValueError("configured NAS evidence root unavailable")
        result = subprocess.run(["findmnt", "-n", "-o", "OPTIONS", "--target", str(root)],
                                check=True, capture_output=True, text=True, timeout=10)
        if "ro" not in result.stdout.strip().split(","):
            raise ValueError("refusing evidence root that is not mounted read-only")
