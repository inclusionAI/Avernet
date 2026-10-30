"""Reproducible builder for the group-workspace density contract.

Emits ``group-workspace-density-contract.json`` from a frozen 63-atom table
(API 26 + CLI 11 + story tail 26).  Every row carries a source anchor: its
``source_quote`` must appear exactly once inside the named upstream function
(group.sh / stories.sh), or the build fails.  A changed upstream harness
therefore cannot silently desynchronize the density contract; an anchor
failure forces an explicit re-review.

Mapping notes (owned-local evidence to upstream clause):

- Upstream ``test_get_group_detail`` creates its own throwaway Group and reads
  it; the owned-local executor proves the same detail projection on its
  confirmed primary Group, so detail atoms address the primary detail read.
- Upstream ``test_list_groups`` lists the shared pool; the owned-local executor
  reads the bounded ``/groups?...group_kind=all`` page containing both owned
  Groups, which is the same projection.
- The legacy patch wire maps the submitted ``name`` onto the read-back
  ``label``; the response-name atom observes the retained PATCH value and is
  NOT_RUN on a product face that does not echo ``name``.

No credential, token or real principal enters the contract; expectations
reference owned-local fixture literals or runtime bindings that the projector
resolves from the journals themselves.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

RUNNER_ROOT = Path(__file__).resolve().parents[1]
E2E_TEST_ROOT = RUNNER_ROOT.parent / 'e2e-test'
OUTPUT_PATH = RUNNER_ROOT / 'group-workspace-density-contract.json'

GROUP_SH = 'group.sh'
STORIES_SH = 'stories.sh'
STORY = 'story_user_operates_group_workspace'
CONTRACT = 'group-workspace-density-v1'
PREDICATE_VOCABULARY = (
    'status_eq', 'field_eq', 'field_not_empty', 'field_len_eq', 'field_ge',
    'exit_zero', 'stdout_contains', 'stdout_contains_any', 'stdout_regex',
    'any_of', 'read_present', 'read_field_eq', 'read_field_not_empty',
    'read_field_len_eq', 'read_field_ge', 'not_found_state',
    'settings_retired_form', 'settings_policy_form',
)


def op(journal: str, operation: str) -> dict:
    return {'journal': journal, 'kind': 'operation', 'operation': operation}


def read(path_template: str, after_operation: str) -> dict:
    return {'journal': 'api', 'kind': 'read', 'path_template': path_template,
            'after_operation': after_operation}


def status_eq(value: int) -> dict:
    return {'type': 'status_eq', 'value': value}


def field_eq(field: str, expected) -> dict:
    return {'type': 'field_eq', 'path': 'value.' + field, 'expected': expected}


def field_not_empty(field: str) -> dict:
    return {'type': 'field_not_empty', 'path': 'value.' + field}


def read_present() -> dict:
    return {'type': 'read_present'}


def read_field_eq(field: str, expected) -> dict:
    return {'type': 'read_field_eq', 'path': field, 'expected': expected}


def read_field_not_empty(field: str) -> dict:
    return {'type': 'read_field_not_empty', 'path': field}


def read_field_len_eq(field: str, value: int) -> dict:
    return {'type': 'read_field_len_eq', 'path': field, 'value': value}


def read_field_ge(field: str, value: int) -> dict:
    return {'type': 'read_field_ge', 'path': field, 'value': value}


def exit_zero() -> dict:
    return {'type': 'exit_zero'}


def stdout_contains(value) -> dict:
    return {'type': 'stdout_contains', 'value': value}


def stdout_contains_any(values: list) -> dict:
    return {'type': 'stdout_contains_any', 'values': values}


def stdout_regex(pattern: str, capture_eq_binding: str) -> dict:
    return {'type': 'stdout_regex', 'pattern': pattern, 'capture_eq': capture_eq_binding}


def any_of(checks: list) -> dict:
    return {'type': 'any_of', 'checks': checks}


def settings_retired_form() -> dict:
    return {'type': 'settings_retired_form'}


def settings_policy_form() -> dict:
    return {'type': 'settings_policy_form'}


# --- Frozen 63-atom table -------------------------------------------------
# (id, family, expectation, source_file, source_function, line_start, line_end,
#  source_quote, address, checks)
ROWS = []


def row(atom_id: str, family: str, expectation: str, source_file: str, function: str,
        line_start: int, line_end: int, quote: str, address: dict, checks: list) -> None:
    ROWS.append((atom_id, family, expectation, source_file, function, line_start,
                 line_end, quote, address, checks))


API_PRIMARY_DETAIL = read('/groups/{primary}', 'group-create-with-members')
API_MEMBERS_DETAIL = read('/groups/{members}', 'group-create-with-members')
API_PATCH_READ = read('/groups/{primary}', 'group-patch')
API_LIST_READ = read('/groups?list-all', None)
API_ADD_READ = read('/groups/{primary}', 'group-member-add')
API_REMOVE_READ = read('/groups/{primary}', 'group-member-remove')
API_LABEL_READ = read('/groups/{primary}', 'group-label')
API_VISIBILITY_READ = read('/groups/{primary}', 'group-visibility')

# A. API atoms (26) — group.sh
row('WORKSPACE.api.create.status', 'api', 'create group returns 200', GROUP_SH,
    'test_create_group', 42, 42,
    'assert_eq "create group returns 200" "$HTTP_STATUS" "200"',
    op('api', 'group-create-primary'), [status_eq(200)])
row('WORKSPACE.api.create.group-id', 'api', 'create group returns group_id', GROUP_SH,
    'test_create_group', 45, 45,
    'assert_not_empty "create group returns group_id" "$group_id"',
    op('api', 'group-create-primary'), [field_eq('id', {'binding': 'primary'})])
row('WORKSPACE.api.members.create.status', 'api', 'create group with members returns 200', GROUP_SH,
    'test_create_group_with_members', 52, 52,
    'assert_eq "create group with members returns 200" "$HTTP_STATUS" "200"',
    op('api', 'group-create-with-members'), [status_eq(200)])
row('WORKSPACE.api.members.create.group-id', 'api', 'group with members has id', GROUP_SH,
    'test_create_group_with_members', 55, 55,
    'assert_not_empty "group with members has id" "$group_id"',
    op('api', 'group-create-with-members'), [field_eq('id', {'binding': 'members'})])
row('WORKSPACE.api.members.detail.status', 'api', 'get group detail returns 200', GROUP_SH,
    'test_create_group_with_members', 58, 58,
    'assert_eq "get group detail returns 200" "$HTTP_STATUS" "200"',
    API_MEMBERS_DETAIL, [read_present()])
row('WORKSPACE.api.members.detail.participant-count', 'api', 'group has 3 participants', GROUP_SH,
    'test_create_group_with_members', 61, 61,
    'assert_eq "group has 3 participants" "$count" "3"',
    API_MEMBERS_DETAIL, [read_field_len_eq('participants', 3)])
row('WORKSPACE.api.detail.status', 'api', 'get group detail returns 200', GROUP_SH,
    'test_get_group_detail', 71, 71,
    'assert_eq "get group detail returns 200" "$HTTP_STATUS" "200"',
    API_PRIMARY_DETAIL, [read_present()])
row('WORKSPACE.api.detail.group-id', 'api', 'detail has id', GROUP_SH,
    'test_get_group_detail', 72, 72,
    'assert_not_empty "detail has id" "$(json_field "$RESPONSE" "id")"',
    API_PRIMARY_DETAIL,
    [{'type': 'read_field_eq', 'path': 'id', 'expected': {'binding': 'primary'}}])
row('WORKSPACE.api.detail.driver-bot', 'api', 'detail has driver_bot', GROUP_SH,
    'test_get_group_detail', 73, 73,
    'assert_not_empty "detail has driver_bot" "$(json_field "$RESPONSE" "driver_bot")"',
    API_PRIMARY_DETAIL,
    [{'type': 'read_field_eq', 'path': 'driver_bot', 'expected': {'binding': 'roles.CEO'}}])
row('WORKSPACE.api.detail.group-kind', 'api', 'detail has group_kind', GROUP_SH,
    'test_get_group_detail', 74, 74,
    'assert_not_empty "detail has group_kind" "$(json_field "$RESPONSE" "group_kind")"',
    API_PRIMARY_DETAIL, [read_field_not_empty('group_kind')])
row('WORKSPACE.api.detail.visibility', 'api', 'detail has visibility', GROUP_SH,
    'test_get_group_detail', 75, 75,
    'assert_not_empty "detail has visibility" "$(json_field "$RESPONSE" "visibility")"',
    API_PRIMARY_DETAIL, [read_field_not_empty('visibility')])
row('WORKSPACE.api.patch.status', 'api', 'legacy group PATCH returns 200', GROUP_SH,
    'test_patch_group', 85, 85,
    'assert_eq "legacy group PATCH returns 200" "$HTTP_STATUS" "200"',
    op('api', 'group-patch'), [status_eq(200)])
row('WORKSPACE.api.patch.responds-name', 'api', 'legacy group PATCH returns the new name', GROUP_SH,
    'test_patch_group', 86, 87,
    'assert_eq "legacy group PATCH returns the new name" "$(json_field "$RESPONSE" "name")" "legacy-patch-e2e"',
    op('api', 'group-patch'), [field_eq('name', 'e2e-workspace-patched')])
row('WORKSPACE.api.patch.persists-label', 'api', 'legacy group PATCH persists the label', GROUP_SH,
    'test_patch_group', 90, 91,
    'assert_eq "legacy group PATCH persists the label" "$(json_field "$RESPONSE" "label")" "legacy-patch-e2e"',
    API_PATCH_READ, [read_field_eq('label', 'e2e-workspace-patched')])
row('WORKSPACE.api.patch.persists-context', 'api', 'legacy group PATCH persists the context', GROUP_SH,
    'test_patch_group', 92, 93,
    'assert_eq "legacy group PATCH persists the context" "$(json_field "$RESPONSE" "context")" "legacy-context-e2e"',
    API_PATCH_READ, [read_field_eq('context', 'e2e-workspace-context')])
row('WORKSPACE.api.list.status', 'api', 'list groups returns 200', GROUP_SH,
    'test_list_groups', 102, 102,
    'assert_eq "list groups returns 200" "$HTTP_STATUS" "200"',
    API_LIST_READ, [read_present()])
row('WORKSPACE.api.list.total-not-empty', 'api', 'list groups returns total', GROUP_SH,
    'test_list_groups', 105, 105,
    'assert_not_empty "list groups returns total" "$total"',
    API_LIST_READ, [read_field_not_empty('total')])
row('WORKSPACE.api.list.total-at-least-one', 'api', 'list groups has at least 1 group', GROUP_SH,
    'test_list_groups', 107, 109, 'pass "list groups has at least 1 group"',
    API_LIST_READ, [read_field_ge('total', 1)])
row('WORKSPACE.api.add.status', 'api', 'add member returns 200', GROUP_SH,
    'test_add_member', 123, 123,
    'assert_eq "add member returns 200" "$HTTP_STATUS" "200"',
    op('api', 'group-member-add'), [status_eq(200)])
row('WORKSPACE.api.add.participant-count', 'api', 'group has 2 participants after add', GROUP_SH,
    'test_add_member', 128, 128,
    'assert_eq "group has 2 participants after add" "$count" "2"',
    API_ADD_READ, [read_field_len_eq('participants', 2)])
row('WORKSPACE.api.remove.status', 'api', 'remove member returns 200', GROUP_SH,
    'test_remove_member', 139, 139,
    'assert_eq "remove member returns 200" "$HTTP_STATUS" "200"',
    op('api', 'group-member-remove'), [status_eq(200)])
row('WORKSPACE.api.remove.participant-count', 'api', 'group has 1 participant after remove', GROUP_SH,
    'test_remove_member', 144, 144,
    'assert_eq "group has 1 participant after remove" "$count" "1"',
    API_REMOVE_READ, [read_field_len_eq('participants', 1)])
row('WORKSPACE.api.label.status', 'api', 'update label returns 200', GROUP_SH,
    'test_update_group_label', 160, 160,
    'assert_eq "update label returns 200" "$HTTP_STATUS" "200"',
    op('api', 'group-label'), [status_eq(200)])
row('WORKSPACE.api.label.persists-label', 'api', 'label is updated', GROUP_SH,
    'test_update_group_label', 165, 165,
    'assert_eq "label is updated" "$label" "e2e-test-label"',
    API_LABEL_READ, [read_field_eq('label', 'e2e-workspace-label')])
row('WORKSPACE.api.visibility.status', 'api', 'update visibility returns 200', GROUP_SH,
    'test_update_group_visibility', 175, 175,
    'assert_eq "update visibility returns 200" "$HTTP_STATUS" "200"',
    op('api', 'group-visibility'), [status_eq(200)])
row('WORKSPACE.api.visibility.persists-public', 'api', 'visibility is public', GROUP_SH,
    'test_update_group_visibility', 180, 180,
    'assert_eq "visibility is public" "$vis" "public"',
    API_VISIBILITY_READ, [read_field_eq('visibility', 'public')])

# B. CLI atoms (11) — group.sh bcs-cli functions
row('WORKSPACE.cli.create.exit-and-id', 'cli', 'bcs-cli create-group returns the Group ID', GROUP_SH,
    'test_group_create_via_cli', 213, 213,
    'fail "bcs-cli create-group returned no group_id"',
    op('cli', 'create-primary'),
    [exit_zero(), stdout_regex(r'^  ID: (.+)$', 'cli_primary')])
row('WORKSPACE.cli.create.roundtrip', 'cli', 'bcs-cli create-group + get-group round-trip ok', GROUP_SH,
    'test_group_create_via_cli', 218, 218,
    'pass "bcs-cli create-group + get-group round-trip ok ($gid)"',
    op('cli', 'get-primary'),
    [exit_zero(), stdout_contains({'binding': 'cli_primary'})])
row('WORKSPACE.cli.get.exit', 'cli', 'get-group exited 0', GROUP_SH,
    'test_group_get_via_cli', 233, 233,
    'fail "get-group exited $BCS_CLI_EXIT"',
    op('cli', 'get-primary'), [exit_zero()])
row('WORKSPACE.cli.get.id-and-driver', 'cli', 'get-group returns id + driver', GROUP_SH,
    'test_group_get_via_cli', 235, 235, 'pass "get-group returns id + driver"',
    op('cli', 'get-primary'),
    [exit_zero(), stdout_contains({'binding': 'cli_primary'}),
     stdout_contains({'binding': 'roles.PM'})])
row('WORKSPACE.cli.add.exit', 'cli', 'add-member exited 0', GROUP_SH,
    'test_group_add_member_via_cli', 255, 255,
    'fail "add-member exited $BCS_CLI_EXIT"',
    op('cli', 'add-member'), [exit_zero()])
row('WORKSPACE.cli.add.echoes-member', 'cli', 'add-member via CLI ok', GROUP_SH,
    'test_group_add_member_via_cli', 250, 250, 'pass "add-member via CLI ok"',
    op('cli', 'get-after-add'),
    [exit_zero(), stdout_contains({'binding': 'roles.QA'})])
row('WORKSPACE.cli.fuse.exit-or-json', 'cli', 'fuse via CLI ok (exit 0 or payload)', GROUP_SH,
    'test_group_fuse_via_cli', 266, 266, 'pass "fuse via CLI ok (exit 0)"',
    op('cli', 'fuse'),
    [any_of([exit_zero(), stdout_contains('{')])])
row('WORKSPACE.cli.status.exit', 'cli', 'group-status exited 0', GROUP_SH,
    'test_group_status_via_cli', 292, 292,
    'fail "group-status exited $BCS_CLI_EXIT"',
    op('cli', 'status-primary'), [exit_zero()])
row('WORKSPACE.cli.status.reflects-completed', 'cli', 'group-status via CLI ok', GROUP_SH,
    'test_group_status_via_cli', 287, 287, 'pass "group-status via CLI ok"',
    op('cli', 'get-after-status'), [stdout_contains('completed')])
row('WORKSPACE.cli.terminate.exit', 'cli', 'terminate-group exited 0', GROUP_SH,
    'test_group_terminate_via_cli', 310, 310,
    'fail "terminate-group exited $BCS_CLI_EXIT"',
    op('cli', 'terminate'), [exit_zero()])
row('WORKSPACE.cli.terminate.reflects', 'cli', 'terminate-group via CLI ok', GROUP_SH,
    'test_group_terminate_via_cli', 305, 305, 'pass "terminate-group via CLI ok"',
    op('cli', 'get-termination'),
    [any_of([exit_zero(), stdout_contains_any(['completed', 'closed', 'terminated'])])])

# C. Story-tail atoms (26) — stories.sh _story_group_metadata_without_workspace
TAIL = '_story_group_metadata_without_workspace'
row('WORKSPACE.tail.create.status', 'tail', 'driver creates the incident response group', STORIES_SH,
    TAIL, 433, 433, 'require_status "driver creates the incident response group" "200"',
    op('incident', 'incident-create'), [status_eq(200)])
row('WORKSPACE.tail.create.group-id', 'tail', 'incident response group has an id', STORIES_SH,
    TAIL, 436, 436, 'assert_not_empty "incident response group has an id" "$group_id"',
    op('incident', 'incident-create'), [field_eq('id', {'binding': 'incident'})])
row('WORKSPACE.tail.read.status', 'tail', 'user reads the incident response group', STORIES_SH,
    TAIL, 440, 440, 'require_status "user reads the incident response group" "200"',
    op('incident', 'incident-detail'), [status_eq(200)])
row('WORKSPACE.tail.read.keep-label', 'tail', 'incident response group keeps its label', STORIES_SH,
    TAIL, 441, 441, 'assert_json_eq "incident response group keeps its label" "$RESPONSE" "label" "Incident response room"',
    op('incident', 'incident-detail'), [field_eq('label', 'Incident response room')])
row('WORKSPACE.tail.mute.status', 'tail', 'participant mutes itself', STORIES_SH,
    TAIL, 444, 444, 'require_status "participant mutes itself" "200"',
    op('incident', 'participant-mode'), [status_eq(200)])
row('WORKSPACE.tail.mute.mode', 'tail', 'participant mode is muted', STORIES_SH,
    TAIL, 445, 445, 'assert_json_eq "participant mode is muted" "$RESPONSE" "data.mode" "muted"',
    op('incident', 'participant-mode'),
    [{'type': 'field_eq', 'path': 'value.data.mode', 'expected': 'muted'}])
row('WORKSPACE.tail.mute.actor', 'tail', 'participant mode response identifies the actor', STORIES_SH,
    TAIL, 446, 446,
    'assert_json_eq "participant mode response identifies the actor" "$RESPONSE" "data.actor_id" "$BOT_ENG_UUID"',
    op('incident', 'participant-mode'),
    [{'type': 'field_eq', 'path': 'value.data.actor_id', 'expected': {'binding': 'roles.ENG'}}])
row('WORKSPACE.tail.workspace.write-gone', 'tail', 'deprecated group workspace writes are unavailable', STORIES_SH,
    TAIL, 450, 450,
    'require_status "deprecated group workspace writes are unavailable" "404"',
    op('incident', 'workspace-put'), [status_eq(404)])
row('WORKSPACE.tail.workspace.read-gone', 'tail', 'deprecated group workspace reads are unavailable', STORIES_SH,
    TAIL, 453, 453,
    'require_status "deprecated group workspace reads are unavailable" "404"',
    op('incident', 'workspace-get'), [status_eq(404)])
row('WORKSPACE.tail.routing.status', 'tail', 'driver configures structured routing', STORIES_SH,
    TAIL, 457, 457, 'require_status "driver configures structured routing" "200"',
    op('incident', 'routing-policy-put'), [status_eq(200)])
row('WORKSPACE.tail.routing.mode', 'tail', 'routing mode is persisted', STORIES_SH,
    TAIL, 458, 458, 'assert_json_eq "routing mode is persisted" "$RESPONSE" "routing_policy.mode" "structured"',
    op('incident', 'routing-policy-put'),
    [{'type': 'field_eq', 'path': 'value.routing_policy.mode', 'expected': 'structured'}])
row('WORKSPACE.tail.routing.default-delivery', 'tail', 'default delivery is persisted', STORIES_SH,
    TAIL, 459, 459,
    'assert_json_eq "default delivery is persisted" "$RESPONSE" "routing_policy.default_bot_final_delivery" "inject_observers"',
    op('incident', 'routing-policy-put'),
    [{'type': 'field_eq', 'path': 'value.routing_policy.default_bot_final_delivery',
      'expected': 'inject_observers'}])
row('WORKSPACE.tail.settings.gone-or-legacy', 'tail', 'retired group policy settings return Gone', STORIES_SH,
    TAIL, 462, 462,
    'require_status "retired group policy settings return Gone" "410"',
    op('incident', 'settings-patch'), [settings_retired_form()])
row('WORKSPACE.tail.settings.policy-identified', 'tail', 'retired policy is identified', STORIES_SH,
    TAIL, 463, 463,
    'assert_json_eq "retired policy is identified" "$RESPONSE" "error" "service_policy_retired"',
    op('incident', 'settings-patch'), [settings_policy_form()])
row('WORKSPACE.tail.message.status', 'tail', 'human posts a persistent group message', STORIES_SH,
    TAIL, 467, 467, 'require_status "human posts a persistent group message" "200"',
    op('messages', 'persistent-message'), [status_eq(200)])
row('WORKSPACE.tail.message.receives-id', 'tail', 'persistent message receives an id', STORIES_SH,
    TAIL, 468, 468,
    'assert_json_not_empty "persistent message receives an id" "$RESPONSE" "message_id"',
    op('messages', 'persistent-message'), [field_not_empty('message_id')])
row('WORKSPACE.tail.message.records-routing', 'tail', 'persistent message records routing targets', STORIES_SH,
    TAIL, 469, 469,
    'assert_json_not_empty "persistent message records routing targets" "$RESPONSE" "routed_to"',
    op('messages', 'persistent-message'), [field_not_empty('routed_to')])
row('WORKSPACE.tail.chat.status', 'tail', 'driver sends a live group chat message', STORIES_SH,
    TAIL, 473, 473, 'require_status "driver sends a live group chat message" "200"',
    op('messages', 'live-chat'), [status_eq(200)])
row('WORKSPACE.tail.chat.identifies-group', 'tail', 'group chat response identifies the group', STORIES_SH,
    TAIL, 474, 474, 'assert_json_eq "group chat response identifies the group" "$RESPONSE" "group_id" "$group_id"',
    op('messages', 'live-chat'), [field_eq('group_id', {'binding': 'incident'})])
row('WORKSPACE.tail.chat.delivery-results', 'tail', 'group chat reports delivery results', STORIES_SH,
    TAIL, 475, 475,
    'assert_json_not_empty "group chat reports delivery results" "$RESPONSE" "delivery_results"',
    op('messages', 'live-chat'), [field_not_empty('delivery_results')])
row('WORKSPACE.tail.callback.status', 'tail', 'agent callback is delivered into the group', STORIES_SH,
    TAIL, 479, 479, 'require_status "agent callback is delivered into the group" "200"',
    op('messages', 'agent-callback'), [status_eq(200)])
row('WORKSPACE.tail.callback.identifies-group', 'tail', 'callback response identifies the group', STORIES_SH,
    TAIL, 480, 480, 'assert_json_eq "callback response identifies the group" "$RESPONSE" "group_id" "$group_id"',
    op('messages', 'agent-callback'), [field_eq('group_id', {'binding': 'incident'})])
row('WORKSPACE.tail.callback.delivery-results', 'tail', 'callback reports delivery results', STORIES_SH,
    TAIL, 481, 481,
    'assert_json_not_empty "callback reports delivery results" "$RESPONSE" "delivery_results"',
    op('messages', 'agent-callback'), [field_not_empty('delivery_results')])
row('WORKSPACE.tail.delete.status', 'tail', 'driver closes the temporary incident group', STORIES_SH,
    TAIL, 484, 484, 'require_status "driver closes the temporary incident group" "200"',
    op('delete', 'incident-delete'), [status_eq(200)])
row('WORKSPACE.tail.delete.confirms-id', 'tail', 'group deletion confirms the id', STORIES_SH,
    TAIL, 485, 485, 'assert_json_eq "group deletion confirms the id" "$RESPONSE" "id" "$group_id"',
    op('delete', 'incident-delete'), [field_eq('id', {'binding': 'incident'})])
row('WORKSPACE.tail.delete.gone', 'tail', 'closed incident group is gone', STORIES_SH,
    TAIL, 487, 487, 'require_status "closed incident group is gone" "404"',
    {'journal': 'delete', 'kind': 'state', 'state_key': 'not_found'},
    [{'type': 'not_found_state', 'value': 404}])


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1_048_576), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _function_body(lines: list[str], function: str) -> str:
    start = None
    for index, line in enumerate(lines):
        if line.startswith(function + '() {'):
            start = index
            break
    if start is None:
        raise ValueError('function not found: ' + function)
    body = [lines[start]]
    for line in lines[start + 1:]:
        body.append(line)
        if line == '}':
            return '\n'.join(body)
    raise ValueError('function body never closes: ' + function)


def _norm(text: str) -> str:
    # Shell line continuations join wrapped assertions; drop the backslash.
    return ' '.join(text.replace('\\\n', ' ').split())


def validate_and_emit() -> dict:
    sources = {}
    for name in (GROUP_SH, STORIES_SH):
        sources[name] = _sha256(E2E_TEST_ROOT / name)
    seen = set()
    rows = []
    for (atom_id, family, expectation, source_file, function, line_start, line_end,
         quote, address, checks) in ROWS:
        if atom_id in seen:
            raise ValueError('duplicate atom id: ' + atom_id)
        seen.add(atom_id)
        lines = (E2E_TEST_ROOT / source_file).read_text().splitlines()
        body = _function_body(lines, function)
        normalized_body = _norm(body)
        hits = normalized_body.count(_norm(quote))
        if hits != 1:
            raise ValueError(f'anchor not unique ({hits}) for {atom_id}: {quote!r} in {function}')
        quoted_lines = _norm('\n'.join(lines[line_start - 1:line_end]))
        if _norm(quote) not in quoted_lines:
            raise ValueError(f'anchor lines mismatch for {atom_id}: quote not within recorded lines')
        rows.append({
            'id': atom_id, 'family': family, 'expectation': expectation,
            'source_file': source_file, 'source_function': function,
            'line_start': line_start, 'line_end': line_end, 'source_quote': quote,
            'address': address, 'checks': checks,
        })
    counts = {family: sum(1 for r in rows if r['family'] == family)
              for family in ('api', 'cli', 'tail')}
    if (len(rows) != 63 or counts != {'api': 26, 'cli': 11, 'tail': 26}):
        raise ValueError(f'density table shape changed: {len(rows)} rows, {counts}')
    payload = {
        'schema_version': 1, 'contract': CONTRACT, 'parent_story': STORY,
        'predicate_vocabulary': list(PREDICATE_VOCABULARY),
        'sources': {'group.sh': sources[GROUP_SH], 'stories.sh': sources[STORIES_SH],
                    'source_root': str(E2E_TEST_ROOT.relative_to(RUNNER_ROOT.parent))},
        'families': counts, 'atom_count': len(rows),
        'gap_policy': ('Missing event or absent field is NOT_RUN, never a fabricated '
                       'PASS; present-but-failing evidence is FAIL; dual product forms '
                       'are admitted explicitly (settings_retired_form).'),
        'density_requirements': rows,
    }
    OUTPUT_PATH.write_text(json.dumps(payload, indent=1, sort_keys=False, ensure_ascii=False) + '\n',
                           encoding='utf-8')
    return payload


if __name__ == '__main__':
    built = validate_and_emit()
    print('wrote', OUTPUT_PATH)
    print('atoms:', built['atom_count'], 'families:', built['families'])
    print('sources:', built['sources'])
