from __future__ import annotations

import argparse
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clawevolve_plan.pipeline.bench_flow import _ensure_clawweb_domains
from clawevolve_plan.pipeline.runner import _handle_existing_plan
from clawevolve_plan.pipeline.upload import upload_bench_domains
from test_dual_domain_upload import _write_split_zip


class UploadOwnerTests(unittest.TestCase):
    def test_explicit_owner_overrides_bot_runtime_for_both_domains(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            packages = {}
            for split in ('train', 'test'):
                path = root / f'{split}.zip'
                packages[split] = {
                    'zip_path': str(path),
                    'zip_sha256': _write_split_zip(path, split, [f'task_{split}']),
                    'template_names': [f'task_{split}'],
                }
            for owner in ('task-operator', ''):
                with self.subTest(owner=owner), patch.dict(
                    os.environ, {'CLAWBENCH_OWNER_ID': 'bot-owner'}
                ), patch('clawevolve_plan.pipeline.upload.upload_templates', return_value={}) as upload:
                    result = upload_bench_domains(
                        plan={'bot_id': 'bot'}, manifest={'split_packages': packages},
                        task_id='EV-1', owner_id=owner,
                    )
                    self.assertEqual(upload.call_count, 2)
                    for call in upload.call_args_list:
                        self.assertEqual(call.args[2], owner or 'bot-owner')
                    self.assertEqual(result['domains']['train']['user_meta']['source'],
                                     'argument' if owner else 'env:CLAWBENCH_OWNER_ID')

    def test_fresh_upload_receives_owner(self):
        args = argparse.Namespace(overwrite=True, owner_id='task-operator', step_id='STEP-1')
        with tempfile.TemporaryDirectory() as td, patch(
            'clawevolve_plan.pipeline.bench_flow.upload_bench_domains', return_value={}
        ) as upload:
            _ensure_clawweb_domains(args, {}, Path(td), {}, task_id='EV-1')
        self.assertEqual(upload.call_args.kwargs['owner_id'], 'task-operator')

    def test_retry_upload_receives_owner(self):
        class UploadReached(Exception):
            pass
        args = argparse.Namespace(owner_id='task-operator')
        with tempfile.TemporaryDirectory() as td, patch(
            'clawevolve_plan.pipeline.runner._existing_plan_result',
            return_value={'clawweb_upload_result': {'status': 'failed'}},
        ), patch('clawevolve_plan.pipeline.runner.load_json', return_value={}), patch(
            'clawevolve_plan.pipeline.runner.ensure_split_packages', return_value={}
        ), patch('clawevolve_plan.pipeline.runner.upload_bench_domains', side_effect=UploadReached) as upload:
            with self.assertRaises(UploadReached):
                _handle_existing_plan(args=args, task_id='EV-1', step_id='STEP-RETRY',
                                      output_dir=Path(td), step_reporter=lambda *a, **k: {},
                                      invocation_identity={})
        self.assertEqual(upload.call_args.kwargs['owner_id'], 'task-operator')
        self.assertEqual(upload.call_args.kwargs['step_id'], 'STEP-RETRY')
