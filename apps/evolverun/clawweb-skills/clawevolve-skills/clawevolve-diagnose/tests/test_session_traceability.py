import json
from datetime import datetime, timezone

import pytest

from clawevolve_diagnose.acquisition.sessions import (
    _SessionStoreCandidate, _session_locator_from_store_candidate, _merge_store_metadata,
)
from clawevolve_diagnose.artifacts.session_source import source_session_key
from clawevolve_diagnose.integration.output import build_execution_output
from clawevolve_diagnose.models import RunResult, SessionRow


def row(path, key=''):
    return SessionRow('source-id', str(path), '', '', '', '', '', '', '', session_key=key)


def test_store_key_is_preserved_and_preferred(tmp_path):
    path = tmp_path / 'source.jsonl'
    path.write_text(json.dumps({'sessionKey': 'other'}))
    candidate = _SessionStoreCandidate('source-id', path, datetime.now(timezone.utc), store_key='store-key')
    assert source_session_key(_session_locator_from_store_candidate(candidate)) == 'store-key'
    assert _merge_store_metadata(row(path), candidate).session_key == 'store-key'


@pytest.mark.parametrize('keys,expected', [(['key-a', 'key-a'], 'key-a'), (['key-a', 'key-b'], ''), ([], '')])
def test_explicit_history_metadata_only(tmp_path, keys, expected):
    path = tmp_path / 'source.jsonl'
    events = [{'message': {'historyMeta': {'sessionKey': key}}} for key in keys]
    events.append({'message': {'content': 'sessionKey: not-metadata'}})
    path.write_text('\n'.join(json.dumps(event) for event in events))
    assert source_session_key(row(path)) == expected


def test_missing_file_keeps_missing_key(tmp_path):
    assert source_session_key(row(tmp_path / 'missing')) == ''


@pytest.mark.parametrize('artifact', ['diagnose_result_json', 'plan_source_json'])
@pytest.mark.parametrize('kind', ['good', 'bad'])
def test_source_survives_primary_and_fallback_output(tmp_path, artifact, kind):
    case = {'case_id': 'case-a', 'case_type': kind, 'summary': 'summary', 'session_id': 'full-source-id'}
    if artifact == 'plan_source_json':
        case['context'] = {'source_session_key': 'full-source-key'}
    else:
        case['session_key'] = 'full-source-key'
    path = tmp_path / 'artifact.json'
    path.write_text(json.dumps({'cases': [case]}))
    result = RunResult(tmp_path / 'summary.json', {'artifacts': {artifact: str(path)}})
    item = build_execution_output(result)['cases']['items'][0]
    assert item['sourceSessionId'] == 'full-source-id'
    assert item['sourceSessionKey'] == 'full-source-key'
    assert item['type'] == kind


def test_legacy_output_does_not_invent_source(tmp_path):
    path = tmp_path / 'artifact.json'
    path.write_text(json.dumps({'cases': [{'case_id': 'task_session_truncated', 'case_type': 'bad'}]}))
    result = RunResult(tmp_path / 'summary.json', {'artifacts': {'diagnose_result_json': str(path)}})
    assert set(build_execution_output(result)['cases']['items'][0]) == {'caseId', 'type', 'summary'}
