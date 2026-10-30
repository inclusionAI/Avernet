#!/usr/bin/env python3
"""Black-box Bot-authority user journey for the dual owned-local/preprod slice.

One frozen 42-obligation journey replays the upstream shell story
(``apps/bcs/scripts/e2e-test/authority.sh``) over raw product HTTP:

- a product-CLI readiness gate runs first (bcs-cli ``--json health``); the
  frozen bcs-cli exposes no authority business verbs, so the CLI binary owns
  only this gate and never appears on a business path;
- the trusted registration plane mints one-time tokens under explicit mock
  identity headers and commits two disposable fixture Bots (owner and
  transfer target);
- every business call carries a freshly minted Gateway Principal HS256 JWT in
  ``X-Avernet-Principal`` only and must answer the frozen business envelope
  ``{code, message, request_id, data}`` (hard precondition, never counted);
- the manager single-item surface exercises the ``owner_not_manager`` and
  ``bot_authority_human_unknown`` polarities, the public ``bots/mine`` read
  ensures the mine-only human, and the renewal-window transfer chain moves
  ownership before both inboxes observe the accepted round.

The child emits only a fixed marker on stdout and writes a non-secret
semantic observation file for the runner; every register token, Bot token,
minted principal and signing-key value stays in memory and is scanned out of
the payload before the exclusive evidence write.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.parse
import uuid

EXPECTED_CHECKS = 42
MARKER_PREFIX = 'Bot authority journey: '
MARKER_SUFFIX = ' live HTTP assertions passed'
CLAIM_CANARIES = ('bot_token', 'X-Avernet-Principal', 'register?token', 'Bearer ')
CLI_TIMEOUT_SECONDS = 30
HTTP_TIMEOUT_SECONDS = 30
TRANSFER_WINDOW_MAX_RENEWAL_MS = 300000
DEFAULT_PRINCIPAL_SIGNING_KEY_ENV = 'AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE'
INTERNAL_PREFIX = '/api/v1/collaboration'
PUBLIC_PREFIX = '/openapi/v1/collaboration'
_TARGET_ACTOR = 'human_'
_BOT_ID = __import__('re').compile(r'^bot_[0-9a-f]{8}$')
_UUID = __import__('re').compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$')


class StoryFailure(AssertionError):
    pass


class Story:
    def __init__(self, base_url: str, owner_human: str, target_user_id: str, mine_user_id: str,
                 cli_binary: Path, *, signing_key_env: str, run_tag: str):
        self.base = urllib.parse.urlsplit(base_url.rstrip('/'))
        if self.base.scheme != 'http' or self.base.hostname != '127.0.0.1' or not self.base.port:
            raise StoryFailure('authority owned journey requires an owned loopback origin')
        self.owner_human = owner_human
        self.target_user_id = target_user_id
        self.mine_user_id = mine_user_id
        self.cli_binary = cli_binary
        self.signing_key_env = signing_key_env
        self.run_tag = run_tag
        self.checks = 0
        self.http_call_count = 0
        self.bootstrap_http_call_count = 0
        self.business_http_call_count = 0
        self.secrets: list[str] = []
        self.obl: dict[str, dict] = {}
        self.cli_readiness_gate: dict = {}
        self.registration_plane: dict = {}
        self.business_envelope: dict = {}
        self.transfer: dict = {}
        self.owner_bot_id = ''
        self.target_bot_id = ''

    # ---------------------------------------------------------------- primitives
    def check(self, condition: object, label: str) -> None:
        if not condition:
            raise StoryFailure(label)
        self.checks += 1

    def _secret(self, value: str, label: str) -> None:
        if not isinstance(value, str) or not value:
            raise StoryFailure(label + ': credential observation is missing')
        if value not in self.secrets:
            self.secrets.append(value)

    def _signing_key(self, label: str) -> str:
        value = os.environ.get(self.signing_key_env, '')
        if (not isinstance(value, str) or not value or not value.isascii()
                or any(ord(char) < 32 or ord(char) == 127 for char in value)):
            raise StoryFailure(label + ': principal signing key environment reference is unsafe')
        self._secret(value, label)
        return value

    def _principal(self, user_id: str, label: str) -> str:
        now = int(time.time())
        header = {'alg': 'HS256', 'kid': 'bare', 'typ': 'JWT'}
        claims = {'iss': 'gateway', 'aud': 'bcs', 'iat': now, 'exp': now + 60,
                  'principals': [{'type': 'user', 'tenant': 'bcs-e2e',
                                  'subject': {'id': user_id, 'username': user_id,
                                              'display_name': None, 'full_name': None,
                                              'tenant_id': 'bcs-e2e'}}]}

        def encode(value: dict) -> str:
            return base64.urlsafe_b64encode(
                json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
            ).rstrip(b'=').decode('ascii')

        signing_input = (encode(header) + '.' + encode(claims)).encode('ascii')
        digest = hmac.new(self._signing_key(label).encode(), signing_input,
                          hashlib.sha256).digest()
        token = signing_input.decode('ascii') + '.' + (
            base64.urlsafe_b64encode(digest).rstrip(b'=').decode('ascii'))
        self._secret(token, label)
        return token

    def _headers(self, actor: str) -> dict[str, str]:
        if actor == 'register-owner':
            return {'X-Mock-User-Id': self.owner_human, 'X-Mock-Nick-Name': self.owner_human}
        if actor == 'register-target':
            return {'X-Mock-User-Id': self.target_user_id, 'X-Mock-Nick-Name': self.target_user_id}
        if actor == 'owner':
            return {'X-Avernet-Principal': self._principal(self.owner_human, 'owner principal'),
                    'Content-Type': 'application/json'}
        if actor == 'target':
            return {'X-Avernet-Principal': self._principal(self.target_user_id, 'target principal'),
                    'Content-Type': 'application/json'}
        if actor == 'mine':
            return {'X-Avernet-Principal': self._principal(self.mine_user_id, 'mine principal'),
                    'Content-Type': 'application/json'}
        raise StoryFailure('unknown authority actor: ' + actor)

    def _business_envelope(self, label: str, value: object) -> dict:
        if not isinstance(value, dict):
            raise StoryFailure(label + ': business response envelope is not an object')
        keys = set(value)
        if keys != {'code', 'message', 'request_id', 'data'}:
            raise StoryFailure(label + ': business response envelope keys differ from the frozen map')
        if type(value['code']) is not int or not isinstance(value['message'], str) \
                or not isinstance(value['request_id'], str) \
                or not isinstance(value['data'], (dict, list)):
            raise StoryFailure(label + ': business response envelope shape differs from the frozen map')
        return value

    def request(self, label: str, method: str, path: str, *, body=None, actor: str,
                expected_status=200, plane: str = 'business') -> tuple[int, dict, bytes]:
        connection = (http.client.HTTPSConnection if self.base.scheme == 'https'
                      else http.client.HTTPConnection)(self.base.netloc, timeout=HTTP_TIMEOUT_SECONDS)
        payload = None if body is None else json.dumps(body).encode('utf-8')
        try:
            connection.putrequest(method, self.base.path + path)
            for name, value in self._headers(actor).items():
                connection.putheader(name, value)
            if payload is not None:
                connection.putheader('Content-Length', str(len(payload)))
            connection.endheaders(payload)
            response = connection.getresponse()
            raw = response.read()
            status = response.status
        except (OSError, http.client.HTTPException) as error:
            raise StoryFailure(label + ': HTTP transport failed: ' + type(error).__name__) from None
        finally:
            connection.close()
        if plane == 'bootstrap':
            self.bootstrap_http_call_count += 1
        else:
            self.business_http_call_count += 1
            value = self._business_envelope(label, json.loads(raw) if raw else None)
        self.http_call_count += 1
        acceptable = expected_status if isinstance(expected_status, (set, tuple, frozenset)) else {expected_status}
        if status not in acceptable:
            raise StoryFailure(label + ': unexpected HTTP status ' + str(status))
        return status, (value if plane != 'bootstrap' else json.loads(raw) if raw else {}), raw

    # ------------------------------------------------------------------- journey
    def _cli_readiness_gate(self) -> None:
        if not isinstance(self.cli_binary, Path) or not self.cli_binary.name:
            raise StoryFailure('authority readiness gate: CLI binary path is invalid')
        environment = dict(os.environ)
        environment['MOLTIS_BCS_URL'] = self.base.scheme + '://' + self.base.netloc
        try:
            done = subprocess.run([str(self.cli_binary), '--json', 'health'], env=environment,
                                  stdin=subprocess.DEVNULL, capture_output=True,
                                  timeout=CLI_TIMEOUT_SECONDS)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise StoryFailure('authority readiness gate: CLI invocation failed: '
                               + type(error).__name__) from None
        stdout_text = done.stdout.decode('utf-8', errors='replace')
        parsed = None
        try:
            parsed = json.loads(stdout_text) if stdout_text.strip() else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            parsed = None
        health_status = parsed.get('status') if isinstance(parsed, dict) else None
        self.cli_readiness_gate = {'exit_code': done.returncode,
                                   'health_status': health_status,
                                   'transport': 'product-cli', 'role': 'readiness-gate-only',
                                   'authority_business_verbs': 'none-frozen'}
        if done.returncode != 0 or health_status != 'healthy':
            raise StoryFailure('authority readiness gate: product CLI did not report a healthy BCS')

    def _register(self, label: str, actor: str, name: str) -> tuple[str, str]:
        _, minted, _ = self.request(label + ' register token mint', 'GET', '/register/token',
                                    actor=actor, plane='bootstrap')
        token = minted.get('token')
        if not isinstance(token, str) or not token:
            raise StoryFailure(label + ': registration token minting differs')
        self._secret(token, label + ' register token')
        query = urllib.parse.urlencode({'token': token, 'bot-name': name})
        _, registered, _ = self.request(label + ' trusted registration', 'POST',
                                        '/register?' + query, actor=actor, plane='bootstrap')
        bot_id = registered.get('bot_uuid')
        bot_token = registered.get('bot_token')
        if not isinstance(bot_id, str) or not bot_id:
            raise StoryFailure(label + ': trusted registration committed no Bot id')
        if not isinstance(bot_token, str) or not bot_token:
            raise StoryFailure(label + ': trusted registration committed no Bot token')
        self._secret(bot_token, label + ' bot token')
        return bot_id, bot_token

    def _bootstrap(self) -> None:
        owner_tag = (self.run_tag or 'authority')[:20]
        target_tag = owner_tag
        owner_name = 'authority-story-owner-' + owner_tag
        target_name = 'authority-story-target-' + target_tag
        for name in (owner_name, target_name):
            if not (2 <= len(name) <= 64 and name.isascii()
                    and all(33 <= ord(char) < 127 for char in name)):
                raise StoryFailure('authority Bot name is not a bounded ASCII identifier')
        self.owner_bot_id, _ = self._register('owner', 'register-owner', owner_name)
        self.target_bot_id, _ = self._register('target', 'register-target', target_name)
        if not (_BOT_ID.fullmatch(self.owner_bot_id) or _UUID.fullmatch(self.owner_bot_id)):
            raise StoryFailure('owner fixture Bot id shape differs from the committed registry')
        if not (_BOT_ID.fullmatch(self.target_bot_id) or _UUID.fullmatch(self.target_bot_id)):
            raise StoryFailure('target fixture Bot id shape differs from the committed registry')
        if self.owner_bot_id == self.target_bot_id:
            raise StoryFailure('trusted registration committed two distinct disposable Bots')
        self.registration_plane = {'mock_admitted': True, 'bots_registered': 2,
                                   'distinct_bot_ids': True}
        self.business_envelope = {'enforced': True,
                                  'observed_keys': ['code', 'message', 'request_id', 'data']}

    def _ownership_path(self, bot_id: str) -> str:
        return INTERNAL_PREFIX + '/bots/' + bot_id + '/ownership'

    def _managers_path(self, bot_id: str, user_id: str = '') -> str:
        path = INTERNAL_PREFIX + '/bots/' + bot_id + '/managers'
        return path + '/' + user_id if user_id else path

    def run(self) -> dict:
        self._cli_readiness_gate()
        self._bootstrap()
        owner_bot = self.owner_bot_id
        transfer_path = (INTERNAL_PREFIX + '/bots/' + owner_bot
                         + '/ownership-transfers/' + _TARGET_ACTOR + self.target_user_id)
        never_user_id = 'never-seen-' + uuid.uuid4().hex[:12]

        # O1-O3 ownership view: frozen status, bot identity, registered owner.
        status, value, _ = self.request('owner reads the collaboration ownership view', 'GET',
                                        self._ownership_path(owner_bot), actor='owner')
        self.check(status == 200, 'owner-ownership-read-status: ownership view answers the frozen status')
        self.obl['AUTHORITY-JOURNEY.owner-ownership-read-status'] = {
            'transport': 'product-http', 'method': 'GET', 'status': status}
        data = value['data']
        self.check(data.get('bot_id') == owner_bot, 'ownership keeps the bot id')
        self.obl['AUTHORITY-JOURNEY.ownership-keeps-bot-id'] = {
            'transport': 'product-http', 'bot_id': data.get('bot_id')}
        self.check(data.get('owner_user_id') == self.owner_human,
                   'ownership identifies the registered owner')
        self.obl['AUTHORITY-JOURNEY.ownership-identifies-registered-owner'] = {
            'transport': 'product-http', 'owner_user_id': data.get('owner_user_id')}

        # O4-O5 empty manager page.
        status, value, _ = self.request('owner reads an empty manager page', 'GET',
                                        self._managers_path(owner_bot), actor='owner')
        self.check(status == 200, 'manager-page-empty-status: manager page answers the frozen status')
        self.obl['AUTHORITY-JOURNEY.manager-page-empty-status'] = {
            'transport': 'product-http', 'status': status}
        data = value['data']
        self.check(data.get('total') == 0, 'manager page starts empty')
        self.obl['AUTHORITY-JOURNEY.manager-page-starts-empty'] = {
            'transport': 'product-http', 'total': data.get('total')}

        # O6-O7 owner-as-manager polarity.
        status, value, _ = self.request('adding the owner as a manager is rejected', 'PUT',
                                        self._managers_path(owner_bot, self.owner_human), actor='owner',
                                        expected_status=409)
        self.check(status == 409, 'owner-manager-add-rejected-status: owner add answers the frozen polarity status')
        self.obl['AUTHORITY-JOURNEY.owner-manager-add-rejected-status'] = {
            'transport': 'product-http', 'status': status}
        data = value['data']
        self.check(data.get('error_code') == 'owner_not_manager',
                   'owner add uses the stable error code')
        self.obl['AUTHORITY-JOURNEY.owner-manager-add-error-code'] = {
            'transport': 'product-http', 'error_code': data.get('error_code')}

        # O8-O12 add, list, remove, idempotent remove, restore.
        status, value, _ = self.request('owner adds the target human as a manager', 'PUT',
                                        self._managers_path(owner_bot, self.target_user_id),
                                        actor='owner')
        self.check(status == 200, 'manager-add-status: manager add answers the frozen status')
        self.obl['AUTHORITY-JOURNEY.manager-add-status'] = {
            'transport': 'product-http', 'status': status}
        data = value['data']
        self.check(data.get('added') == 1, 'manager add reports one added')
        self.obl['AUTHORITY-JOURNEY.manager-add-added-one'] = {
            'transport': 'product-http', 'added': data.get('added')}

        status, value, _ = self.request('owner reads the populated manager page', 'GET',
                                        self._managers_path(owner_bot), actor='owner')
        self.check(status == 200, 'manager-page-populated-status: manager page answers the frozen status')
        self.obl['AUTHORITY-JOURNEY.manager-page-populated-status'] = {
            'transport': 'product-http', 'status': status}
        data = value['data']
        self.check(data.get('total') == 1, 'manager page keeps the manager')
        self.obl['AUTHORITY-JOURNEY.manager-page-keeps-manager'] = {
            'transport': 'product-http', 'total': data.get('total')}
        items = data.get('items')
        self.check(isinstance(items, list) and self.target_user_id in items,
                   'manager page exposes the added manager')
        self.obl['AUTHORITY-JOURNEY.manager-page-exposes-added-manager'] = {
            'transport': 'product-http', 'items': list(items) if isinstance(items, list) else None,
            'target_user_id_present': isinstance(items, list)
            and self.target_user_id in items}

        status, value, _ = self.request('owner removes the manager', 'DELETE',
                                        self._managers_path(owner_bot, self.target_user_id),
                                        actor='owner')
        self.check(status == 200, 'manager-remove-status: manager removal answers the frozen status')
        self.obl['AUTHORITY-JOURNEY.manager-remove-status'] = {
            'transport': 'product-http', 'status': status}
        data = value['data']
        self.check(data.get('removed') == 1, 'manager removal reports one removed')
        self.obl['AUTHORITY-JOURNEY.manager-remove-removed-one'] = {
            'transport': 'product-http', 'removed': data.get('removed')}

        status, value, _ = self.request('manager removal is idempotent for absent', 'DELETE',
                                        self._managers_path(owner_bot, self.target_user_id),
                                        actor='owner')
        self.check(status == 200, 'manager-absent-remove-status: absent removal answers the frozen status')
        self.obl['AUTHORITY-JOURNEY.manager-absent-remove-status'] = {
            'transport': 'product-http', 'status': status}
        data = value['data']
        self.check(data.get('removed') == 0, 'absent manager removal reports no change')
        self.obl['AUTHORITY-JOURNEY.manager-absent-remove-removed-zero'] = {
            'transport': 'product-http', 'removed': data.get('removed')}

        status, value, _ = self.request('manager is restored before the transfer', 'PUT',
                                        self._managers_path(owner_bot, self.target_user_id),
                                        actor='owner')
        self.check(status == 200, 'manager-restore-status: manager restore answers the frozen status')
        self.obl['AUTHORITY-JOURNEY.manager-restore-status'] = {
            'transport': 'product-http', 'status': status}

        # O18 public mine read ensures the mine-only human.
        public_mine = PUBLIC_PREFIX + '/bots/mine?' + urllib.parse.urlencode({'limit': '20'})
        status, value, _ = self.request('mine-ensured human reads its own mine', 'GET',
                                        public_mine, actor='mine')
        self.check(status == 200, 'mine-ensured-human-mine-status: public mine read answers the frozen status')
        self.obl['AUTHORITY-JOURNEY.mine-ensured-human-mine-status'] = {
            'transport': 'product-http', 'api_face': 'public-v1', 'status': status}

        # O19-O20 mine-ensured add.
        status, value, _ = self.request('mine-ensured human is a valid manager add target', 'PUT',
                                        self._managers_path(owner_bot, self.mine_user_id),
                                        actor='owner')
        self.check(status == 200, 'mine-manager-add-status: mine-ensured add answers the frozen status')
        self.obl['AUTHORITY-JOURNEY.mine-manager-add-status'] = {
            'transport': 'product-http', 'status': status}
        data = value['data']
        self.check(data.get('added') == 1, 'mine-ensured manager add reports one added')
        self.obl['AUTHORITY-JOURNEY.mine-manager-add-added-one'] = {
            'transport': 'product-http', 'added': data.get('added')}

        # O21-O22 never-seen polarity.
        status, value, _ = self.request('never-seen human is not a manager add target', 'PUT',
                                        self._managers_path(owner_bot, never_user_id), actor='owner',
                                        expected_status=400)
        self.check(status == 400, 'never-seen-manager-add-status: never-seen add answers the frozen polarity status')
        self.obl['AUTHORITY-JOURNEY.never-seen-manager-add-status'] = {
            'transport': 'product-http', 'status': status}
        data = value['data']
        self.check(data.get('error_code') == 'bot_authority_human_unknown',
                   'unknown manager add uses the stable error code')
        self.obl['AUTHORITY-JOURNEY.never-seen-manager-error-code'] = {
            'transport': 'product-http', 'error_code': data.get('error_code')}

        # O23-O24 remove the mine-ensured manager.
        status, value, _ = self.request('mine-ensured manager is removed before the transfer',
                                        'DELETE', self._managers_path(owner_bot, self.mine_user_id),
                                        actor='owner')
        self.check(status == 200, 'mine-manager-remove-status: mine-ensured removal answers the frozen status')
        self.obl['AUTHORITY-JOURNEY.mine-manager-remove-status'] = {
            'transport': 'product-http', 'status': status}
        data = value['data']
        self.check(data.get('removed') == 1, 'mine-ensured manager removal reports one removed')
        self.obl['AUTHORITY-JOURNEY.mine-manager-remove-removed-one'] = {
            'transport': 'product-http', 'removed': data.get('removed')}

        # O25-O29 transfer initiate and renewal refresh.
        status, value, _ = self.request('owner initiates the ownership transfer', 'PUT',
                                        transfer_path, actor='owner', expected_status=201)
        self.check(status == 201, 'transfer-initiate-status: transfer initiation answers the frozen status')
        self.obl['AUTHORITY-JOURNEY.transfer-initiate-status'] = {
            'transport': 'product-http', 'status': status}
        data = value['data']
        expires_at = data.get('expires_at')
        self.check(data.get('status') == 'pending', 'initiated transfer is pending')
        self.obl['AUTHORITY-JOURNEY.transfer-initiate-pending'] = {
            'transport': 'product-http', 'transfer_status': data.get('status')}
        self.check(data.get('target_owner_user_id') == self.target_user_id,
                   'initiated transfer names the target')
        self.obl['AUTHORITY-JOURNEY.transfer-initiate-target-owner'] = {
            'transport': 'product-http', 'target_owner_user_id': data.get('target_owner_user_id')}

        status, value, _ = self.request('transfer refresh reuses the pending round', 'PUT',
                                        transfer_path, actor='owner')
        self.check(status == 200, 'transfer-refresh-status: transfer refresh answers the frozen status')
        self.obl['AUTHORITY-JOURNEY.transfer-refresh-status'] = {
            'transport': 'product-http', 'status': status}
        data = value['data']
        refreshed_expires = data.get('expires_at')
        renewed = (type(expires_at) is int and type(refreshed_expires) is int
                   and refreshed_expires >= expires_at
                   and refreshed_expires - expires_at < TRANSFER_WINDOW_MAX_RENEWAL_MS)
        self.check(renewed, 'transfer refresh renews the window without shrinking it')
        self.obl['AUTHORITY-JOURNEY.transfer-refresh-window-renewed'] = {
            'transport': 'product-http',
            'initiated_expires_at': expires_at if type(expires_at) is int else None,
            'refreshed_expires_at': refreshed_expires if type(refreshed_expires) is int else None,
            'delta_ms': (refreshed_expires - expires_at
                         if type(refreshed_expires) is int and type(expires_at) is int else None),
            'renewed': renewed}

        # O30-O33 target reads and accepts.
        status, value, _ = self.request('target human reads the pending transfer', 'GET',
                                        transfer_path, actor='target')
        self.check(status == 200, 'transfer-target-read-status: target read answers the frozen status')
        self.obl['AUTHORITY-JOURNEY.transfer-target-read-status'] = {
            'transport': 'product-http', 'status': status}
        data = value['data']
        self.check(data.get('status') == 'pending', 'target reads a pending transfer')
        self.obl['AUTHORITY-JOURNEY.transfer-target-read-pending'] = {
            'transport': 'product-http', 'transfer_status': data.get('status')}

        status, value, _ = self.request('target human accepts the ownership transfer', 'POST',
                                        transfer_path + '/accept', actor='target')
        self.check(status == 200, 'transfer-accept-status: transfer accept answers the frozen status')
        self.obl['AUTHORITY-JOURNEY.transfer-accept-status'] = {
            'transport': 'product-http', 'status': status}
        data = value['data']
        self.check(data.get('status') == 'accepted', 'accept records the accepted status')
        self.obl['AUTHORITY-JOURNEY.transfer-accept-accepted'] = {
            'transport': 'product-http', 'transfer_status': data.get('status')}
        self.transfer = {'initiated_expires_at': expires_at if type(expires_at) is int else None,
                         'refreshed_expires_at': refreshed_expires
                         if type(refreshed_expires) is int else None,
                         'delta_ms': (refreshed_expires - expires_at
                                      if type(refreshed_expires) is int and type(expires_at) is int
                                      else None),
                         'renewed': renewed}

        # O34-O35 transferred ownership.
        status, value, _ = self.request('the new owner reads the collaboration ownership view',
                                        'GET', self._ownership_path(owner_bot), actor='target')
        self.check(status == 200, 'transferred-ownership-status: transferred ownership answers the frozen status')
        self.obl['AUTHORITY-JOURNEY.transferred-ownership-status'] = {
            'transport': 'product-http', 'status': status}
        data = value['data']
        self.check(data.get('owner_user_id') == self.target_user_id,
                   'ownership transfer moved the owner')
        self.obl['AUTHORITY-JOURNEY.transferred-ownership-owner-moved'] = {
            'transport': 'product-http', 'owner_user_id': data.get('owner_user_id')}

        # O36-O38 transferred manager ledger.
        status, value, _ = self.request('the new owner reads the transferred manager ledger',
                                        'GET', self._managers_path(owner_bot), actor='target')
        self.check(status == 200, 'transferred-ledger-status: transferred ledger answers the frozen status')
        self.obl['AUTHORITY-JOURNEY.transferred-ledger-status'] = {
            'transport': 'product-http', 'status': status}
        data = value['data']
        ledger_owner = data.get('owner')
        self.check(isinstance(ledger_owner, dict)
                   and ledger_owner.get('owner_user_id') == self.target_user_id,
                   'the transferred owner is in the ledger')
        self.obl['AUTHORITY-JOURNEY.transferred-ledger-owner-names-target'] = {
            'transport': 'product-http', 'ledger_owner_user_id':
                ledger_owner.get('owner_user_id') if isinstance(ledger_owner, dict) else None}
        items = data.get('items')
        self.check(isinstance(items, list) and self.owner_human in items,
                   'the original owner is now a plain manager')
        self.obl['AUTHORITY-JOURNEY.transferred-ledger-original-owner-plain-manager'] = {
            'transport': 'product-http', 'items': list(items) if isinstance(items, list) else None,
            'original_owner_present': isinstance(items, list) and self.owner_human in items}

        # O39-O42 both transfer inboxes.
        received_path = INTERNAL_PREFIX + '/ownership-transfers?' + urllib.parse.urlencode(
            {'direction': 'received'})
        status, _, received_raw = self.request('the new owner lists received transfers', 'GET',
                                               received_path, actor='target')
        self.check(status == 200, 'received-inbox-status: received inbox answers the frozen status')
        self.obl['AUTHORITY-JOURNEY.received-inbox-status'] = {
            'transport': 'product-http', 'status': status}
        self.check(owner_bot.encode('utf-8') in received_raw,
                   'received inbox contains the accepted bot')
        self.obl['AUTHORITY-JOURNEY.received-inbox-contains-bot'] = {
            'transport': 'product-http', 'contains_bot': True, 'bot_id_seen': owner_bot}

        sent_path = INTERNAL_PREFIX + '/ownership-transfers?' + urllib.parse.urlencode(
            {'direction': 'sent'})
        status, _, sent_raw = self.request('the ex-owner lists sent transfers', 'GET',
                                           sent_path, actor='owner')
        self.check(status == 200, 'sent-inbox-status: sent outbox answers the frozen status')
        self.obl['AUTHORITY-JOURNEY.sent-inbox-status'] = {
            'transport': 'product-http', 'status': status}
        self.check(owner_bot.encode('utf-8') in sent_raw,
                   'sent outbox contains the accepted bot')
        self.obl['AUTHORITY-JOURNEY.sent-inbox-contains-bot'] = {
            'transport': 'product-http', 'contains_bot': True, 'bot_id_seen': owner_bot}

        return {
            'schema_version': 1,
            'case_id': 'LOCAL-AUTHORITY-JOURNEY-02',
            'identity_mode': 'register',
            'owner': {'human_id': self.owner_human, 'bot_id': self.owner_bot_id},
            'target': {'user_id': self.target_user_id, 'bot_id': self.target_bot_id},
            'mine': {'user_id': self.mine_user_id},
            'never_user_id': never_user_id,
            'cli_readiness_gate': self.cli_readiness_gate,
            'registration_plane': self.registration_plane,
            'business_envelope': self.business_envelope,
            'http_call_count': self.http_call_count,
            'bootstrap_http_call_count': self.bootstrap_http_call_count,
            'business_http_call_count': self.business_http_call_count,
            'transfer': self.transfer,
            'resource_disposition': 'AUTHORITY_TWO_BOTS_CREATED_AND_RETAINED',
            'obligations': self.obl,
        }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--owner-human', required=True)
    parser.add_argument('--target-user-id', required=True)
    parser.add_argument('--mine-user-id', required=True)
    parser.add_argument('--evidence-file', required=True)
    parser.add_argument('--cli-binary', required=True)
    parser.add_argument('--principal-signing-key-env',
                        default=DEFAULT_PRINCIPAL_SIGNING_KEY_ENV)
    parser.add_argument('--run-tag', required=True)
    args = parser.parse_args(argv)
    story = Story(args.base_url, args.owner_human, args.target_user_id, args.mine_user_id,
                  Path(args.cli_binary), signing_key_env=args.principal_signing_key_env,
                  run_tag=args.run_tag)
    evidence = story.run()
    if story.checks != EXPECTED_CHECKS:
        raise StoryFailure('assertion count mismatch: expected ' + str(EXPECTED_CHECKS)
                           + ', observed ' + str(story.checks))
    payload = json.dumps(evidence, sort_keys=True, indent=2, allow_nan=False).encode('utf-8')
    for canary in CLAIM_CANARIES:
        if canary.encode('utf-8') in payload:
            raise StoryFailure('evidence redaction gate rejected credential-shaped content')
    for secret in story.secrets:
        if secret.encode('utf-8') in payload:
            raise StoryFailure('evidence redaction gate rejected retained journey secret')
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
        print('Bot authority journey failed: ' + str(error), file=sys.stderr, flush=True)
        raise SystemExit(1)
