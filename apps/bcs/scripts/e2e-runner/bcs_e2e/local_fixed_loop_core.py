"""Owned-local fixed-Loop case core machinery: journal plumbing, wire transport, run scopes.

Split out of local_fixed_loop_case under the 1,000-line source limit; text moved verbatim. The eight public seam methods remain in local_fixed_loop_case on OwnedFixedLoopCaseAdapter(OwnedFixedLoopCaseCore).
"""

from __future__ import annotations

from __future__ import annotations
from dataclasses import dataclass
import hashlib
import json
import time
from http.client import HTTPConnection
from urllib.parse import quote, urlsplit
from .case_adapters import FIXED_LOOP_ADAPTER_METHOD_NAMES, AdapterCall
from .case_rewrite import fixed_loop_case_rewrite
from .local_fixed_loop_sandbox import (
    FixedLoopReceiptCursor,
    SHARED_CONTROL_RESETS,
)
from .local_judge_sandbox import OwnedLocalJudgeSandbox
from .local_provider_storage import OwnedLocalProviderStorage
from .local_role_identity import LocalRoleIdentityError, OwnedLocalRoleIdentity
from .owned_bcs import OwnedBcsProcess
from .profile import identifier, unique_keys
from .run_state import RunStateError, read, write


_MAX_BODY_BYTES = 64 * 1024
_TRANSPORT_TIMEOUT_SECONDS = 5.0
_JUDGE_WINDOW_SECONDS = 5.0
_DELIVERY_WINDOW_SECONDS = 20.0

_HUMAN_INPUTS = ('first-human-input', 'second-human-input')
_EDITOR_RESULTS = ('first-editor-result', 'second-editor-result')
_FINAL_OUTPUT = 'final-editorial-output'


def _require(value: object, message: str = 'Owned-local fixed-Loop contract was not confirmed') -> None:
    if not value:
        raise RunStateError(message)


def _safe_token(value: object, label: str) -> str:
    _require(isinstance(value, str) and 1 <= len(value) <= 4096 and value.isascii()
             and not any(ord(char) <= 32 or ord(char) == 127 for char in value),
             f'owned-local fixed-Loop {label} is missing or invalid')
    return value


def _output_events(messages: object) -> list[dict]:
    _require(isinstance(messages, list),
             'Owned-local fixed-Loop session history is not a list')
    return [message for message in messages
            if isinstance(message, dict)
            and (message.get('metadata') or {}).get('state_machine', {}).get('event') == 'output']


@dataclass
class _RunScope:
    """One Run's immutable receipt ledger and identity bindings."""

    scope_id: str
    approved: bool
    cursor: FixedLoopReceiptCursor
    judge_first: int
    judge_consumed: int = 0
    group_id: str = ''
    session_id: str = ''
    run_id: str = ''
    receipts: int = 0
    graph_digest: str = ''

    def bind(self, *, group_id: str, session_id: str, run_id: str) -> None:
        _require(group_id and session_id and run_id)
        self.group_id, self.session_id, self.run_id = group_id, session_id, run_id


