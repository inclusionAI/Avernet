import importlib.util
import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "collect_clawevolve_task_logs.py"
SPEC = importlib.util.spec_from_file_location("collect_clawevolve_task_logs", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class CollectTaskLogsTest(unittest.TestCase):
    def test_archive_preserves_symlink_and_empty_directory_without_following(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "source"
            root.mkdir()
            (root / "empty").mkdir()
            (root / "result.json").write_text('{"ok":true}\n', encoding="utf-8")
            os.symlink("/tmp/outside", root / "outside-link")
            output = Path(temp) / "logs.tar.gz"

            metadata = MODULE.build_archive(root, output, "EV-TEST", "LOG-TEST")

            self.assertGreater(metadata["size"], 0)
            with tarfile.open(output, "r:gz") as archive:
                names = set(archive.getnames())
                self.assertIn("EV-TEST/empty", names)
                self.assertIn("EV-TEST/result.json", names)
                link = archive.getmember("EV-TEST/outside-link")
                self.assertTrue(link.issym())
                self.assertEqual(link.linkname, "/tmp/outside")
                manifest = json.load(archive.extractfile("manifest.json"))
                self.assertEqual(manifest["schemaVersion"], "clawevolve.task-log-archive.v1")
                self.assertEqual(manifest["sourceRoot"], "clawevolve_results/EV-TEST")
                self.assertEqual(manifest["symlinkPolicy"], "preserve-no-follow")

    def test_special_file_is_skipped(self):
        if not hasattr(os, "mkfifo"):
            self.skipTest("mkfifo unavailable")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "source"
            root.mkdir()
            os.mkfifo(root / "pipe")
            output = Path(temp) / "logs.tar.gz"
            metadata = MODULE.build_archive(root, output, "EV-TEST", "LOG-TEST")
            self.assertEqual(metadata["skippedCount"], 1)
            with tarfile.open(output, "r:gz") as archive:
                self.assertNotIn("EV-TEST/pipe", archive.getnames())

    def test_rejects_unsafe_identifiers(self):
        with self.assertRaises(ValueError):
            MODULE._safe_id("../outside", "task-id")


if __name__ == "__main__":
    unittest.main()
