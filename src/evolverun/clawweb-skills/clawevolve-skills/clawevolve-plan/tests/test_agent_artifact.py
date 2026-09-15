from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


PLAN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLAN_ROOT))

from clawevolve_plan.agent_artifact import (  # noqa: E402
    StructuredArtifactError,
    materialize_structured_artifact,
    parse_json_artifact,
    quarantine_stale_artifact,
)


class StructuredAgentArtifactTests(unittest.TestCase):
    def _materialize(
        self,
        root: Path,
        *,
        candidate_text: str | None,
        response_text: str = "",
        validator=lambda payload: payload,
        canonicalizer=lambda payload: payload,
        attempt: int = 1,
    ):
        candidate = root / "artifact.candidate.json"
        final = root / "artifact.json"
        if candidate_text is not None:
            candidate.write_text(candidate_text, encoding="utf-8")
        result = materialize_structured_artifact(
            label="test artifact",
            candidate_path=candidate,
            final_path=final,
            response_text=response_text,
            validator=validator,
            canonicalizer=canonicalizer,
            attempt=attempt,
        )
        return result, candidate, final

    def test_valid_candidate_is_canonicalized_and_published(self):
        with tempfile.TemporaryDirectory(prefix="plan-artifact-valid-") as td:
            root = Path(td)
            result, _, final = self._materialize(
                root,
                candidate_text='{"value": 1}',
                canonicalizer=lambda payload: {"value": payload["value"] + 1},
            )

            self.assertEqual(result.source, "candidate_file")
            self.assertEqual(
                json.loads(final.read_text(encoding="utf-8")), {"value": 2}
            )

    def test_bom_and_complete_json_fence_are_supported(self):
        self.assertEqual(parse_json_artifact('\ufeff{"value": 1}'), {"value": 1})
        self.assertEqual(
            parse_json_artifact('```json\n{"value": 2}\n```'), {"value": 2}
        )

    def test_invalid_candidate_can_recover_from_response_and_is_archived(self):
        with tempfile.TemporaryDirectory(prefix="plan-artifact-response-") as td:
            root = Path(td)
            result, candidate, final = self._materialize(
                root,
                candidate_text='{"value": }',
                response_text='Agent result:\n```json\n{"value": 3}\n```',
            )

            self.assertEqual(result.source, "agent_response")
            self.assertEqual(
                json.loads(final.read_text(encoding="utf-8")), {"value": 3}
            )
            self.assertTrue(candidate.exists())
            archives = list(root.glob("artifact.invalid.attempt-1.json"))
            self.assertEqual(len(archives), 1)
            self.assertTrue(
                any("recovered from agent response" in item for item in result.warnings)
            )

    def test_invalid_response_is_seeded_as_candidate_for_bounded_correction(self):
        with tempfile.TemporaryDirectory(prefix="plan-artifact-seed-") as td:
            root = Path(td)
            with self.assertRaises(StructuredArtifactError) as raised:
                self._materialize(
                    root,
                    candidate_text=None,
                    response_text="result: {invalid",
                )

            candidate = root / "artifact.candidate.json"
            self.assertEqual(raised.exception.repair_source_path, candidate)
            self.assertEqual(
                candidate.read_text(encoding="utf-8"), "result: {invalid\n"
            )

    def test_missing_candidate_and_response_has_no_repair_source(self):
        with tempfile.TemporaryDirectory(prefix="plan-artifact-empty-") as td:
            root = Path(td)
            with self.assertRaises(StructuredArtifactError) as raised:
                self._materialize(root, candidate_text=None, response_text="")

            self.assertIsNone(raised.exception.repair_source_path)

    def test_invalid_sources_raise_precise_error_and_archive_both(self):
        with tempfile.TemporaryDirectory(prefix="plan-artifact-invalid-") as td:
            root = Path(td)
            with self.assertRaises(StructuredArtifactError) as raised:
                self._materialize(
                    root,
                    candidate_text='{"value": 1 "broken": true}',
                    response_text="not json",
                )

            message = str(raised.exception)
            self.assertIn("line 1 column", message)
            self.assertIn("context=", message)
            self.assertIn("pointer=", message)
            self.assertEqual(len(raised.exception.archive_paths), 2)
            self.assertTrue(
                all(path.is_file() for path in raised.exception.archive_paths)
            )

    def test_validator_rejection_never_publishes_final_artifact(self):
        with tempfile.TemporaryDirectory(prefix="plan-artifact-schema-") as td:
            root = Path(td)

            def reject(_payload):
                raise ValueError("schema mismatch")

            with self.assertRaisesRegex(StructuredArtifactError, "schema mismatch"):
                self._materialize(
                    root,
                    candidate_text='{"value": 1}',
                    validator=reject,
                )
            self.assertFalse((root / "artifact.json").exists())

    def test_embedded_json_rejects_nonempty_trailing_garbage(self):
        with self.assertRaises((ValueError, json.JSONDecodeError)):
            parse_json_artifact(
                'prefix {"value": 1} trailing garbage', allow_embedded=True
            )

    def test_embedded_parser_can_skip_earlier_non_json_brace(self):
        self.assertEqual(
            parse_json_artifact(
                'status {not-json; final={"value": 4}', allow_embedded=True
            ),
            {"value": 4},
        )

    def test_stale_artifact_is_moved_without_overwriting_existing_archive(self):
        with tempfile.TemporaryDirectory(prefix="plan-artifact-stale-") as td:
            root = Path(td)
            artifact = root / "artifact.json"
            artifact.write_text("bad-1", encoding="utf-8")
            first = quarantine_stale_artifact(artifact, reason="stale")
            artifact.write_text("bad-2", encoding="utf-8")
            second = quarantine_stale_artifact(artifact, reason="stale")

            self.assertIsNotNone(first)
            self.assertIsNotNone(second)
            self.assertNotEqual(first, second)
            self.assertFalse(artifact.exists())
            self.assertEqual(first.read_text(encoding="utf-8"), "bad-1")
            self.assertEqual(second.read_text(encoding="utf-8"), "bad-2")


if __name__ == "__main__":
    unittest.main()
