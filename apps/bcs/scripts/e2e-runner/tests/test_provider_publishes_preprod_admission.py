"""Guard tests for the S04 preprod admission surface (profile contract v2).

The frozen v1 no-network seal stays loadable byte-for-byte; the additive v2
gateway profile admits an HTTPS origin plus Authorization/Cookie
environment-NAME references. The preprod leg is covered end to end with a
fake ReadClient: named credential-reference admission (presence and bounded
shape only, never values), the live read-only admission probe
(environment.read, identity.read, driver Bot bot.read), honest fail-closed
probe errors, and the disclosed journey seam with named gaps. No test opens
a socket, hits the network, or stores a credential value.
"""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.provider_publishes import dual_case_run as dual
from bcs_e2e.provider_publishes.contract import CLI_SHA256, SERVER_SHA256
from bcs_e2e.profile import ConfigError
from bcs_e2e.transport import Observation, ObservationError

RUN_ID = 's04-admission-test'
DRIVER_BOT_ID = '20261031_providerdriver:7'
DRIVER_ENV = 'BCS_E2E_DRIVER_TOKEN'
COOKIE_ENV = 'BCS_E2E_HUMAN_COOKIE'
AUTHORIZATION_CANARY = 'FIXTURE-NOT-A-CREDENTIAL-AUTHORIZATION'
COOKIE_CANARY = 'FIXTURE-NOT-A-CREDENTIAL-COOKIE'
PRESENT_ENV = {'PATH': '/usr/bin:/bin', DRIVER_ENV: AUTHORIZATION_CANARY,
               COOKIE_ENV: COOKIE_CANARY}

BASE_PROFILE = {
    'schema_version': 2,
    'contract': 'dual-provider-publishes-profile-v2',
    'environment': {'tier': 'preprod', 'id': 's04-preprod', 'expected_bcs_env': 'pre'},
    'entrypoint': {'kind': 'gateway',
                   'origin': 'https://bcs-preprod-admission.example.invalid',
                   'manifest_face': 'legacy'},
    'auth': {'header_env': {'Authorization': DRIVER_ENV, 'Cookie': COOKIE_ENV}},
    'actors': {'driver': {'bot_id': DRIVER_BOT_ID}},
    'policy': {'timeout_seconds': 30, 'max_response_bytes': 1048576},
}


def admission_profile(**overrides):
    profile = copy.deepcopy(BASE_PROFILE)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(profile.get(key), dict):
            profile[key] = {**profile[key], **value}
        else:
            profile[key] = value
    return profile


class FakeReadClient:
    """Stand-in for the shared ReadClient; records the probe contract exactly."""

    constructed = []

    def __init__(self, profile, *, local_auth=None):
        self.profile = profile
        self.evidence = []
        self.reads = []
        self.fail_on = None
        self.force_status = None
        FakeReadClient.constructed.append(self)

    def read(self, operation, bot_id='', **kwargs):
        self.reads.append((operation, bot_id))
        path = {'environment.read': '/manifest',
                'identity.read': '/openapi/v1/auth/user',
                'bot.read': '/openapi/v1/collaboration/bots/' + bot_id}.get(operation)
        record = {'operation': operation, 'method': 'GET', 'path': path,
                  'submitted_request_id': 'e2e-check-fixture', 'http_status': None,
                  'request_id_echoed': False, 'elapsed_ms': 1.0}
        self.evidence.append(record)
        if self.fail_on == operation:
            record['http_status'] = 401
            raise ObservationError('HTTP 401; no redirect or route fallback attempted')
        record['http_status'] = self.force_status if self.force_status is not None else 200
        record['request_id_echoed'] = True
        return Observation({'fixture': 'admission-body-not-a-credential'}, record)

    @classmethod
    def reset(cls):
        cls.constructed = []


