"""Explicit maintainer generator; never invoked by the executor or on import."""
import hashlib
import json
import re
import shlex
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT.parent / 'e2e-test/stories.sh'
PARENT = 'story_provider_operator_publishes_agent'
HELPER = '_story_provider_manages_organization'


def generate():
    lines = SOURCE.read_text().splitlines()
    def function(name):
        start = lines.index(name + '() {')
        end = lines.index('}', start)
        return list(enumerate(lines[start:end + 1], start + 1))
    parent, helper = function(PARENT), function(HELPER)
    expanded = []
    for line, text in parent:
        if text.strip().startswith(HELPER + ' '):
            expanded.extend((n, t, HELPER) for n, t in helper)
        else:
            expanded.append((line, text, PARENT))
    names = ('register slug-read collaboration-slug-read collaboration-slug-missing '
             'collaboration-slug-invalid slug-missing slug-invalid duplicate-slug-conflict '
             'read update old-slug-missing renamed-slug-read bot-publish bot-update '
             'attributes-read-denied attributes-write-denied org-create org-list org-read '
             'org-update candidate-list-before candidate-before member-add candidate-list-after '
             'candidate-after member-profile member-read member-list admin-start admin-read '
             'member-remove bot-list roster-denied agentpass-negative unknown-run-callback '
             'takeover-denied disable disabled-slug-read enable enabled-slug-read bot-retire '
             'bot-list-empty').split()
    operations, requirements, excluded = [], [], []
    operation = None
    for i, (n, text, func) in enumerate(expanded):
        if text.strip().startswith(('api_get ', 'api_post ', 'api_request_headers ')):
            tokens = shlex.split(text.strip().removesuffix('\\'))
            method = 'GET' if tokens[0] == 'api_get' else 'POST' if tokens[0] == 'api_post' else tokens[1]
            path = tokens[1] if tokens[0] != 'api_request_headers' else tokens[2]
            operation = names[len(operations)]
            operations.append({'id': operation, 'method': method, 'path': path})
        if not re.match(r'\s*(require_status|assert\w*) ', text):
            continue
        quote = text.strip()
        j = i
        while quote.endswith('\\'):
            j += 1
            quote += '\n' + expanded[j][1].strip()
        tokens = shlex.split(quote.replace('\\\n', ' '))
        command, label = tokens[:2]
        row = {'id': 'PROVIDER-PUBLISHES.' + operation + '.' + (
            'status' if command == 'require_status' else re.sub('[^a-z0-9]+', '-', label.lower()).strip('-')),
            'operation': operation, 'source': 'stories.sh', 'function': func,
            'line': n, 'end_line': expanded[j][0], 'quote': quote, 'expectation': label}
        if 'registration returns admin token' in label or 'registration returns BCS callback token' in label:
            row['classification'] = 'credential-admission-precondition-not-counted'
            excluded.append(row)
            continue
        row['classification'] = 'baseline-operation' if command == 'require_status' else 'field-density'
        if command == 'require_status':
            row.update(kind='status', expected=int(tokens[2]))
            operations[-1]['expected_status'] = int(tokens[2])
        elif command == 'assert_json_eq':
            expected = tokens[4]
            try:
                expected = json.loads(expected) if expected[:1] in ('[', '{') or expected in ('true', 'false', 'null') else expected
            except json.JSONDecodeError:
                pass
            row.update(kind='eq', path=tokens[3], expected=expected)
        elif command == 'assert_json_not_empty':
            row.update(kind='nonempty', path=tokens[3])
        elif command == 'assert_not_empty':
            path = {'$provider_id': 'provider_id', '$provider_bot_uuid': 'bot_uuid',
                    '$runtime_token': 'bot_runtime_token', '$admin_run_id': 'data.run_id'}[tokens[2]]
            row.update(kind='nonempty', path=path)
        elif command == 'assert_contains':
            row.update(kind='contains', expected=tokens[3])
        elif tokens[2] == '$public_keys':
            row.update(kind='keys', expected=tokens[3].split(','))
        elif tokens[2] == '$collaboration_projection':
            row.update(kind='projection', expected='$legacy_projection')
        else:
            key, field, ref = {'$listed_bot': ('items', 'bot_uuid', '$provider_bot_uuid'),
                              '$listed_organization': ('organizations', 'organization_code', '$organization_code'),
                              '$candidate_bot': ('bots', 'bot_uuid', '$provider_bot_uuid'),
                              '$listed_member': ('members', 'bot_uuid', '$provider_bot_uuid')}[tokens[2]]
            row.update(kind='membership', path=key, field=field, target=ref, expected=tokens[3] == '1')
        requirements.append(row)
    assert len(operations) == 42 and len(requirements) == 98 and len(excluded) == 2
    old = json.loads((ROOT / 'provider-migration-contract.json').read_text())
    prior = {r['id'].removeprefix('PROVIDER.'): r['id'] for r in old['requirements']}
    for row in requirements:
        row['prior_operation'] = prior.get(row['operation'])
        row['net_inflow'] = ('new-slug-operation-or-field' if row['operation'] not in prior else
                            'new-field-receipt-existing-inline-semantics' if row['kind'] != 'status' else
                            'existing-operation')
    authority = json.loads((ROOT / 'authority-dual-migration-contract.json').read_text())
    result = {'schema_version': 1, 'parent': PARENT, 'suite': 'owned-local-provider-publishes-v1',
        'case_id': 'LOCAL-PROVIDER-PUBLISHES-01', 'dual_case_id': 'DUAL-PROVIDER-PUBLISHES-01',
        'dual_suite': 'dual-provider-publishes-v1', 'obligation_prefix': 'PROVIDER-PUBLISHES.',
        'environment': {'local': 'LOCAL_EXECUTOR_ONLY', 'preprod': 'BLOCKED_AT_ENTRY_NO_NETWORK'},
        'source_revision': '61d2ec3d4e5a353ac3e52a77ea146f7b741f528e',
        'sources': {'stories.sh': hashlib.sha256(SOURCE.read_bytes()).hexdigest()},
        'baseline_obligations': 42, 'prior_executor_operations': 32, 'story_checks': 98,
        'field_obligations': 56, 'parsed_upstream_steps': 98, 'raw_assertion_clauses': 100,
        'extracted_digest_claim': 104, 'extracted_json_actual': 45,
        'net_inflow_note': '98 receipt obligations are not 56 newly invented behaviors: legacy inline field semantics are explicitly classified; 10 slug operations were absent from the old 32-operation map.',
        'scope_note': 'These functions contain no visibility enumeration or grey-release assertions; do not fabricate them.',
        'runtime_binary': authority['owned_runtime_binary'], 'cli_runtime_binary': authority['cli_runtime_binary'],
        'operations': operations, 'requirements': requirements, 'admission_preconditions': excluded,
        'remote_validation': 'NOT_RUN', 'grants_release_credit': False}
    result['cli_runtime_binary'] = dict(result['cli_runtime_binary'],
        role='author-cli-pair-admitted-not-consumed-by-http-journey')
    for key in ('authority_business_verbs', 'separation_note'):
        result['cli_runtime_binary'].pop(key, None)
    return result


if __name__ == '__main__':
    (ROOT / 'provider-publishes-migration-contract.json').write_text(
        json.dumps(generate(), ensure_ascii=False, indent=2) + '\n')
