"""Internal-v1 structured-collaboration core slice from the reviewed Story.

The slice freezes the reviewed 15 group, definition, state-machine-run,
Human-input and cleanup obligations of the Structured collaboration Story:
the CEO Bot creates an exact CEO/PM state-machine Group with the frozen
inline definition, the Human reads and patches the bound definition, the
unpublished-upgrade, bot-task Human-response and aborted-rerun rejections
are asserted as explicit negative envelopes, the Human starts, reads,
inspects and cancels the state-machine run, and the creator-scoped Group
delete finalizes the fixture. Every credential is recorded per request;
the fixture is finalized, not retained.
"""
from __future__ import annotations

from dataclasses import dataclass

from .case_adapters import AdapterCall
from .case_executor import CaseExecution, StepOutcome
from .local_structured_contract import (
    PARENT,
    REQUIREMENTS,
    structured_source_binding,
)
from .route_bindings import (
    STRUCTURED_GROUP_DEFINITION_PATH,
    STRUCTURED_GROUP_DEFINITION_UPGRADE_PATH,
    STRUCTURED_GROUP_DELETE_PATH,
    STRUCTURED_GROUP_PATH,
    STRUCTURED_GROUP_RUN_START_PATH,
    STRUCTURED_GROUPS_CREATE_PATH,
    STRUCTURED_RUN_CANCEL_PATH,
    STRUCTURED_RUN_GRAPH_PATH,
    STRUCTURED_RUN_HUMAN_RESPOND_PATH,
    STRUCTURED_RUN_NODE_PATH,
    STRUCTURED_RUN_PENDING_HUMAN_PATH,
    STRUCTURED_RUN_PATH,
    STRUCTURED_RUN_RERUN_PATH,
)
from .run_state import RunStateError

CASE_ID = 'PUBLIC-STRUCTURED-CORE-01'
SUITE = 'public-structured-core-v1'
CORE_REQUIREMENT_IDS = REQUIREMENTS[2:]
GROUP_LABEL = 'Structured release review'
BOT_UUID_PLACEHOLDER = '${BOT_CEO_UUID}'
DEFINITION_YAML = (
    'name: E2E Guided Answer\n'
    'participants:\n'
    '  driver:\n'
    '    bot_id: "${BOT_CEO_UUID}"\n'
    '    required: true\n'
    'runtime:\n'
    '  kind: state_machine\n'
    '  state_machine:\n'
    '    graph_mode: acyclic\n'
    '    nodes:\n'
    '      answer:\n'
    '        kind: bot_task\n'
    '        display_name: Draft answer\n'
    '        assignee:\n'
    '          type: bot_binding\n'
    '          binding: driver\n'
    '        instruction: Draft a concise release recommendation.\n'
    '        final_output: true'
)
PATCHED_INSTRUCTION = 'Draft and verify a concise release recommendation.'
ORIGINAL_INSTRUCTION = 'Draft a concise release recommendation.'
RUN_INPUT = {'input': {'question': 'Is the release ready?'}}
HUMAN_RESPONSE_BODY = {'content': 'this endpoint only accepts HumanInput nodes'}
CANCEL_BODY = {'reason': 'E2E fixture completed'}

ADAPTER_METHODS = (
    'create_structured_group',
    'read_structured_group',
    'read_collaboration_definition',
    'patch_collaboration_definition',
    'upgrade_missing_definition',
    'start_state_machine_run',
    'read_state_machine_run',
    'read_state_machine_graph',
    'read_state_machine_node',
    'read_pending_human_nodes',
    'respond_human_input_denied',
    'cancel_state_machine_run',
    'read_terminal_state_machine_run',
    'rerun_aborted_denied',
    'delete_structured_group',
)
PUBLIC_OPERATIONS = (
    'structured.group.create', 'structured.group.read',
    'structured.definition.read', 'structured.definition.patch',
    'structured.definition.upgrade.missing', 'structured.run.start',
    'structured.run.read', 'structured.run.graph.read',
    'structured.run.node.read', 'structured.run.pending.human.nodes',
    'structured.run.human.response.denied', 'structured.run.cancel',
    'structured.run.rerun.aborted.denied', 'structured.group.delete',
)
_REQUEST_OPERATIONS = (
    'structured.group.create',
    'structured.group.read',
    'structured.definition.read',
    'structured.definition.patch',
    'structured.definition.upgrade.missing',
    'structured.run.start',
    'structured.run.read',
    'structured.run.graph.read',
    'structured.run.node.read',
    'structured.run.pending.human.nodes',
    'structured.run.human.response.denied',
    'structured.run.cancel',
    'structured.run.read',
    'structured.run.rerun.aborted.denied',
    'structured.group.delete',
)
_REQUEST_METHODS = ('POST', 'GET', 'GET', 'PATCH', 'POST', 'POST',
                    'GET', 'GET', 'GET', 'GET', 'POST', 'POST',
                    'GET', 'POST', 'DELETE')
