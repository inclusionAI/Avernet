"""Complete Home snapshots preserve npm launchers without following links."""

from pathlib import Path
import shutil
import subprocess
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.service_bot.services.agentclaw_artifact import (
    restore_agentclaw_artifact,
    sync_agentclaw_artifact,
)


@pytest.fixture
def layout(tmp_path):
    source = tmp_path / 'home'
    root = source / '.agentclaw'
    (root / 'lib/node_modules/pkg').mkdir(parents=True)
    (root / 'bin').mkdir()
    cli = root / 'lib/node_modules/pkg/cli'
    cli.write_text("#!/bin/sh\nprintf 'npm works'\n")
    cli.chmod(0o755)
    (root / 'bin/cli').symlink_to('../lib/node_modules/pkg/cli')
    artifact = tmp_path / 'versions/3/openclaw'
    artifact.mkdir(parents=True)
    return source, artifact


def run_command(**kwargs):
    # Local non-root tests preserve rsync behavior while Linux verifies chown.
    cmd = [arg for arg in kwargs['cmd'][1:] if not arg.startswith('--chown=')]
    subprocess.run(cmd, check=True, capture_output=True)


def sync(source, artifact, rules=(), run=run_command):
    sync_agentclaw_artifact(source_home=source, target_dir=artifact,
                           device_id='device-cli', version='3',
                           build_ignore_paths=rules, run_command=run)


def restore(artifact, home, run=run_command):
    restore_agentclaw_artifact(artifact_dir=artifact, target_home=home,
                              bot_id='bot-cli', version=3,
                              run_command=run, timeout_seconds=30)


def test_complete_snapshot_and_restore_keep_npm_links(layout, tmp_path, caplog):
    caplog.set_level('INFO')
    source, artifact = layout
    (source / '.agentclaw/config').mkdir()
    (source / '.agentclaw/config/settings').write_text('private-token-value')
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'secret').write_text('external-secret-value')
    (source / '.agentclaw/external').symlink_to(outside)
    sync(source, artifact)
    copied = artifact.parent / '.agentclaw'
    assert (copied / 'bin/cli').readlink() == Path('../lib/node_modules/pkg/cli')
    assert subprocess.check_output([str(copied / 'bin/cli')], text=True) == 'npm works'
    assert (copied / 'external').is_symlink()
    assert (copied / 'config/settings').read_text() == 'private-token-value'
    home = tmp_path / 'restored'
    (home / '.agentclaw').mkdir(parents=True)
    (home / '.agentclaw/local-only').write_text('keep')
    restore(artifact, home)
    assert (home / '.agentclaw/local-only').read_text() == 'keep'
    assert subprocess.check_output([str(home / '.agentclaw/bin/cli')], text=True) == 'npm works'
    for event in ('request', 'success', 'restore.request', 'restore.success'):
        assert 'service_bot.agentclaw.' + event in caplog.text
    assert 'device_id=device-cli' in caplog.text and 'bot_id=bot-cli' in caplog.text
    assert 'elapsed_ms=' in caplog.text
    assert 'private-token-value' not in caplog.text and 'external-secret-value' not in caplog.text


@pytest.mark.parametrize('state', ['removed', 'missing', 'excluded', 'child-excluded'])
def test_retry_converges_snapshot(layout, state):
    source, artifact = layout
    root = source / '.agentclaw'
    (root / 'stale').write_text('old')
    sync(source, artifact)
    rules = ()
    if state == 'missing':
        shutil.rmtree(root)
    elif state == 'excluded':
        rules = ('.agentclaw',)
    elif state == 'child-excluded':
        rules = ('.agentclaw/stale',)
    else:
        (root / 'stale').unlink()
    sync(source, artifact, rules)
    assert not (artifact.parent / '.agentclaw/stale').exists()
    assert artifact.is_dir()


