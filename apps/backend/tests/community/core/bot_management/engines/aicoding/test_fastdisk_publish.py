from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import base64
import json
import shlex

import pytest

from agentclaw.community.core.bot_management.engines.aicoding.artifact_transfer import (
    DETECTION, detection_command, FastdiskPublishConfig, AicodingArtifactTransferStrategy, FastdiskPublishError,
)
from agentclaw.community.core.workspace.engines.aicoding import _AICODING_BUILD_PLAN


@pytest.fixture(autouse=True)
def transport(monkeypatch):
    from agentclaw.community.core.bot_management.engines.aicoding import artifact_transfer as module
    from agentclaw.community.core.bot_management.engines.aicoding.artifact_command import ArtifactCommandError
    def run(executor, device_id, command, *, timeout_seconds):
        result = executor.exec_shell_new(device_id=device_id, shell_cmd=command,
                                         timeout_seconds=timeout_seconds)
        if result.exit_code != 0:
            raise ArtifactCommandError('transfer failed')
    monkeypatch.setattr(module, 'run_artifact_command', run)


def result(code=0, status='run'):
    return SimpleNamespace(exit_code=code, stdout=json.dumps({
        'status': status, 'home': '/home/admin/.aicoding', 'storage': '/opt/.aicoding',
    }))


def publisher(results):
    executor = Mock()
    executor.exec_shell_new.side_effect = results
    return AicodingArtifactTransferStrategy(executor, FastdiskPublishConfig(mist_secret_name="test-secret-name"), bucket='test',
                             endpoint='https://oss.example.test', oss_mount_root='/aidesktop', bindings=Mock())


def publish(p):
    return p.try_publish(device_id='device', plan=_AICODING_BUILD_PLAN,
        source_root='/home/admin/.aicoding',
        target_dir=Path('/aidesktop/aidesktop_prod/bolt_data/staff_u/b/3/aicoding'),
        ignore_paths=())


def test_non_fastdisk_preserves_original_path_and_never_uploads():
    p = publisher([result(status='skip_no_fastdisk')])
    assert publish(p) is False
    assert p.executor.exec_shell_new.call_count == 1


@pytest.mark.parametrize('failure', [result(1), TimeoutError(), result(status='bad')])
def test_detection_failure_blocks_without_retry_or_nas(failure):
    p = publisher([failure])
    with pytest.raises(FastdiskPublishError, match='detection failed'):
        publish(p)
    assert p.executor.exec_shell_new.call_count == 1


def test_detector_is_self_contained_and_fits_transport_limit():
    command = detection_command()
    assert len(command.encode()) < 8192
    assert '/home/admin/bin' not in command
    # The package deliberately contains no transfer implementation.
    assert {p.name for p in DETECTION.glob('*.py')} == {'persist_mount_gate.py', 'runtime_layout.py'}


def test_upload_uses_installed_script_and_legacy_unbounded_transfer():
    p = publisher([result(), result(), result()])
    assert publish(p) is True
    call = p.executor.exec_shell_new.call_args
    assert call.kwargs['timeout_seconds'] is None
    assert '/home/admin/bin/service_publish.py upload' in call.kwargs['shell_cmd']
    assert 'TemporaryDirectory' not in call.kwargs['shell_cmd']


@pytest.mark.parametrize('code,message', [(44, 'missing'), (45, 'incompatible'), (1, 'probe')])
def test_missing_or_incompatible_script_fails_without_fallback(code, message):
    p = publisher([result(), result(code)])
    with pytest.raises(FastdiskPublishError, match=message):
        publish(p)
    assert p.executor.exec_shell_new.call_count == 2


def test_upload_error_does_not_retry_another_script_or_legacy_source():
    p = publisher([result(), result(), result(44)])
    with pytest.raises(FastdiskPublishError, match='upload failed'):
        publish(p)
    assert p.executor.exec_shell_new.call_count == 3


