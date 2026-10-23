"""Local boundary and publication-policy checks for the isolated batch module."""
import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ArchitectureTests(unittest.TestCase):
    def test_policy_modules_do_not_import_external_adapters_or_read_environment(self):
        for name in ("core.py", "evidence.py", "verification.py", "pipeline.py", "contracts.py"):
            path = ROOT / "clawweb_batch" / name
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn(alias.name.split(".")[0], {"urllib", "requests", "odps", "pypai", "subprocess"}, name)
                if isinstance(node, ast.ImportFrom):
                    self.assertNotIn("adapters", (node.module or "").split("."), name)
                if isinstance(node, ast.Attribute):
                    self.assertNotIn(node.attr, {"environ", "getenv"}, name)

    def test_every_source_file_is_below_repository_limit(self):
        for path in ROOT.rglob("*.py"):
            self.assertLessEqual(len(path.read_text().splitlines()), 1000, str(path))

    def test_public_runner_adapter_exists_and_uses_standard_contract(self):
        script = ROOT / "scripts" / "run.sh"
        text = script.read_text()
        self.assertIn("--task-file", (ROOT / "scripts" / "run.py").read_text())
        self.assertTrue(script.stat().st_mode & 0o100)

    def test_public_code_has_no_private_service_endpoint(self):
        for path in ROOT.rglob("*.py"):
            text = path.read_text()
            if path == Path(__file__):
                continue
            for hostname in ("alipay.com", "antfin.com", "aliyuncs.com"):
                self.assertNotIn(hostname, text, str(path))


if __name__ == "__main__":
    unittest.main()
