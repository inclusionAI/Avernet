"""Keep the live Provider receipt assertions fail-closed."""
import importlib.util
from pathlib import Path
import queue
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    'caller_header_story', ROOT / 'apps/bcs/scripts/e2e-test/caller_execution_story.py',
)
STORY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STORY)


@pytest.mark.parametrize('environment,has_credential,header,accepted', [
    ('local', True, None, True),
    ('pre', True, 'test-value', True),
    ('pre', False, None, True),
    ('pre', True, None, False),
    ('pre', True, 'rewritten-value', False),
    ('local', True, 'test-value', False),
    ('pre', False, 'test-value', False),
])
def test_provider_receipt_requires_exact_policy_header(environment, has_credential, header, accepted):
    provider = SimpleNamespace(received=queue.Queue())
    story = STORY.Story('http://unused.invalid', 'key', provider, environment)
    story.registration = {'bcs_to_provider_token': 'test-value'}
    body = {'id': 'run', 'extensions': {}}
    if has_credential:
        body['extensions']['caller_execution_token'] = {'type': 'iam-token', 'token': 'test-value'}
    provider.received.put((body, 'Bearer test-value', header))
    if accepted:
        assert story.receive() == body
        assert story.delivered == [body]
    else:
        with pytest.raises(AssertionError, match='deployment caller header'):
            story.receive()
        assert not story.delivered


def test_provider_receipt_still_checks_service_authorization():
    provider = SimpleNamespace(received=queue.Queue())
    story = STORY.Story('http://unused.invalid', 'key', provider)
    story.registration = {'bcs_to_provider_token': 'test-value'}
    provider.received.put(({}, 'Bearer wrong-value', None))
    with pytest.raises(AssertionError, match='provider auth changed'):
        story.receive()