def test_resolved_request_keeps_prefixes_and_home_optional_mappings():
    p = publisher([result(), result(), result()])
    assert publish(p)
    cmd = shlex.split(p.executor.exec_shell_new.call_args.kwargs['shell_cmd'])
    req = json.loads(base64.b64decode(cmd[-1]))
    assert req['version_prefix'] == 'aidesktop/aidesktop_prod/bolt_data/staff_u/b/3'
    assert req['engine_path'] == 'aicoding'
    assert req['additional_excludes'] == []
    assert req['build_ignore_paths'] == []
    assert not {'excludes', 'extra_sync', 'extra_include', 'local_bin', 'agentclaw'} & req.keys()
    assert req['timeout_seconds'] is None
    assert not any('secret' in k.lower() for k in req)


def test_unknown_probe_error_is_not_missing_upload_script():
    p = publisher([result(), result(1)])
    with pytest.raises(FastdiskPublishError, match='probe'):
        publish(p)
    assert p.executor.exec_shell_new.call_count == 2


def test_optional_timeouts_are_validated():
    for value in (0, -1, True, '900'):
        with pytest.raises(ValueError):
            FastdiskPublishConfig(upload_timeout_seconds=value)



def test_request_uses_mist_name_not_backend_credentials():
    p = publisher([result(), result(), result()])
    publish(p)
    req = json.loads(base64.b64decode(shlex.split(p.executor.exec_shell_new.call_args.kwargs['shell_cmd'])[-1]))
    assert req['schema_version'] == 6
    assert req['credential'] == {'provider': 'mist', 'secret_name': 'test-secret-name', 'concat_env': True}
    assert 'agentclaw' not in req


def restore(p):
    return p.try_restore(device_id='draft-device', plan=_AICODING_BUILD_PLAN,
        source_root='/home/admin/.aicoding', artifact_path='/opt/nfs/bot-data/3/aicoding',
        artifact_dir=Path('/aidesktop/aidesktop_prod/bolt_data/staff_u/b/3/aicoding'),
        remaining_timeout=lambda: 1800)


def test_fastdisk_restore_uses_existing_draft_device_and_long_timeout():
    p = publisher([result(), result(), result()])
    assert restore(p)
    call = p.executor.exec_shell_new.call_args
    assert call.kwargs['device_id'] == 'draft-device'
    assert 0 < call.kwargs['timeout_seconds'] <= 1800
    command = shlex.split(call.kwargs['shell_cmd'])
    assert command[2] == 'restore'
    req = json.loads(base64.b64decode(command[-1]))
    assert req['artifact_path'] == '/opt/nfs/bot-data/3/aicoding'
    assert 'extra_sync' not in req
    assert req['additional_excludes'] == []
    assert req['credential']['provider'] == 'mist'
    assert req['version_prefix'] == 'aidesktop/aidesktop_prod/bolt_data/staff_u/b/3'
    assert 'object_prefix' not in req
    assert req['chown'] == '1000:1000'


def test_non_fastdisk_restore_retains_nas_path():
    p = publisher([result(status='skip_no_fastdisk')])
    assert restore(p) is None
    assert p.executor.exec_shell_new.call_count == 1


def test_failed_restore_stops_without_nas_fallback():
    p = publisher([result(), result(), result(1)])
    with pytest.raises(FastdiskPublishError, match='restore failed'):
        restore(p)
    assert p.executor.exec_shell_new.call_count == 3


