import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from configure import configure
from smoke import main


class BailianConfigTest(unittest.TestCase):
    def test_protocols_and_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "profiles"
            with patch.dict("os.environ", {"BAILIAN_API_KEY": "test-only-sensitive-value"}):
                configure(root, "https://workspace.example/compatible-mode/v1", "qwen3.8-flash")
            files = [path.read_text() for path in root.rglob("*") if path.is_file()]
            self.assertNotIn("test-only-sensitive-value", "".join(files))
            claude = json.loads((root / "claude/settings.json").read_text())["env"]
            self.assertEqual(claude["ANTHROPIC_BASE_URL"], "https://workspace.example/apps/anthropic")
            self.assertIn('wire_api = "responses"', (root / "codex/config.toml").read_text())
            dsh = json.loads((root / "dsh/profiles/headless/cordis.patch.yml").read_text())
            self.assertEqual(dsh[1]["config"], {"provider": "bailian", "model": "qwen3.8-flash"})
            with self.assertRaises(FileExistsError):
                configure(root, "https://workspace.example/compatible-mode/v1", "qwen3.8-flash")

    def test_rejects_credentials_and_malformed_endpoints(self):
        for url in ("http://host/compatible-mode/v1", "https://user:pass@host/compatible-mode/v1",
                    "https://host/compatible-mode/v1?key=value", "https://host/v1/responses"):
            with tempfile.TemporaryDirectory() as directory, self.assertRaises(ValueError):
                configure(Path(directory) / "profiles", url, "qwen3.8-flash")

    def test_local_execution_is_blocked_before_starting_engine(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "result.json"
            argv = ["smoke.py", "--engine", "claude-code", "--profile", directory,
                    "--report", str(report)]
            with patch.object(sys, "argv", argv), patch.dict("os.environ", {}, clear=True), \
                    patch("smoke.subprocess.Popen") as process:
                self.assertEqual(main(), 1)
            process.assert_not_called()
            self.assertEqual(json.loads(report.read_text())["status"], "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
