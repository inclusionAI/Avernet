from functools import partial

import pytest

from agentclaw.community.core.bot_management.snapshot_guard import (
    ManagedSnapshotConflictError, template_snapshot_hash, validate_snapshot_write,
)
from agentclaw.community.core.repository.implementations.bot.template import TemplateRepository
from tests.community.core.bot_management.test_template_bot_repository import Database, seed


def test_conditional_write_rejects_stale_snapshot_and_keeps_newer_record():
    db = Database()
    seed(db)
    repository = TemplateRepository(db)
    old = repository.get_by_bot_id('bot')['ext']
    candidate = {**old, 'template_version_id': 8}
    guard = partial(validate_snapshot_write, candidate=candidate, expected_hash=template_snapshot_hash(old))
    repository.update_by_bot_id('bot', {'ext': {**old, 'template_version_id': 9}})
    with pytest.raises(ManagedSnapshotConflictError):
        repository.update_by_bot_id('bot', {'ext': candidate}, validator=guard)
    assert repository.get_by_bot_id('bot')['ext']['template_version_id'] == 9


@pytest.mark.parametrize('version', [9, '9', 9.0, '9.0'])
def test_even_matching_hash_cannot_downgrade_or_change_template_scope(version):
    current = {'template_key': 'coding', 'template_version_id': version}
    for candidate in [{'template_key': 'coding', 'template_version_id': 4}, {'template_key': 'other', 'template_version_id': 10}]:
        with pytest.raises(ManagedSnapshotConflictError):
            validate_snapshot_write(current, candidate=candidate, expected_hash=template_snapshot_hash(current))


def test_guard_allows_same_version_policy_refresh_and_hash_is_key_order_independent():
    current = {'template_key': 'coding', 'template_version_id': 9}
    assert template_snapshot_hash(current) == template_snapshot_hash(dict(reversed(list(current.items()))))
    validate_snapshot_write(current, candidate={**current, 'envs': {'DOMAIN_POLICY': 'new'}}, expected_hash=template_snapshot_hash(current))


def test_same_version_concurrent_form_change_is_protected_from_stale_merge():
    old = {'template_key': 'coding', 'template_version_id': 7, 'envs': {'CUSTOM': 'old'}}
    current = {**old, 'envs': {'CUSTOM': 'new'}}
    candidate = {**old, 'envs': {'CUSTOM': 'old', 'DOMAIN_POLICY': 'published'}}
    with pytest.raises(ManagedSnapshotConflictError):
        validate_snapshot_write(current, candidate=candidate, expected_hash=template_snapshot_hash(old))