_REQUEST_STATUS = (200, 200, 200, 200, 404, 202, 200, 200, 200, 200,
                   400, 200, 200, 409, 200)
_STEP_EFFECTS = {
    'STRUCTURED.group-create': 'STRUCTURED_CORE_GROUP_CREATE',
    'STRUCTURED.definition-patch': 'STRUCTURED_CORE_DEFINITION_PATCH',
    'STRUCTURED.run-start': 'STRUCTURED_CORE_RUN_START',
    'STRUCTURED.run-cancel': 'STRUCTURED_CORE_RUN_CANCEL',
    'STRUCTURED.group-delete': 'STRUCTURED_CORE_GROUP_DELETE_FINALIZED',
}
FIXTURE_EFFECT = 'FINALIZED_STRUCTURED_COLLABORATION_GROUP'
_ROUTES = {
    'STRUCTURED.group-create': STRUCTURED_GROUPS_CREATE_PATH,
    'STRUCTURED.group-read': STRUCTURED_GROUP_PATH,
    'STRUCTURED.definition-read': STRUCTURED_GROUP_DEFINITION_PATH,
    'STRUCTURED.definition-patch': STRUCTURED_GROUP_DEFINITION_PATH,
    'STRUCTURED.upgrade-missing': STRUCTURED_GROUP_DEFINITION_UPGRADE_PATH,
    'STRUCTURED.run-start': STRUCTURED_GROUP_RUN_START_PATH,
    'STRUCTURED.run-read': STRUCTURED_RUN_PATH,
    'STRUCTURED.graph-read': STRUCTURED_RUN_GRAPH_PATH,
    'STRUCTURED.node-read': STRUCTURED_RUN_NODE_PATH,
    'STRUCTURED.pending-human-empty': STRUCTURED_RUN_PENDING_HUMAN_PATH,
    'STRUCTURED.human-response-denied': STRUCTURED_RUN_HUMAN_RESPOND_PATH,
    'STRUCTURED.run-cancel': STRUCTURED_RUN_CANCEL_PATH,
    'STRUCTURED.run-terminal': STRUCTURED_RUN_PATH,
    'STRUCTURED.rerun-aborted-denied': STRUCTURED_RUN_RERUN_PATH,
    'STRUCTURED.group-delete': STRUCTURED_GROUP_DELETE_PATH,
}


@dataclass(frozen=True)
class StructuredCoreRequirement:
    requirement_id: str
    source_method: str
    source_path: str
    public_path: str
    actor: str
    adapter_method: str
    expectation: str

    def validate(self) -> None:
        if (self.requirement_id not in _ROUTES
                or self.public_path != _ROUTES[self.requirement_id]
                or self.source_method not in {'GET', 'POST', 'PATCH', 'DELETE'}
                or not self.source_path.startswith('/')
                or self.actor not in {'CEO', 'HUMAN'}
                or self.adapter_method not in ADAPTER_METHODS
                or not self.expectation):
            raise RunStateError('structured-core requirement is invalid')


@dataclass(frozen=True)
class StructuredCoreCasePlan:
    case_id: str
    parent_id: str
    requirements: tuple[StructuredCoreRequirement, ...]
    adapter_methods: tuple[str, ...]
    local_executor_status: str
    remote_validation: str
    grants_release_credit: bool

    @property
    def requirement_ids(self) -> tuple[str, ...]:
        return tuple(row.requirement_id for row in self.requirements)

    @property
    def adapters(self) -> dict[str, str]:
        return {row.requirement_id: row.adapter_method for row in self.requirements}

    def validate(self) -> None:
        if (self.case_id != CASE_ID or self.parent_id != PARENT
                or self.adapter_methods != ADAPTER_METHODS
                or len(self.requirements) != 15
                or self.local_executor_status != 'LOCAL_FULL_EXECUTOR'
                or self.remote_validation != 'NOT_RUN'
                or self.grants_release_credit is not False
                or tuple(row.requirement_id for row in self.requirements) != CORE_REQUIREMENT_IDS
                or len(self.adapters.values()) != len(set(self.adapters.values()))):
            raise RunStateError('structured-core case is incomplete')
        for requirement in self.requirements:
            requirement.validate()


def structured_core_case_plan() -> StructuredCoreCasePlan:
    binding = structured_source_binding()
    if binding['parent'] != PARENT:
        raise RunStateError('structured-core source parent changed')
    rows = [row for row in binding['requirements'] if row['id'] in CORE_REQUIREMENT_IDS]
    if [row['id'] for row in rows] != list(CORE_REQUIREMENT_IDS):
        raise RunStateError('structured-core source obligations changed')
    plan = StructuredCoreCasePlan(
        case_id=CASE_ID,
        parent_id=PARENT,
        requirements=tuple(
            StructuredCoreRequirement(
                requirement_id=row['id'], source_method=row['method'],
                source_path=row['path'], public_path=_ROUTES[row['id']],
                actor=row['actor'], adapter_method=ADAPTER_METHODS[index],
                expectation=row['expectation'],
            )
            for index, row in enumerate(rows)
        ),
        adapter_methods=ADAPTER_METHODS,
        local_executor_status='LOCAL_FULL_EXECUTOR',
        remote_validation='NOT_RUN',
        grants_release_credit=False,
    )
    plan.validate()
    return plan


