import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1] / 'scripts' / 'handlers'
sys.path.insert(0, str(ROOT))
from bench_resource_owner import bench_owner, frozen_templates


class BenchResourceOwnerTests(unittest.TestCase):
    def test_legacy_inputs_keep_bot_owner(self):
        args = types.SimpleNamespace(owner_id='runtime')
        self.assertEqual(bench_owner(args, 'train'), 'runtime')
        self.assertEqual(bench_owner(args, 'test'), 'runtime')
        self.assertEqual(frozen_templates({'same': [1]}, 'runtime', 'same'), [1])

    def test_train_test_authors_do_not_change_runtime_identity(self):
        args = types.SimpleNamespace(owner_id='runtime', train_bench_owner_id='alice', test_bench_owner_id='bob')
        self.assertEqual((bench_owner(args, 'train'), bench_owner(args, 'test')), ('alice', 'bob'))
        self.assertEqual(args.owner_id, 'runtime')

    def test_same_domain_names_from_two_authors_do_not_collide(self):
        pinned = {'["alice","same"]': [1], '["bob","same"]': [2]}
        self.assertEqual(frozen_templates(pinned, 'alice', 'same', scoped=True), [1])
        self.assertEqual(frozen_templates(pinned, 'bob', 'same', scoped=True), [2])
        self.assertIsNone(frozen_templates(pinned, 'other', 'same', scoped=True))

    def test_real_bench_plan_loads_role_owners_and_frozen_context(self):
        spec = importlib.util.spec_from_file_location('bench_plan_space_test', ROOT / 'clawevolve_bench_plan_run.py')
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        args = types.SimpleNamespace(task_id='T', step_id='S', clawweb_url='http://127.0.0.1:5173', owner_id='runtime', train_domain_id='same', test_domain_id='same')
        config = {'entryPoint': 'teamclaw', 'objective': 'goal', 'ownerUserId': 'runtime', 'trainBenchDomainId': 'same', 'testBenchDomainId': 'same',
                  'trainBenchOwnerUserId': 'alice', 'testBenchOwnerUserId': 'bob', 'pinnedBenchDomains': {'["alice","same"]': [{'templateName':'a','templateVersion':1}], '["bob","same"]': [{'templateName':'b','templateVersion':2}]}}
        with tempfile.TemporaryDirectory() as folder, patch.object(mod, '_http_json', return_value={'task': {'config': config}}):
            path = Path(folder)
            mod._load_frozen_input(args, path)
            train = json.loads((path / 'bench/context/train/bench_context.json').read_text())
            test = json.loads((path / 'bench/context/test/bench_context.json').read_text())
            self.assertEqual((train['ownerId'], test['ownerId']), ('alice', 'bob'))
            self.assertEqual((train['templates'][0]['templateName'], test['templates'][0]['templateName']), ('a', 'b'))
            self.assertEqual(args.owner_id, 'runtime')

if __name__ == '__main__':
    unittest.main()
