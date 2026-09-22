"""Real filesystem regression for bounded symlink counting."""
import os

import pytest

from engine.community.plugins.file_count_worker import count


def test_links_count_logical_entries_and_external_allowed_root(tmp_path):
    root = tmp_path / '.openclaw'
    work = root / 'workspace'
    work.mkdir(parents=True)
    external = tmp_path / 'openclawExt' / 'clawmind'
    external.mkdir(parents=True)
    (external / 'a').write_text('a')
    (work / 'a').write_text('a')
    (work / 'file-link').symlink_to('a')
    (work / 'clawmind').symlink_to('../../openclawExt/clawmind')
    (work / 'alias').symlink_to('clawmind')
    assert count(str(root), 'workspace') == 4
    assert count(str(root), 'workspace/clawmind') == 1
    assert count(str(root), str(external)) == 1
    (external / 'sub').mkdir()
    (external / 'sub' / 'nested').touch()
    assert count(str(root), 'workspace/alias/sub') == 1


@pytest.mark.parametrize('kind', ['self', 'mutual', 'outside', 'dangling', 'ancestor'])
def test_abnormal_links_are_skipped(tmp_path, kind):
    root = tmp_path / 'root'
    root.mkdir()
    (root / 'file').write_text('a')
    link = root / 'link'
    target = {'self': 'link', 'mutual': 'other', 'outside': '/etc',
              'dangling': 'missing', 'ancestor': '.'}[kind]
    link.symlink_to(target)
    if kind == 'mutual':
        (root / 'other').symlink_to('link')
    assert count(str(root), '.') == 1
    if kind != 'ancestor':
        assert count(str(root), 'link') == 0


def test_directory_aliases_not_globally_deduplicated(tmp_path):
    root = tmp_path / 'root'
    child = root / 'dir'
    child.mkdir(parents=True)
    (child / 'file').touch()
    os.link(child / 'file', child / 'hardlink')
    (root / 'alias').symlink_to('dir')
    assert count(str(root), '.') == 4


def test_backlink_parent_does_not_reenter_active_ordinary_child(tmp_path):
    root = tmp_path / 'root'
    sub = root / 'sub'
    sub.mkdir(parents=True)
    (sub / 'sub-file').touch()
    (root / 'parent-file').touch()
    (sub / 'backlink').symlink_to('..')
    skipped = {}
    assert count(str(root), 'sub', skipped) == 2
    assert skipped == {'cycle': 1}


def test_link_dotdot_uses_resolved_parent_not_lexical_parent(tmp_path):
    root = tmp_path / 'root'
    work = root / 'workspace'
    deep = root / 'other' / 'deep'
    deep.mkdir(parents=True)
    work.mkdir()
    (deep.parent / 'file').touch()
    (work / 'bridge').symlink_to('../other/deep')
    (work / 'target').symlink_to('bridge/../file')
    assert count(str(root), 'workspace') == 1


def test_linked_subtree_ordinary_scan_error_is_not_skipped(tmp_path, monkeypatch):
    from engine.community.plugins import file_count_worker as worker
    root = tmp_path / 'root'
    child = root / 'child'
    child.mkdir(parents=True)
    (root / 'alias').symlink_to('child')

    def vanished(_fd):
        raise FileNotFoundError

    monkeypatch.setattr(worker.os, 'scandir', vanished)
    with pytest.raises(worker.ScanError, match='directory_changed'):
        count(str(root), 'alias')


def test_symlink_replacement_during_resolution_fails(tmp_path, monkeypatch):
    from engine.community.plugins import file_count_worker as worker
    child = tmp_path / 'child'
    child.mkdir()
    link = tmp_path / 'link'
    link.symlink_to('child')
    readlink = worker.os.readlink

    def replace(name, **kwargs):
        result = readlink(name, **kwargs)
        link.rename(tmp_path / 'old-link')
        link.symlink_to('/etc')
        return result

    monkeypatch.setattr(worker.os, 'readlink', replace)
    with pytest.raises(worker.ScanError, match='directory_changed'):
        count(str(tmp_path), 'link')


