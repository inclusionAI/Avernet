"""One-lifecycle local Bot credentials and fixed, read-only identity proof.

No raw-token accessor, user-supplied credentials, remote origin, arbitrary request
or Human-identity fallback is exposed. Clearing references is not secure erasure
or server-side token revocation.
"""
from __future__ import annotations

import json
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, ProxyHandler, build_opener

from .profile import fingerprint, identifier, unique_keys
from .run_state import read
from .transport import NoRedirect


class LocalRoleIdentityError(RuntimeError):
    pass


class OwnedLocalRoleIdentity:
    def __init__(self, fixtures, *, roles: tuple[str, ...]):
        from .local_fixtures import OwnedLocalFixtures
        if type(fixtures) is not OwnedLocalFixtures or fixtures.fixture_set == 'pilot':
            raise LocalRoleIdentityError('role credentials require an explicit expanded local fixture set')
        if (type(roles) is not tuple or not roles
                or any(not isinstance(role, str) or role not in fixtures.roles for role in roles)
                or len(set(roles)) != len(roles)):
            raise LocalRoleIdentityError('select distinct roles from the bound local fixture set')
        fixtures._require_owned()
        self.fixtures, self.roles = fixtures, roles
        self.runtime = fixtures.runtime
        self.run_id, self.root = self.runtime.run_id, self.runtime.root
        self.origin, self.human = self.runtime.inputs.origin, self.runtime.inputs.human_id
        self.fixture_set, self.config_digest = fixtures.fixture_set, fixtures.config_digest
        self._state, self._begun, self._receipt_digest = 'NEW', False, ''
        self._entries = {}
        self._attempted = set()
        self._verified = []
        self._baseline_attempted = False
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def __enter__(self):
        if self._state != 'NEW':
            raise LocalRoleIdentityError('role credential lifecycle cannot be entered again')
        self._require_runtime()
        self._state = 'CAPTURING'
        return self

    def __exit__(self, *_args):
        self.close()

    def close(self):
        self._entries.clear()
        self._receipt_digest = ''
        self._state = 'CLOSED'

    def summary(self) -> dict:
        return {'state': self._state, 'selected_roles': list(self.roles),
                'verified_roles': list(self._verified),
                'credential_memory': ('CLEARED' if self._state == 'CLOSED' else
                                      'HELD' if self._entries else 'NOT_CAPTURED'),
                'memory_cleanup_scope': 'HOLDER_REFERENCES_ONLY',
                'secure_erasure': False, 'remote_authentication': 'NOT_TESTED'}

    def _require_runtime(self):
        self.fixtures._require_owned()
        if (self.runtime.run_id != self.run_id or self.runtime.root != self.root
                or self.runtime.inputs.origin != self.origin or self.runtime.inputs.human_id != self.human
                or self.fixtures.fixture_set != self.fixture_set or self.fixtures.config_digest != self.config_digest):
            raise LocalRoleIdentityError('role identity runtime binding changed')

    def _begin(self, fixtures):
        if fixtures is not self.fixtures or self._state != 'CAPTURING' or self._begun:
            raise LocalRoleIdentityError('credentials must bind one fresh fixture initialization')
        self._require_runtime()
        self._begun = True

    def _capture(self, fixtures, role: str, bot_id: str, token: object):
        if fixtures is not self.fixtures or self._state != 'CAPTURING' or not self._begun:
            raise LocalRoleIdentityError('credential capture is outside its initialization lifecycle')
        self._require_runtime()
        if role not in self.roles:
            return
        if (role in self._entries or any(bot_id == value[0] for value in self._entries.values())
                or not isinstance(token, str) or not 1 <= len(token) <= 4096
                or not token.isascii() or any(ord(c) <= 32 or ord(c) == 127 for c in token)):
            raise LocalRoleIdentityError('selected role credential is missing, invalid or duplicated')
        self._entries[role] = (identifier(bot_id, 'local role Bot ID'), token)

    def _seal(self, fixtures):
        if fixtures is not self.fixtures or self._state != 'CAPTURING' or not self._begun:
            raise LocalRoleIdentityError('role credential capture is not active')
        self._require_runtime()
        verified = fixtures.check()
        if (set(self._entries) != set(self.roles)
                or any(verified['roles'][role]['bot_id'] != self._entries[role][0] for role in self.roles)):
            raise LocalRoleIdentityError('captured credentials do not match all selected verified roles')
        self._receipt_digest = fingerprint(read(fixtures.path))
        self._state = 'READY'

    def _require_bound(self, role: str):
        self._require_roles((role,))

    def _require_roles(self, roles: tuple[str, ...]):
        if (self._state != 'READY' or not roles
                or any(role not in self.roles or role not in self._entries for role in roles)):
            raise LocalRoleIdentityError('role identity is not ready or selected')
        self._require_runtime()
        receipt = read(self.fixtures.path)
        if (fingerprint(receipt) != self._receipt_digest
                or any(receipt['roles'][role]['bot_id'] != self._entries[role][0] for role in roles)):
            raise LocalRoleIdentityError('role fixture receipt or captured Bot binding changed')

    def _request(self, operation: str, bot_id: str, token: str) -> dict:
        paths = {'visibility': '/bots/' + quote(bot_id, safe='') + '/visibility',
                 'friends': '/bots/' + quote(bot_id, safe='') + '/friends',
                 'pending-sent': '/friends/requests?direction=sent&status=pending',
                 'pending-received': '/friends/requests?direction=received&status=pending',
                 'accepted-sent': '/friends/requests?direction=sent&status=accepted',
                 'accepted-received': '/friends/requests?direction=received&status=accepted',
                 'rejected-sent': '/friends/requests?direction=sent&status=rejected',
                 'rejected-received': '/friends/requests?direction=received&status=rejected'}
        if operation not in paths:
            raise LocalRoleIdentityError('unreviewed role identity operation')
        # No bot_uuid query selector: pending requests must resolve the caller
        # from the token, never from the local Human ownership fallback.
        request = Request(self.origin + paths[operation],
                          method='GET', headers={'Accept': 'application/json', 'Authorization': 'Bearer ' + token})
        try:
            with self.opener.open(request, timeout=5) as response:
                if response.status != 200:
                    raise LocalRoleIdentityError('local Bot identity read was not confirmed')
                raw = response.read(65_537)
            if len(raw) > 65_536:
                raise LocalRoleIdentityError('local Bot identity response exceeds its budget')
            value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_keys)
            if not isinstance(value, dict) or value.get('success') is not True:
                raise LocalRoleIdentityError('local Bot identity envelope is invalid')
            return value
        except HTTPError as error:
            error.close()
            raise LocalRoleIdentityError('local Bot identity authorization or route was rejected') from None
        except LocalRoleIdentityError:
            raise
        except Exception:
            raise LocalRoleIdentityError('local Bot identity transport or response failed') from None

    def start_session_state_machine_run(
            self, role: str, *, session_id: str, definition_yaml: str,
            participant_bindings: dict, input: dict) -> dict:
        """Start exactly one session-scoped state-machine Run as one Bot.

        This is the single purpose-bound authenticated Bot write the
        fixed-Loop case needs: the session-scoped Run start route
        authenticates the calling Bot, while every other state-machine
        route accepts the ambient local Human. The Bot credential is
        consumed inside this module and never returned, logged or
        exposed; the method, path template and body shape are fixed.
        """
        self._require_roles((role,))
        if (not isinstance(session_id, str) or not session_id
                or len(session_id) > 512
                or any(ord(character) < 0x21 or ord(character) > 0x7e
                       for character in session_id)):
            raise LocalRoleIdentityError('local session ID is missing or invalid')
        if (not isinstance(definition_yaml, str) or not definition_yaml
                or len(definition_yaml) > 1_048_576):
            raise LocalRoleIdentityError('local collaboration definition is missing or oversized')
        if (not isinstance(participant_bindings, dict) or not participant_bindings
                or len(participant_bindings) > 64
                or any(not isinstance(binding, dict)
                       or not isinstance(binding.get('source'), str) or not binding['source']
                       or not isinstance(binding.get('bot_ids'), list) or not binding['bot_ids']
                       or any(not isinstance(item, str) or not item
                              for item in binding['bot_ids'])
                       for binding in participant_bindings.values())):
            raise LocalRoleIdentityError('local participant bindings are missing or invalid')
        if not isinstance(input, dict):
            raise LocalRoleIdentityError('local state-machine Run input must be an object')
        bot_id, token = self._entries[role]
        path = '/sessions/' + quote(session_id, safe='') + '/state-machine-runs'
        payload = json.dumps({'definition_yaml': definition_yaml,
                              'participant_bindings': participant_bindings,
                              'input': input},
                             allow_nan=False, separators=(',', ':')).encode('utf-8')
        if len(payload) > 1_048_576:
            raise LocalRoleIdentityError('local state-machine Run request exceeds its byte budget')
        request = Request(self.origin + path, method='POST', data=payload,
                          headers={'Accept': 'application/json',
                                   'Content-Type': 'application/json',
                                   'Authorization': 'Bearer ' + token})
        try:
            with self.opener.open(request, timeout=15) as response:
                if response.status != 202:
                    raise LocalRoleIdentityError('local session state-machine Run start was not accepted')
                raw = response.read(65_537)
            if len(raw) > 65_536:
                raise LocalRoleIdentityError('local session state-machine Run response exceeds its budget')
            value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_keys)
            run = value.get('run') if isinstance(value, dict) else None
            if (not isinstance(value, dict) or not isinstance(run, dict)
                    or not isinstance(run.get('run_id'), str) or not run['run_id']
                    or not isinstance(run.get('session_id'), str) or run['session_id'] != session_id):
                raise LocalRoleIdentityError('local session state-machine Run view is invalid')
            return value
        except HTTPError:
            raise LocalRoleIdentityError('local session state-machine Run start was rejected') from None
        except LocalRoleIdentityError:
            raise
        except Exception:
            raise LocalRoleIdentityError('local session state-machine Run transport failed') from None

    def role_bot_ids(self) -> dict[str, str]:
        """Public role to Bot-ID projection for density bindings; never tokens."""
        return {role: entry[0] for role, entry in self._entries.items()}

    def actor_id(self, role: str) -> str:
        """Return a verified role's Bot ID without exposing its credential."""
        self._require_roles((role,))
        if role not in self._verified:
            raise LocalRoleIdentityError('role identity has not completed verification')
        return self._entries[role][0]

    def verify(self, role: str) -> dict:
        try:
            self._require_bound(role)
            if role in self._attempted:
                raise LocalRoleIdentityError('role identity probe cannot be replayed')
            self._attempted.add(role)
            entry = self._entries[role]
            bot_id, token = entry
            # Bracket the exact-self read with strict token-validity reads.
            # Friends alone may fall back to the local Human; visibility alone
            # allows public/protected cross-Bot reads. This is still a non-atomic
            # check in a controlled runtime, not a server WhoAmI attestation.
            for operation in ('visibility', 'friends', 'visibility'):
                self._require_bound(role)
                value = self._request(operation, bot_id, token)
                self._require_bound(role)
                data = value.get('data')
                if operation == 'visibility':
                    if (not isinstance(data, dict) or data.get('bot_uuid') != bot_id
                            or data.get('visibility') not in ('public', 'protected', 'private')):
                        raise LocalRoleIdentityError('role identity visibility target is mismatched')
                elif (not isinstance(data, list) or len(data) > 100
                        or any(not isinstance(row, dict) for row in data)):
                    raise LocalRoleIdentityError('self-scoped friend read is invalid or oversized')
            self._verified.append(role)
            return {'status': 'VERIFIED', 'role': role, 'bot_id': bot_id,
                    'authentication': 'OWNED_LOCAL_BOT', 'api_face': 'legacy-local',
                    'operations': ['bot.visibility.read', 'bot.friends.self.read', 'bot.visibility.recheck'],
                    'identity_basis': 'CREATED_BINDING_AND_BRACKETED_SELF_READS',
                    'atomic_identity_attestation': False,
                    'assumption': 'no concurrent credential mutation in the owned test runtime',
                    'business_writes': 0, 'relationship_baseline': 'NOT_VERIFIED'}
        except BaseException:
            self.close()
            raise

    def trust_baseline(self) -> dict:
        """One read-only baseline for all five fresh, identity-verified roles."""
        from .trust_baseline import ROLES, TrustBaselineError, require_empty, result, visibility
        try:
            if (self.fixture_set != 'trusted-team' or set(self.roles) != set(ROLES)
                    or set(self._verified) != set(ROLES) or self._baseline_attempted):
                raise LocalRoleIdentityError('trust baseline requires all five verified roles and a fresh attempt')
            self._baseline_attempted = True
            bindings, original = {}, {}
            for role in ROLES:
                self._require_bound(role)
                entry = self._entries[role]
                bot_id, token = entry
                bindings[role] = bot_id
                for operation in ('visibility', 'friends', 'pending-sent', 'pending-received', 'visibility'):
                    self._require_bound(role)
                    payload = self._request(operation, bot_id, token)
                    self._require_bound(role)
                    if operation == 'visibility':
                        observed = visibility(payload, bot_id)
                        if role in original and original[role] != observed:
                            raise TrustBaselineError('visibility changed during baseline observation')
                        original[role] = observed
                    else:
                        require_empty(payload, operation=operation, bot_id=bot_id)
            from .local_trust_store import observe_legacy_baseline
            storage = observe_legacy_baseline(self.fixtures, bindings)
            for role in ROLES:
                self._require_bound(role)
            return {**result(bindings, original), 'status': 'BASELINE_OBSERVED',
                    'storage_completeness': 'LEGACY_INCIDENT_ROWS_VERIFIED', 'storage': storage,
                    'note': 'API observations plus a separate read-transaction snapshot of zero incident legacy '
                            'friendship/request rows for the five bound local Bots; edge-permission graph '
                            'excluded, no atomic API/SQL consistency or remote admission.'}
        except BaseException:
            self.close()
            raise
