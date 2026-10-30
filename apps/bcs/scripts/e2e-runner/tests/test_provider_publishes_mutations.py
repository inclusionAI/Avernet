"""Every frozen obligation has a passing specimen and independent negative mutation."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.provider_publishes.checks import evaluate
from bcs_e2e.provider_publishes.contract import source_binding


def put(body, path, value):
    keys = path.split('.')
    for key in keys[:-1]:
        body = body.setdefault(key, {})
    body[keys[-1]] = value


def specimen(row):
    context = {'provider_id': 'provider-fixture', 'provider_bot_uuid': 'bot_fixture',
               'provider_bot_ref': 's04-fixture', 'provider_slug': 's04-slug',
               'organization_code': 's04-organization', 'admin_run_id': 's04-admin-run',
               'legacy_projection': {'name': 'E2E Provider'}}
    body, status = {}, 200
    expected = row.get('expected')
    if isinstance(expected, str) and expected.startswith('$'):
        expected = context[expected[1:]]
    kind = row['kind']
    if kind == 'status':
        status = expected
    elif kind == 'eq':
        put(body, row['path'], expected)
    elif kind == 'nonempty':
        put(body, row['path'], 'identifier-fixture-not-credential')
    elif kind == 'contains':
        body = {'error': expected}
    elif kind == 'keys':
        body = dict.fromkeys(expected, 'public-field')
    elif kind == 'projection':
        body = copy.deepcopy(expected)
    elif kind == 'membership':
        body = {row['path']: [{row['field']: context[row['target'][1:]]}] if expected else []}
    return status, body, context


class MutationTests(unittest.TestCase):
    def test_missing_null_is_not_null(self):
        row = {'kind': 'eq', 'path': 'agent_code', 'expected': None}
        self.assertFalse(evaluate(row, 200, {}, {}))
        self.assertTrue(evaluate(row, 200, {'agent_code': None}, {}))

    def test_bool_is_not_int(self):
        for expected, actual in ((True, 1), (False, 0), ([], {})):
            with self.subTest(expected=expected):
                row = {'kind': 'eq', 'path': 'value', 'expected': expected}
                self.assertFalse(evaluate(row, 200, {'value': actual}, {}))

    def test_missing_member_identifier_is_not_negative_membership_evidence(self):
        row = {'kind': 'membership', 'path': 'bots', 'field': 'bot_uuid',
               'target': '$target', 'expected': False}
        self.assertFalse(evaluate(row, 200, {'bots': [{}]}, {'target': 'bot_fixture'}))


def make_test(row):
    def test(self):
        status, body, context = specimen(row)
        self.assertTrue(evaluate(row, status, body, context))
        kind = row['kind']
        if kind == 'status':
            self.assertFalse(evaluate(row, status + 1, body, context))
            self.assertFalse(evaluate(row, str(status), body, context))
        elif kind == 'eq':
            put(body, row['path'], {'unexpected': True})
            self.assertFalse(evaluate(row, status, body, context))
            self.assertFalse(evaluate(row, status, {}, context))
        elif kind == 'nonempty':
            for value in ('', None, 1, False, [], '   '):
                put(body, row['path'], value)
                self.assertFalse(evaluate(row, status, body, context))
        elif kind == 'contains':
            self.assertFalse(evaluate(row, status, {'error': 'unrelated failure'}, context))
        elif kind == 'keys':
            body['private_field'] = 'should-not-leak'
            self.assertFalse(evaluate(row, status, body, context))
            body.pop('private_field')
            body.pop(next(iter(body)))
            self.assertFalse(evaluate(row, status, body, context))
        elif kind == 'projection':
            body['name'] = 'wrong-provider'
            self.assertFalse(evaluate(row, status, body, context))
        elif kind == 'membership':
            body[row['path']] = [] if row['expected'] else [{row['field']: context[row['target'][1:]]}]
            self.assertFalse(evaluate(row, status, body, context))
            self.assertFalse(evaluate(row, status, {}, context))
    return test


for index, row in enumerate(source_binding()['requirements'], 1):
    setattr(MutationTests, 'test_obligation_%03d_%s' % (index, row['id'].replace('-', '_').replace('.', '_')),
            make_test(row))