class SealedV1CompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

    def test_frozen_v1_shapes_load_exactly_as_before(self):
        profile = self.root / 'sealed.json'
        for tier in ('owned-local', 'preprod'):
            with self.subTest(tier=tier):
                profile.write_text(json.dumps(dual.profile_for(tier)))
                self.assertEqual(dual.load_profile(profile), dual.profile_for(tier))

    def test_sealed_v1_shape_is_byte_stable(self):
        self.assertEqual(dual.profile_for('owned-local'), {
            'schema_version': 1, 'contract': 'dual-provider-publishes-profile-v1',
            'environment': {'tier': 'owned-local', 'id': 's04-owned-local',
                            'expected_bcs_env': 'local'},
            'entrypoint': {'kind': 'owned-local'}})
        self.assertEqual(dual.profile_for('preprod'), {
            'schema_version': 1, 'contract': 'dual-provider-publishes-profile-v1',
            'environment': {'tier': 'preprod', 'id': 's04-preprod',
                            'expected_bcs_env': 'pre'},
            'entrypoint': {'kind': 'blocked-no-network'}})

    def test_sealed_v1_drift_is_still_rejected(self):
        profile = self.root / 'drift.json'
        profile.write_text(json.dumps(dict(dual.profile_for('preprod'), unexpected='bad')))
        with self.assertRaises(ConfigError):
            dual.load_profile(profile)

    def test_v2_profile_admits_preprod_only(self):
        profile = self.root / 'local-v2.json'
        body = admission_profile()
        body['environment']['tier'] = 'owned-local'
        profile.write_text(json.dumps(body))
        with self.assertRaises(ConfigError):
            dual.load_profile(profile)

    def test_v2_loads_without_any_credential_environment_present(self):
        # Loading validates shapes only; presence is an admission-time gate.
        profile = self.root / 'unloaded-env.json'
        profile.write_text(json.dumps(admission_profile()))
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(dual.load_profile(profile), admission_profile())


class V2LoaderMatrixTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

    def load(self, body):
        path = self.root / 'profile.json'
        path.write_text(json.dumps(body))
        return dual.load_profile(path)

    def test_valid_v2_shapes_load(self):
        for face in ('legacy', 'internal-v1'):
            with self.subTest(manifest_face=face):
                body = admission_profile(entrypoint={'manifest_face': face})
                self.assertEqual(self.load(body), body)
        self.assertEqual(
            self.load(admission_profile(policy={'timeout_seconds': 1,
                                                'max_response_bytes': 1})),
            admission_profile(policy={'timeout_seconds': 1, 'max_response_bytes': 1}))

    def test_rejected_v2_shapes(self):
        bodies = {
            'wrong contract': admission_profile(contract='dual-provider-publishes-profile-v1'),
            'wrong schema_version': admission_profile(schema_version=1),
            'owned-local tier': admission_profile(environment={'tier': 'owned-local'}),
            'expected_bcs_env drift': admission_profile(environment={'expected_bcs_env': 'prod'}),
            'http origin': admission_profile(entrypoint={'origin': 'http://bcs.example.invalid'}),
            'origin with path': admission_profile(
                entrypoint={'origin': 'https://bcs.example.invalid/manifest'}),
            'origin with userinfo': admission_profile(
                entrypoint={'origin': 'https://user:pass@bcs.example.invalid'}),
            'unknown manifest face': admission_profile(entrypoint={'manifest_face': 'preview'}),
            'bcs-direct kind': admission_profile(entrypoint={'kind': 'bcs-direct'}),
            'missing auth block': {k: v for k, v in admission_profile().items() if k != 'auth'},
            'auth with kind on v2': admission_profile(auth={'header_env': {
                'Authorization': DRIVER_ENV, 'Cookie': COOKIE_ENV}, 'kind': 'preprod-bearer'}),
            'missing cookie ref': admission_profile(auth={'header_env': {
                'Authorization': DRIVER_ENV}}),
            'unknown header name': admission_profile(auth={'header_env': {
                'Authorization': DRIVER_ENV, 'Cookie': COOKIE_ENV, 'X-Custom': DRIVER_ENV}}),
            'lowercase reference': admission_profile(auth={'header_env': {
                'Authorization': 'bcs_e2e_driver_token', 'Cookie': COOKIE_ENV}}),
            'literal credential material': admission_profile(auth={'header_env': {
                'Authorization': 'Bearer eyJhbGciOiJIUzI1NiJ9', 'Cookie': COOKIE_ENV}}),
            'placeholder reference': admission_profile(auth={'header_env': {
                'Authorization': 'REPLACE_WITH_DRIVER_TOKEN', 'Cookie': COOKIE_ENV}}),
            'placeholder origin': admission_profile(entrypoint={
                'origin': 'https://REPLACE_WITH_GATEWAY.example.invalid'}),
            'missing actors': {k: v for k, v in admission_profile().items() if k != 'actors'},
            'extra member actor': admission_profile(actors={'driver': {'bot_id': DRIVER_BOT_ID},
                                                             'member': {'bot_id': 'x'}}),
            'non station bot id': admission_profile(
                actors={'driver': {'bot_id': 'provider-driver'}}),
            'bot id with space': admission_profile(actors={'driver': {'bot_id': 'bad bot id'}}),
            'missing policy': {k: v for k, v in admission_profile().items() if k != 'policy'},
            'timeout out of range': admission_profile(policy={'timeout_seconds': 0,
                                                              'max_response_bytes': 1048576}),
            'response budget out of range': admission_profile(
                policy={'timeout_seconds': 30, 'max_response_bytes': 0}),
            'unknown top level key': dict(admission_profile(), unexpected='bad'),
            'unknown environment key': admission_profile(environment={'lifecycle': 'remote'}),
        }
        for label, body in bodies.items():
            with self.subTest(label=label):
                with self.assertRaises(ConfigError):
                    self.load(body)