def structured_core_adaptability() -> dict:
    plan = structured_core_case_plan()
    return {
        'case_id': plan.case_id,
        'legacy_id': plan.parent_id,
        'requirement_ids': list(plan.requirement_ids),
        'selected_obligations': [row.requirement_id.split('.', 1)[1]
                                  for row in plan.requirements],
        'public_paths': [row.public_path for row in plan.requirements],
        'environments': ['owned-local-full-story', 'singlebox-public-slice',
                         'preprod-public-slice'],
        'local_executor_status': plan.local_executor_status,
        'remote_validation': plan.remote_validation,
        'grants_release_credit': plan.grants_release_credit,
        'blocker': ('This is the stateful group/definition/run core slice. It '
                    'creates and finalizes one owned Group per execution; a live '
                    'Singlebox or Gateway fixture still has to be admitted before '
                    'it can grant release credit.'),
    }


def validate_structured_core_adapter(adapter: object) -> None:
    plan = structured_core_case_plan()
    missing = [name for name in plan.adapter_methods
               if not callable(getattr(adapter, name, None))]
    if missing:
        raise RunStateError('structured-core adapter is missing methods: '
                            + ', '.join(missing))


def _validate_result(requirement: StructuredCoreRequirement, result: AdapterCall,
                     index: int, *, binding_auth: str | None = None) -> None:
    evidence = result.evidence
    # The reviewed actor per obligation is environment-bound: the community
    # Singlebox contract keeps the authenticated CEO Bot bearer on structured
    # Group create, while station-internal preprod ACE enforces the owning
    # Human cookie (see the route_bindings preprod override). Remote adapters
    # expose per-operation bindings and make that environment truth
    # authoritative; adapters without bindings (owned-local Singlebox) keep
    # the reviewed CEO actor.
    authenticated = (binding_auth == 'authenticated_bot'
                     if binding_auth is not None
                     else requirement.actor == 'CEO')
    expected_effect = _STEP_EFFECTS.get(requirement.requirement_id, 'NONE')
    if (not isinstance(evidence, dict)
            or evidence.get('requirement_id') != requirement.requirement_id
            or evidence.get('operation') != _REQUEST_OPERATIONS[index]
            or evidence.get('http_method') != _REQUEST_METHODS[index]
            or evidence.get('http_status') != _REQUEST_STATUS[index]
            or evidence.get('transport') != 'native-v1-http'
            or evidence.get('identity') != ('AUTHENTICATED_DRIVER_BOT'
                                             if authenticated else 'HUMAN_COOKIE')
            or evidence.get('credentials_sent') is not True
            or evidence.get('authorization_sent') is not authenticated
            or evidence.get('fixture_effect') != expected_effect
            or not isinstance(result.data, dict)):
        raise RunStateError('structured-core evidence is outside the reviewed slice')


def execute_structured_core_case(adapter: object) -> CaseExecution:
    """Run the 15 reviewed structured-collaboration obligations in order."""
    plan = structured_core_case_plan()
    validate_structured_core_adapter(adapter)
    steps: list[StepOutcome] = []
    error_type: str | None = None
    completed = 0
    for index, requirement in enumerate(plan.requirements):
        method_name = requirement.adapter_method
        if error_type is not None:
            steps.append(StepOutcome(requirement_id=requirement.requirement_id,
                                     method=method_name, status='NOT_RUN',
                                     data=None, evidence={}, error_type=None))
            continue
        try:
            result = getattr(adapter, method_name)()
            if not isinstance(result, AdapterCall):
                raise RunStateError(method_name + ' did not return AdapterCall')
            result.validate()
            operation = _REQUEST_OPERATIONS[index]
            adapter_bindings = getattr(adapter, 'bindings', None) or {}
            binding = adapter_bindings.get(operation) if isinstance(adapter_bindings, dict) else None
            _validate_result(requirement, result, index,
                             binding_auth=binding.get('auth') if isinstance(binding, dict) else None)
            steps.append(StepOutcome(requirement_id=requirement.requirement_id,
                                     method=method_name, status='PASS',
                                     data=dict(result.data),
                                     evidence=dict(result.evidence),
                                     error_type=None))
            completed += 1
        except Exception as error:
            error_type = type(error).__name__
            steps.append(StepOutcome(requirement_id=requirement.requirement_id,
                                     method=method_name, status='NOT_RUN',
                                     data=None, evidence={},
                                     error_type=error_type))
    execution = CaseExecution(case_id=plan.case_id, parent_id=plan.parent_id,
                              status='PASS' if error_type is None else 'BLOCKED',
                              steps=tuple(steps), completed_count=completed,
                              total_count=15, error_type=error_type,
                              ready_to_execute=error_type is None)
    execution.validate()
    return execution
