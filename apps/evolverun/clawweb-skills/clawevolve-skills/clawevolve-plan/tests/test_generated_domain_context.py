from __future__ import annotations
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clawevolve_plan.integration.clawweb import ClawWebClient

class GeneratedDomainContextTest(unittest.TestCase):
    def test_old_payload_unchanged(self):
        client = ClawWebClient(user_id="owner")
        with patch.object(client, "_request_json", return_value={"body": {"domainId": "domain"}}) as request:
            client.create_domain("domain", "description")
        request.assert_called_once_with("POST", "/api/bench/domains", {
            "domainId": "domain", "name": "domain", "description": "description",
        })

    def test_optional_context_keeps_original_endpoint_and_owner_identity(self):
        client = ClawWebClient(user_id="owner")
        with patch.object(client, "_request_json", return_value={"body": {"domainId": "domain"}}) as request:
            client.create_domain("domain", "description", source_task_id="EV-1", source_step_id="STEP-1")
        body = request.call_args.args[2]
        self.assertEqual(request.call_args.args[:2], ("POST", "/api/bench/domains"))
        self.assertEqual(body["sourceTaskId"], "EV-1")
        self.assertEqual(body["sourceStepId"], "STEP-1")
        self.assertNotIn("space_id", body)
        self.assertEqual(client._headers()["X-User-Id"], "owner")

    def test_pipeline_forwards_context_without_changing_the_packages(self):
        from argparse import Namespace
        from clawevolve_plan.pipeline.bench_flow import _ensure_clawweb_domains
        manifest = {"split_packages": {}}
        with patch("clawevolve_plan.pipeline.bench_flow.upload_bench_domains", return_value={}) as upload:
            _ensure_clawweb_domains(
                Namespace(skip_clawweb_report=False, overwrite=True, step_id="STEP-1"),
                {}, Path("unused"), manifest, task_id="EV-1",
            )
        upload.assert_called_once_with(plan={}, manifest=manifest, task_id="EV-1", step_id="STEP-1")

    def test_partial_context_is_not_sent(self):
        client = ClawWebClient(user_id="owner")
        with patch.object(client, "_request_json") as request, self.assertRaises(ValueError):
            client.create_domain("domain", "description", source_task_id="EV-1")
        request.assert_not_called()

if __name__ == "__main__":
    unittest.main()