class CredentialAdmissionTests(unittest.TestCase):
    def test_admission_record_is_names_and_booleans_only(self):
        with patch.dict(os.environ, PRESENT_ENV, clear=True):
            record = dual._credential_admission(BASE_PROFILE['auth']['header_env'])
        self.assertEqual(record['status'], 'ADMITTED')
        self.assertIsNone(record['reason_class'])
        self.assertEqual(record['resolved_references_by_name'],
                         [DRIVER_ENV, COOKIE_ENV])
        self.assertEqual([row['environment_reference'] for row in record['references']],
                         [DRIVER_ENV, COOKIE_ENV])  # header names are sorted
        self.assertTrue(all(row['present'] and row['shape_admitted']
                            for row in record['references']))
        self.assertFalse(record['credential_values_stored'])
        self.assertNotIn(AUTHORIZATION_CANARY, json.dumps(record))
        self.assertNotIn(COOKIE_CANARY, json.dumps(record))

    def test_missing_and_shaped_references_are_disclosed_by_name(self):
        environment = {'PATH': '/usr/bin:/bin', COOKIE_ENV: COOKIE_CANARY}
        with patch.dict(os.environ, environment, clear=True):
            record = dual._credential_admission(BASE_PROFILE['auth']['header_env'])
        self.assertEqual(record['status'], 'BLOCKED')
        self.assertEqual(record['reason_class'], dual.PREPROD_CREDENTIALS_REQUIRED)
        rows = {row['environment_reference']: row for row in record['references']}
        self.assertFalse(rows[DRIVER_ENV]['present'])
        self.assertTrue(rows[COOKIE_ENV]['present'])
        self.assertTrue(rows[COOKIE_ENV]['shape_admitted'])
        self.assertEqual(record['resolved_references_by_name'], [])
        self.assertIn(DRIVER_ENV, record['reason'])  # only missing refs are named
        self.assertNotIn(COOKIE_ENV, record['reason'])
        self.assertNotIn(COOKIE_CANARY, json.dumps(record))

    def test_control_character_values_fail_the_bounded_shape(self):
        environment = {'PATH': '/usr/bin:/bin', DRIVER_ENV: 'bad\x01value', COOKIE_ENV: 'ok'}
        with patch.dict(os.environ, environment, clear=True):
            record = dual._credential_admission(BASE_PROFILE['auth']['header_env'])
        self.assertEqual(record['status'], 'BLOCKED')
        rows = {row['environment_reference']: row for row in record['references']}
        self.assertTrue(rows[DRIVER_ENV]['present'])
        self.assertFalse(rows[DRIVER_ENV]['shape_admitted'])


class PreprodLegTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.profile_path = self.root / 'profile.json'
        self.profile_path.write_text(json.dumps(admission_profile()))
        self.loaded_profile = dual.load_profile(self.profile_path)
        self.state = self.root / 'fresh-state'
        self.kw = dict(profile_path=self.profile_path,
                       binary=self.root / 'bcs', binary_sha256=SERVER_SHA256,
                       cli_binary=self.root / 'bcs-cli', cli_binary_sha256=CLI_SHA256,
                       state_dir=self.state, run_id=RUN_ID, confirm_run_id=RUN_ID,
                       allow_write=True)
        FakeReadClient.reset()

    def tearDown(self):
        FakeReadClient.reset()

    def write_v2(self, body=None):
        self.profile_path.write_text(json.dumps(body or admission_profile()))
        self.loaded_profile = dual.load_profile(self.profile_path)
        return self.loaded_profile

    def run_case(self, env=None, fake=None):
        patches = [patch.object(dual, 'validate_binary', side_effect=lambda p, *_: p),
                   patch.object(dual, 'ReadClient', FakeReadClient)]
        if env is None:
            environment = patch.dict(os.environ, PRESENT_ENV, clear=True)
        else:
            environment = patch.dict(os.environ, env, clear=True)
        patches.append(environment)
        if fake is not None:
            patches.append(patch.object(dual, 'ReadClient', lambda profile: fake))
        for context in patches:
            context.start()
            self.addCleanup(context.stop)
        return dual.dual_provider_publishes_case_run(**self.kw)

    def test_sealed_v1_profile_never_builds_a_probe(self):
        self.profile_path.write_text(json.dumps(dual.profile_for('preprod')))
        with patch('socket.socket', side_effect=AssertionError('network forbidden')):
            result = self.run_case(env={'PATH': '/usr/bin:/bin'})
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['reason_class'], dual.PREPROD_PROFILE_SEALED_V1)
        self.assertEqual(result['preprod_blocker']['network_requests'], 0)
        self.assertIn('dual-provider-publishes-profile-v2',
                      result['preprod_admission']['named_gaps']
                      ['PREPROD_PROFILE_SEALED_V1_NO_ADMISSION_MATERIAL'])
        self.assertEqual(FakeReadClient.constructed, [])

    def test_missing_references_report_the_named_gap_and_keep_not_run(self):
        result = self.run_case(env={'PATH': '/usr/bin:/bin'})
        self.assertEqual(result['status'], 'NOT_RUN')
        self.assertEqual(result['reason_class'], dual.PREPROD_CREDENTIALS_REQUIRED)
        self.assertEqual(result['preprod_blocker']['credential_resolution'], 'BLOCKED_BY_NAME_ONLY')
        self.assertEqual(result['preprod_blocker']['credential_refs'], [DRIVER_ENV, COOKIE_ENV])
        admission = result['preprod_admission']['credential_admission']
        self.assertEqual(admission['status'], 'BLOCKED')
        self.assertEqual(admission['reason_class'], dual.PREPROD_CREDENTIALS_REQUIRED)
        self.assertTrue(all(not row['present'] for row in admission['references']))
        self.assertEqual(result['preprod_admission']['probe'], None)
        self.assertEqual(result['preprod_admission']['network_requests'], 0)
        self.assertEqual(result['remote_entry_status'], 'NOT_RUN_CREDENTIALS_REQUIRED')
        self.assertEqual(result['obligation_summary']['not_run'], 98)
        self.assertEqual({row['status'] for row in result['assertions']}, {'NOT_RUN'})
        self.assertFalse(result['grants_release_credit'])
        self.assertEqual(result['resource_disposition'], 'NO_REMOTE_RESOURCES_CREATED')
        self.assertEqual(FakeReadClient.constructed, [])
        verdict = json.loads((self.state / 'verdict.json').read_text())
        self.assertEqual(verdict['status'], 'NOT_RUN')
        self.assertFalse(verdict['grants_release_credit'])
        self.assertNotIn(COOKIE_CANARY, (self.state / 'provider-publishes-dual-run.json').read_text())

    def test_partial_reference_gap_discloses_names_only(self):
        result = self.run_case(env={'PATH': '/usr/bin:/bin', COOKIE_ENV: COOKIE_CANARY})
        self.assertEqual(result['status'], 'NOT_RUN')
        self.assertEqual(result['reason_class'], dual.PREPROD_CREDENTIALS_REQUIRED)
        rows = {row['environment_reference']: row
                for row in result['preprod_admission']['credential_admission']['references']}
        self.assertTrue(rows[COOKIE_ENV]['present'])
        self.assertFalse(rows[DRIVER_ENV]['present'])
        for path in ('provider-publishes-dual-run.json', 'verdict.json',
                     'provider-publishes-profile.json', 'provider-publishes-dual-binding.json'):
            self.assertNotIn(COOKIE_CANARY, (self.state / path).read_text())

    def test_probe_failure_is_blocked_with_sanitized_detail(self):
        fake = FakeReadClient(admission_profile())
        fake.fail_on = 'environment.read'
        result = self.run_case(fake=fake)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['reason_class'], dual.PREPROD_PROBE_FAILED)
        self.assertEqual(result['preprod_blocker']['error_type'], 'ObservationError')
        self.assertEqual(result['preprod_blocker']['error_detail'],
                         'HTTP 401; no redirect or route fallback attempted')
        self.assertIn('HTTP 401', result['preprod_blocker']['reason'])
        self.assertEqual(result['preprod_admission']['network_requests'], 1)
        self.assertEqual(result['preprod_blocker']['network_requests'], 1)
        self.assertEqual(result['preprod_admission']['probe']['operations'], [])
        self.assertEqual(result['remote_entry_status'], 'BLOCKED-at-admission-probe')
        self.assertEqual(result['obligation_summary']['not_run'], 98)
        self.assertFalse(result['grants_release_credit'])
        self.assertEqual(fake.reads, [('environment.read', '')])

    def test_probe_non_200_without_raise_is_blocked_honestly(self):
        fake = FakeReadClient(admission_profile())
        fake.force_status = 403
        result = self.run_case(fake=fake)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['reason_class'], dual.PREPROD_PROBE_FAILED)
        self.assertEqual(result['preprod_blocker']['observed_status'], 403)
        self.assertEqual(result['preprod_admission']['probe']['status'], 'BLOCKED')
        self.assertEqual(result['preprod_admission']['network_requests'], 3)
        self.assertEqual(fake.reads[0], ('environment.read', ''))

    def test_probe_pass_fail_closes_at_the_documented_seam(self):
        fake = FakeReadClient(self.loaded_profile)
        FakeReadClient.reset()  # the manual fixture above must not count as a probe
        result = self.run_case(fake=fake)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['reason_class'], dual.PREPROD_JOURNEY_SEAM_PENDING)
        self.assertEqual(result['remote_entry_status'], 'PROBE_ADMITTED_JOURNEY_SEAM_PENDING')
        self.assertEqual(FakeReadClient.constructed, [])
        self.assertEqual(fake.profile, self.loaded_profile)
        self.assertEqual(fake.reads, [('environment.read', ''), ('identity.read', ''),
                                      ('bot.read', DRIVER_BOT_ID)])
        probe = result['preprod_admission']['probe']
        self.assertEqual(probe['status'], 'PASS')
        self.assertEqual(probe['admitted_operations'], list(dual.PROBE_OPERATIONS))
        self.assertEqual([row['operation'] for row in probe['operations']],
                         ['environment.read', 'identity.read', 'bot.read'])
        self.assertEqual(probe['operations'][0]['http_status'], 200)
        self.assertEqual(probe['operations'][2]['path'],
                         '/openapi/v1/collaboration/bots/' + DRIVER_BOT_ID)
        self.assertEqual(result['preprod_admission']['network_requests'], 3)
        admission = result['preprod_admission']['credential_admission']
        self.assertEqual(admission['status'], 'ADMITTED')
        self.assertEqual(admission['resolved_references_by_name'], [DRIVER_ENV, COOKIE_ENV])
        gaps = result['preprod_admission']['named_gaps']
        self.assertIn('PREPROD_PROVIDER_ADMIN_WRITE_FACE_ABSENT', gaps)
        self.assertIn('PREPROD_PROVIDER_REGISTRATION_PRECONDITIONS_PENDING', gaps)
        self.assertIn('PREPROD_PROVIDER_JOURNEY_CHILD_UNPUBLISHED', gaps)
        self.assertEqual(result['obligation_summary']['not_run'], 98)
        self.assertFalse(result['grants_release_credit'])
        self.assertEqual(result['full_release_status'], 'NOT_QUALIFIED')
        self.assertEqual(result['remote_validation'], 'NOT_RUN')
        self.assertEqual(result['resource_disposition'], 'NO_REMOTE_RESOURCES_CREATED')
        verdict = json.loads((self.state / 'verdict.json').read_text())
        self.assertEqual(verdict['status'], 'BLOCKED')
        self.assertEqual(verdict['reason_class'], dual.PREPROD_JOURNEY_SEAM_PENDING)
        self.assertFalse(verdict['grants_release_credit'])
        self.assertEqual(verdict['network_requests'], 3)
        self.assertEqual(verdict['credential_admission_status'], 'ADMITTED')
        for path in ('provider-publishes-dual-run.json', 'verdict.json'):
            text = (self.state / path).read_text()
            self.assertNotIn(AUTHORIZATION_CANARY, text)
            self.assertNotIn(COOKIE_CANARY, text)

    def test_the_shared_read_client_contract_is_used(self):
        with patch.dict(os.environ, PRESENT_ENV, clear=True), \
             patch.object(dual, 'validate_binary', side_effect=lambda p, *_: p):
            leg = dual._preprod_leg(self.loaded_profile, client=FakeReadClient(self.loaded_profile))
        self.assertEqual(leg['status'], 'BLOCKED')
        self.assertEqual(leg['reason_class'], dual.PREPROD_JOURNEY_SEAM_PENDING)
        self.assertEqual(leg['probe']['admitted_operations'], list(dual.PROBE_OPERATIONS))

    def test_static_refusal_point_is_removed(self):
        self.assertFalse(hasattr(dual, 'PREPROD_REASON'))
        source = Path(dual.__file__).read_text()
        self.assertNotIn('PREPROD_PROVIDER_PUBLISHES_CREDENTIALS_UNAVAILABLE', source)
        self.assertNotIn('cookie expired', source)
        self.assertNotIn('USER_NOT_LOGIN', source)
        # v2 runs build the shared ReadClient instead of refusing statically:
        # the injected fake records the live probe trio.
        fake = FakeReadClient(self.loaded_profile)
        FakeReadClient.reset()
        self.run_case(fake=fake)
        self.assertEqual(len(FakeReadClient.constructed), 0)
        self.assertEqual(len(fake.reads), 3)


if __name__ == '__main__':
    unittest.main()
