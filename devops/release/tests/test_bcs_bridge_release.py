import copy
import hashlib
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bcs_bridge_release as release


def plan_fixture():
    info = release.metadata()
    artifacts = {"bcs-bridge-installer.sh": {}, "sha256.sum": {}}
    for target in release.TARGETS:
        name = release.archive_name(target)
        artifacts[name] = {"target_triples": [target], "assets": [
            {"name": "bcs-bridge", "kind": "executable"},
            {"name": "LICENSE", "kind": "license"},
            {"name": "QUICKSTART.md", "kind": "unknown"}]}
        artifacts[name + ".sha256"] = {}
    return {"announcement_tag": info["tag"], "artifacts": artifacts, "releases": [{
        "app_name": "bcs-bridge", "app_version": info["version"], "artifacts": list(artifacts),
        "hosting": {"github": {"owner": "inclusionAI", "repo": "Avernet"}}}]}


class ReleaseGates(unittest.TestCase):
    def test_plan_excludes_other_binaries_and_repositories(self):
        good = plan_fixture()
        info = release.metadata()
        release.verify_plan(good, info["tag"], info["version"])
        for mutate in [
            lambda p: p["releases"].append(copy.deepcopy(p["releases"][0])),
            lambda p: p["artifacts"][release.archive_name(release.TARGETS[0])]["assets"].append({"name": "bcs-cli", "kind": "executable"}),
            lambda p: p["releases"][0]["hosting"]["github"].update(repo="bcs"),
            lambda p: p["artifacts"].update({"server.tar.gz": {}}),
            lambda p: p["artifacts"][release.archive_name(release.TARGETS[0])]["assets"].append({"name": "bridge.toml", "kind": "unknown"}),
        ]:
            bad = copy.deepcopy(good)
            mutate(bad)
            with self.assertRaises(ValueError):
                release.verify_plan(bad, info["tag"], info["version"])

    def test_release_channel(self):
        self.assertEqual(release.release_channel("0.1.4")["prerelease"], "false")
        for version in ["0.1.4-beta.1", "0.1.4-beta.2", "0.1.4-rc.1"]:
            self.assertEqual(release.release_channel(version)["prerelease"], "true")
        for version in ["0.1.4-beta", "0.1.4-beta.0", "0.1.4-nightly.1", "v0.1.4", "01.1.4"]:
            with self.assertRaises(ValueError):
                release.release_channel(version)

    def test_source_gate_keeps_stable_on_dev(self):
        with patch.object(release.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)) as run:
            with self.assertRaises(ValueError):
                release.verify_source("0.1.4")
            self.assertEqual(run.call_count, 1)
        with patch.object(release.subprocess, "run", side_effect=[
            subprocess.CompletedProcess([], 1), subprocess.CompletedProcess([], 0),
        ]) as run:
            release.verify_source("0.1.4-beta.1")
            self.assertEqual(run.call_args.args[0][-1], "refs/remotes/origin/codex/bcs-bridge-release")
        with patch.object(release.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)):
            with self.assertRaises(ValueError):
                release.verify_source("0.1.4-beta.1")

    def test_tag_must_match_binary_version(self):
        with self.assertRaises(ValueError):
            release.metadata("bcs-bridge-v999.0.0")

    def test_checksum_detects_corruption(self):
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / "archive.tar.gz"
            archive.write_bytes(b"original")
            archive.with_name(archive.name + ".sha256").write_text(hashlib.sha256(b"original").hexdigest())
            release.verify_checksum(archive)
            archive.write_bytes(b"corrupted")
            with self.assertRaises(ValueError):
                release.verify_checksum(archive)

    def test_installer_requires_embedded_hashes_and_hardening_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            script = f'#!/bin/sh\n# {release.REPOSITORY}/releases/download/{release.metadata()["tag"]}\n'
            hashes = []
            for target in release.TARGETS:
                archive = directory / release.archive_name(target)
                archive.write_bytes(target.encode())
                digest = hashlib.sha256(archive.read_bytes()).hexdigest()
                archive.with_name(archive.name + ".sha256").write_text(digest)
                hashes.append(digest)
            installer = directory / "bcs-bridge-installer.sh"
            installer.write_text(script + f'_checksum_value="{hashes[0]}"\n')
            with self.assertRaises(ValueError):
                release.prepare_installer(directory, release.metadata()["tag"], list(release.TARGETS))
            installer.write_text(script + "".join(f'_checksum_value="{h}"\n' for h in hashes))
            release.prepare_installer(directory, release.metadata()["tag"], list(release.TARGETS))
            before = installer.read_text()
            release.prepare_installer(directory, release.metadata()["tag"], list(release.TARGETS))
            self.assertEqual(installer.read_text(), before)
            self.assertIn("shasum -a 256", before)
            self.assertEqual(len((directory / "sha256.sum").read_text().splitlines()), 3)

    def test_linkage_rejects_private_and_homebrew_paths(self):
        release.verify_linkage("binary:\n\t/usr/lib/libSystem.B.dylib (compatibility version 1.0)\n")
        for library in ["/opt/homebrew/lib/libssl.dylib", "@rpath/libsql.dylib", "/Users/example/lib.dylib"]:
            with self.assertRaises(ValueError):
                release.verify_linkage(f"binary:\n\t{library} (compatibility version 1.0)\n")

    def test_archive_rejects_links_before_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            archive = directory / release.archive_name(release.TARGETS[0])
            with tarfile.open(archive, "w:gz") as tar:
                link = tarfile.TarInfo("package/bcs-bridge")
                link.type = tarfile.SYMTYPE
                link.linkname = "/usr/bin/false"
                tar.addfile(link)
            archive.with_name(archive.name + ".sha256").write_text(hashlib.sha256(archive.read_bytes()).hexdigest())
            with self.assertRaises(ValueError):
                release.verify_native(directory, release.TARGETS[0], release.metadata()["version"])


if __name__ == "__main__":
    unittest.main()
