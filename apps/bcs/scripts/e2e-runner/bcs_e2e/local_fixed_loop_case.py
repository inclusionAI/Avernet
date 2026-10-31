"""Owned-local fixed-Loop case public seams: the eight environment-neutral adapter obligations.

Core machinery lives in local_fixed_loop_core (1,000-line source-limit split); contract text moved verbatim.
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

from .local_fixed_loop_core import OwnedFixedLoopCaseCore


class OwnedFixedLoopCaseAdapter(OwnedFixedLoopCaseCore):
    """Live local implementation of the fixed-Loop case seams."""


    # The eight environment-neutral fixed-Loop seams.
    # ------------------------------------------------------------------

    def provision_per_run_sandbox(self) -> AdapterCall:
        row = self._begin('provision_per_run_sandbox')
        try:
            snapshot = self.sandbox.snapshot()
            scope = self._new_scope(approved=True)
            evidence = {
                'isolation': 'per-Run immutable receipt cursor over the deterministic scripted sandbox',
                'run_scope': scope.scope_id,
                'deterministic_judge': {
                    'admissions': snapshot['judge_admissions'],
                    'outcomes': snapshot['judge_outcomes'],
                    'consumption': 'story order; the approved Run consumes the first two admissions',
                },
                'provider_receipt_budget': snapshot['provider_receipt_limit'],
                'receipt_partition': 'bcn_group_id equals the Run session ID',
                'shared_control_resets': sorted(SHARED_CONTROL_RESETS),
                'shared_control_reset_issued': 0,
                'sandbox_state': snapshot['state'],
                'ownership': 'runner-owned single process; no shared endpoint or service state is reset',
            }
            return self._pass(row, evidence)
        except BaseException as error:
            self._blocked(row, error)
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise RunStateError('Owned-local fixed-Loop per-Run sandbox admission failed') from None

    def establish_human_provider_fixture(self) -> AdapterCall:
        row = self._begin('establish_human_provider_fixture')
        try:
            status, _ = self._wire(method='POST', path='/me/ensure-human',
                                   body={}, exact_status=200, accepted_statuses=(200, 201))
            self._acknowledge_pending(status)
            self._ids['provider_request_id'] = self.run_id
            body = {'name': 'Fixed Loop E2E Provider',
                    'webhook_url': self.sandbox.provider_url,
                    'auth': {'mode': 'static_bearer'}, 'protocol_version': '2.0',
                    'coordination': {'mode': 'native_tool'}}
            status, value = self._wire(method='POST', path='/providers', body=body,
                                       exact_status=200)
            self._acknowledge(row, status)
            provider_id = identifier(value.get('provider_id'), 'owned Provider ID')
            admin_token = self._credential('provider_admin', value.get('provider_admin_token'))
            downlink_token = self._credential('downlink', value.get('bcs_to_provider_token'))
            self._ids['provider_id'] = provider_id
            self._ids['provider_bot_ref'] = self.run_id
            stored = self.storage.provider(provider_id)
            provider, credentials = stored['provider'], stored['credentials']
            _require(provider['provider_id'] == provider_id and provider['created_by'] == self.human
                     and provider['owners'] == [self.human] and provider['name'] == 'Fixed Loop E2E Provider'
                     and provider['disabled'] == 0 and provider['auth_mode'] == 'static_bearer'
                     and provider['protocol_version'] == '2.0' and provider['downlink_enabled'] is True
                     and provider['coordination_mode'] == 'native_tool'
                     and provider['sink_url_sha256'] == hashlib.sha256(self.sandbox.provider_url.encode()).hexdigest()
                     and credentials == [{'credential_kind': 'downlink_bcs_to_provider', 'disabled': 0},
                                         {'credential_kind': 'provider_admin', 'disabled': 0}],
                     'Owned fixed-Loop Provider storage does not match its registration contract')

            bot_body = {
                'name': 'Fixed Loop E2E Editor', 'summary': 'Scripted editorial provider',
                'owners': [self.human], 'provider_bot_ref': self._ids['provider_bot_ref'],
                'domains': ['release'], 'skills': ['review'], 'scopes': ['local'],
            }
            status, value = self._wire(
                method='POST', path='/providers/' + quote(provider_id, safe='') + '/bots',
                body=bot_body, authorization='Bearer ' + self._credentials['provider_admin'],
                exact_status=200)
            self._acknowledge(row, status)
            bot_uuid = identifier(value.get('bot_uuid'), 'owned Provider Bot ID')
            _require(value.get('provider_id') == provider_id
                     and value.get('provider_bot_ref') == self._ids['provider_bot_ref']
                     and bot_uuid != provider_id)
            runtime_token = self._credential('bot_runtime', value.get('bot_runtime_token'))
            self._ids['provider_bot_uuid'] = bot_uuid
            self.sandbox.arm_provider(provider_id, bot_uuid, self._credentials['downlink'])
            stored = self.storage.bot(provider_id, bot_uuid, self._ids['provider_bot_ref'],
                                      runtime_token)
            binding, bot = stored['binding'], stored['bot']
            _require(binding['provider_id'] == provider_id and binding['disabled'] == 0
                     and binding['provider_bot_ref'] == self._ids['provider_bot_ref']
                     and bot['bot_uuid'] == bot_uuid and bot['created_by'] == self.human
                     and bot['actor_kind'] == 'bot' and bot['is_deleted'] == 0
                     and bot['credential_matches'] == 1,
                     'Owned fixed-Loop Provider Bot storage does not match its publication contract')

            visibility_path = '/bots/' + quote(bot_uuid, safe='') + '/visibility'
            status, value = self._wire(method='PUT', path=visibility_path,
                                       body={'visibility': 'public'}, exact_status=200)
            self._acknowledge(row, status)
            _require(isinstance(value, dict) and value.get('success') is True
                     and isinstance(value.get('data'), dict) and value['data'].get('visibility') == 'public',
                     'Owned fixed-Loop Provider Bot visibility response did not report public')
            stored = self.storage.bot(provider_id, bot_uuid, self._ids['provider_bot_ref'],
                                      runtime_token)
            _require(stored['bot']['visibility'] == 'public',
                     'Owned fixed-Loop Provider Bot visibility was not persisted')

            status, _ = self._wire(
                method='POST', path='/friends/request',
                body={'from_bot': self.driver_bot_id, 'to_bot': bot_uuid},
                exact_status=200, accepted_statuses=(200, 201))
            self._acknowledge(row, status)
            evidence = {
                'transport': 'owned-local-http',
                'fixture': {'human': 'ensured', 'provider': provider_id, 'provider_bot': bot_uuid,
                            'visibility': 'public', 'driver_to_worker_trust': 'requested'},
                'provider_sandbox_armed': True,
                'credential_kinds_returned': ['provider_admin', 'downlink', 'bot_runtime'],
                'shared_control_resets_issued': 0,
            }
            return self._pass(row, evidence)
        except BaseException as error:
            self._blocked(row, error)
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise RunStateError('Owned-local fixed-Loop Human/Provider fixture failed') from None

    def start_approved_loop_run(self) -> AdapterCall:
        row = self._begin('start_approved_loop_run')
        try:
            scope = self._scopes['approved-loop']
            completed = self._run_fixed_loop(scope)
            run = completed.get('run')
            view = self._run_view(scope)
            nodes = {node.get('node_id'): node
                     for node in view.get('nodes', []) if isinstance(node, dict)}
            _require(nodes.get('polish', {}).get('status') == 'completed'
                     and nodes.get('rewrite', {}).get('status') == 'skipped',
                     'Owned-local fixed-Loop approved Run did not route approved to polish')
            scope.graph_digest = self._retained_history_digest(scope)
            evidence = {
                'transport': 'owned-local-http',
                'approved_loop': {
                    'group_id': scope.group_id, 'session_id': scope.session_id,
                    'run_id': scope.run_id,
                    'iterations': 2, 'stale_human_response_rejected': True,
                    'prior_output_preserved': True, 'loop_route': 'approved',
                    'branch': 'polish', 'run_status': 'completed',
                    'final_output': _FINAL_OUTPUT,
                    'journal_receipt_iterations': scope.cursor.last_iteration,
                },
                'shared_control_resets_issued': 0,
            }
            return self._pass(row, evidence)
        except BaseException as error:
            self._blocked(row, error)
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise RunStateError('Owned-local fixed-Loop approved Run failed') from None

    def start_exhausted_loop_run(self) -> AdapterCall:
        row = self._begin('start_exhausted_loop_run')
        try:
            scope = self._new_scope(approved=False)
            completed = self._run_fixed_loop(scope)
            session = self._session_view(session_id=scope.session_id)
            _require(session.get('status') == 'running',
                     'Owned-local fixed-Loop one-shot Session did not remain running after Run completion')
            scope.graph_digest = self._retained_history_digest(scope)
            evidence = {
                'transport': 'owned-local-http',
                'second_run_transport': 'session-scoped Run start with purpose-bound driver Bot credential',
                'exhausted_loop': {
                    'group_id': scope.group_id, 'session_id': scope.session_id,
                    'run_id': scope.run_id,
                    'iterations': 2, 'stale_human_response_rejected': True,
                    'prior_output_preserved': True, 'loop_route': 'exhausted',
                    'branch': 'rewrite', 'run_status': 'completed',
                    'session_status': 'running',
                    'final_output': _FINAL_OUTPUT,
                    'journal_receipt_iterations': scope.cursor.last_iteration,
                },
                'shared_control_resets_issued': 0,
            }
            return self._pass(row, evidence)
        except BaseException as error:
            self._blocked(row, error)
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise RunStateError('Owned-local fixed-Loop exhausted Run failed') from None

    def verify_one_shot_publication(self) -> AdapterCall:
        row = self._begin('verify_one_shot_publication')
        try:
            exhausted = self._scopes['exhausted-loop']
            session = self._session_view(session_id=exhausted.session_id)
            messages = self._session_messages(session_id=exhausted.session_id)
            outputs = _output_events(messages)
            published = [message for message in messages
                         if isinstance(message, dict) and message.get('content') == _FINAL_OUTPUT
                         and (message.get('metadata') or {}).get('state_machine', {}).get('event') != 'output']
            _require(session.get('status') == 'running',
                     'Owned-local fixed-Loop one-shot Session did not remain running')
            _require(len(outputs) == 6,
                     'Owned-local fixed-Loop Session did not publish exactly six node outputs')
            _require(len(published) == 1,
                     'Owned-local fixed-Loop final result did not reach chat exactly once')
            evidence = {
                'transport': 'owned-local-http',
                'session': {'session_id': exhausted.session_id, 'status': 'running'},
                'node_output_event_count': len(outputs), 'chat_publication_count': len(published),
                'final_output': _FINAL_OUTPUT,
                'publication_basis': 'distinct from the six state-machine node output events',
            }
            return self._pass(row, evidence)
        except BaseException as error:
            self._blocked(row, error)
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise RunStateError('Owned-local fixed-Loop one-shot publication was not verified') from None

    def poll_loop_history_graph(self) -> AdapterCall:
        row = self._begin('poll_loop_history_graph')
        try:
            results = []
            for scope in (self._scopes['approved-loop'], self._scopes['exhausted-loop']):
                branch, skipped, outcome, route = (
                    ('polish', 'rewrite', 'approved', 'approved') if scope.approved
                    else ('rewrite', 'polish', 'revise', 'exhausted'))
                view = self._graph(scope)
                nodes = {node.get('node_id'): node
                         for node in view.get('nodes', []) if isinstance(node, dict)}
                _require(nodes.get(branch, {}).get('status') == 'completed'
                         and nodes.get(skipped, {}).get('status') == 'skipped',
                         'Owned-local fixed-Loop graph does not expose the expected branch routing')
                if scope.approved:
                    future = [node for node in nodes.values()
                              if isinstance(node.get('execution'), dict)
                              and node['execution'].get('iteration') == 3]
                    _require(len(future) == 2
                             and all(node.get('status') == 'skipped' for node in future),
                             'Owned-local fixed-Loop approved Run did not skip its unreached third iteration')
                result = next((node for node in nodes.values()
                               if isinstance(node.get('execution'), dict)
                               and node['execution'].get('iteration') == 2
                               and node['execution'].get('definition_node_id') == 'review'), None)
                _require(isinstance(result, dict) and result.get('outcome') == outcome,
                         'Owned-local fixed-Loop second-iteration review outcome differs from the scripted Judge result')
                edge = next((item for item in view.get('edges', [])
                             if isinstance(item, dict) and item.get('source') == result['node_id']
                             and item.get('target') == branch), None)
                _require(isinstance(edge, dict)
                         and isinstance(edge.get('loop_route'), dict)
                         and edge['loop_route'].get('logical_outcome') == route,
                         'Owned-local fixed-Loop graph edge does not record the Loop logical outcome')

                run_view = self._run_view(scope)
                history = self._session_messages(session_id=scope.session_id)
                outputs = _output_events(history)
                _require(len(outputs) == 6,
                         'Owned-local fixed-Loop history does not carry exactly six node outputs')
                metadata = run_view.get('node_execution_metadata')
                _require(isinstance(metadata, dict),
                         'Owned-local fixed-Loop Run view does not expose execution metadata')
                for node in run_view.get('nodes', []):
                    if not isinstance(node, dict):
                        continue
                    node_id = node.get('node_id')
                    detail = self._node_detail(scope, node_id=node_id)
                    graph_node = nodes.get(node_id, {})
                    _require(graph_node.get('status') == node.get('status')
                             and detail.get('execution') == graph_node.get('execution')
                             == metadata.get(node_id),
                             'Owned-local fixed-Loop graph, detail and Run execution metadata disagree')
                    matching = [message for message in outputs
                                if isinstance(message.get('metadata'), dict)
                                and isinstance(message['metadata'].get('state_machine'), dict)
                                and message['metadata']['state_machine'].get('node_id') == node_id]
                    if node.get('status') == 'skipped':
                        _require(not matching,
                                 'Owned-local fixed-Loop skipped node published an output event')
                    else:
                        _require(len(matching) == 1
                                 and matching[0].get('content') == node.get('artifact_text'),
                                 'Owned-local fixed-Loop completed node output event does not match its artifact')
                _require(self._graph(scope) == view
                         and self._session_messages(session_id=scope.session_id) == history,
                         'Owned-local fixed-Loop graph or history changed on reload')
                results.append({'run_id': scope.run_id, 'branch_completed': branch,
                                'loop_route': route, 'review_outcome': outcome, 'output_event_count': len(outputs),
                                'history_retained_digest': scope.graph_digest, 'reload_immutable': True})
            evidence = {
                'transport': 'owned-local-http',
                'runs': results,
                'execution_metadata_agreement': ['graph', 'node_detail', 'run_view'],
            }
            return self._pass(row, evidence)
        except BaseException as error:
            self._blocked(row, error)
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise RunStateError('Owned-local fixed-Loop history-graph verification failed') from None

    def delete_loop_groups_exactly(self) -> AdapterCall:
        row = self._begin('delete_loop_groups_exactly')
        try:
            deleted_groups = []
            for scope in (self._scopes['approved-loop'], self._scopes['exhausted-loop']):
                path = ('/groups/' + quote(scope.group_id, safe='')
                        + '?bot_id=' + quote(self.driver_bot_id, safe=''))
                status, value = self._wire(method='DELETE', path=path, body=None,
                                           exact_status=200)
                self._acknowledge(row, status)
                _require(value.get('deleted') is True and value.get('id') == scope.group_id,
                         'Owned-local fixed-Loop Group deletion was not confirmed')
                deleted_groups.append(scope.group_id)
            provider_path = ('/providers/' + quote(self._ids['provider_id'], safe='')
                             + '/bots/' + quote(self._ids['provider_bot_ref'], safe=''))
            status, value = self._wire(
                method='DELETE', path=provider_path, body=None,
                authorization='Bearer ' + self._credentials['provider_admin'],
                exact_status=200)
            self._acknowledge(row, status)
            _require(value.get('deleted') is True
                     and value.get('provider_id') == self._ids['provider_id']
                     and value.get('provider_bot_ref') == self._ids['provider_bot_ref']
                     and value.get('bot_uuid') == self._ids['provider_bot_uuid'],
                     'Owned-local fixed-Loop Provider Bot retirement was not confirmed')
            evidence = {
                'transport': 'owned-local-http',
                'deletions': {'groups': 2, 'provider_bot': 1},
                'deleted_group_count': len(deleted_groups),
                'deleted_through': 'original business APIs only',
                'retained_history': {
                    'run_ids': [self._scopes[key].run_id for key in ('approved-loop', 'exhausted-loop')],
                    'graph_digests': [self._scopes[key].graph_digest
                                      for key in ('approved-loop', 'exhausted-loop')],
                    'association': 'Run histories and editorial outputs remain explicit and unfrozen',
                },
                'non_target_state_deleted': 0,
            }
            return self._pass(row, evidence)
        except BaseException as error:
            self._blocked(row, error)
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise RunStateError('Owned-local fixed-Loop exact cleanup failed') from None

    def verify_shared_reset_replacement(self) -> AdapterCall:
        row = self._begin('verify_shared_reset_replacement')
        try:
            paths = list(self._issued)
            shared = [path for path in paths if path in SHARED_CONTROL_RESETS]
            _require(not shared, 'Owned-local fixed-Loop issued a shared control-plane reset')
            sandbox = self.sandbox.snapshot()
            receipts = sandbox['provider_receipts']
            partitions: dict[str, int] = {}
            for receipt in receipts:
                partitions[receipt['bcn_group_id']] = partitions.get(receipt['bcn_group_id'], 0) + 1
            approved = self._scopes['approved-loop']
            exhausted = self._scopes['exhausted-loop']
            _require(partitions == {approved.session_id: 4, exhausted.session_id: 4},
                     'Owned-local fixed-Loop Provider receipt partition leaked across Runs')
            approved_judge = {item['judge_receipt']['body_sha256']
                              for item in approved.cursor.snapshot()}
            exhausted_judge = {item['judge_receipt']['body_sha256']
                               for item in exhausted.cursor.snapshot()}
            _require(approved_judge and exhausted_judge and not (approved_judge & exhausted_judge),
                     'Owned-local fixed-Loop Judge receipts leaked across Runs')
            output_refs = [item['output_ref'] for key in ('approved-loop', 'exhausted-loop')
                           for item in self._scopes[key].cursor.snapshot()]
            _require(len(output_refs) == 4 and len(set(output_refs)) == 4,
                     'Owned-local fixed-Loop per-Run output references are not unique and deterministic')
            evidence = {
                'replacement': {
                    'shared_control_resets_issued': len(shared),
                    'per_run_receipt_ledgers': 2,
                    'per_run_receipt_counts': partitions,
                    'per_run_journaled_iterations': {
                        approved.scope_id: approved.cursor.last_iteration,
                        exhausted.scope_id: exhausted.cursor.last_iteration,
                    },
                    'deterministic_judge_admissions': sandbox['judge_request_count'],
                    'scripted_judge_sequence': sandbox['judge_outcomes'],
                    'judge_receipt_partition_disjoint': True,
                    'output_refs_unique': len(set(output_refs)),
                },
                'requested_paths': len(paths),
                'cross_run_leakage': 0,
            }
            return self._pass(row, evidence)
        except BaseException as error:
            self._blocked(row, error)
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise RunStateError('Owned-local fixed-Loop shared-reset replacement was not verified') from None
