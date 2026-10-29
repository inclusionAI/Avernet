"""Dual-environment adapter for the reviewed structured-collaboration core.

The adapter executes the 15 fixed Group, collaboration-definition and
state-machine-run obligations from one admitted profile: the dedicated
CEO Bot creates the exact two-Bot state-machine Group with the frozen
inline definition, the owning Human reads and patches the bound
definition and drives the state-machine run, the three reviewed
negative envelopes (unpublished upgrade, bot-task Human response,
aborted-run rerun) are asserted explicitly, and the creator-scoped
Group delete finalizes the fixture. State is chained inside the adapter
between obligations and every obligation runs at most once.
"""
from __future__ import annotations

import json

from .case_adapters import AdapterCall
from .local_structured_core_contract import (
    BOT_UUID_PLACEHOLDER,
    CANCEL_BODY,
    CORE_REQUIREMENT_IDS,
    DEFINITION_YAML,
    GROUP_LABEL,
    HUMAN_RESPONSE_BODY,
    ORIGINAL_INSTRUCTION,
    PATCHED_INSTRUCTION,
    RUN_INPUT,
    StructuredCoreRequirement,
    _REQUEST_OPERATIONS,
    _STEP_EFFECTS,
    structured_core_case_plan,
)
from .profile import ConfigError, identifier
from .route_bindings import require_edge_case_binding
from .run_state import RunStateError
from .transport import ReadClient
from .write_transport import WriteClient

_OPERATIONS = tuple(dict.fromkeys(_REQUEST_OPERATIONS))
HUMAN_COOKIE_IDENTITY = 'HUMAN_COOKIE'
DRIVER_BOT_IDENTITY = 'AUTHENTICATED_DRIVER_BOT'