class OwnedFixedLoopCaseCore:
    """Live local implementation of the fixed-Loop case seams."""

    def __init__(self, runtime: OwnedBcsProcess, sandbox: OwnedLocalJudgeSandbox,
                 identities: OwnedLocalRoleIdentity):
        self.plan = fixed_loop_case_rewrite()
        if (type(runtime) is not OwnedBcsProcess or type(sandbox) is not OwnedLocalJudgeSandbox
                or type(identities) is not OwnedLocalRoleIdentity):
            raise RunStateError('Owned-local fixed-Loop case requires exact owned runtime, sandbox and identity objects')
        if (runtime.inputs is None or runtime.state != 'RUNNING' or runtime.ready is not True
                or runtime.store_messages is not True or runtime.judge_sandbox is not sandbox
                or identities.runtime is not runtime or identities.root != runtime.root
                or runtime.run_id != identities.run_id):
            raise RunStateError('Owned-local fixed-Loop runtime and identity ownership differ')
        sandbox.validate_live(runtime.run_id)
        snapshot = sandbox.snapshot()
        _require(snapshot['state'] == 'LIVE' and snapshot['failed'] is False
                 and snapshot['judge_outcomes'] == ['revise', 'approved', 'revise', 'revise']
                 and snapshot['judge_admissions'] == 4 and snapshot['judge_request_count'] == 0
                 and snapshot['provider_receipt_limit'] == 8 and not snapshot['provider_receipts'],
                 'Owned-local fixed-Loop sandbox is not the fresh deterministic scripted sandbox')
        self.runtime, self.sandbox, self.identities = runtime, sandbox, identities
        self.run_id, self.root = runtime.run_id, runtime.root
        self.origin = runtime.inputs.origin
        parsed = urlsplit(self.origin)
        _require(parsed.scheme == 'http' and parsed.hostname == '127.0.0.1'
                 and parsed.port is not None and not parsed.path and not parsed.query and not parsed.fragment,
                 'Owned-local fixed-Loop runtime origin is outside its loopback contract')
        self._host, self._port = parsed.hostname, parsed.port
        try:
            self.driver_bot_id = identities.actor_id('CEO')
        except LocalRoleIdentityError:
            raise RunStateError('Owned-local fixed-Loop driver identity is not ready') from None
        self.human = runtime.inputs.human_id
        self.storage = OwnedLocalProviderStorage(runtime)
        self.path = self.root / 'local-fixed-loop-adapter.json'
        if self.path.exists() or self.path.is_symlink():
            raise RunStateError('Owned-local fixed-Loop adapter already attempted this runtime')
        self._credentials: dict[str, str] = {}
        self._ids: dict[str, str] = {}
        self._secrets = tuple()
        self._attempted: set[str] = set()
        self._issued: list[str] = []
        self._scopes: dict[str, _RunScope] = {}
        self._digest = ''
        self.state = {
            'schema_version': 1, 'status': 'RUNNING', 'case_id': self.plan.case_id,
            'parent_id': self.plan.parent_id, 'run_id': self.run_id,
            'adapter_family': self.plan.environments[0].adapter_family,
            'adapter_class': type(self).__name__, 'steps': [],
        }

    # ------------------------------------------------------------------
    # Journal plumbing: ordered single-attempt evidence, secret-free.
    # ------------------------------------------------------------------

    def _no_secrets(self, value: object) -> None:
        encoded = json.dumps(value, ensure_ascii=True, allow_nan=False)
        _require(not any(secret and secret in encoded for secret in self._secrets),
                 'Owned-local fixed-Loop evidence contains non-public data')

    def _save(self) -> None:
        self._no_secrets(self.state)
        write(self.path, self.state)
        self._digest = json.dumps(self.state, sort_keys=True, separators=(',', ':'))

    def _check_integrity(self) -> None:
        if self._digest:
            _require(read(self.path) == self.state
                     and json.dumps(read(self.path), sort_keys=True, separators=(',', ':')) == self._digest,
                     'Owned-local fixed-Loop adapter journal changed externally')

    def _begin(self, method: str) -> dict:
        if method not in FIXED_LOOP_ADAPTER_METHOD_NAMES:
            raise RunStateError('Owned-local fixed-Loop adapter operation is unmapped')
        self._check_integrity()
        _require(method not in self._attempted,
                 'Owned-local fixed-Loop operation cannot be replayed')
        index = FIXED_LOOP_ADAPTER_METHOD_NAMES.index(method)
        _require([row['method'] for row in self.state['steps']] == list(FIXED_LOOP_ADAPTER_METHOD_NAMES[:index]),
                 'Owned-local fixed-Loop operation is outside the contract order')
        if index:
            _require(self.state['steps'][-1]['status'] == 'PASS',
                     'Owned-local fixed-Loop operation follows an unfinished predecessor')
        row = {'method': method, 'requirement_id': self.plan.requirements[index].requirement_id,
               'status': 'INTENDED', 'http_status': None}
        self.state['steps'].append(row)
        self._attempted.add(method)
        self._save()
        return row

    def _acknowledge(self, row: dict, status: int) -> None:
        row['status'] = 'ACKNOWLEDGED'
        row['http_status'] = status
        self._save()

    def _pass(self, row: dict, evidence: dict) -> AdapterCall:
        _require(isinstance(evidence, dict))
        self._no_secrets(evidence)
        row['status'] = 'PASS'
        row['evidence'] = evidence
        self._save()
        return AdapterCall(data=evidence, evidence=evidence)

    def _blocked(self, row: dict, error: BaseException) -> None:
        row['status'] = 'UNKNOWN'
        row['error_type'] = type(error).__name__
        try:
            self._save()
        except BaseException:
            pass

    def _wire(self, *, method: str, path: str, body: dict | None,
              authorization: str = '', exact_status: int,
              accepted_statuses: tuple[int, ...] | None = None,
              provider_id: str = '') -> tuple[int, dict]:
        _require(path.startswith('/') and len(path) <= 2048
                 and not any(ord(char) < 32 for char in path)
                 and path not in SHARED_CONTROL_RESETS,
                 'Owned-local fixed-Loop path is invalid or is a shared control-plane reset')
        accepted = accepted_statuses or (exact_status,)
        _require(exact_status in accepted,
                 'Owned-local fixed-Loop accepted status set is invalid')
        self._issued.append(path)
        payload = (None if body is None
                   else json.dumps(body, allow_nan=False, separators=(',', ':')).encode())
        _require(payload is None or len(payload) <= _MAX_BODY_BYTES,
                 'Owned-local fixed-Loop request exceeds its byte budget')
        headers = {'Accept': 'application/json', 'Content-Type': 'application/json',
                   'X-Request-ID': 'e2e-fixed-loop-' + self.run_id + '-'
                   + str(len(self.state['steps']))}
        if authorization:
            headers['Authorization'] = authorization
        if provider_id:
            headers['X-BCN-Provider-Id'] = provider_id
        connection = HTTPConnection(self._host, self._port, timeout=_TRANSPORT_TIMEOUT_SECONDS)
        try:
            connection.request(method, path, body=payload, headers=headers)
            response = connection.getresponse()
            with response:
                status = response.status
                raw = bytearray()
                while True:
                    chunk = response.read(4096)
                    if not chunk:
                        break
                    raw.extend(chunk)
                    _require(len(raw) <= _MAX_BODY_BYTES,
                             'Owned-local fixed-Loop response exceeds its byte budget')
                _require(response.length in (None, 0),
                         'Owned-local fixed-Loop response was truncated')
            _require(status in accepted,
                     'Owned-local fixed-Loop HTTP status differs from the original contract')
            value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_keys) if raw else {}
            _require(isinstance(value, dict) or isinstance(value, list),
                     'Owned-local fixed-Loop response is not a JSON value')
            return status, value
        except BaseException as error:
            raise RunStateError('Owned-local fixed-Loop exchange failed; outcome unknown, no retry or cleanup') from None
        finally:
            connection.close()

    def _credential(self, key: str, value: object) -> str:
        token = _safe_token(value, key + ' token')
        _require(key not in self._credentials and token not in self._credentials.values()
                 and token not in (self._ids.get('provider_id', ''), self._ids.get('provider_bot_uuid', '')),
                 'Owned-local fixed-Loop credential is missing, reused or borrowed')
        self._credentials[key] = token
        self._secrets = tuple(set(self._credentials.values())
                              | {entry[1] for entry in self.identities._entries.values()})
        return token

    # ------------------------------------------------------------------
    # Frozen original-Story definition and shared drivers.
    # ------------------------------------------------------------------

    @staticmethod
    def _editorial_node(display_name: str) -> dict:
        return {
            'kind': 'bot_task', 'display_name': display_name,
            'assignee': {'type': 'bot_binding', 'binding': 'worker'},
            'instruction': 'Use the preceding input and return the requested editorial result.',
        }

    def _definition(self, *, approved: bool) -> dict:
        review = self._editorial_node('Editor review')
        review['judge'] = {'type': 'llm', 'criteria': ['The editor approves the content.'],
                           'outcomes': ['revise', 'approved']}
        nodes = {
            'cycle': {
                'kind': 'loop', 'display_name': 'Editorial loop',
                'loop': {
                    'mode': 'fixed', 'max_iterations': 3 if approved else 2,
                    'entry_node': 'input', 'result_node': 'review',
                    'continue_outcomes': ['revise'], 'break_outcomes': ['approved'],
                    'exhausted_outcome': 'exhausted',
                    'nodes': {
                        'input': {
                            'kind': 'human_input', 'display_name': 'Author input',
                            'instruction': 'Revise the content using the previous editorial result.',
                            'node_timeout_ms': 60000,
                            'transitions': {'complete': {'targets': ['review']}},
                        },
                        'review': review,
                    },
                },
                'transitions': {
                    'approved': {'targets': ['polish'], 'display_name': 'Ready to polish'},
                    'exhausted': {'targets': ['rewrite'], 'display_name': 'Rewrite required'},
                },
            },
        }
        for name, display in (('polish', 'Polish approved content'),
                              ('rewrite', 'Rewrite unapproved content'),
                              ('summary', 'Summarize result')):
            nodes[name] = self._editorial_node(display)
        nodes['summary']['final_output'] = True
        for branch in ('polish', 'rewrite'):
            nodes[branch]['transitions'] = {'complete': {'targets': ['summary']}}
        return {'name': 'E2E editorial workflow',
                'participants': {'worker': {'display_name': 'Editor', 'required': True}},
                'runtime': {'kind': 'state_machine',
                            'state_machine': {'version': 2, 'graph_mode': 'hierarchical',
                                               'nodes': nodes}}}

    def _new_scope(self, *, approved: bool) -> _RunScope:
        order = 'approved-loop' if approved else 'exhausted-loop'
        scope = _RunScope(scope_id=self.run_id + '-' + order, approved=approved,
                          cursor=FixedLoopReceiptCursor(self.run_id + '-' + order),
                          judge_first=0 if approved else 2)
        self._scopes[order] = scope
        return scope

    def _validate_definition(self, document: dict) -> dict:
        status, value = self._wire(method='POST', path='/collaboration/definitions/validate',
                                   body={'definition_yaml': json.dumps(document, allow_nan=False,
                                                                      separators=(',', ':'))},
                                   exact_status=200)
        self._acknowledge_pending(status)
        _require(isinstance(value, dict) and value.get('valid') is True,
                 'Owned-local fixed-Loop definition validation did not confirm the definition')
        return value

    def _acknowledge_pending(self, status: int) -> None:
        row = self.state['steps'][-1]
        if row['status'] == 'INTENDED' and row['http_status'] is None:
            self._acknowledge(row, status)

    def _create_group(self, scope: _RunScope, *, label: str, document: dict,
                      provider_bot_uuid: str, state_machine: bool) -> str:
        body = {
            'driver_bot': self.driver_bot_id, 'label': label,
            'participants': [{'bot_uuid': actor} for actor in (self.driver_bot_id,
                                                               provider_bot_uuid,
                                                               'human_' + self.human)],
        }
        if state_machine:
            body.update({
                'group_strategy': 'state_machine',
                'collaboration_definition_yaml': json.dumps(document, allow_nan=False,
                                                            separators=(',', ':')),
                'start_initial_run': False,
                'participant_bindings': {'worker': {'source': 'manual',
                                                     'bot_ids': [provider_bot_uuid]}},
            })
        status, value = self._wire(method='POST', path='/groups', body=body,
                                   exact_status=200, accepted_statuses=(200, 201))
        self._acknowledge_pending(status)
        _require(isinstance(value, dict) and isinstance(value.get('id'), str) and value['id'],
                 'Owned-local fixed-Loop Group creation did not return its ID')
        return value['id']

    def _create_session(self, scope: _RunScope, *, title: str) -> str:
        status, value = self._wire(
            method='POST',
            path='/groups/' + quote(scope.group_id, safe='') + '/sessions',
            body={'session_title': title}, exact_status=200, accepted_statuses=(200, 201))
        self._acknowledge_pending(status)
        _require(isinstance(value, dict) and isinstance(value.get('session_id'), str)
                 and value['session_id'],
                 'Owned-local fixed-Loop Session creation did not return its ID')
        return value['session_id']

    def _start_group_run(self, scope: _RunScope, input_value: dict) -> dict:
        status, value = self._wire(
            method='POST',
            path='/groups/' + quote(scope.group_id, safe='') + '/state-machine-runs',
            body={'input': input_value}, exact_status=202)
        self._acknowledge_pending(status)
        run = value.get('run') if isinstance(value, dict) else None
        _require(isinstance(run, dict) and isinstance(run.get('run_id'), str) and run['run_id']
                 and isinstance(run.get('session_id'), str) and run['session_id'],
                 'Owned-local fixed-Loop Group-scoped Run start did not return its view')
        return value

    def _start_session_run(self, scope: _RunScope, *, document: dict,
                           provider_bot_uuid: str, input_value: dict) -> dict:
        # The session-scoped route authenticates the calling Bot; the
        # purpose-bound identity operation consumes the credential in
        # module and never exposes it.
        value = self.identities.start_session_state_machine_run(
            'CEO', session_id=scope.session_id,
            definition_yaml=json.dumps(document, allow_nan=False, separators=(',', ':')),
            participant_bindings={'worker': {'source': 'manual',
                                             'bot_ids': [provider_bot_uuid]}},
            input=input_value)
        self._issued.append('/sessions/' + scope.session_id + '/state-machine-runs')
        row = self.state['steps'][-1]
        if row['status'] == 'INTENDED' and row['http_status'] is None:
            self._acknowledge(row, 202)
        run = value.get('run') if isinstance(value, dict) else None
        _require(isinstance(run, dict) and isinstance(run.get('run_id'), str) and run['run_id']
                 and run.get('session_id') == scope.session_id,
                 'Owned-local fixed-Loop session-scoped Run view does not bind this Session')
        return value

    def _await_pending(self, scope: _RunScope) -> dict:
        path = '/state-machine-runs/' + quote(scope.run_id, safe='') + '/pending-human-nodes'
        deadline = time.monotonic() + _DELIVERY_WINDOW_SECONDS
        last_error = 'no pending Human node was observed'
        while True:
            _, value = self._wire(method='GET', path=path, body=None, exact_status=200)
            if isinstance(value, list) and len(value) == 1:
                node = value[0]
                if isinstance(node, dict) and isinstance(node.get('node_id'), str) and node['node_id']:
                    return node
                last_error = 'pending Human node view is invalid'
            elif isinstance(value, list):
                last_error = 'pending Human node count differs from the original one-at-a-time contract'
            if time.monotonic() >= deadline:
                raise RunStateError('Owned-local fixed-Loop ' + last_error)
            time.sleep(0.05)

    def _respond(self, scope: _RunScope, *, node_id: str, content: str,
                 expected_status: int = 200) -> dict:
        status, value = self._wire(
            method='POST',
            path=('/state-machine-runs/' + quote(scope.run_id, safe='')
                  + '/nodes/' + quote(node_id, safe='') + '/respond'),
            body={'content': content}, exact_status=expected_status,
            accepted_statuses=None if expected_status == 200 else (expected_status,))
        self._acknowledge_pending(status)
        return value

    def _stale_respond_is_rejected(self, scope: _RunScope, *, node_id: str) -> None:
        value = self._respond(scope, node_id=node_id, content='stale input',
                               expected_status=409)
        _require(isinstance(value, dict),
                 'Owned-local fixed-Loop stale Human response rejection is not a JSON error')

    def _claim_provider_receipt(self, scope: _RunScope, index: int) -> dict:
        deadline = time.monotonic() + _DELIVERY_WINDOW_SECONDS
        while True:
            snapshot = self.sandbox.snapshot()
            mine = [item for item in snapshot['provider_receipts']
                    if item['bcn_group_id'] == scope.session_id]
            if len(mine) > index:
                receipt = dict(mine[index])
                _require(receipt['method'] == 'chat.send'
                         and receipt['provider_id'] == self._ids['provider_id']
                         and receipt['bot_id'] == self._ids['provider_bot_uuid']
                         and receipt['provider_bot_ref'] == self.run_id,
                         'Owned-local fixed-Loop Provider delivery does not match the bound fixture')
                scope.receipts += 1
                return receipt
            if time.monotonic() >= deadline:
                raise RunStateError('Owned-local fixed-Loop Provider delivery was not confirmed within its window')
            time.sleep(0.02)

    def _submit_provider_callback(self, receipt: dict, *, text: str) -> None:
        body = {'run_id': receipt['delivery_request_id'], 'state': 'final',
                'message': {'text': text}}
        deadline = time.monotonic() + 5.0
        while True:
            status, value = self._wire(
                method='POST', path='/bot/events', body=body,
                authorization='Bearer ' + self._credentials['bot_runtime'],
                provider_id=self._ids['provider_id'], exact_status=200,
                accepted_statuses=(200, 409))
            if value.get('error') != 'transport_conflict':
                _require(status == 200 and not value.get('error'),
                         'Owned-local fixed-Loop Provider callback was not accepted')
                return
            if time.monotonic() >= deadline:
                raise RunStateError('Owned-local fixed-Loop Provider callback exceeded its transport negotiation window')
            time.sleep(0.05)

    def _advance_scripted_judge(self, scope: _RunScope) -> dict:
        index = scope.judge_first + scope.judge_consumed
        _require(index < scope.judge_first + 2,
                 'Owned-local fixed-Loop Run consumed more scripted Judge admissions than its Loop')
        receipt = self.sandbox.wait_judge_started(
            time.monotonic() + _JUDGE_WINDOW_SECONDS, index)
        self.sandbox.release_judge(index)
        scope.judge_consumed += 1
        return receipt.to_dict()

    def _await_run_completed(self, scope: _RunScope) -> dict:
        path = '/state-machine-runs/' + quote(scope.run_id, safe='')
        deadline = time.monotonic() + _DELIVERY_WINDOW_SECONDS
        while True:
            _, value = self._wire(method='GET', path=path, body=None, exact_status=200)
            run = value.get('run') if isinstance(value, dict) else None
            if isinstance(run, dict) and run.get('status') == 'completed':
                return value
            if time.monotonic() >= deadline:
                raise RunStateError('Owned-local fixed-Loop Run did not complete within its window')
            time.sleep(0.05)

    def _run_view(self, scope: _RunScope) -> dict:
        _, value = self._wire(method='GET',
                              path='/state-machine-runs/' + quote(scope.run_id, safe=''),
                              body=None, exact_status=200)
        return value

    def _graph(self, scope: _RunScope) -> dict:
        _, value = self._wire(method='GET',
                              path='/state-machine-runs/' + quote(scope.run_id, safe='') + '/graph',
                              body=None, exact_status=200)
        return value

    def _node_detail(self, scope: _RunScope, *, node_id: str) -> dict:
        _, value = self._wire(method='GET',
                              path=('/state-machine-runs/' + quote(scope.run_id, safe='')
                                    + '/nodes/' + quote(node_id, safe='')),
                              body=None, exact_status=200)
        return value

    def _session_view(self, *, session_id: str) -> dict:
        _, value = self._wire(method='GET',
                              path='/sessions/' + quote(session_id, safe=''),
                              body=None, exact_status=200)
        return value

    def _session_messages(self, *, session_id: str) -> list:
        _, value = self._wire(method='GET',
                              path='/sessions/' + quote(session_id, safe='') + '/messages',
                              body=None, exact_status=200)
        _require(isinstance(value, list),
                 'Owned-local fixed-Loop session history is not a list')
        return value

    def _retained_history_digest(self, scope: _RunScope) -> str:
        return hashlib.sha256(json.dumps(self._graph(scope), sort_keys=True,
                                         separators=(',', ':')).encode()).hexdigest()

    # ------------------------------------------------------------------
    # The two Run executions share one iteration driver.
    # ------------------------------------------------------------------

    def _run_fixed_loop(self, scope: _RunScope) -> dict:
        scope.definition = self._definition(approved=scope.approved)
        self._validate_definition(scope.definition)
        label = 'Fixed Loop E2E ' + ('approved' if scope.approved else 'exhausted')
        provider_bot_uuid = self._ids['provider_bot_uuid']
        if scope.approved:
            scope.group_id = self._create_group(
                scope, label=label + ' Loop', document=scope.definition,
                provider_bot_uuid=provider_bot_uuid, state_machine=True)
            view = self._start_group_run(scope, {'question': 'Review the draft'})
        else:
            scope.group_id = self._create_group(
                scope, label=label + ' Session', document=scope.definition,
                provider_bot_uuid=provider_bot_uuid, state_machine=False)
            scope.session_id = self._create_session(scope, title='One-shot editorial trial')
            view = self._start_session_run(
                scope, document=scope.definition, provider_bot_uuid=provider_bot_uuid,
                input_value={'question': 'Review the draft'})
        run = view['run']
        scope.run_id = run['run_id']
        if not scope.session_id:
            scope.session_id = run['session_id']
        scope.bind(group_id=scope.group_id, session_id=scope.session_id, run_id=scope.run_id)

        first = self._await_pending(scope)
        loop_context = first.get('loop_context')
        _require(isinstance(loop_context, dict) and loop_context.get('iteration') == 1
                 and loop_context.get('previous_result') is None,
                 'Owned-local fixed-Loop first iteration does not start without prior output')
        first_node, first_ref = first['node_id'], first['response_ref']
        self._respond(scope, node_id=first_node, content=_HUMAN_INPUTS[0])
        first_receipt = self._claim_provider_receipt(scope, 0)
        self._submit_provider_callback(first_receipt, text=_EDITOR_RESULTS[0])
        first_judge = self._advance_scripted_judge(scope)
        scope.cursor.record(1, provider_receipt=first_receipt, judge_receipt=first_judge)

        second = self._await_pending(scope)
        second_context = second.get('loop_context')
        previous = second_context.get('previous_result') if isinstance(second_context, dict) else None
        _require(isinstance(second_context, dict) and second_context.get('iteration') == 2
                 and second.get('response_ref') != first_ref
                 and isinstance(previous, dict)
                 and previous.get('output') == _EDITOR_RESULTS[0]
                 and previous.get('outcome') == 'revise',
                 'Owned-local fixed-Loop second iteration lost the preserved prior output')
        self._stale_respond_is_rejected(scope, node_id=first_node)
        unchanged = self._await_pending(scope)
        _require(unchanged['node_id'] == second['node_id']
                 and unchanged['response_ref'] == second['response_ref'],
                 'Owned-local fixed-Loop stale Human response consumed the next Human input')
        self._respond(scope, node_id=second['node_id'], content=_HUMAN_INPUTS[1])
        second_receipt = self._claim_provider_receipt(scope, 1)
        self._submit_provider_callback(second_receipt, text=_EDITOR_RESULTS[1])
        second_judge = self._advance_scripted_judge(scope)
        scope.cursor.record(2, provider_receipt=second_receipt, judge_receipt=second_judge)

        branch = 'polish' if scope.approved else 'rewrite'
        branch_receipt = self._claim_provider_receipt(scope, 2)
        self._submit_provider_callback(branch_receipt, text=branch + '-result')
        summary_receipt = self._claim_provider_receipt(scope, 3)
        self._submit_provider_callback(summary_receipt, text=_FINAL_OUTPUT)

        completed = self._await_run_completed(scope)
        run = completed.get('run')
        _require(isinstance(run, dict) and run.get('run_id') == scope.run_id
                 and run.get('status') == 'completed'
                 and run.get('output') == _FINAL_OUTPUT,
                 'Owned-local fixed-Loop Run did not finish with the final editorial output')
        return completed