@pytest.mark.parametrize('operation', ['build', 'restore'])
@pytest.mark.parametrize('position', ['home', 'root', 'target-home', 'target-root'])
@pytest.mark.parametrize('kind', ['symlink', 'file'])
def test_invalid_fixed_roots_fail_before_copy(tmp_path, operation, position, kind):
    source = tmp_path / 'source'
    target = tmp_path / 'target'
    (source / '.agentclaw').mkdir(parents=True)
    (target / '.agentclaw').mkdir(parents=True)
    selected = {'home': source, 'root': source / '.agentclaw',
                'target-home': target, 'target-root': target / '.agentclaw'}[position]
    shutil.rmtree(selected)
    if kind == 'symlink':
        selected.symlink_to(tmp_path / 'missing')
    else:
        selected.write_text('not-directory')
    run = MagicMock()
    with pytest.raises((ValueError, NotADirectoryError)):
        if operation == 'build':
            sync(source, target / 'openclaw', run=run)
        else:
            restore(source / 'openclaw', target, run=run)
    run.assert_not_called()


def test_missing_legacy_artifact_skips_and_preserves_home(tmp_path, caplog):
    caplog.set_level('INFO')
    home = tmp_path / 'home'
    home.mkdir()
    (home / 'keep').write_text('keep')
    run = MagicMock()
    restore(tmp_path / 'old/openclaw', home, run)
    run.assert_not_called()
    assert (home / 'keep').read_text() == 'keep'
    sync(tmp_path / 'missing', tmp_path / 'version/openclaw', run=run)
    run.assert_not_called()
    assert 'reason=source_missing' in caplog.text


@pytest.mark.parametrize('operation', ['build', 'cleanup', 'restore'])
def test_copy_failure_propagates_and_logs(layout, operation, tmp_path, caplog):
    caplog.set_level('INFO')
    source, artifact = layout
    if operation == 'cleanup':
        sync(source, artifact)
        shutil.rmtree(source / '.agentclaw')
    elif operation == 'restore':
        sync(source, artifact)
    caplog.clear()
    run = MagicMock(side_effect=PermissionError('copy denied'))
    with pytest.raises(PermissionError, match='copy denied'):
        if operation == 'restore':
            restore(artifact, tmp_path / 'restored', run)
        else:
            sync(source, artifact, run=run)
    assert '.failure' in caplog.text and 'error_type=PermissionError' in caplog.text
    assert '.success' not in caplog.text


def test_restore_replaces_target_directory_link_without_writing_outside(layout, tmp_path):
    source, artifact = layout
    sync(source, artifact)
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'sentinel').write_text('untouched')
    home = tmp_path / 'restored'
    (home / '.agentclaw').mkdir(parents=True)
    (home / '.agentclaw/lib').symlink_to(outside)
    restore(artifact, home)
    assert not (home / '.agentclaw/lib').is_symlink()
    assert (home / '.agentclaw/lib/node_modules/pkg/cli').is_file()
    assert sorted(path.name for path in outside.iterdir()) == ['sentinel']
    assert (outside / 'sentinel').read_text() == 'untouched'


def test_snapshot_does_not_dereference_external_directory_link(layout, tmp_path):
    source, artifact = layout
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'secret').write_text('never materialize')
    (source / '.agentclaw/external').symlink_to(outside)
    sync(source, artifact)
    copied = artifact.parent / '.agentclaw/external'
    assert copied.is_symlink()
    assert copied.readlink() == outside
    # Moving the source outside proves no external payload was materialized.
    outside.rename(tmp_path / 'moved-outside')
    assert not copied.exists()


def test_restore_forwards_remaining_timeout_and_admin_owner(layout, tmp_path):
    source, artifact = layout
    sync(source, artifact)
    run = MagicMock()
    restore(artifact, tmp_path / 'restored', run)
    command = run.call_args.kwargs
    assert command['timeout_seconds'] == 30
    assert '--chown=1000:1000' in command['cmd']
    assert '-a' in command['cmd'] and '-L' not in command['cmd']
    assert '--delete' not in command['cmd']