class RemoteStructuredCoreAdapter:
    """Execute the 15 fixed structured-collaboration obligations in order."""

    def __init__(self, profile: dict):
        if not isinstance(profile, dict):
            raise RunStateError('structured-core requires an admitted profile')
        entrypoint = profile.get('entrypoint', {})
        environment = profile.get('environment', {})
        kind = entrypoint.get('kind')
        valid_entrypoint = (
            kind == 'singlebox' and environment.get('tier') == 'local'
            or kind == 'gateway' and environment.get('tier') == 'preprod'
            or kind == 'bcs-direct' and environment.get('tier') == 'preprod'
        )
        valid_manifest_face = (
            'internal-v1' if kind in {'singlebox', 'gateway'} else 'legacy'
        )
        if (not valid_entrypoint or entrypoint.get('api_face') != 'public-v1'
                or entrypoint.get('manifest_face') != valid_manifest_face):
            raise RunStateError(
                'structured-core requires an admitted public-v1/native-face '
                'Singlebox, gateway, or station-direct profile')
        self.plan = structured_core_case_plan()
        self.bindings: dict[str, dict] = {}
        try:
            for operation in _OPERATIONS:
                self.bindings[operation] = require_edge_case_binding(profile, operation)
        except (ConfigError, ValueError) as error:
            raise RunStateError(str(error)) from None
        self.profile = profile
        self.driver_bot_id = identifier(profile['actors']['driver']['bot_id'],
                                        'structured-core driver actor ID')
        self.member_bot_id = identifier(profile['actors']['member']['bot_id'],
                                        'structured-core member actor ID')
        self.human_id = identifier(profile['human']['user_id'],
                                   'structured-core human actor ID')
        self.read_client = ReadClient(profile)
        self.write_client = WriteClient(profile)
        self._attempted: set[str] = set()
        self._group_id = ''
        self._definition_id = ''
        self._base_version = 0
        self._patched_version = 0
        self._run_id = ''

    # --- ordering and chaining guards ---

    def _requirement(self, requirement_id: str) -> StructuredCoreRequirement:
        requirement = next((row for row in self.plan.requirements
                            if row.requirement_id == requirement_id), None)
        if requirement is None or requirement.adapter_method in self._attempted:
            raise RunStateError('structured-core contract changed or obligation was repeated')
        self._attempted.add(requirement.adapter_method)
        return requirement

    def _require_group(self) -> None:
        if not self._group_id:
            raise RunStateError(
                'structured-core group creation must precede group-scoped obligations')

    def _require_definition(self) -> None:
        self._require_group()
        if not self._definition_id:
            raise RunStateError(
                'structured-core definition read must precede definition obligations')

    def _require_patched(self) -> None:
        self._require_definition()
        if not self._patched_version:
            raise RunStateError(
                'structured-core definition patch must precede the upgrade obligation')

    def _require_run(self) -> None:
        self._require_group()
        if not self._run_id:
            raise RunStateError(
                'structured-core run start must precede run-scoped obligations')

    def _nonempty(self, value: object, what: str) -> str:
        if not isinstance(value, str) or not value:
            raise RunStateError('structured-core response lacks the ' + what)
        return value

    # --- transport evidence ---

    def _secrets(self) -> list[str]:
        return [secret for client in (self.read_client, self.write_client)
                for secret in client.headers.values()]

    def _evidence(self, record: dict, *, requirement_id: str, identity: str,
                  fixture_effect: str, **extra) -> dict:
        evidence = {
            'transport': 'native-v1-http',
            'requirement_id': requirement_id,
            'operation': record.get('operation'),
            'path': record.get('path'),
            'http_method': record.get('method'),
            'http_status': record.get('http_status'),
            'identity': identity,
            'credentials_sent': record.get('credentials_sent'),
            'authorization_sent': record.get('authorization_sent'),
            'fixture_effect': fixture_effect,
        }
        evidence.update(extra)
        if any(secret and secret in repr(evidence) for secret in self._secrets()):
            raise RunStateError('structured-core evidence must not expose a credential')
        return evidence

    def _read(self, *, requirement_id: str, operation: str,
              group_id: str = '',
              run_id: str = '') -> tuple[dict, dict]:
        requirement = self._requirement(requirement_id)
        observation = self.read_client.read(operation, group_id=group_id, run_id=run_id)
        data, record = observation.data, observation.evidence
        checks = [
            record.get('operation') == operation,
            record.get('method') == 'GET',
            record.get('http_status') == 200,
            record.get('credentials_sent') is True,
            record.get('authorization_sent') is False,
        ]
        if group_id:
            checks.append(record.get('bound_group_id') == group_id)
        if run_id:
            checks.append(record.get('bound_run_id') == run_id)
        if not all(checks):
            raise RunStateError(
                'structured-core read transport evidence differs from the fixed contract')
        return data, record

    # --- the 15 reviewed obligations ---

    def create_structured_group(self) -> AdapterCall:
        requirement = self._requirement(CORE_REQUIREMENT_IDS[0])
        definition_yaml = DEFINITION_YAML.replace(BOT_UUID_PLACEHOLDER, self.driver_bot_id)
        body = {
            'driver_bot': self.driver_bot_id,
            'label': GROUP_LABEL,
            'participants': [{'bot_uuid': self.driver_bot_id},
                             {'bot_uuid': self.member_bot_id}],
            'group_strategy': 'state_machine',
            'collaboration_definition_yaml': definition_yaml,
        }
        if self.profile['entrypoint']['kind'] == 'bcs-direct':
            # The reviewed definition declares only the ``driver`` slot. The
            # member Bot remains an explicit Group participant, but must not
            # be sent as an undeclared state-machine binding slot.
            body['participant_bindings'] = {
                'driver': {'source': 'manual',
                           'bot_ids': [self.driver_bot_id]},
            }
        create_by_bot = (self.bindings['structured.group.create'].get('auth')
                         == 'authenticated_bot')
        data, record = self.write_client.structured_call(
            'structured.group.create', body=body, expected=(200,))
        if (record.get('credentials_sent') is not True
                or record.get('http_status') != 200
                or create_by_bot != (record.get('authorization_sent') is True)):
            raise RunStateError(
                'structured-core group create transport evidence differs from the fixed contract')
        group_id = self._nonempty(data.get('id') if isinstance(data, dict) else None,
                                  'created structured Group ID')
        self._group_id = group_id
        return AdapterCall(
            data={'group_id': group_id, 'label': GROUP_LABEL},
            evidence=self._evidence(record,
                                    requirement_id=requirement.requirement_id,
                                    identity=DRIVER_BOT_IDENTITY if create_by_bot
                                    else HUMAN_COOKIE_IDENTITY,
                                    fixture_effect=_STEP_EFFECTS[requirement.requirement_id],
                                    group_id=group_id, label=GROUP_LABEL))

    def read_structured_group(self) -> AdapterCall:
        self._require_group()
        data, record = self._read(requirement_id=CORE_REQUIREMENT_IDS[1],
                                  operation='structured.group.read',
                                  group_id=self._group_id)
        if not isinstance(data, dict) or data.get('group_strategy') != 'state_machine':
            raise RunStateError('structured-core group read does not record the state-machine strategy')
        return AdapterCall(
            data={'group_id': self._group_id, 'group_strategy': data.get('group_strategy')},
            evidence=self._evidence(record,
                                    requirement_id=CORE_REQUIREMENT_IDS[1],
                                    identity=HUMAN_COOKIE_IDENTITY,
                                    fixture_effect='NONE',
                                    group_id=self._group_id))

    def read_collaboration_definition(self) -> AdapterCall:
        self._require_group()
        data, record = self._read(requirement_id=CORE_REQUIREMENT_IDS[2],
                                  operation='structured.definition.read',
                                  group_id=self._group_id)
        definition = data.get('default_definition') if isinstance(data, dict) else None
        if not isinstance(definition, dict):
            raise RunStateError('structured-core bound definition response is invalid')
        definition_id = self._nonempty(definition.get('id'), 'bound definition ID')
        base_version = definition.get('version')
        if type(base_version) is not int or base_version < 1:
            raise RunStateError('structured-core bound definition version is invalid')
        if data.get('yaml_source') != 'original':
            raise RunStateError('structured-core bound definition does not preserve the original YAML')
        self._definition_id = definition_id
        self._base_version = base_version
        return AdapterCall(
            data={'definition_id': definition_id, 'base_version': base_version,
                  'yaml_source': 'original'},
            evidence=self._evidence(record,
                                    requirement_id=CORE_REQUIREMENT_IDS[2],
                                    identity=HUMAN_COOKIE_IDENTITY,
                                    fixture_effect='NONE',
                                    group_id=self._group_id,
                                    definition_id=definition_id,
                                    base_version=base_version))

    def patch_collaboration_definition(self) -> AdapterCall:
        self._require_definition()
        patched_yaml = DEFINITION_YAML.replace(ORIGINAL_INSTRUCTION, PATCHED_INSTRUCTION)
        body = {
            'base_definition': {'id': self._definition_id,
                                'version': self._base_version},
            'definition_yaml': patched_yaml,
        }
        data, record = self.write_client.structured_call(
            'structured.definition.patch', group_id=self._group_id,
            body=body, expected=(200,))
        patched = data.get('default_definition') if isinstance(data, dict) else None
        if not isinstance(patched, dict):
            raise RunStateError('structured-core patched definition response is invalid')
        patched_version = patched.get('version')
        if (type(patched_version) is not int
                or patched_version != self._base_version + 1):
            raise RunStateError(
                'structured-core definition patch did not increment the version by exactly one')
        if PATCHED_INSTRUCTION not in json.dumps(data, sort_keys=True):
            raise RunStateError('structured-core definition patch did not persist the revised instruction')
        self._patched_version = patched_version
        return AdapterCall(
            data={'definition_id': self._definition_id,
                  'patched_version': patched_version},
            evidence=self._evidence(record,
                                    requirement_id=CORE_REQUIREMENT_IDS[3],
                                    identity=HUMAN_COOKIE_IDENTITY,
                                    fixture_effect=_STEP_EFFECTS[CORE_REQUIREMENT_IDS[3]],
                                    group_id=self._group_id,
                                    definition_id=self._definition_id,
                                    patched_version=patched_version))

    def upgrade_missing_definition(self) -> AdapterCall:
        self._require_patched()
        missing_version = self._patched_version + 100
        body = {
            'base_definition': {'id': self._definition_id,
                                'version': self._patched_version},
            'target_definition': {'id': self._definition_id,
                                  'version': missing_version},
        }
        projection, record = self.write_client.structured_call(
            'structured.definition.upgrade.missing', group_id=self._group_id,
            body=body, expected=(404,), parse_error=True)
        haystack = json.dumps(projection, sort_keys=True)
        if (self._definition_id not in haystack
                or '@' + str(missing_version) not in haystack):
            raise RunStateError(
                'structured-core upgrade rejection does not name the exact definition and version')
        return AdapterCall(
            data={'missing_version': missing_version},
            evidence=self._evidence(record,
                                    requirement_id=CORE_REQUIREMENT_IDS[4],
                                    identity=HUMAN_COOKIE_IDENTITY,
                                    fixture_effect='NONE',
                                    group_id=self._group_id,
                                    definition_id=self._definition_id,
                                    missing_version=missing_version))

    def start_state_machine_run(self) -> AdapterCall:
        self._require_patched()
        data, record = self.write_client.structured_call(
            'structured.run.start', group_id=self._group_id,
            body=dict(RUN_INPUT), expected=(202,))
        run = data.get('run') if isinstance(data, dict) else None
        nodes = data.get('nodes') if isinstance(data, dict) else None
        if (not isinstance(run, dict)
                or not self._nonempty(run.get('run_id'), 'state-machine run ID')
                or run.get('group_id') != self._group_id
                or not isinstance(nodes, list) or not nodes
                or not isinstance(nodes[0], dict)
                or not self._nonempty(nodes[0].get('node_id'),
                                      'first state-machine node ID')):
            raise RunStateError(
                'structured-core run start did not return the exact Run, Group and node identity')
        self._run_id = run['run_id']
        return AdapterCall(
            data={'run_id': self._run_id, 'group_id': self._group_id,
                  'first_node_id': nodes[0]['node_id']},
            evidence=self._evidence(record,
                                    requirement_id=CORE_REQUIREMENT_IDS[5],
                                    identity=HUMAN_COOKIE_IDENTITY,
                                    fixture_effect=_STEP_EFFECTS[CORE_REQUIREMENT_IDS[5]],
                                    group_id=self._group_id,
                                    run_id=self._run_id))

    def read_state_machine_run(self) -> AdapterCall:
        self._require_run()
        data, record = self._read(requirement_id=CORE_REQUIREMENT_IDS[6],
                                  operation='structured.run.read',
                                  run_id=self._run_id)
        run = data.get('run') if isinstance(data, dict) else None
        if (not isinstance(run, dict)
                or run.get('run_id') != self._run_id
                or not self._nonempty(run.get('status'), 'run lifecycle state')):
            raise RunStateError('structured-core run read does not keep the exact Run identity')
        return AdapterCall(
            data={'run_id': self._run_id, 'status': run['status']},
            evidence=self._evidence(record,
                                    requirement_id=CORE_REQUIREMENT_IDS[6],
                                    identity=HUMAN_COOKIE_IDENTITY,
                                    fixture_effect='NONE',
                                    run_id=self._run_id))

    def read_state_machine_graph(self) -> AdapterCall:
        self._require_run()
        data, record = self._read(requirement_id=CORE_REQUIREMENT_IDS[7],
                                  operation='structured.run.graph.read',
                                  run_id=self._run_id)
        run = data.get('run') if isinstance(data, dict) else None
        nodes = data.get('nodes') if isinstance(data, dict) else None
        if (not isinstance(run, dict) or run.get('run_id') != self._run_id
                or not isinstance(nodes, list) or not nodes
                or not isinstance(nodes[0], dict)
                or nodes[0].get('node_id') != 'answer'):
            raise RunStateError('structured-core graph read does not refer to the same Run and answer node')
        return AdapterCall(
            data={'run_id': self._run_id, 'first_node_id': nodes[0]['node_id']},
            evidence=self._evidence(record,
                                    requirement_id=CORE_REQUIREMENT_IDS[7],
                                    identity=HUMAN_COOKIE_IDENTITY,
                                    fixture_effect='NONE',
                                    run_id=self._run_id))

    def read_state_machine_node(self) -> AdapterCall:
        self._require_run()
        data, record = self._read(requirement_id=CORE_REQUIREMENT_IDS[8],
                                  operation='structured.run.node.read',
                                  run_id=self._run_id)
        node = data.get('node') if isinstance(data, dict) else None
        if (not isinstance(node, dict)
                or node.get('node_id') != 'answer'
                or node.get('run_id') != self._run_id):
            raise RunStateError('structured-core node detail does not belong to the exact Run')
        return AdapterCall(
            data={'node_id': node['node_id'], 'run_id': self._run_id},
            evidence=self._evidence(record,
                                    requirement_id=CORE_REQUIREMENT_IDS[8],
                                    identity=HUMAN_COOKIE_IDENTITY,
                                    fixture_effect='NONE',
                                    run_id=self._run_id))

    def read_pending_human_nodes(self) -> AdapterCall:
        self._require_run()
        data, record = self._read(requirement_id=CORE_REQUIREMENT_IDS[9],
                                  operation='structured.run.pending.human.nodes',
                                  run_id=self._run_id)
        if data != {'items': []}:
            raise RunStateError('structured-core bot-only workflow still exposes pending Human input')
        return AdapterCall(
            data={'items': []},
            evidence=self._evidence(record,
                                    requirement_id=CORE_REQUIREMENT_IDS[9],
                                    identity=HUMAN_COOKIE_IDENTITY,
                                    fixture_effect='NONE',
                                    run_id=self._run_id))

    def respond_human_input_denied(self) -> AdapterCall:
        self._require_run()
        projection, record = self.write_client.structured_call(
            'structured.run.human.response.denied', run_id=self._run_id,
            body=dict(HUMAN_RESPONSE_BODY), expected=(400,), parse_error=True)
        if 'not a human_input node' not in json.dumps(projection, sort_keys=True):
            raise RunStateError(
                'structured-core Human response rejection does not identify the node kind')
        return AdapterCall(
            data={'diagnostic': 'not a human_input node'},
            evidence=self._evidence(record,
                                    requirement_id=CORE_REQUIREMENT_IDS[10],
                                    identity=HUMAN_COOKIE_IDENTITY,
                                    fixture_effect='NONE',
                                    run_id=self._run_id))

    def cancel_state_machine_run(self) -> AdapterCall:
        self._require_run()
        data, record = self.write_client.structured_call(
            'structured.run.cancel', run_id=self._run_id,
            body=dict(CANCEL_BODY), expected=(200,))
        run = data.get('run') if isinstance(data, dict) else None
        if (not isinstance(run, dict)
                or run.get('run_id') != self._run_id
                or run.get('status') != 'aborted'):
            raise RunStateError(
                'structured-core cancellation did not abort the exact owned Run')
        return AdapterCall(
            data={'run_id': self._run_id, 'status': 'aborted'},
            evidence=self._evidence(record,
                                    requirement_id=CORE_REQUIREMENT_IDS[11],
                                    identity=HUMAN_COOKIE_IDENTITY,
                                    fixture_effect=_STEP_EFFECTS[CORE_REQUIREMENT_IDS[11]],
                                    run_id=self._run_id))

    def read_terminal_state_machine_run(self) -> AdapterCall:
        self._require_run()
        data, record = self._read(requirement_id=CORE_REQUIREMENT_IDS[12],
                                  operation='structured.run.read',
                                  run_id=self._run_id)
        run = data.get('run') if isinstance(data, dict) else None
        if (not isinstance(run, dict)
                or run.get('run_id') != self._run_id
                or run.get('status') != 'aborted'):
            raise RunStateError('structured-core cancelled Run does not remain aborted')
        return AdapterCall(
            data={'run_id': self._run_id, 'status': 'aborted'},
            evidence=self._evidence(record,
                                    requirement_id=CORE_REQUIREMENT_IDS[12],
                                    identity=HUMAN_COOKIE_IDENTITY,
                                    fixture_effect='NONE',
                                    run_id=self._run_id))

    def rerun_aborted_denied(self) -> AdapterCall:
        self._require_run()
        projection, record = self.write_client.structured_call(
            'structured.run.rerun.aborted.denied', run_id=self._run_id,
            body=None, expected=(409,), parse_error=True)
        if 'must be failed before rerun' not in json.dumps(projection, sort_keys=True):
            raise RunStateError(
                'structured-core rerun rejection does not explain the Failed-only source rule')
        return AdapterCall(
            data={'diagnostic': 'must be failed before rerun'},
            evidence=self._evidence(record,
                                    requirement_id=CORE_REQUIREMENT_IDS[13],
                                    identity=HUMAN_COOKIE_IDENTITY,
                                    fixture_effect='NONE',
                                    run_id=self._run_id))

    def delete_structured_group(self) -> AdapterCall:
        self._require_group()
        data, record = self.write_client.structured_call(
            'structured.group.delete', group_id=self._group_id,
            body=None, expected=(200,))
        if (record.get('credentials_sent') is not True
                or record.get('authorization_sent') is not False
                or record.get('http_status') != 200):
            raise RunStateError(
                'structured-core group delete transport evidence differs from the fixed contract')
        evidence = self._evidence(record,
                                  requirement_id=CORE_REQUIREMENT_IDS[14],
                                  identity=HUMAN_COOKIE_IDENTITY,
                                  fixture_effect=_STEP_EFFECTS[CORE_REQUIREMENT_IDS[14]],
                                  group_id=self._group_id,
                                  driver_actor_id=self.driver_bot_id)
        if not isinstance(data, dict):
            raise RunStateError('structured-core group delete acknowledgement is invalid')
        acknowledged_group_id = data.get('id', data.get('group_id'))
        if acknowledged_group_id != self._group_id:
            raise RunStateError(
                'structured-core group delete acknowledgement names another Group')
        return AdapterCall(data={'group_id': self._group_id}, evidence=evidence)
