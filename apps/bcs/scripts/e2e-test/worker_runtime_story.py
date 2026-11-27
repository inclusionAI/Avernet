#!/usr/bin/env python3
"""Black-box Worker process scenarios with a memory Store and SQLite archives.

This suite verifies behavior and in-process ownership takeover. The memory
Store is intentionally lost on restart; persistent-store acceptance is separate.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import queue
import subprocess
import tempfile
import threading
import time


class Host:
    def __init__(self, binary, archive, log, *options):
        self.process = subprocess.Popen([str(binary), '--archive', str(archive), *options],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=log, text=True, bufsize=1)
        self.replies = queue.Queue()
        def read():
            try:
                for line in self.process.stdout:
                    self.replies.put(json.loads(line))
            except Exception as exc:
                self.replies.put(exc)
            finally:
                self.replies.put(EOFError('Worker companion exited'))
        self.reader = threading.Thread(target=read, daemon=True)
        self.reader.start()
        try:
            ready = self.read()
            assert ready == {'ready': True, 'store': 'memory_mock', 'archive': 'sqlite', 'durable_store': False}, ready
        except BaseException:
            # Construction owns the child as soon as Popen succeeds, including
            # a missing/malformed readiness frame before main receives Host.
            if self.process.poll() is None:
                self.process.kill()
            self.process.wait()
            self.close(False)
            raise

    def read(self):
        value = self.replies.get(timeout=60)
        if isinstance(value, Exception):
            raise value
        return value

    def call(self, op, *, error=None, **fields):
        self.process.stdin.write(json.dumps({'op': op, **fields}) + '\n')
        self.process.stdin.flush()
        result = self.read()
        if error is not None:
            assert not result['ok'] and error in result['error'], result
            return result
        assert result['ok'], result
        if op == 'tick':
            assert set(result['value']['processor_errors']) <= {'risk_outcome_pending'}, result
        return result['value']

    def until(self, predicate, description):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            self.call('tick', count=5)
            if predicate():
                return
        raise AssertionError(description)

    def status(self, tid, status):
        self.until(lambda: self.call('snapshot', tid=tid)['content']['status'] == status,
                   f'{tid} did not reach {status}; snapshot={self.call("snapshot", tid=tid)}')

    def close(self, success):
        try:
            if self.process.poll() is None:
                try:
                    self.process.stdin.write('{"op":"quit"}\n')
                    self.process.stdin.flush()
                except BrokenPipeError:
                    pass
                self.process.wait(timeout=10)
            if success:
                assert self.process.returncode == 0, self.process.returncode
        finally:
            if self.process.poll() is None:
                self.process.kill()
                self.process.wait()
            self.reader.join(timeout=2)
            try:
                self.process.stdin.close()
            except BrokenPipeError:
                pass
            self.process.stdout.close()


def document_story(h):
    def call(action, **fields):
        return h.call('storage', action=action, key='contract', **fields)
    assert call('read') is None
    assert call('paths', paths=['/missing']) is None
    assert 'Applied' in call('create', value={'a/b': [1, None, 3], 'object': {'old': 1}, 'scalar': 2})
    projected = call('paths', paths=['/a~1b/1', '/missing', '/object/old'])
    assert projected['values'] == [None, None, 1] and projected['present'] == [True, False, True]
    edits = [{'op': 'set', 'pointer': '/object/new', 'value': None},
             {'op': 'remove', 'pointer': '/object/old'},
             {'op': 'set', 'pointer': '/a~1b/0', 'value': 2},
             {'op': 'remove', 'pointer': '/a~1b/2'}]
    assert 'Applied' in call('patch', version=1, edits=edits)
    assert call('read') == {'version': 2, 'value': {'a/b': [2, None], 'object': {'new': None}, 'scalar': 2}}
    assert 'ConditionNotMet' in call('patch', version=1, edits=edits)
    stable = call('read')
    for pointer in ['/missing/x', '/scalar/x', '/a~1b/01', '/a~1b/-', '/object/absent']:
        call('patch', version=2, edits=[{'op': 'set', 'pointer': '/scalar', 'value': 9},
                                       {'op': 'remove', 'pointer': pointer}], error='invalid')
        assert call('read') == stable
    for pointer in ['bad', '/bad~2']:
        call('paths', paths=[pointer], error='invalid')
    call('patch', version=2, edits=[], error='invalid')
    call('patch', version=2, edits=[{'op': 'set', 'pointer': '', 'value': {}}], error='invalid')
    h.call('lose_next_reply')
    call('patch', version=2, edits=[{'op': 'set', 'pointer': '/scalar', 'value': 8}], error='unknown')
    assert call('read')['value']['scalar'] == 8


def run(h, checks):
    from gateway_worker_story import run as gateway_run
    gateway_run(h, checks)
    document_story(h)
    checks.append('document_projection_atomic_patch_and_unknown')
    h.until(lambda: h.call('runtime')['ready'], 'Worker never became ready')
    for mode, size in [('finish', 12), ('append', 70_000)]:
        sid, tid = f's0001.{mode}', f's0001.{mode}.task'
        request = dict(sid=sid, tid=tid, id=sid + '.start', payload={'mode': mode, 'bytes': size})
        admitted = h.call('admit', **request)
        assert admitted.startswith('Accepted'), admitted
        assert h.call('admit', **request).startswith('Duplicate')
        h.status(tid, 'completed')
        saved = h.call('archive', tid=tid)
        assert saved is not None
        restored = h.call('restore', tid=tid)
        assert restored['kind'] == 'final' and restored['view']['tid'] == tid
        display = restored['view']['content']['snapshot']
        assert display['kind'] == 'inline'
        assert display['value'] == {'format_version': 1, 'units': [
            {'source': 'answer', 'artifact_id': 'answer', 'class': 'output', 'content': 'x' * size}]}
        assert saved['result'] == {'kind': 'inline', 'value': {'finished': True}}
        consumed = h.call('checkpoint', sid=sid)['checkpoint']['consumed']
        calls = h.call('tick')['runner_calls']
        h.call('cancel', sid=sid, tid=tid, id=sid + '.late-cancel')
        h.until(lambda: h.call('checkpoint', sid=sid)['checkpoint']['consumed'] != consumed,
                'late cancel was not consumed')
        assert h.call('tick')['runner_calls'] == calls
        assert h.call('archive', tid=tid) == saved
        assert h.call('restore', tid=tid) == restored
        checks.append(f'{mode}_archive_restore_and_late_cancel')
    sid, tid = 's0001.cancel', 's0001.cancel.task'
    h.call('admit', sid=sid, tid=tid, id=sid + '.start', payload={'mode': 'hold'})
    h.status(tid, 'working')
    before = h.call('tick')['runner_calls']
    h.call('cancel', sid=sid, tid=tid, id=sid + '.cancel')
    h.status(tid, 'canceled')
    assert h.call('tick')['runner_calls'] == before
    checks.append('cancel_independent_of_runner')
    from worker_runtime_dispatch_story import run as run_dispatch
    run_dispatch(h, checks)
    for mode, expected in [('quarantine', 'quarantined'), ('blocked', 'blocked')]:
        sid, tid = f's0001.{mode}', f's0001.{mode}.task'
        h.call('admit', sid=sid, tid=tid, id=sid + '.start', payload={'mode': mode})
        h.until(lambda: (h.call('isolation', sid=sid) or {}).get('control', {}).get('mode', {}).get('kind') == expected,
                f'{mode} isolation missing')
        h.call('isolation', sid=sid, wrong_actor=True, error='forbidden')
        if mode == 'quarantine':
            h.status(tid, 'rejected')
            h.until(lambda: h.call('isolation', sid=sid)['control']['mode']['isolation']['completion'] is not None,
                    'isolation closure did not complete')
            snapshot = h.call('isolation', sid=sid)
            isolation = snapshot['control']['mode']['isolation']
            authorization = {
                'guard': {'owner': snapshot['owner'], 'expected_head': snapshot['head'],
                          'expected_revision': snapshot['control']['revision']},
                'isolation_id': isolation['operation_id'], 'completion': isolation['completion'],
                'request_id': 'explicit-test-recovery',
            }
            h.call('resume', authorization=authorization, wrong_actor=True, error='forbidden')
            resumed = h.call('resume', authorization=authorization)
            assert resumed.startswith(('Applied', 'AlreadyApplied')), resumed
            new_tid = sid + '.after-resume'
            h.call('admit', sid=sid, tid=new_tid, id=sid + '.after-resume', payload={'mode': 'finish'})
            h.status(new_tid, 'completed')
        checks.append(f'{mode}_and_control_authorization')
    # Same Store instance, a new Worker incarnation: this is not process-restart
    # persistence. Explicit Store time advancement expires the old member lease.
    sid, tid = 's0001.takeover', 's0001.takeover.task'
    h.call('admit', sid=sid, tid=tid, id=sid + '.start', payload={'mode': 'hold'})
    h.status(tid, 'working')
    prior = h.call('checkpoint', sid=sid)
    h.call('stop_worker', index=0)
    h.call('advance_store_clock', millis=2000)
    h.call('add_worker', name='worker-b')
    h.call('cancel', sid=sid, tid=tid, id=sid + '.cancel-after-takeover')
    h.status(tid, 'canceled')
    assert h.call('checkpoint', sid=sid)['owner'] != prior['owner']
    checks.append('memory_store_worker_takeover')
    sid, tid = 's0001.handoff', 's0001.handoff.task'
    h.call('admit', sid=sid, tid=tid, id=sid + '.start', payload={'mode': 'hold'})
    h.status(tid, 'working')
    before_handoff_calls = h.call('tick')['runner_calls']
    handoff = h.call('handoff')
    assert handoff['outcome'] == 'Drained', handoff
    assert handoff['owner'] == handoff['target'] != handoff['previous_owner'], handoff
    assert handoff['handoff_recorded'], handoff
    h.call('cancel', sid=sid, tid=tid, id=sid + '.cancel-after-handoff')
    h.status(tid, 'canceled')
    assert h.call('checkpoint', sid=sid)['owner']['instance'] == handoff['target']
    assert h.call('tick')['runner_calls'] == before_handoff_calls
    checks.append('graceful_handoff_preserves_pending_task')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', required=True, type=Path)
    parser.add_argument('--report', required=True, type=Path)
    args = parser.parse_args()
    report = {'status': 'failed', 'store': 'memory_mock', 'archive': 'sqlite',
              'durable_store': False, 'checks': []}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report) + '\n')
    try:
        with tempfile.TemporaryDirectory(prefix='worker-story-') as directory:
            with args.report.with_suffix('.log').open('w') as log:
                from direct_worker_story import run as run_direct
                for scenario, database, flags in [(run, 'archive.db', []),
                                                  (run_direct, 'direct.db', ['--direct'])]:
                    h = Host(args.binary.resolve(), Path(directory) / database, log, *flags)
                    passed = False
                    try:
                        scenario(h, report['checks'])
                        passed = True
                    finally:
                        h.close(passed)
        report['status'] = 'passed'
    except Exception as exc:
        report['error'] = str(exc)
        raise
    finally:
        args.report.write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
