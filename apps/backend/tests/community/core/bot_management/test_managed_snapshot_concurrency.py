from contextlib import contextmanager
from functools import partial
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.orm import Session

from agentclaw.community.core.bot_management.engines.aicoding.strategy import (
    AicodingProvisioningStrategy,
)
from agentclaw.community.core.bot_management.engines.provisioning import (
    BotProvisioningContext,
)
from agentclaw.community.core.bot_management.services.template_service import (
    TemplateService,
)
from agentclaw.community.core.bot_management.snapshot_guard import (
    ManagedSnapshotConflictError,
    ManagedSnapshotPersistenceError,
    template_snapshot_hash,
    validate_snapshot_write,
)
from agentclaw.community.core.bot_management.token_vault import TokenVault
from agentclaw.community.core.repository.implementations.bot.template import (
    TemplateRepository,
)
from agentclaw.community.plugin_api.bot_template_write_extension import (
    TemplateWriteInvalidInput,
)
from tests.community.core.bot_management.managed_bot_support import Database, seed


class ProductionShapeDatabase(Database):
    def __init__(self):
        super().__init__()
        self.transactions = 0
        self.fail_commit = False

    @contextmanager
    def orm_session(self):
        with Session(self.engine.execution_options(isolation_level='AUTOCOMMIT')) as session:
            yield session
            session.flush()

    @contextmanager
    def transactional_orm_session(self):
        self.transactions += 1
        with Session(self.engine) as session, session.begin():
            yield session
            if self.fail_commit:
                raise RuntimeError('commit refused')


def test_guarded_writes_use_real_transaction_in_autocommit_runtime():
    db = ProductionShapeDatabase()
    seed(db)
    repository = TemplateRepository(db)
    current = repository.get_by_bot_id('bot')['ext']
    candidate = {**current, 'template_version_id': 8}
    guard = partial(validate_snapshot_write, candidate=candidate, expected_hash=template_snapshot_hash(current))
    repository.update_by_bot_id('bot', {'ext': candidate}, validator=guard)
    assert db.transactions == 1
    assert repository.get_by_bot_id('bot')['ext']['template_version_id'] == 8


def test_guarded_write_rolls_back_when_transaction_exit_fails():
    db = ProductionShapeDatabase()
    seed(db)
    repository = TemplateRepository(db)
    current = repository.get_by_bot_id('bot')['ext']
    candidate = {**current, 'template_version_id': 8}
    guard = partial(validate_snapshot_write, candidate=candidate, expected_hash=template_snapshot_hash(current))
    db.fail_commit = True
    with pytest.raises(RuntimeError, match='commit refused'):
        repository.update_by_bot_id('bot', {'ext': candidate}, validator=guard)
    assert repository.get_by_bot_id('bot')['ext']['template_version_id'] == 7


@pytest.mark.parametrize('stage', ['read', 'hook', 'encrypt', 'input'])
def test_guarded_preparation_errors_abort_without_persisting_or_exposing_secrets(stage):
    repository, extension, vault = Mock(), Mock(), Mock()
    repository.get_by_bot_id.return_value = {'ext': {'template_key': 'coding', 'template_version_id': 7}}
    extension.validate_template_write.return_value = None
    error = TemplateWriteInvalidInput('unsupported input') if stage == 'input' else RuntimeError('sensitive value')
    if stage == 'read':
        repository.get_by_bot_id.side_effect = error
    elif stage in {'hook', 'input'}:
        extension.validate_template_write.side_effect = error
    else:
        vault.encrypt.side_effect = error
    service = TemplateService(repository, vault, extension)
    expected = TemplateWriteInvalidInput if stage == 'input' else ManagedSnapshotPersistenceError
    with pytest.raises(expected) as caught:
        service.update_template_conditionally(bot_id='bot', template_config={
            'template_key': 'coding', 'template_version_id': 8, 'token': 'plain-token',
        }, expected_hash='a' * 64, template_type='applicationCoding', active_engine='aicoding')
    assert 'sensitive value' not in str(caught.value)
    if stage != 'input':
        assert AicodingProvisioningStrategy('aicoding').restart_config_error_is_fatal(caught.value)
    repository.update_by_bot_id.assert_not_called()


