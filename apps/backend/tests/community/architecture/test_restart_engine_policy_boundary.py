"""Shared published restart dispatches policies; it must not interpret coding state."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_CORE = Path(__file__).resolve().parents[3] / 'src/agentclaw/community/core'
_FLOW = 'service_bot/services/publish_flow/'
_SHARED_PATHS = (
    _FLOW + 'restart_mixin.py',
    _FLOW + 'progress_sync_mixin.py',
    _FLOW + 'operation_runner.py',
    _FLOW + 'tasks.py',
    'service_bot/services/service_publication_facade.py',
    'bot_management/engines/provisioning.py',
    'bot_management/engines/restart_contract.py',
)
_POLICY_KEYS = {'guard', 'precondition_error', 'fastdisk_targets', 'coding_restart',
                'fastdisk_fail_block'}
_POLICY_ERRORS = {'RestartPreconditionError', 'RestartBackupBlocked', 'RestartBackupError'}
_ENGINES = {'aicoding', 'claude_code'}


@pytest.mark.parametrize('relative', _SHARED_PATHS)
def test_shared_restart_does_not_interpret_coding_policy(relative):
    tree = ast.parse((_CORE / relative).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert '.engines.aicoding' not in (node.module or ''), (relative, node.lineno)
            assert not _POLICY_ERRORS.intersection(alias.name for alias in node.names)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert node.value not in _POLICY_KEYS | _POLICY_ERRORS | _ENGINES, (relative, node.lineno)
        if isinstance(node, ast.Name):
            assert node.id not in _POLICY_ERRORS, (relative, node.lineno)


def test_instance_dispatch_does_not_assemble_coding_backup_dependencies():
    tree = ast.parse((_CORE / 'bot_management/engines/registry.py').read_text())
    dispatcher = next(node for node in tree.body if getattr(node, 'name', '') == 'prepare_instance_restart')
    assert not any(isinstance(node, (ast.If, ast.Try, ast.ImportFrom, ast.Import))
                   for node in ast.walk(dispatcher))
    assert any(isinstance(node, ast.Attribute) and node.attr == 'prepare_restart_async'
               for node in ast.walk(dispatcher))


def test_published_restart_keeps_common_workflow_in_its_original_entrypoint():
    tree = ast.parse((_CORE / (_FLOW + 'restart_mixin.py')).read_text())
    mixin = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'RestartMixin')
    methods = {node.name: node for node in mixin.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert '_execute_prepared_restart' not in methods
    entry = methods['execute_restart']
    assert any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
               and node.func.id == 'acquire_deploy_workflow' for node in ast.walk(entry))
    assert any(isinstance(node, ast.With) and any(
        isinstance(item.context_expr, ast.Call)
        and isinstance(item.context_expr.func, ast.Attribute)
        and item.context_expr.func.attr == 'published_restart_scope'
        for item in node.items) for node in ast.walk(entry))


@pytest.mark.parametrize('relative, function', [
    ('adapters/http/bot_management/router.py', 'get_bot_status'),
    ('adapters/http/openapi_v1/bots/router.py', 'get_bot_status'),
    ('adapters/http/service_bot/router_publish.py', 'restart_status'),
    ('core/service_bot/services/publish_flow/progress_sync_mixin.py', 'sync_restart_progress'),
])
def test_status_consumers_do_not_interpret_engine_restart_fields(relative, function):
    source = _CORE.parent / relative
    tree = ast.parse(source.read_text())
    method = next(node for node in ast.walk(tree)
                  if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function)
    for node in ast.walk(method):
        if isinstance(node, ast.Attribute):
            assert node.attr != 'restart', (relative, node.lineno)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert node.value not in {'aicoding', 'coding_restart', 'BACKING_UP', 'QUEUED', 'RUNNING', 'SUCCEEDED'}, (relative, node.lineno)


def test_status_extensions_are_open_to_another_engine_strategy(monkeypatch):
    from agentclaw.community.core.bot_management.engines import registry
    from agentclaw.community.core.bot_management.engines.default import DefaultProvisioningStrategy
    from types import SimpleNamespace

    class OtherEngineStrategy(DefaultProvisioningStrategy):
        def project_status_ext(self, *, ext, response_ext, **kwargs):
            return {**response_ext, 'another_engine': {'progress': ext['custom_progress']}}

    strategies = registry.EngineProvisioningRegistry()
    strategies.register(OtherEngineStrategy('another_engine'))
    monkeypatch.setattr(registry, '_REGISTRY', strategies)
    ext = {'custom_progress': {'done': True}}
    result = registry.resolve_status_ext(
        {'active_engine': 'another_engine', 'ext': ext}, response_ext={'keep': 1},
    )
    assert result == {'keep': 1, 'another_engine': {'progress': {'done': True}}}
    record = SimpleNamespace(ext={**ext, 'restart': {'engine': 'another_engine'}})
    assert registry.resolve_published_status_ext(record) == {
        'another_engine': {'progress': {'done': True}},
    }
    assert strategies.resolve('another_engine').published_restart_result(record=record) is None
    assert 'aicoding' not in result
    assert ext == {'custom_progress': {'done': True}}