def test_intermediate_outside_link_is_not_lexically_cancelled(tmp_path):
    root = tmp_path / 'root'
    root.mkdir()
    (root / 'file').touch()
    (root / 'escape').symlink_to('/etc')
    (root / 'cancelled').symlink_to('escape/../file')
    skipped = {}
    assert count(str(root), 'cancelled', skipped) == 0
    assert skipped == {'outside': 1}


def test_special_link_and_dangling_intermediate_are_skipped(tmp_path):
    os.mkfifo(tmp_path / 'pipe')
    (tmp_path / 'link').symlink_to('pipe')
    (tmp_path / 'broken').symlink_to('pipe/subdir')
    skipped = {}
    assert count(str(tmp_path), '.', skipped) == 0
    assert skipped == {'dangling': 1}


def test_permission_failure_in_link_target_remains_error(tmp_path, monkeypatch):
    from engine.community.plugins import file_count_worker as worker
    child = tmp_path / 'child'
    child.mkdir()
    (tmp_path / 'link').symlink_to('child')
    opening = worker.os.open

    def denied(name, flags, **kwargs):
        if name == 'child':
            raise PermissionError
        return opening(name, flags, **kwargs)

    monkeypatch.setattr(worker.os, 'open', denied)
    with pytest.raises(worker.ScanError, match='permission_denied'):
        count(str(tmp_path), 'link')


def test_requested_directory_replaced_before_open_is_detected(tmp_path, monkeypatch):
    from engine.community.plugins import file_count_worker as worker
    child = tmp_path / 'child'
    child.mkdir()
    opening = worker.os.open

    def replace(name, flags, **kwargs):
        if name == 'child':
            child.rename(tmp_path / 'old')
            child.mkdir()
        return opening(name, flags, **kwargs)

    monkeypatch.setattr(worker.os, 'open', replace)
    with pytest.raises(worker.ScanError, match='directory_changed'):
        count(str(tmp_path), 'child')


def test_link_to_shared_parent_is_outside_and_extra_root_must_be_real(tmp_path):
    from engine.community.plugins.file_count_worker import ScanError
    root = tmp_path / 'root'
    root.mkdir()
    (root / 'parent').symlink_to('..')
    skipped = {}
    assert count(str(root), 'parent', skipped) == 0
    assert skipped == {'outside': 1}
    (tmp_path / 'openclawExt').symlink_to(root)
    with pytest.raises(ScanError, match='path_forbidden'):
        count(str(root), str(tmp_path / 'openclawExt'))


@pytest.mark.asyncio
async def test_scan_budget_and_bounded_skip_logs(tmp_path, monkeypatch, caplog):
    from engine.community.plugins import file_count as scanner
    from engine.community.kernel.file_count import file_count_request_id
    import asyncio
    import logging

    root = tmp_path / 'root'
    root.mkdir()
    (root / 'private-target-must-not-be-logged').symlink_to('/secret-do-not-log')
    (root / 'dangling').symlink_to('missing')
    (root / 'cycle').symlink_to('cycle')
    original_timeout = asyncio.timeout
    budgets = []

    def timeout(delay):
        budgets.append(delay)
        return original_timeout(delay)

    monkeypatch.setattr(scanner.asyncio, 'timeout', timeout)
    request_context = file_count_request_id.set('skip-test')
    try:
        with caplog.at_level(logging.INFO, logger='engine.file_count'):
            result = await scanner.count_files(root, '.')
    finally:
        file_count_request_id.reset(request_context)
    assert result['file_count'] == 0
    assert 120 in budgets
    event = next(r for r in caplog.records if r.msg == 'engine.file_count.scan_completed')
    assert event.skipped_links == {'outside': 1, 'dangling': 1, 'cycle': 1}
    assert event.request_id == 'skip-test'
    assert 'secret-do-not-log' not in caplog.text
