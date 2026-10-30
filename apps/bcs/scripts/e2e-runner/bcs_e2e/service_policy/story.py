#!/usr/bin/env python3
"""Bearer black-box retired service policy story for the dual preprod leg.

This child replays the frozen upstream journey
(``apps/bcs/scripts/e2e-test/service_policy_story.py``, 23 local checks)
against the reviewed bcs-direct preprod origin using only product HTTP and a
pre-admitted dedicated-pool driver Bot bearer identity:

- the local bootstrap obligations map honestly: the driver Bot identity is
  confirmed live (``GET /bots/{driver_bot_id}`` -> 200 with matching
  ``bot_uuid``), and the registration obligation is never executed on shared
  preprod (no ``/register`` plane, no ``/bots/onboard``, no Bot registration);
- both legacy ``service_spec`` encodings (disabled policy object and null
  compatibility shim) are accepted by ``POST /groups`` while persisting
  nothing, on two disposable Groups;
- the retired settings surface stays Gone (``PATCH /groups/{id}/settings``
  -> 410 ``service_policy_retired``);
- two distinct ``service_invocation`` Sessions run with persisted inputs;
- a running invocation refuses reactivation with 409 and keeps its input;
- a completed chat Session refuses conversion into a service invocation
  with 400 ``not service_invocation`` and stays intact;
- teardown deletes exactly the three Sessions and two Groups the story
  created; the shared driver Bot is never registered and never deleted.

The Human Cookie environment reference is admitted for presence only and is
never attached to any request. Only the driver Bot bearer travels on the
wire. The child emits only the frozen marker on stdout, writes one bounded
non-secret evidence file exclusively, and sweeps every credential canary
before the write.
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
from pathlib import Path
import re
import sys
import urllib.parse

# Frozen literals; the dual contract pins this surface beside the flat
# owned-local contract (local 23 checks, bearer 20 checks).
EXPECTED_CHECKS = 20
MARKER_PREFIX = 'Retired service policy story: '
MARKER_SUFFIX = ' HTTP/response checks passed'
REQUIREMENTS = tuple('SERVICE-POLICY.' + name for name in (
    'mock-human-bootstrap', 'provider-free-bot-registration', 'legacy-encoding-ignored',
    'retired-settings-gone', 'invocation-lifecycle', 'running-reactivation-denied',
    'chat-session-guard', 'cleanup-disposition'))
NOT_APPLICABLE_REQUIREMENT = REQUIREMENTS[1]
DRIVER_BOT_ID = re.compile(r'^\d{8}_[a-z0-9]+:\d+$')
REGISTRATION_DISPOSITION = 'REMOTE_NOT_APPLICABLE_PRE_ADMITTED_DRIVER'
DRIVER_BOT_DELETE_DISPOSITION = 'SKIPPED_SHARED_DRIVER_BOT'
REDACTION_CANARIES = ('bot_token', 'X-BCS-Bot-Token', 'Bearer ', 'starpoint-data',
                      'x-proxypass-token')
HTTP_TIMEOUT_SECONDS = 30
MAX_CREDENTIAL_BYTES = 8192
BOUNDED_EVIDENCE_BYTES = 8 * 1024 * 1024


class StoryFailure(AssertionError):
    pass


def segment(value: str) -> str:
    return urllib.parse.quote(value, safe='')


class Story:
    def __init__(self, base_url: str, driver_bot: str, *, bearer_token_env: str,
                 cookie_env: str):
        base = urllib.parse.urlsplit(base_url.rstrip('/'))
        if base.scheme != 'https' or not base.hostname or base.path not in ('', '/'):
            raise StoryFailure('bearer service policy story requires a https product origin')
        if base.port not in (None, 443):
            raise StoryFailure('bearer service policy story binds the default https port only')
        if DRIVER_BOT_ID.fullmatch(driver_bot) is None:
            raise StoryFailure('driver Bot id does not match the dedicated-pool station shape')
        token = os.environ.get(bearer_token_env, '')
        if (not isinstance(token, str) or not token or not token.isascii()
                or any(ord(char) < 32 or ord(char) == 127 for char in token)
                or len(token) > MAX_CREDENTIAL_BYTES):
            raise StoryFailure('driver Bot bearer environment reference is not loaded or unsafe')
        cookie = os.environ.get(cookie_env, '')
        if (not isinstance(cookie, str) or not cookie or not cookie.isascii()
                or any(ord(char) < 32 or ord(char) == 127 for char in cookie)
                or len(cookie) > MAX_CREDENTIAL_BYTES):
            raise StoryFailure('human cookie environment reference is not loaded or unsafe')
        # The cookie is presence-validated only; its value is dropped here and
        # never attached to any request on this story.
        del cookie
        self.scheme = base.scheme
        self.host = base.hostname
        self.port = base.port or 443
        self.prefix = (base.path or '').rstrip('/')
        self.driver_bot = driver_bot
        self.bearer_token_env = bearer_token_env
        self.cookie_env = cookie_env
        self._bearer_token = token
        self.secrets = [token]
        self.checks = 0
        self.groups: list[str] = []
        self.sessions: list[str] = []
        self.obligations: dict = {}

    # ---------------------------------------------------------------- primitives
    def check(self, condition: object, label: str) -> None:
        if not condition:
            raise StoryFailure(label)
        self.checks += 1

    def _headers(self) -> dict:
        return {'Authorization': 'Bearer ' + self._bearer_token,
                'Content-Type': 'application/json'}

    def _exchange(self, method: str, path: str, *, body=None):
        payload = None if body is None else json.dumps(body).encode('utf-8')
        connection = http.client.HTTPSConnection(self.host, self.port,
                                                 timeout=HTTP_TIMEOUT_SECONDS)
        try:
            connection.putrequest(method, self.prefix + path)
            for name, value in self._headers().items():
                connection.putheader(name, value)
            if payload is not None:
                connection.putheader('Content-Length', str(len(payload)))
            connection.endheaders(payload)
            response = connection.getresponse()
            raw = response.read()
            status = response.status
        except (OSError, http.client.HTTPException) as error:
            raise StoryFailure('HTTP transport failed: ' + type(error).__name__) from None
        finally:
            connection.close()
        return status, (json.loads(raw) if raw else {})

    def request(self, label: str, method: str, path: str, *, body=None,
                expected_status=200) -> tuple[int, dict]:
        status, value = self._exchange(method, path, body=body)
        acceptable = expected_status if isinstance(expected_status, (set, tuple,
                                                                     frozenset)) \
            else {expected_status}
        if status not in acceptable:
            route = path.split('?', 1)[0]
            raise StoryFailure(label + ': unexpected HTTP status ' + str(status)
                               + ' on ' + method + ' ' + route)
        self.checks += 1
        return status, value

    def _assert_policy_absent(self, group: object, label: str) -> None:
        if not isinstance(group, dict):
            raise StoryFailure(label + ': Group response is not an object')
        if 'service_spec' in group:
            raise StoryFailure(label + ': retired policy leaked into the Group response')
        if 'retired_service_spec' in group:
            raise StoryFailure(label + ': compatibility shim leaked into the Group response')
        self.checks += 1

    # ------------------------------------------------------------------- journey
    def _confirm_driver_identity(self) -> None:
        requirement = REQUIREMENTS[0]
        status, value = self.request('driver identity confirmation', 'GET',
                                     '/bots/' + segment(self.driver_bot),
                                     expected_status=200)
        if not isinstance(value, dict) or value.get('bot_uuid') != self.driver_bot:
            raise StoryFailure('driver identity read did not match the pre-admitted driver Bot')
        self.obligations[requirement] = {
            'transport': 'product-http', 'route': 'GET /bots/{driver_bot_id}',
            'status': status, 'actor_match': True, 'driver_bot': self.driver_bot,
            'cookie_mode': 'presence_validated_never_attached'}

    def _registration_disposition(self) -> None:
        requirement = NOT_APPLICABLE_REQUIREMENT
        self.obligations[requirement] = {
            'transport': 'none', 'disposition': REGISTRATION_DISPOSITION,
            'driver_bot_deletion_forbidden': True}

    def _legacy_encoding_groups(self) -> None:
        requirement = REQUIREMENTS[2]
        observations = []
        for policy in ({'enabled': False, 'max_concurrency': 1}, None):
            created_status, created = self.request(
                'legacy service_spec Group creation', 'POST', '/groups',
                body={'driver_bot': self.driver_bot,
                      'label': 'Retired policy compatibility',
                      'participants': [{'bot_uuid': self.driver_bot, 'role': 'driver'}],
                      'service_spec': policy},
                expected_status={200, 201})
            self._assert_policy_absent(created, 'created Group response')
            group_id = created.get('id')
            if not isinstance(group_id, str) or not group_id:
                raise StoryFailure('legacy Group creation committed no Group id')
            if created.get('driver_bot') not in (None, self.driver_bot):
                raise StoryFailure('created Group is not owned by the story driver identity')
            self.groups.append(group_id)
            readback_status, readback = self.request(
                'legacy Group read-back', 'GET', '/groups/' + segment(group_id),
                expected_status=200)
            self._assert_policy_absent(readback, 'read-back Group response')
            observations.append({
                'group_id': group_id, 'created_status': created_status,
                'created_service_spec_absent': True,
                'created_retired_service_spec_absent': True,
                'readback_status': readback_status,
                'readback_service_spec_absent': True,
                'readback_retired_service_spec_absent': True,
                'driver_owned': True})
        self.obligations[requirement] = {'transport': 'product-http',
                                         'groups': observations}

    def _retired_settings_gone(self) -> None:
        requirement = REQUIREMENTS[3]
        status, retired = self.request(
            'retired settings PATCH', 'PATCH',
            '/groups/' + segment(self.groups[0]) + '/settings',
            body={'service_spec': {'enabled': True}}, expected_status=410)
        if retired.get('error') != 'service_policy_retired':
            raise StoryFailure('retired settings surface lost the exact service_policy_retired error')
        self.obligations[requirement] = {'transport': 'product-http', 'status': status,
                                         'error': retired.get('error')}

    def _invocation_lifecycle(self) -> None:
        requirement = REQUIREMENTS[4]
        group = self.groups[0]
        invocations = []
        for index in range(2):
            status, invoked = self.request(
                'service invocation creation', 'POST',
                '/services/' + segment(group) + '/sessions',
                body={'session_title': 'Legacy policy ignored ' + str(index),
                      'input': {'run': index}},
                expected_status=202)
            session_id = invoked.get('session_id')
            if (not isinstance(session_id, str) or not session_id
                    or invoked.get('session_kind') != 'service_invocation'
                    or invoked.get('status') != 'running'):
                raise StoryFailure('service invocation creation did not answer a running service_invocation Session')
            self.sessions.append(session_id)
            _, stored = self.request('invocation stored input read-back', 'GET',
                                     '/services/' + segment(group) + '/sessions/'
                                     + segment(session_id), expected_status=200)
            if stored.get('input') != {'run': index}:
                raise StoryFailure('invocation Session did not persist its submitted input')
            invocations.append({'session_id': session_id, 'status': status,
                                'session_kind': invoked.get('session_kind'),
                                'lifecycle_status': invoked.get('status'),
                                'stored_input': {'run': index}})
        if self.sessions[0] == self.sessions[1]:
            raise StoryFailure('Invocations unexpectedly reused a Session')
        self.obligations[requirement] = {'transport': 'product-http', 'group_id': group,
                                         'invocations': invocations}

    def _running_reactivation_denied(self) -> None:
        requirement = REQUIREMENTS[5]
        group = self.groups[0]
        session_id = self.sessions[0]
        status, conflict = self.request(
            'running invocation reactivation', 'POST',
            '/services/' + segment(group) + '/sessions',
            body={'session_id': session_id, 'input': {'run': 'denied'}},
            expected_status=409)
        if 'running' not in str(conflict.get('error', '')):
            raise StoryFailure('running reactivation lost its running-shaped error')
        _, stored = self.request('stored input after refused reactivation', 'GET',
                                 '/services/' + segment(group) + '/sessions/'
                                 + segment(session_id), expected_status=200)
        if stored.get('input') != {'run': 0}:
            raise StoryFailure('stored invocation input changed across the refused reactivation')
        self.obligations[requirement] = {'transport': 'product-http', 'status': status,
                                         'error': conflict.get('error'),
                                         'stored_input': {'run': 0}}

    def _chat_session_guard(self) -> None:
        requirement = REQUIREMENTS[6]
        group = self.groups[0]
        chat_create_status, chat = self.request(
            'chat Session creation', 'POST',
            '/groups/' + segment(group) + '/sessions',
            body={'session_title': 'Chat lifecycle guard'}, expected_status=201)
        chat_session_id = chat.get('session_id')
        if not isinstance(chat_session_id, str) or not chat_session_id:
            raise StoryFailure('chat Session creation committed no Session id')
        self.sessions.append(chat_session_id)
        _, completed = self.request('chat Session completion', 'POST',
                                    '/sessions/' + segment(chat_session_id) + '/complete',
                                    body={'output': {'ok': True}}, expected_status=200)
        if completed.get('status') != 'completed':
            raise StoryFailure('chat Session completion did not answer a completed Session')
        conversion_status, invalid = self.request(
            'completed chat Session conversion refusal', 'POST',
            '/services/' + segment(group) + '/sessions',
            body={'session_id': chat_session_id}, expected_status=400)
        if 'not service_invocation' not in str(invalid.get('error', '')):
            raise StoryFailure('service conversion of a chat Session lost its refusal error')
        _, stored = self.request('completed chat Session read-back', 'GET',
                                 '/sessions/' + segment(chat_session_id),
                                 expected_status=200)
        if stored.get('status') != 'completed' or stored.get('output') != {'ok': True}:
            raise StoryFailure('refused conversion disturbed the completed chat Session')
        self.obligations[requirement] = {
            'transport': 'product-http', 'chat_create_status': chat_create_status,
            'completed_status': completed.get('status'),
            'conversion_status': conversion_status, 'error': invalid.get('error'),
            'final_status': stored.get('status'), 'final_output': stored.get('output')}

    def run(self) -> dict:
        self._confirm_driver_identity()
        self._registration_disposition()
        self._legacy_encoding_groups()
        self._retired_settings_gone()
        self._invocation_lifecycle()
        self._running_reactivation_denied()
        self._chat_session_guard()
        return {
            'schema_version': 1,
            'identity_mode': 'bearer',
            'driver_bot': self.driver_bot,
            'groups': list(self.groups),
            'sessions': list(self.sessions),
            'obligations': self.obligations,
            'failures': [],
            'groups_teardown_matches_creation': False,
        }

    # ------------------------------------------------------------------ cleanup
    def _delete(self, path: str) -> int:
        status, _ = self._exchange('DELETE', path)
        return status

    def cleanup(self) -> dict:
        requirement = REQUIREMENTS[7]
        failures: list[str] = []
        delete_statuses: list[int] = []
        sessions_deleted = 0
        for session_id in list(self.sessions):
            path = '/sessions/' + segment(session_id) + '?bot_id=' + segment(self.driver_bot)
            try:
                status = self._delete(path)
                delete_statuses.append(status)
                if status in (200, 204):
                    sessions_deleted += 1
                else:
                    failures.append('session delete answered ' + str(status) + ' on ' + path)
            except StoryFailure as error:
                failures.append(str(error))
        groups_deleted = 0
        for group_id in list(self.groups):
            path = '/groups/' + segment(group_id) + '?bot_id=' + segment(self.driver_bot)
            try:
                status = self._delete(path)
                delete_statuses.append(status)
                if status in (200, 204):
                    groups_deleted += 1
                else:
                    failures.append('group delete answered ' + str(status) + ' on ' + path)
            except StoryFailure as error:
                failures.append(str(error))
        matches_creation = (sessions_deleted == 3 and groups_deleted == 2
                            and len(delete_statuses) == 5 and not failures)
        record = {
            'transport': 'product-http',
            'sessions_deleted': sessions_deleted,
            'groups_deleted': groups_deleted,
            'delete_statuses': delete_statuses,
            'driver_bot_delete_disposition': DRIVER_BOT_DELETE_DISPOSITION,
            'failures': failures,
            'groups_teardown_matches_creation': matches_creation,
        }
        self.obligations[requirement] = record
        return record


def _write_evidence(evidence_path: Path, payload: bytes) -> None:
    if len(payload) > BOUNDED_EVIDENCE_BYTES:
        raise StoryFailure('bearer story evidence exceeded its bounded write limit')
    if evidence_path.exists() or evidence_path.is_symlink():
        raise StoryFailure('bearer story evidence file must be fresh')
    fd = os.open(evidence_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--driver-bot', required=True)
    parser.add_argument('--bearer-token-env', default='BCS_E2E_DRIVER_TOKEN')
    parser.add_argument('--cookie-env', default='BCS_E2E_HUMAN_COOKIE')
    parser.add_argument('--evidence-file', required=True)
    args = parser.parse_args(argv)
    story = Story(args.base_url, args.driver_bot, bearer_token_env=args.bearer_token_env,
                  cookie_env=args.cookie_env)
    try:
        evidence = story.run()
        teardown = story.cleanup()
        evidence['failures'] = teardown['failures']
        evidence['groups_teardown_matches_creation'] = teardown[
            'groups_teardown_matches_creation']
        if teardown['failures'] or not teardown['groups_teardown_matches_creation']:
            raise StoryFailure('Story cleanup failed: ' + repr(teardown['failures'])[:400])
    except StoryFailure as error:
        print(MARKER_PREFIX[:-2] + ' failed: ' + str(error), file=sys.stderr, flush=True)
        return 1
    if story.checks != EXPECTED_CHECKS:
        print(MARKER_PREFIX[:-2] + ' failed: assertion count mismatch: expected '
              + str(EXPECTED_CHECKS) + ', observed ' + str(story.checks),
              file=sys.stderr, flush=True)
        return 1
    payload = json.dumps(evidence, sort_keys=True, indent=2, allow_nan=False).encode('utf-8')
    for canary in REDACTION_CANARIES:
        if canary.encode('utf-8') in payload:
            print(MARKER_PREFIX[:-2] + ' failed: evidence redaction gate rejected '
                  'credential-shaped content', file=sys.stderr, flush=True)
            return 1
    for secret in story.secrets:
        if secret.encode('utf-8') in payload:
            print(MARKER_PREFIX[:-2] + ' failed: evidence redaction gate rejected a '
                  'retained journey credential', file=sys.stderr, flush=True)
            return 1
    try:
        _write_evidence(Path(args.evidence_file), payload)
    except OSError as error:
        print(MARKER_PREFIX[:-2] + ' failed: evidence write failed: ' + type(error).__name__,
              file=sys.stderr, flush=True)
        return 1
    print(MARKER_PREFIX + str(story.checks) + MARKER_SUFFIX, flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
