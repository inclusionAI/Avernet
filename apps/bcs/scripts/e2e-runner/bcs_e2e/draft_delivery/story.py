#!/usr/bin/env python3
"""Black-box Draft delivery journey for the dual owned-local/preprod slice.

One frozen 32-obligation journey runs unchanged on both environments:

- ``register`` identity mode (owned-local): mock-principal bootstrap issues an
  ephemeral author Bot; raw HTTP owner calls carry that Bot token and the mock
  principal headers.
- ``bearer`` identity mode (preprod): a pre-admitted driver Bot token is read
  from the environment reference named by ``--bearer-token-env``; no mock
  headers are sent and no registration occurs.

Author-side product CLI steps (offline preview, create, get, validate, update,
fixed-revision preview, revoke, delete) run the same bcs binary as a bounded
subprocess with the author token supplied through ``BCN_BOT_TOKEN`` only --
never through argv, never through evidence. Raw HTTP steps cover the public
tokenless fixed reads, validation, stale CAS rejection and the late-create
tombstone guard. The child emits only a fixed marker on stdout and writes a
non-secret semantic observation file for the runner.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.parse
import uuid

# Script-mode child: make the shared bcs_e2e package importable so this
# live leg reuses the fail-closed TLS trust context (macOS framework
# Python ships no default CA bundle for bare HTTPSConnection).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bcs_e2e.transport import tls_context  # noqa: E402

EXPECTED_CHECKS = 65
MARKER_PREFIX = 'Draft delivery story: '
MARKER_SUFFIX = ' live HTTP assertions passed'
READ_TOKEN_CANARIES = ('bot_token', 'readToken', 'read_token', 'Bearer ')
CLI_TIMEOUT_SECONDS = 120
HTTP_TIMEOUT_SECONDS = 30
FIXTURE_BYTES = 2174
FIXTURE_HASH = '761dc9bc0f748c06ee5152a6ea9dc8bb1c1bba5e385c001930a26c17be43e7b7'
_UUID = __import__('re').compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$')
# Live owned-local register evidence (2026-10-30): the trusted legacy
# /register route commits BCS bot ids as bot_ + 8 uuid hex
# (bcs-bot core::ids), so bootstrap accepts either that server-issued id
# or a canonical uuid4 string.
_BOT_ID = __import__('re').compile(r'^bot_[0-9a-f]{8}$')


class StoryFailure(AssertionError):
    pass


class Story:
    def __init__(self, base_url: str, human: str, fixture: Path, cli_binary: Path, *,
                 identity_mode: str = 'register', bearer_token_env: str = '',
                 cookie_env: str = '', fixture_hash: str = FIXTURE_HASH):
        self.base = urllib.parse.urlsplit(base_url.rstrip('/'))
        self.human = human
        self.fixture = fixture
        self.cli_binary = cli_binary
        self.identity_mode = identity_mode
        self.bearer_token_env = bearer_token_env
        self.cookie_env = cookie_env
        cookie = os.environ.get(cookie_env, '') if cookie_env else ''
        if cookie and (not cookie.isascii()
                       or any(ord(char) < 32 or ord(char) == 127 for char in cookie)
                       or len(cookie) > 8192):
            raise StoryFailure('human cookie environment reference is unsafe')
        # Station form adaptation: the legacy Draft face sits behind the
        # station ACE, which answers bearer-only requests with a 302 SSO
        # redirect. In bearer identity mode the validated Human cookie is
        # attached beside the author bearer (duo request); its value is
        # never journaled (responses record method/path/status/cache only).
        self._human_cookie = cookie if identity_mode == 'bearer' else ''
        self.checks = 0
        self.responses: list[dict] = []
        self.author_token = ''
        self.author_uuid = ''
        self.obl: dict[str, dict] = {}
        self.work: Path | None = None

    # ---------------------------------------------------------------- primitives
    def check(self, condition: object, label: str) -> None:
        if not condition:
            raise StoryFailure(label)
        self.checks += 1

    def _headers(self, *, token: str = '', mock_identity: bool = True) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.identity_mode == 'register' and mock_identity:
            headers['X-Mock-User-Id'] = self.human
            headers['X-Mock-Nick-Name'] = 'Draft delivery operator'
        if token:
            headers['Authorization'] = 'Bearer ' + token
        if self._human_cookie:
            headers['Cookie'] = self._human_cookie
        return headers

    def request(self, label: str, method: str, path: str, *, body=None,
                token: str = '', expected_status=200, mock_identity: bool = True):
        if self.base.scheme == 'https':
            connection = http.client.HTTPSConnection(self.base.netloc,
                                                     timeout=HTTP_TIMEOUT_SECONDS,
                                                     context=tls_context())
        else:
            connection = http.client.HTTPConnection(self.base.netloc,
                                                    timeout=HTTP_TIMEOUT_SECONDS)
        payload = None if body is None else json.dumps(body).encode('utf-8')
        try:
            connection.putrequest(method, self.base.path + path)
            for name, value in self._headers(token=token, mock_identity=mock_identity).items():
                connection.putheader(name, value)
            if payload is not None:
                connection.putheader('Content-Type', 'application/json')
                connection.putheader('Content-Length', str(len(payload)))
            connection.endheaders(payload)
            response = connection.getresponse()
            raw = response.read()
            status = response.status
            cache_control = response.getheader('Cache-Control')
        except (OSError, http.client.HTTPException) as error:
            raise StoryFailure(label + ': HTTP transport failed: ' + type(error).__name__) from None
        finally:
            connection.close()
        if path.startswith('/collaboration/drafts/'):
            self.responses.append({'method': method, 'path': path, 'status': status,
                                   'cache_control': cache_control})
        acceptable = expected_status if isinstance(expected_status, (set, tuple, frozenset)) else {expected_status}
        self.check(status in acceptable, label + ': unexpected HTTP status ' + str(status))
        try:
            value = json.loads(raw) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            value = {}
        return status, value, cache_control

    def cli(self, label: str, arguments: list[str]) -> dict:
        """Run one product-CLI step as a bounded subprocess; secrets stay in env."""
        if not isinstance(self.cli_binary, Path) or self.cli_binary.name == '':
            raise StoryFailure(label + ': CLI binary path is invalid')
        environment = dict(os.environ)
        environment['MOLTIS_BCS_URL'] = (self.base.scheme + '://' + self.base.netloc
                                         + self.base.path).rstrip('/')
        if self.author_token:
            environment['BCN_BOT_TOKEN'] = self.author_token
        argv = [str(self.cli_binary), '--json', 'collaboration', *arguments]
        try:
            done = subprocess.run(argv, cwd=str(self.work), env=environment,
                                  stdin=subprocess.DEVNULL, capture_output=True,
                                  timeout=CLI_TIMEOUT_SECONDS)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise StoryFailure(label + ': CLI invocation failed: ' + type(error).__name__) from None
        stdout_text = done.stdout.decode('utf-8', errors='replace')
        parsed = None
        try:
            parsed = json.loads(stdout_text) if stdout_text.strip() else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            parsed = None
        return {'exit_code': done.returncode, 'stdout': stdout_text,
                'stderr_bytes': len(done.stderr), 'json': parsed}

    # ------------------------------------------------------------------- journey
    def _workspace(self) -> tuple[bytes, bytes, str]:
        fixture_json = json.loads(self.fixture.read_text(encoding='utf-8'))
        source = fixture_json['revision']['definition_yaml'].encode('utf-8')
        updated = source + b'\n# second revision\n'
        draft_id = str(uuid.uuid4())
        (self.work / 'original.yaml').write_bytes(source)
        (self.work / 'updated.yaml').write_bytes(updated)
        return source, updated, draft_id

    def _bootstrap_register(self) -> None:
        self.request('ensure Human', 'POST', '/me/ensure-human', body={})
        _, issued, _ = self.request('issue registration token', 'GET', '/register/token')
        registration_token = issued.get('token')
        self.check(isinstance(registration_token, str) and bool(registration_token),
                   'registration token is missing')
        name = 'Draft delivery author ' + uuid.uuid4().hex[:10]
        query = urllib.parse.urlencode({'token': registration_token, 'bot-name': name})
        _, registered, _ = self.request('register author', 'POST', '/register?' + query)
        self.author_token = registered.get('bot_token', '')
        self.author_uuid = registered.get('bot_uuid', '')
        self.check((_UUID.fullmatch(self.author_uuid or '') is not None
                    or _BOT_ID.fullmatch(self.author_uuid or '') is not None)
                   and isinstance(self.author_token, str) and bool(self.author_token),
                   'registered author identity is incomplete')
        self.request('onboard author', 'POST', '/bots/onboard', token=self.author_token,
                     body={'name': name, 'summary': 'Draft delivery author'})

    def _bootstrap_bearer(self) -> None:
        token_name = self.bearer_token_env or 'BCS_E2E_DRAFT_AUTHOR_TOKEN'
        token = os.environ.get(token_name, '')
        self.check(isinstance(token, str) and bool(token) and token.isascii()
                   and not any(ord(char) < 32 or ord(char) == 127 for char in token),
                   'bearer author token environment reference is empty or unsafe')
        self.author_token = token
        probe = str(uuid.uuid4())
        _, metadata, _ = self.request('preflight absent Draft metadata', 'GET',
                                      '/collaboration/drafts/' + probe,
                                      token=self.author_token, expected_status=404)
        self.check(metadata.get('error') == 'DRAFT_REFERENCE_UNAVAILABLE',
                   'preflight absent Draft metadata did not report unavailability')
        _, v1_read, _ = self.request('preflight absent public-v1 read', 'GET',
                                     '/api/v1/collaboration/drafts/' + probe + '/revisions/1',
                                     expected_status=404)
        _, legacy_probe, _ = self.request('preflight absent legacy tokenless read', 'GET',
                                          '/collaboration/drafts/' + probe + '/revisions/1',
                                          expected_status=404)
        _, v1_validate, _ = self.request('preflight absent public-v1 validate', 'POST',
                                         '/api/v1/collaboration/drafts/' + probe
                                         + '/revisions/1/validate', expected_status=404)
        self.v1_face = {
            'read_envelope_code': v1_read.get('code') if isinstance(v1_read, dict) else None,
            'read_envelope_message': v1_read.get('message') if isinstance(v1_read, dict) else None,
            'validate_envelope_code': v1_validate.get('code') if isinstance(v1_validate, dict) else None,
            'legacy_tokenless_read_error': legacy_probe.get('error')
            if isinstance(legacy_probe, dict) else None,
        }

    def run(self) -> dict:
        source, updated, draft_id = self._workspace()
        original_hash = hashlib.sha256(source).hexdigest()
        updated_hash = hashlib.sha256(updated).hexdigest()
        draft_path = '/collaboration/drafts/' + draft_id
        title = 'Draft delivery regression'

        if self.identity_mode == 'register':
            self._bootstrap_register()
        else:
            self._bootstrap_bearer()

        # O1-O3 offline schema-1 preview (no server round trip, CLI-side only).
        preview = self.cli('offline preview', ['preview', str(self.work / 'original.yaml')])
        snapshot = preview['json'].get('snapshot') if isinstance(
            preview['json'], dict) else None
        self.obl['DRAFT-DELIVERY.offline-preview-schema1-snapshot'] = {
            'transport': 'offline-cli', 'exit_code': preview['exit_code'],
            'snapshot_present': isinstance(snapshot, dict)}
        self.check(preview['exit_code'] == 0 and isinstance(snapshot, dict),
                   'offline preview did not emit a snapshot')
        schema_version = snapshot.get('schemaVersion', snapshot.get('schema_version'))
        self.obl['DRAFT-DELIVERY.offline-preview-retains-schema1'] = {
            'transport': 'offline-cli', 'schema_version': schema_version}
        self.check(schema_version == 1, 'offline preview did not retain schema 1')
        previewed = snapshot.get('definitionYaml', snapshot.get('definition_yaml'))
        self.obl['DRAFT-DELIVERY.offline-preview-preserves-bytes'] = {
            'transport': 'offline-cli', 'definition_yaml_bytes': len(source),
            'previewed_bytes_equal': isinstance(previewed, str)
            and previewed.encode('utf-8') == source}
        self.check(isinstance(previewed, str) and previewed.encode('utf-8') == source,
                   'offline preview did not preserve the incident source bytes')

        # O4-O7 client-selected create, idempotent replay and conflict.
        create = self.cli('author uploads the incident file',
                          ['draft', 'create', str(self.work / 'original.yaml'),
                           '--draft-id', draft_id, '--title', title])
        created = create['json'] if isinstance(create['json'], dict) else {}
        self.obl['DRAFT-DELIVERY.author-upload-incident-file'] = {
            'transport': 'product-cli', 'exit_code': create['exit_code'],
            'saved': created.get('saved'), 'phase': created.get('phase'),
            'draft_id': created.get('draft_id'),
            'create_stdout_token_field_present': 'readToken' in create['stdout']}
        self.check(create['exit_code'] == 0 and created.get('saved') is True
                   and created.get('phase') == 'ready',
                   'author upload did not complete the ready phase')
        self.check('readToken' not in create['stdout'],
                   'create CLI output exposed the fixed-length card token field')
        self.check(created.get('draft_id') == draft_id and created.get('revision') == 1,
                   'first create did not bind the selected ID and revision 1')
        self.obl['DRAFT-DELIVERY.saved-source-2174-bytes'] = {
            'transport': 'product-cli', 'size_bytes': created.get('size_bytes')}
        self.check(created.get('size_bytes') == FIXTURE_BYTES,
                   'saved source does not have 2174 bytes')
        self.check(created.get('content_hash') == original_hash,
                   'saved source content hash differs from the incident hash')
        nested_validation = created.get('validation') if isinstance(
            created.get('validation'), dict) else {}
        self.obl['DRAFT-DELIVERY.saved-source-validates'] = {
            'transport': 'product-cli',
            'valid': nested_validation.get('validation', {}).get('valid')}
        self.check(nested_validation.get('validation', {}).get('valid') is True,
                   'saved source did not validate')
        replay = self.cli('idempotent create replay',
                          ['draft', 'create', str(self.work / 'original.yaml'),
                           '--draft-id', draft_id, '--title', title])
        replayed = replay['json'] if isinstance(replay['json'], dict) else {}
        conflict = self.cli('different-content create for selected ID',
                            ['draft', 'create', str(self.work / 'updated.yaml'),
                             '--draft-id', draft_id, '--title', title])
        conflicted = conflict['json'] if isinstance(conflict['json'], dict) else {}
        history = self.request('historical revision remains readable after replay/conflict',
                               'GET', draft_path + '/revisions/1', mock_identity=False)
        historical = history[1]
        self.obl['DRAFT-DELIVERY.first-revision-immutable'] = {
            'transport': 'product-cli', 'create_revision': created.get('revision'),
            'replay_exit_code': replay['exit_code'],
            'replay_saved': replayed.get('saved'),
            'replay_revision': replayed.get('revision'),
            'replay_matched_existing': replayed.get('matched_existing_revision'),
            'replay_content_hash': replayed.get('content_hash'),
            'conflict_exit_code': conflict['exit_code'],
            'conflict_http_status': conflicted.get('http_status'),
            'history_revision': historical.get('revision'),
            'history_size_bytes': historical.get('size_bytes'),
            'history_content_hash': historical.get('content_hash')}
        # bcs-cli save() (crates/tools/bcs-cli/src/collaboration_draft.rs)
        # attributes matched_existing_revision only to fixed next-revision
        # update recovery; a create never sets it. Exact-replay idempotency
        # is therefore observed as exit 0 + saved + revision pinned at 1 +
        # unchanged content hash, while the 409 conflict probe and history
        # read below pin the incident bytes.
        self.check(replay['exit_code'] == 0 and replayed.get('saved') is True
                   and replayed.get('revision') == 1
                   and replayed.get('content_hash') == original_hash
                   and replayed.get('matched_existing_revision') is False,
                   'exact create replay was not idempotent on revision 1')
        self.check(conflict['exit_code'] != 0 and conflicted.get('http_status') == 409,
                   'different content for the selected ID did not conflict with 409')
        self.check(historical.get('revision') == 1
                   and historical.get('size_bytes') == FIXTURE_BYTES
                   and historical.get('content_hash') == original_hash,
                   'revision 1 changed after replay and conflict probes')

        # O8-O15 metadata and the public fixed-read card surface.
        _, metadata, _ = self.request('author reads Draft metadata', 'GET', draft_path,
                                      token=self.author_token)
        self.obl['DRAFT-DELIVERY.author-reads-metadata'] = {'transport': 'product-http',
                                                             'status': 200}
        self.obl['DRAFT-DELIVERY.metadata-head-revision-one'] = {
            'transport': 'product-http', 'head_revision': metadata.get('head_revision'),
            'deleted': metadata.get('deleted')}
        self.check(metadata.get('head_revision') == 1 and metadata.get('deleted') is False,
                   'metadata did not point to undeleted revision 1')
        _, public, cache = self.request('card holder reads revision 1 without credentials',
                                        'GET', draft_path + '/revisions/1', mock_identity=False)
        self.obl['DRAFT-DELIVERY.card-holder-tokenless-read'] = {
            'transport': 'product-http', 'status': 200}
        self.obl['DRAFT-DELIVERY.shared-read-preserves-hash'] = {
            'transport': 'product-http', 'content_hash': public.get('content_hash'),
            'size_bytes': public.get('size_bytes')}
        self.check(public.get('content_hash') == original_hash
                   and public.get('size_bytes') == FIXTURE_BYTES,
                   'tokenless fixed read did not preserve the incident bytes')
        self.obl['DRAFT-DELIVERY.shared-source-no-store'] = {
            'transport': 'product-http', 'cache_control': cache}
        self.check(cache is not None and 'no-store' in cache.lower(),
                   'shared read was cacheable')
        _, validated, _ = self.request('card holder validates revision 1', 'POST',
                                       draft_path + '/revisions/1/validate', mock_identity=False)
        self.obl['DRAFT-DELIVERY.card-holder-validates-source'] = {
            'transport': 'product-http', 'status': 200}
        self.obl['DRAFT-DELIVERY.shared-validation-binds-rev1'] = {
            'transport': 'product-http', 'revision': validated.get('revision'),
            'valid': validated.get('validation', {}).get('valid')}
        self.check(validated.get('revision') == 1
                   and validated.get('validation', {}).get('valid') is True,
                   'shared validation did not bind revision 1')
        public_delete_status = self.request('public reader cannot delete', 'DELETE',
                                            draft_path, expected_status=401,
                                            mock_identity=False)[0]
        inert_delete_status = self.request('inert bearer-shaped card token cannot delete', 'DELETE',
                                           draft_path, token='A' * 43,
                                           expected_status={401, 403})[0]
        inert_update_status = self.request(
            'inert bearer-shaped card token cannot append', 'POST', draft_path + '/revisions',
            token='A' * 43, expected_status={401, 403},
            body={'expected_revision': 1, 'definition_yaml': 'unauthorized'})[0]
        _, after_rejection, _ = self.request('metadata survives management rejection',
                                             'GET', draft_path, token=self.author_token)
        self.obl['DRAFT-DELIVERY.public-cannot-delete'] = {
            'transport': 'product-http', 'public_delete_status': public_delete_status,
            'inert_delete_status': inert_delete_status,
            'inert_update_status': inert_update_status,
            'head_after': after_rejection.get('head_revision'),
            'deleted_after': after_rejection.get('deleted')}
        self.check(after_rejection.get('head_revision') == 1
                   and after_rejection.get('deleted') is False,
                   'rejected management operation changed the Draft')

        # O16-O18 fixed-revision download and validation through the CLI.
        download = self.cli('author downloads exact original bytes',
                            ['draft', 'get', draft_id, '--revision', '1',
                             '--output', str(self.work / 'download.yaml')])
        downloaded = download['json'] if isinstance(download['json'], dict) else {}
        downloaded_bytes = (self.work / 'download.yaml').read_bytes() \
            if (self.work / 'download.yaml').is_file() else b''
        self.obl['DRAFT-DELIVERY.author-download-fixed-bytes'] = {
            'transport': 'product-cli', 'exit_code': download['exit_code'],
            'output_path_in_receipt': downloaded.get('output') is not None}
        self.check(download['exit_code'] == 0
                   and downloaded.get('output') is not None,
                   'fixed-revision download did not complete')
        self.obl['DRAFT-DELIVERY.download-matches-incident-bytes'] = {
            'transport': 'product-cli', 'bytes_equal': downloaded_bytes == source,
            'size_bytes': len(downloaded_bytes)}
        self.check(downloaded_bytes == source,
                   'downloaded bytes differ from the incident file')
        validate_cli = self.cli('author validates the fixed revision',
                                ['draft', 'validate', draft_id, '--revision', '1'])
        validated_cli = validate_cli['json'] if isinstance(validate_cli['json'], dict) else {}
        self.obl['DRAFT-DELIVERY.author-validates-fixed-revision'] = {
            'transport': 'product-cli', 'exit_code': validate_cli['exit_code'],
            'valid': validated_cli.get('validation', {}).get('valid')}
        self.check(validate_cli['exit_code'] == 0
                   and validated_cli.get('validation', {}).get('valid') is True,
                   'fixed-revision CLI validation did not pass')

        # O19-O20 CAS append through the CLI.
        appended = self.cli('author appends an explicit new revision',
                            ['draft', 'update', draft_id,
                             str(self.work / 'updated.yaml'), '--expected-revision', '1'])
        updated_result = appended['json'] if isinstance(appended['json'], dict) else {}
        _, after_append, _ = self.request('head after append', 'GET', draft_path,
                                          token=self.author_token)
        _, revision_two, _ = self.request('revision 2 readable after append', 'GET',
                                          draft_path + '/revisions/2', mock_identity=False)
        self.obl['DRAFT-DELIVERY.author-appends-revision'] = {
            'transport': 'product-cli', 'exit_code': appended['exit_code'],
            'saved': updated_result.get('saved'), 'phase': updated_result.get('phase')}
        self.check(appended['exit_code'] == 0 and updated_result.get('saved') is True
                   and updated_result.get('phase') == 'ready',
                   'explicit revision append did not complete the ready phase')
        self.obl['DRAFT-DELIVERY.update-advances-exactly-once'] = {
            'transport': 'product-cli', 'revision': updated_result.get('revision'),
            'head_after': after_append.get('head_revision'),
            'latest_content_hash': revision_two.get('content_hash')}
        self.check(updated_result.get('revision') == 2
                   and after_append.get('head_revision') == 2
                   and revision_two.get('content_hash') == updated_hash,
                   'update did not advance exactly once to revision 2')

        # O21-O23 historical card reissue pinned to revision 1.
        reissued = self.cli('author reissues the historical card',
                            ['draft', 'preview', draft_id, '--revision', '1'])
        card = reissued['json'] if isinstance(reissued['json'], dict) else {}
        reference_cli = card.get('reference') if isinstance(card.get('reference'), dict) else {}
        _, reference_http, _ = self.request('historical card reference over HTTP', 'GET',
                                            draft_path + '/revisions/1/reference',
                                            token=self.author_token)
        _, other_reference, _ = self.request('revision 2 card reference over HTTP', 'GET',
                                             draft_path + '/revisions/2/reference',
                                             token=self.author_token)
        self.obl['DRAFT-DELIVERY.author-reissues-historical-card'] = {
            'transport': 'product-cli', 'exit_code': reissued['exit_code'],
            'phase': card.get('phase'), 'reference': {
                'draft_id': reference_cli.get('draftId'),
                'revision': reference_cli.get('revision'),
                'http_draft_id': reference_http.get('draftId'),
                'http_revision': reference_http.get('revision'),
                'card_token_field_present': isinstance(reference_http.get('readToken'), str),
                'card_token_field_length': len(reference_http.get('readToken') or '')}}
        self.check(reissued['exit_code'] == 0 and card.get('phase') == 'ready',
                   'historical card reissue did not complete')
        # bcs-cli Reference serializes camelCase (draftId/schemaVersion).
        self.check(reference_cli.get('draftId') == draft_id
                   and reference_cli.get('revision') == 1
                   and reference_http.get('draftId') == draft_id
                   and reference_http.get('revision') == 1
                   and isinstance(reference_http.get('readToken'), str)
                   and len(reference_http.get('readToken') or '') == 43,
                   'reissued historical card is not pinned to revision 1')
        self.obl['DRAFT-DELIVERY.historical-preview-original-bytes'] = {
            'transport': 'product-cli', 'size_bytes': card.get('size_bytes')}
        self.check(card.get('size_bytes') == FIXTURE_BYTES,
                   'historical preview did not return the original bytes')
        self.obl['DRAFT-DELIVERY.reissued-card-fixed-version'] = {
            'transport': 'product-cli', 'reference_revision': reference_cli.get('revision'),
            'reference_schema_version': reference_cli.get('schemaVersion'),
            'other_revision_card_revision': other_reference.get('revision')}
        self.check(reference_cli.get('revision') == 1
                   and reference_cli.get('schemaVersion') == 2
                   and other_reference.get('revision') == 2,
                   'reissued card does not preserve its fixed version')

        # O24 stale CAS writer is rejected without replacing the newer revision.
        _, stale, _ = self.request('stale writer is rejected', 'POST',
                                   draft_path + '/revisions', token=self.author_token,
                                   expected_status=409,
                                   body={'expected_revision': 1,
                                         'definition_yaml': 'different'})
        _, head_after_stale, _ = self.request('head after stale rejection', 'GET',
                                              draft_path, token=self.author_token)
        _, latest_after_stale, _ = self.request('revision 2 after stale rejection', 'GET',
                                                draft_path + '/revisions/2', mock_identity=False)
        self.obl['DRAFT-DELIVERY.stale-write-rejected'] = {
            'transport': 'product-http', 'status': 409, 'error': stale.get('error'),
            'head_after': head_after_stale.get('head_revision'),
            'latest_content_hash': latest_after_stale.get('content_hash')}
        self.check(stale.get('error') == 'DRAFT_CONFLICT',
                   'stale writer did not report DRAFT_CONFLICT')
        self.check(head_after_stale.get('head_revision') == 2
                   and latest_after_stale.get('content_hash') == updated_hash,
                   'stale writer replaced or renumbered the newer revision')

        # O25-O28 scoped revocation of exactly the second revision.
        revoked_once = self.cli('author revokes the second revision',
                                ['draft', 'revoke', draft_id, '--revision', '2'])
        revoked_twice = self.cli('repeated revocation is idempotent',
                                 ['draft', 'revoke', draft_id, '--revision', '2'])
        once = revoked_once['json'] if isinstance(revoked_once['json'], dict) else {}
        twice = revoked_twice['json'] if isinstance(revoked_twice['json'], dict) else {}
        _, revoked_read, _ = self.request('revoked card becomes unavailable', 'GET',
                                          draft_path + '/revisions/2', expected_status=404,
                                          mock_identity=False)
        _, sibling, sibling_cache = self.request('old shared revision remains readable', 'GET',
                                                 draft_path + '/revisions/1',
                                                 mock_identity=False)
        self.obl['DRAFT-DELIVERY.revoke-second-revision'] = {
            'transport': 'product-cli', 'exit_code': revoked_once['exit_code'],
            'revoked': once.get('revoked')}
        self.check(revoked_once['exit_code'] == 0 and once.get('revoked') is True,
                   'revocation of revision 2 did not complete')
        self.obl['DRAFT-DELIVERY.revoke-idempotent'] = {
            'transport': 'product-cli', 'exit_code': revoked_twice['exit_code'],
            'revoked': twice.get('revoked')}
        self.check(revoked_twice['exit_code'] == 0 and twice.get('revoked') is True,
                   'repeated revocation was not idempotent')
        self.obl['DRAFT-DELIVERY.revoked-card-unavailable'] = {
            'transport': 'product-http', 'status': 404, 'error': revoked_read.get('error')}
        self.check(revoked_read.get('error') == 'DRAFT_REFERENCE_UNAVAILABLE',
                   'revoked card leaked a different error')
        self.obl['DRAFT-DELIVERY.old-shared-revision-readable'] = {
            'transport': 'product-http', 'status': 200,
            'content_hash': sibling.get('content_hash'),
            'size_bytes': sibling.get('size_bytes'),
            'cache_control': sibling_cache}
        self.check(sibling.get('content_hash') == original_hash
                   and sibling.get('size_bytes') == FIXTURE_BYTES,
                   'revoking revision 2 changed revision 1')

        # O29-O31 tombstone deletion and the late-create guard.
        deleted = self.cli('author deletes the disposable Draft',
                           ['draft', 'delete', draft_id])
        deleted_result = deleted['json'] if isinstance(deleted['json'], dict) else {}
        _, gone_one, _ = self.request('deleted revision 1 unavailable', 'GET',
                                      draft_path + '/revisions/1', expected_status=404,
                                      mock_identity=False)
        _, gone_two, _ = self.request('deleted revision 2 unavailable', 'GET',
                                      draft_path + '/revisions/2', expected_status=404,
                                      mock_identity=False)
        tombstone_status, tombstone, _ = self.request(
            'Draft metadata after deletion', 'GET', draft_path,
            token=self.author_token, expected_status={200, 404})
        _, late, _ = self.request('late create cannot revive', 'PUT', draft_path,
                                  token=self.author_token, expected_status=404,
                                  body={'definition_yaml': source.decode('utf-8'),
                                        'title': title})
        self.obl['DRAFT-DELIVERY.author-deletes-draft'] = {
            'transport': 'product-cli', 'exit_code': deleted['exit_code'],
            'deleted': deleted_result.get('deleted')}
        self.check(deleted['exit_code'] == 0 and deleted_result.get('deleted') is True,
                   'disposable Draft deletion did not complete')
        self.obl['DRAFT-DELIVERY.deletion-revokes-all-cards'] = {
            'transport': 'product-http', 'revision_one_status': 404,
            'revision_two_status': 404, 'metadata_status': tombstone_status,
            'deleted_flag': tombstone.get('deleted')}
        self.check(gone_one.get('error') == 'DRAFT_REFERENCE_UNAVAILABLE'
                   and gone_two.get('error') == 'DRAFT_REFERENCE_UNAVAILABLE'
                   and (tombstone_status == 404 or tombstone.get('deleted') is True),
                   'deletion did not revoke every historical card')
        self.obl['DRAFT-DELIVERY.late-create-cannot-revive'] = {
            'transport': 'product-http', 'method': 'PUT', 'status': 404,
            'error': late.get('error')}
        self.check(late.get('error') == 'DRAFT_REFERENCE_UNAVAILABLE',
                   'late create revived a deleted Draft')

        # O32 journey-wide response hygiene.
        matrix = sorted({row['status'] for row in self.responses})
        self.obl['DRAFT-DELIVERY.journey-completes'] = {
            'transport': 'product-http',
            'responses_no_store': all(row.get('cache_control') == 'no-store'
                                      for row in self.responses),
            'status_matrix': matrix,
            'token_canary_free': all(
                canary not in json.dumps(row, sort_keys=True)
                for row in self.responses for canary in READ_TOKEN_CANARIES)}
        self.check(all(row.get('cache_control') == 'no-store' for row in self.responses),
                   'a Draft response omitted Cache-Control: no-store')
        self.check({200, 401, 404, 409}.issubset(set(matrix)),
                   'response matrix omitted success or error status coverage')
        self.check(all(canary not in json.dumps(row, sort_keys=True)
                       for row in self.responses for canary in READ_TOKEN_CANARIES),
                   'response observation captured token material')

        return {
            'schema_version': 2,
            'draft_id': draft_id,
            'identity_mode': self.identity_mode,
            'cookie_mode': ('duo_attached_after_presence_validation'
                            if self._human_cookie else 'not_attached'),
            'fixture': {'content_hash': original_hash, 'updated_content_hash': updated_hash,
                        'size_bytes': FIXTURE_BYTES},
            'author': {'uuid': self.author_uuid or None,
                       'resolved': bool(self.author_token)},
            'obligations': self.obl,
            'responses': self.responses,
            'public_v1_face': getattr(self, 'v1_face', None),
        }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--human', required=True)
    parser.add_argument('--fixture', required=True)
    parser.add_argument('--evidence-file', required=True)
    parser.add_argument('--cli-binary', required=True)
    parser.add_argument('--identity-mode', choices=('register', 'bearer'),
                        default='register')
    parser.add_argument('--bearer-token-env', default='')
    parser.add_argument('--cookie-env', default='')
    args = parser.parse_args(argv)
    work = Path.cwd() / 'draft-cli-work'
    if work.exists():
        raise StoryFailure('CLI work directory must be fresh')
    work.mkdir(mode=0o700)
    story = Story(args.base_url, args.human, Path(args.fixture), Path(args.cli_binary),
                  identity_mode=args.identity_mode, bearer_token_env=args.bearer_token_env,
                  cookie_env=args.cookie_env)
    story.work = work
    evidence = story.run()
    if story.checks != EXPECTED_CHECKS:
        raise StoryFailure('assertion count mismatch: expected ' + str(EXPECTED_CHECKS)
                           + ', observed ' + str(story.checks))
    payload = json.dumps(evidence, sort_keys=True, indent=2, allow_nan=False).encode('utf-8')
    if any(canary.encode('utf-8') in payload for canary in READ_TOKEN_CANARIES):
        raise StoryFailure('evidence redaction gate rejected credential-shaped content')
    evidence_path = Path(args.evidence_file)
    if evidence_path.exists() or evidence_path.is_symlink():
        raise StoryFailure('evidence file must be fresh')
    fd = os.open(evidence_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    print(MARKER_PREFIX + str(story.checks) + MARKER_SUFFIX, flush=True)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except StoryFailure as error:
        print('Draft delivery story failed: ' + str(error), file=sys.stderr, flush=True)
        raise SystemExit(1)