@pytest.mark.parametrize('content,expected_error', [
    (None, 'missing'),
    ('raise RuntimeError("must not execute old script")', 'incompatible'),
    ('SERVICE_PUBLISH_SCHEMA = 6\nraise RuntimeError("probe must not execute script")', None),
])
def test_probe_inspects_version_without_executing_script(monkeypatch, tmp_path, content, expected_error):
    import subprocess
    from agentclaw.community.core.bot_management.engines.aicoding import artifact_transfer as module
    entry = tmp_path / 'service_publish.py'
    if content is not None:
        entry.write_text(content)
    monkeypatch.setattr(module, 'ENTRY', str(entry))
    calls = []
    def execute(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            proc = subprocess.run(shlex.split(kwargs['shell_cmd']), capture_output=True, text=True)
            return SimpleNamespace(exit_code=proc.returncode)
        return result()
    p = publisher([])
    p.executor.exec_shell_new.side_effect = execute
    if expected_error:
        with pytest.raises(FastdiskPublishError, match=expected_error):
            p._invoke('device', 'upload', {'schema_version': 6}, None)
        assert len(calls) == 1
    else:
        p._invoke('device', 'upload', {'schema_version': 6}, None)
        assert len(calls) == 2
        assert 'TemporaryDirectory' not in calls[-1]['shell_cmd']


def test_restore_missing_script_fails_without_nas_or_upload_fallback():
    p = publisher([result(), result(44)])
    with pytest.raises(FastdiskPublishError, match='missing'):
        restore(p)
    assert p.executor.exec_shell_new.call_count == 2


def test_restore_detection_uses_remaining_operation_budget():
    p = publisher([result(status='skip_no_fastdisk')])
    assert p.try_restore(device_id='draft', plan=_AICODING_BUILD_PLAN,
        source_root='/home/admin/.aicoding', artifact_path='/opt/nfs/bot-data/3/aicoding',
        artifact_dir=Path('/aidesktop/aidesktop_prod/bolt_data/staff_u/b/3/aicoding'),
        remaining_timeout=lambda: 4.2) is None
    assert p.executor.exec_shell_new.call_args.kwargs['timeout_seconds'] == 5


def test_embedded_detector_does_not_import_container_scripts(tmp_path):
    """Execute the actual generated detector against a legacy empty layout."""
    import subprocess
    import zlib
    command = detection_command()
    code = shlex.split(command)[2]
    encoded = code.split('b64decode(', 1)[1].split(')', 1)[0]
    import ast
    source = zlib.decompress(base64.b64decode(ast.literal_eval(encoded))).decode()
    home, storage = tmp_path / 'home', tmp_path / 'storage'
    mountinfo = tmp_path / 'mountinfo'
    mountinfo.write_text('1 0 0:1 / / rw - ext4 /dev/root rw\n')
    source = source.replace('/var/run/agentclaw/persist-runtime.json', str(tmp_path / 'no-config'))
    source = source.replace('/home/admin/.aicoding', str(home)).replace('/opt/.aicoding', str(storage))
    source = source.replace('/proc/self/mountinfo', str(mountinfo))
    proc = subprocess.run(['python3', '-c', source], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)['status'] == 'skip_no_fastdisk'
    assert 'publish_oss' not in source
    assert 'service_publish' not in source


def test_restore_failure_is_logged_and_never_runs_nas(monkeypatch):
    from agentclaw.community.core.bot_management.engines.aicoding import artifact_transfer as module
    logger = Mock()
    monkeypatch.setattr(module, 'logger', logger)
    p = publisher([result(), result(44)])
    with pytest.raises(FastdiskPublishError, match='missing'):
        restore(p)
    assert logger.error.called
    assert p.executor.exec_shell_new.call_count == 2


def test_only_dynamic_rules_cross_container_boundary():
    from dataclasses import replace
    p = publisher([result(), result(), result()])
    plan = replace(_AICODING_BUILD_PLAN,
                   rsync_excludes=[*_AICODING_BUILD_PLAN.rsync_excludes,
                                   'workspace/template-repo', 'workspace/shared-corpus'])
    assert p.try_publish(device_id='device', plan=plan,
        source_root='/home/admin/.aicoding',
        target_dir=Path('/aidesktop/aidesktop_prod/bolt_data/staff_u/b/3/aicoding'),
        ignore_paths=('workspace/private',))
    request = json.loads(base64.b64decode(shlex.split(
        p.executor.exec_shell_new.call_args.kwargs['shell_cmd'])[-1]))
    assert request['additional_excludes'] == ['workspace/template-repo', 'workspace/shared-corpus']
    assert request['build_ignore_paths'] == ['workspace/private']
    assert 'sessions' not in request['additional_excludes']
    assert 'extra_include' not in request


def test_schema6_fixed_policy_matches_container_contract():
    import hashlib
    from agentclaw.community.core.workspace.engines.aicoding import _AICODING_RSYNC_EXCLUDES
    from agentclaw.community.core.workspace.engines.claude_code import _CLAUDE_CODE_RSYNC_EXCLUDES
    # Also pinned by container test_publish_policy; drift needs a coordinated upgrade.
    policy = {'aicoding': _AICODING_RSYNC_EXCLUDES, 'claude_code': _CLAUDE_CODE_RSYNC_EXCLUDES}
    assert hashlib.sha256(json.dumps(policy, sort_keys=True, separators=(',', ':')).encode()).hexdigest() == '079b6126d384fe48639c72de19bfcd3034e52b6c488fd956a0dbf1991c71cdaf'


@pytest.mark.parametrize('engine', ['aicoding', 'claude_code'])
@pytest.mark.parametrize('env_folder', ['aidesktop_pre', 'aidesktop_prod'])
def test_upload_and_restore_keep_same_bot_version_and_engine(engine, env_folder):
    from dataclasses import replace
    from agentclaw.community.core.workspace.engines.claude_code import _CLAUDE_CODE_BUILD_PLAN
    from agentclaw.community.core.bot_management.engines.aicoding.artifact_transfer import build_request
    plan = _AICODING_BUILD_PLAN if engine == 'aicoding' else _CLAUDE_CODE_BUILD_PLAN
    home = '/home/admin/.aicoding'
    prefix = f'aidesktop/{env_folder}/bolt_data/staff_owner/bot-example/17/{engine}'
    request = build_request(plan=plan, source_root=home, object_prefix=prefix,
        ignore_paths=(),
        layout={'home': home, 'storage': '/opt/fastdisk'},
        config=FastdiskPublishConfig(mist_secret_name='test'), bucket='test',
        endpoint='https://oss.example.test')
    assert request['version_prefix'] + '/' + request['engine_path'] == prefix
    assert request['source_root'] == request['fastdisk']['home'] == home
    assert request['version_prefix'] + '/.local/bin' == prefix.rsplit('/', 1)[0] + '/.local/bin'
    detected = SimpleNamespace(exit_code=0, stdout=json.dumps({
        'status': 'run', 'home': home, 'storage': '/opt/fastdisk'}))
    p = publisher([detected, result(), result()])
    mounted_artifact = '/opt/nfs/bot-data/17/' + engine
    assert p.try_restore(device_id='draft-device', plan=plan, source_root=home,
        artifact_path=mounted_artifact,
        artifact_dir=Path('/aidesktop') / Path(prefix).relative_to('aidesktop'), remaining_timeout=lambda: 1800)
    restored = json.loads(base64.b64decode(shlex.split(
        p.executor.exec_shell_new.call_args.kwargs['shell_cmd'])[-1]))
    assert restored['version_prefix'] + '/' + restored['engine_path'] == prefix
    assert restored['credential']['provider'] == 'mist'
    assert restored['artifact_path'] == mounted_artifact
    assert restored['source_root'] == home
    assert restored['fastdisk'] == request['fastdisk']
    with pytest.raises(FastdiskPublishError, match='does not match'):
        build_request(plan=replace(plan, migration_subpath='wrong-engine'), source_root=home,
            object_prefix=prefix, ignore_paths=(),
            layout={'home': home, 'storage': '/opt/fastdisk'},
            config=FastdiskPublishConfig(), bucket='test', endpoint='https://oss.example.test')


@pytest.mark.parametrize('engine', ['aicoding', 'claude_code', 'openclaw', 'teclaw', 'hermes', 'deepseek_harness', 'future_engine'])
def test_engine_scope_is_owned_by_aicoding_strategy(engine):
    from dataclasses import replace
    plan = replace(_AICODING_BUILD_PLAN, engine_type=engine)
    p = publisher([])
    supported = engine in ('aicoding', 'claude_code')
    assert p.supports(plan) is supported
    p.executor.exec_shell_new.assert_not_called()
    if not supported:
        assert p.try_publish(device_id='unused', plan=plan, source_root='unused',
                             target_dir=Path('/unused'), ignore_paths=()) is False
        budget = Mock(side_effect=AssertionError('unsupported engine consumed timeout'))
        assert p.try_restore(device_id='unused', plan=plan, source_root='unused',
                             artifact_path='unused', artifact_dir=Path('/unused'),
                             remaining_timeout=budget) is None
        p.executor.exec_shell_new.assert_not_called()
        budget.assert_not_called()


def test_transfer_failure_logs_operation_identity_without_request_secrets(monkeypatch):
    from agentclaw.community.core.bot_management.engines.aicoding import artifact_transfer as module
    logger = Mock()
    monkeypatch.setattr(module, 'logger', logger)
    p = publisher([result(), result(), result(1)])
    with pytest.raises(FastdiskPublishError):
        publish(p)
    request = json.loads(base64.b64decode(shlex.split(p.executor.exec_shell_new.call_args.kwargs['shell_cmd'])[-1]))
    messages = [call.args[0] % call.args[1:] for call in logger.error.call_args_list]
    assert any(request['operation_id'] in message and request['version_prefix'] in message
               for message in messages)
    assert all('test-secret-name' not in message for message in messages)


@pytest.mark.parametrize('engine', ['aicoding', 'claude_code'])
def test_detected_physical_home_owns_transfer_source_and_restore_destination(engine):
    from agentclaw.community.core.workspace.engines.claude_code import _CLAUDE_CODE_BUILD_PLAN
    plan = _AICODING_BUILD_PLAN if engine == 'aicoding' else _CLAUDE_CODE_BUILD_PLAN
    p = publisher([result(), result(), result(), result(), result(), result()])
    provider = Mock(get_base_path=Mock(return_value='/home/admin/.' + engine))
    target = Path('/aidesktop/test/bolt_data/staff_u/b/3') / engine
    assert p.publish(device_id='draft', plan=plan, provider=provider, target_dir=target, ignore_paths=())
    uploaded = json.loads(base64.b64decode(shlex.split(p.executor.exec_shell_new.call_args.kwargs['shell_cmd'])[-1]))
    assert uploaded['source_root'] == uploaded['fastdisk']['home'] == '/home/admin/.aicoding'
    assert uploaded['engine_path'] == engine
    p._bindings.get_by_id.return_value = SimpleNamespace(device_id='draft', status='ACTIVE')
    restored = p.restore(bot={'binding_id': 1, 'entity_id': 'u', 'bot_id': 'b'}, source_version=3,
        plan=plan, provider=provider, artifact_dir=target,
        migration_path_base=lambda **kw: '/opt/nfs/bot-data',
        remaining_timeout=lambda: 1800, migration_error=RuntimeError)
    request = json.loads(base64.b64decode(shlex.split(p.executor.exec_shell_new.call_args.kwargs['shell_cmd'])[-1]))
    assert request['source_root'] == '/home/admin/.aicoding'
    assert restored['draft_path'] == '/home/admin/.aicoding'
    assert request['engine_path'] == engine
    assert request['version_prefix'] == uploaded['version_prefix']


@pytest.mark.parametrize('enabled', [False, True])
def test_publish_forwards_runtime_artifact_decision_to_container(enabled):
    p = publisher([result(), result(), result()])
    assert p.publish(device_id='draft', plan=_AICODING_BUILD_PLAN,
        provider=Mock(get_base_path=Mock(return_value='/home/admin/.aicoding')),
        target_dir=Path('/aidesktop/test/bolt_data/staff_u/b/3/aicoding'),
        ignore_paths=(), include_runtime_artifacts=enabled)
    req = json.loads(base64.b64decode(shlex.split(p.executor.exec_shell_new.call_args.kwargs['shell_cmd'])[-1]))
    assert req['include_runtime_artifacts'] is enabled
    assert req['schema_version'] == 6


def test_schema5_container_is_rejected_before_upload(monkeypatch, tmp_path):
    import subprocess
    from agentclaw.community.core.bot_management.engines.aicoding import artifact_transfer as module
    entry = tmp_path / 'service_publish.py'
    entry.write_text('SERVICE_PUBLISH_SCHEMA = 5\nraise RuntimeError("must not execute")')
    monkeypatch.setattr(module, 'ENTRY', str(entry))
    p = publisher([])
    def execute(**kwargs):
        output = subprocess.run(shlex.split(kwargs['shell_cmd']), capture_output=True, text=True)
        return SimpleNamespace(exit_code=output.returncode)
    p.executor.exec_shell_new.side_effect = execute
    with pytest.raises(FastdiskPublishError, match='incompatible'):
        p._invoke('device', 'upload', {'schema_version': 6}, None)
    assert p.executor.exec_shell_new.call_count == 1
