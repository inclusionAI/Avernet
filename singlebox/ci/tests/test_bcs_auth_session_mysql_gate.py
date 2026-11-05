"""The ignored MySQL session contract must have a mandatory, isolated CI gate."""
from pathlib import Path
import unittest
from urllib.parse import urlsplit

import yaml

ROOT = Path(__file__).resolve().parents[3]
SELECTOR = (
    "python3 scripts/ci_contract_test.py --package bcs-user-identity "
    "--binary conformance_auth_session_mysql "
    "--test auth_session_mysql_contract_and_two_connection_race"
)


class AuthSessionMysqlGateTest(unittest.TestCase):
    def test_session_contract_runs_on_its_own_empty_database(self):
        workflow = yaml.safe_load((ROOT / '.github/workflows/unit-tests.yml').read_text())
        job = workflow['jobs']['bcs']
        steps = [step for step in job['steps'] if SELECTOR in step.get('run', '')]
        self.assertEqual(len(steps), 1, 'the MySQL session contract must execute exactly once')
        step = steps[0]
        self.assertEqual(step['if'], "steps.changes.outputs.skip-bcs != 'true'")
        self.assertFalse(step.get('continue-on-error', False))
        url = urlsplit(step['env']['BCS_TEST_MYSQL_URL'])
        default_url = urlsplit(job['env']['BCS_TEST_MYSQL_URL'])
        self.assertNotEqual(url.path, default_url.path)
        service = job['services']['mysql_auth_session']
        self.assertEqual(url.path, '/' + service['env']['MYSQL_DATABASE'])
        self.assertIn(f'{url.port}:3306', service['ports'])
        self.assertIn('mysqladmin ping', service['options'])
        for other in job['steps']:
            if other is not step and 'ci_contract_test.py' in other.get('run', ''):
                database = other.get('env', {}).get(
                    'BCS_TEST_MYSQL_URL', job['env']['BCS_TEST_MYSQL_URL'])
                self.assertNotEqual(url.path, urlsplit(database).path,
                                    'the session contract requires an EMPTY isolated database')


if __name__ == '__main__':
    unittest.main()
