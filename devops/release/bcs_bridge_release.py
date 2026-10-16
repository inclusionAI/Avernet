"""Release gates for the bridge-only macOS preview distribution."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tarfile
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGETS = ("aarch64-apple-darwin", "x86_64-apple-darwin")
REPOSITORY = "https://github.com/inclusionAI/Avernet"
CHECKSUM_BOOTSTRAP = """# macOS ships shasum; cargo-dist's verifier expects sha256sum.
if ! command -v sha256sum >/dev/null 2>&1; then
    command -v shasum >/dev/null 2>&1 || { echo 'SHA-256 verification requires shasum' >&2; exit 1; }
    sha256sum() { shasum -a 256 "$@"; }
fi
"""


def metadata(tag: str = "") -> dict[str, str]:
    cargo = tomllib.loads((ROOT / "apps/bcs/Cargo.toml").read_text())
    version = cargo["workspace"]["package"]["version"]
    expected = f"bcs-bridge-v{version}"
    if tag and tag != expected:
        raise ValueError(f"Expected tag {expected}, got {tag}")
    return {"version": version, "tag": expected, **release_channel(version)}


def release_channel(version: str) -> dict[str, str]:
    if not re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-(?:beta|rc)\.[1-9][0-9]*)?", version):
        raise ValueError("Release version must be X.Y.Z, X.Y.Z-beta.N or X.Y.Z-rc.N")
    prerelease = "-" in version
    return {"channel": "prerelease" if prerelease else "stable",
            "prerelease": "true" if prerelease else "false"}


def verify_source(version: str) -> None:
    # Stable releases must have merged. Preview builds may originate from the
    # dedicated reviewed release branch without publishing unrelated branches.
    refs = ["refs/remotes/origin/dev"]
    if release_channel(version)["prerelease"] == "true":
        refs.append("refs/remotes/origin/codex/bcs-bridge-release")
    for ref in refs:
        result = subprocess.run(["git", "merge-base", "--is-ancestor", "HEAD", ref],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if result.returncode == 0:
            return
    raise ValueError("Release commit must belong to dev (or the dedicated preview release branch)")


def archive_name(target: str) -> str:
    if target not in TARGETS:
        raise ValueError(f"Unsupported bridge target {target}")
    return f"bcs-bridge-{target}.tar.gz"


def verify_plan(plan: dict, tag: str, version: str) -> None:
    if plan.get("announcement_tag") != tag:
        raise ValueError("dist selected a different tag")
    releases = plan.get("releases", [])
    if len(releases) != 1 or releases[0]["app_name"] != "bcs-bridge" or releases[0]["app_version"] != version:
        raise ValueError("Release must contain only the selected bridge version")
    expected = {"bcs-bridge-installer.sh", "sha256.sum"}
    for target in TARGETS:
        name = archive_name(target)
        expected.update((name, name + ".sha256"))
        info = plan["artifacts"][name]
        binaries = [a["name"] for a in info["assets"] if a["kind"] == "executable"]
        if binaries != ["bcs-bridge"] or info["target_triples"] != [target]:
            raise ValueError("Unexpected binary or platform in archive")
        if {a["name"] for a in info["assets"]} != {"bcs-bridge", "LICENSE", "QUICKSTART.md"}:
            raise ValueError("Archive includes unexpected files or lacks release documentation")
    if set(plan["artifacts"]) != expected or set(releases[0]["artifacts"]) != expected:
        raise ValueError("Unexpected release artifacts")
    hosting = releases[0]["hosting"]["github"]
    if (hosting["owner"], hosting["repo"]) != ("inclusionAI", "Avernet"):
        raise ValueError("Release points to the wrong repository")


def verify_checksum(archive: Path) -> str:
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    expected = archive.with_name(archive.name + ".sha256").read_text().split()[0]
    if not re.fullmatch(r"[0-9a-f]{64}", expected) or expected != digest:
        raise ValueError(f"Checksum mismatch: {archive.name}")
    return digest


def prepare_installer(directory: Path, tag: str, targets: list[str]) -> None:
    path = directory / "bcs-bridge-installer.sh"
    script = path.read_text()
    if f"{REPOSITORY}/releases/download/{tag}" not in script:
        raise ValueError("Installer must pin the selected release URL")
    for target in targets:
        digest = verify_checksum(directory / archive_name(target))
        if script.count(f'_checksum_value="{digest}"') != 1:
            raise ValueError("Installer is missing an embedded archive checksum")
    if CHECKSUM_BOOTSTRAP not in script:
        script = "#!/bin/sh\n" + CHECKSUM_BOOTSTRAP + script
        path.write_text(script)
    subprocess.run(["sh", "-n", str(path)], check=True)
    # The generated checksum list predates installer hardening. Recompute the
    # public list for exactly the downloadable files, excluding manifest scratch.
    names = ["bcs-bridge-installer.sh"] + [archive_name(t) for t in targets]
    lines = [f"{hashlib.sha256((directory / n).read_bytes()).hexdigest()}  {n}\n" for n in sorted(names)]
    (directory / "sha256.sum").write_text("".join(lines))


def verify_linkage(output: str) -> None:
    libraries = [line.strip().split(" (", 1)[0] for line in output.splitlines()[1:] if line.strip()]
    if not libraries or any(not p.startswith(("/usr/lib/", "/System/Library/")) for p in libraries):
        raise ValueError(f"Binary depends on non-system libraries: {libraries}")


def verify_native(directory: Path, target: str, version: str) -> None:
    archive = directory / archive_name(target)
    verify_checksum(archive)
    with tempfile.TemporaryDirectory(prefix="bcs-bridge-archive-") as temp:
        root = Path(temp)
        with tarfile.open(archive) as tar:
            members = tar.getmembers()
            if any(m.issym() or m.islnk() or (not m.isfile() and not m.isdir()) for m in members):
                raise ValueError("Unexpected links or special files in release archive")
            prefix = archive_name(target).removesuffix(".tar.gz")
            expected = {f"{prefix}/{name}" for name in ("bcs-bridge", "LICENSE", "QUICKSTART.md")}
            if {m.name for m in members if m.isfile()} != expected:
                raise ValueError("Archive must contain only the executable, license and quickstart")
            tar.extractall(root, filter="data")
        binaries = list(root.rglob("bcs-bridge"))
        if len(binaries) != 1 or not binaries[0].is_file():
            raise ValueError("Archive must contain one bridge binary")
        binary = binaries[0]
        arch = "arm64" if target.startswith("aarch64") else "x86_64"
        if subprocess.check_output(["lipo", "-archs", str(binary)], text=True).strip() != arch:
            raise ValueError("Archive architecture mismatch")
        verify_linkage(subprocess.check_output(["otool", "-L", str(binary)], text=True))
        actual = subprocess.check_output([str(binary), "--version"], text=True).strip()
        if actual != f"bcs-bridge {version}":
            raise ValueError(f"Binary version mismatch: {actual}")
        subprocess.run([str(binary), "--help"], check=True, stdout=subprocess.DEVNULL)
        subprocess.run([str(binary), "init", "--help"], check=True, stdout=subprocess.DEVNULL)
        if not list(root.rglob("LICENSE")) or not list(root.rglob("QUICKSTART.md")):
            raise ValueError("Missing release documentation")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["metadata", "source", "plan", "prepare", "native"])
    parser.add_argument("--tag", default="")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--directory", type=Path, default=ROOT / "target/distrib")
    parser.add_argument("--targets", nargs="+", choices=TARGETS, default=list(TARGETS))
    args = parser.parse_args()
    info = metadata(args.tag)
    if args.command == "metadata":
        print(json.dumps(info))
    elif args.command == "source":
        verify_source(info["version"])
    elif args.command == "plan":
        if args.manifest is None:
            parser.error("plan requires --manifest")
        verify_plan(json.loads(args.manifest.read_text()), info["tag"], info["version"])
    elif args.command == "prepare":
        prepare_installer(args.directory, info["tag"], args.targets)
    else:
        for target in args.targets:
            verify_native(args.directory, target, info["version"])


if __name__ == "__main__":
    main()