def test_old_marker_cleanup_cannot_overwrite_concurrent_new_snapshot():
    db = ProductionShapeDatabase()
    seed(db)
    repository = TemplateRepository(db)
    old = repository.get_by_bot_id('bot')['ext']
    old['_aicoding_restart'] = {'resync_authorization': True, 'template_version_id': 7}
    repository.update_by_bot_id('bot', {'ext': old})
    latest = {**old, 'template_version_id': 9, '_aicoding_restart': {
        'resync_authorization': True, 'template_version_id': 9,
    }}
    extension = Mock()

    def write_new_snapshot(**_kwargs):
        repository.update_by_bot_id('bot', {'ext': latest})
        return None

    extension.validate_template_write.side_effect = write_new_snapshot
    service = TemplateService(repository, TokenVault(master_key=''), extension)
    context = BotProvisioningContext(bot_id='bot', owner_id='owner', bot_type='personal',
                                    active_engine='aicoding', template_type='architect')
    with pytest.raises(ManagedSnapshotConflictError):
        AicodingProvisioningStrategy._clear_restart_resync_marker(context, template_service=service, template_config=old)
    assert repository.get_by_bot_id('bot')['ext'] == latest


def test_resync_does_not_clear_a_marker_written_during_its_projection(monkeypatch):
    db = ProductionShapeDatabase()
    seed(db)
    repository = TemplateRepository(db)
    old = {'template_key': 'coding', 'template_version_id': 7, '_aicoding_restart': {
        'resync_authorization': True, 'template_version_id': 7,
    }}
    latest = {**old, 'template_version_id': 9, '_aicoding_restart': {
        'resync_authorization': True, 'template_version_id': 9,
    }}
    repository.update_by_bot_id('bot', {'ext': old})
    service = TemplateService(repository, TokenVault(master_key=''))

    class InlineThread:
        def __init__(self, target, **_kwargs):
            self.target = target

        def start(self):
            self.target()

    async def replace_snapshot(**_kwargs):
        repository.update_by_bot_id('bot', {'ext': latest})

    monkeypatch.setattr('agentclaw.community.core.bot_management.engines.aicoding.strategy.threading.Thread', InlineThread)
    reconciler = Mock()
    reconciler.project_mcp_and_cli = AsyncMock(side_effect=replace_snapshot)
    context = BotProvisioningContext(bot_id='bot', owner_id='owner', bot_type='personal',
                                    active_engine='aicoding', template_type='architect', template_config=old)
    assert AicodingProvisioningStrategy('aicoding').refresh_restart_authorization(
        context, {'entity_id': 'owner', 'entity_type': 'staff'}, None,
        runtime_reconciler=reconciler, template_service=service,
    ) is True
    assert repository.get_by_bot_id('bot')['ext'] == latest


@pytest.mark.parametrize('replace_before_scheduling', [False, True])
def test_delayed_resync_worker_cannot_adopt_a_newer_operations_snapshot(monkeypatch, replace_before_scheduling):
    db = ProductionShapeDatabase()
    seed(db)
    repository = TemplateRepository(db)
    old = {'template_key': 'coding', 'template_version_id': 7, '_aicoding_restart': {
        'resync_authorization': True, 'template_version_id': 7,
    }}
    latest = {**old, 'template_version_id': 9, '_aicoding_restart': {
        'resync_authorization': True, 'template_version_id': 9,
    }}
    repository.update_by_bot_id('bot', {'ext': latest if replace_before_scheduling else old})
    service = TemplateService(repository, TokenVault(master_key=''))
    workers = []

    class DelayedThread:
        def __init__(self, target, **_kwargs):
            workers.append(target)

        def start(self):
            return None

    monkeypatch.setattr('agentclaw.community.core.bot_management.engines.aicoding.strategy.threading.Thread', DelayedThread)
    reconciler = Mock(project_mcp_and_cli=AsyncMock())
    context = BotProvisioningContext(bot_id='bot', owner_id='owner', bot_type='personal',
                                    active_engine='aicoding', template_type='architect', template_config=old)
    assert AicodingProvisioningStrategy('aicoding').refresh_restart_authorization(
        context, {'entity_id': 'owner', 'entity_type': 'staff'}, None,
        runtime_reconciler=reconciler, template_service=service,
    ) is True
    if not replace_before_scheduling:
        repository.update_by_bot_id('bot', {'ext': latest})
    workers[0]()
    assert repository.get_by_bot_id('bot')['ext'] == latest
