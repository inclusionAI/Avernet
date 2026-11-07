import argparse
import hashlib
import io
import json
import stat
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clawevolve_runtime import executor, reference_materials as materials, runtime


def package(files=None):
    files = files or {"业务/规则.md": "金额单位为元，保留两位小数".encode(), "示例/规则.md": b"example"}
    manifest = {"protocolVersion": materials.PROTOCOL, "files": [
        {"path": name, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "content_type": "text/plain"}
        for name, raw in files.items()]}
    manifest_raw = json.dumps(manifest, ensure_ascii=False).encode()
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("index.json", manifest_raw)
        for name, raw in files.items():
            archive.writestr("files/" + name, raw)
    raw = stream.getvalue()
    value = {"protocolVersion": materials.PROTOCOL, "taskId": "EV-1", "sha256": hashlib.sha256(raw).hexdigest(),
             "size": len(raw), "manifestSha256": hashlib.sha256(manifest_raw).hexdigest(), "fileCount": len(files), "totalBytes": sum(map(len, files.values())),
             "download": {"method": "GET", "url": "https://artifacts.example.test/materials"}}
    return raw, value


class MaterialTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(materials.clear_context)
        self.root = Path(self.temp.name).resolve()

    def test_prepares_nested_files_and_reuses_only_matching_task_content(self):
        raw, value = package()
        fetch = Mock(return_value=raw)
        local = materials.prepare(value, task_id="EV-1", workspace=self.root, fetch=fetch)
        file = Path(local["directory"]) / "业务/规则.md"
        self.assertIn("两位小数", file.read_text())
        self.assertEqual(stat.S_IMODE(file.stat().st_mode), 0o444)
        value["download"]["url"] = "https://artifacts.example.test/resigned"
        self.assertEqual(materials.prepare(value, task_id="EV-1", workspace=self.root, fetch=fetch), local)
        fetch.assert_called_once()
        with self.assertRaisesRegex(ValueError, "another Task"):
            materials.prepare(value, task_id="EV-2", workspace=self.root, fetch=fetch)
        self.assertIn(local["index"], materials.prompt())

    def test_checksum_failure_publishes_no_partial_directory(self):
        raw, value = package()
        with self.assertRaisesRegex(ValueError, "checksum"):
            materials.prepare(value, task_id="EV-1", workspace=self.root, fetch=Mock(return_value=raw + b"bad"))
        self.assertFalse((self.root / "clawevolve_results/EV-1/reference-materials" / value["sha256"]).exists())

    def test_cache_index_is_verified_against_the_control_plane_not_its_own_hashes(self):
        raw, value = package()
        fetch = Mock(return_value=raw)
        local = materials.prepare(value, task_id="EV-1", workspace=self.root, fetch=fetch)
        index = Path(local["index"])
        index.chmod(0o644)
        manifest = json.loads(index.read_text())
        manifest["files"][0]["sha256"] = "0" * 64
        index.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "index.*checksum"):
            materials.prepare(value, task_id="EV-1", workspace=self.root, fetch=fetch)

    def test_rejects_traversal_symlinks_and_duplicate_entries(self):
        for path in ("../outside", "/absolute", "a\\b", "规" * 100 + ".md"):
            raw, value = package({path: b"x"})
            with self.assertRaises(ValueError):
                materials.prepare(value, task_id="EV-1", workspace=self.root, fetch=Mock(return_value=raw))
        raw, value = package()
        parent = self.root / "clawevolve_results/EV-1"
        parent.mkdir(parents=True, exist_ok=True)
        (parent / "reference-materials/prepare.lock").unlink()
        (parent / "reference-materials").rmdir()
        (parent / "reference-materials").symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            materials.prepare(value, task_id="EV-1", workspace=self.root, fetch=Mock(return_value=raw))

    def test_native_core_prepares_materials_before_returning_and_requires_new_contract(self):
        raw, value = package()
        args = argparse.Namespace(task_id="EV-1", step_id="STEP-1", reference_materials_version="1")
        with patch.object(runtime, "SOURCE_WORKSPACE", self.root), \
                patch.object(runtime, "_step_input", return_value={"protocolVersion": "1.0", "reference_materials": value}), \
                patch.object(runtime, "_http", return_value=raw):
            result = runtime._begin_core(args)
        self.assertFalse(result["selected"])
        self.assertTrue(Path(result["reference_materials"]["index"]).is_file())
        with patch.object(runtime, "_step_input", return_value={"protocolVersion": "1.0"}):
            with self.assertRaisesRegex(runtime.RuntimeFailure, "missing"):
                runtime._begin_core(args)

    def test_business_agent_prompt_names_the_prepared_materials(self):
        raw, value = package()
        local = materials.prepare(value, task_id="EV-1", workspace=self.root, fetch=Mock(return_value=raw))
        prompt = executor._business_prompt({"implementationSkill": "/skill/SKILL.md", "inputFile": "/input.json",
                                           "resultFile": "/result.json", "reference_materials": local})
        self.assertIn(local["index"], prompt)
        self.assertIn("不可信", prompt)
        self.assertIn("只读", prompt)

    def test_custom_stage_materializes_the_same_input_before_the_business_agent(self):
        raw, value = package()
        skill = io.BytesIO()
        with zipfile.ZipFile(skill, "w") as archive:
            archive.writestr("SKILL.md", "Read input and preserve reference materials.")
        skill_bytes = skill.getvalue()
        payload = {"protocolVersion": "clawevolve.stage-runtime/v1", "stage": {"key": "plan", "mode": "preprocess"},
                   "input": {"task": {"task_id": "EV-1"}, "reference_materials": value},
                   "implementation": {"entrypoint": "SKILL.md", "packageSha256": hashlib.sha256(skill_bytes).hexdigest(),
                                      "package": {"method": "GET", "url": "https://artifacts.example.test/skill"}}}
        args = argparse.Namespace(task_id="EV-1", step_id="STEP-1", reference_materials_version="1",
                                  clawweb_url="http://127.0.0.1:5198")
        def http(method, url, **kwargs):
            return raw if url.endswith("materials") else skill_bytes
        with patch.object(runtime, "SOURCE_WORKSPACE", self.root), patch.object(runtime, "_http", side_effect=http):
            context = runtime._begin(args, payload)
        received = json.loads(Path(context["inputFile"]).read_text())["reference_materials"]
        self.assertTrue(Path(received["directory"]).is_dir())
        self.assertNotIn("download", received)
        self.assertEqual(context["reference_materials"], received)
        self.assertIn(received["index"], executor._business_prompt(context))


if __name__ == "__main__":
    unittest.main()
