import { strict as assert } from 'node:assert';
import { SessionRuntimeRegistry } from '../src/runtime/session-runtime-registry.js';

describe('SessionRuntimeRegistry', () => {
  let registry: SessionRuntimeRegistry;

  beforeEach(() => {
    registry = new SessionRuntimeRegistry({ orphanGraceMs: 100 });
  });

  afterEach(() => {
    registry.shutdown();
  });

  // ---- Attach / Detach ----

  describe('attachConnection', () => {
    it('creates a session runtime on first attach', () => {
      const rt = registry.attachConnection('sess-a', 'conn-1');
      assert.equal(rt.controllerConnId, 'conn-1');
      assert.equal(rt.sessionKey, 'sess-a');
    });

    it('is idempotent — re-attach same connection returns same runtime', () => {
      const rt1 = registry.attachConnection('sess-a', 'conn-1');
      const rt2 = registry.attachConnection('sess-a', 'conn-1');
      assert.equal(rt1, rt2);
      assert.equal(rt2.controllerConnId, 'conn-1');
    });

    it('handoff — attaching a new connection replaces controller', () => {
      registry.attachConnection('sess-a', 'conn-1');
      const rt = registry.attachConnection('sess-a', 'conn-2');
      assert.equal(rt.controllerConnId, 'conn-2');
    });

    it('cancels orphan cleanup on re-attach', async () => {
      registry.attachConnection('sess-a', 'conn-1');
      // Register a run so detach starts orphan grace
      registry.registerRun('sess-a', {
        runId: 'run-1',
        sessionKey: 'sess-a',
        abort: () => {},
        startedAt: Date.now(),
        state: 'running',
      });
      registry.detachConnection('sess-a', 'conn-1');
      // Re-attach before grace expires
      registry.attachConnection('sess-a', 'conn-2');
      await new Promise(r => setTimeout(r, 200));
      // Session should still exist
      assert(registry.has('sess-a'), 'session should survive after re-attach');
    });
  });

  describe('detachConnection', () => {
    it('is idempotent — detaching unknown session is a no-op', () => {
      registry.detachConnection('sess-unknown', 'conn-1');
      assert.equal(registry.size(), 0);
    });

    it('detaching a non-controller connection is a no-op', () => {
      registry.attachConnection('sess-a', 'conn-1');
      registry.detachConnection('sess-a', 'conn-2'); // conn-2 is not controller
      assert(registry.isController('sess-a', 'conn-1'));
    });

    it('removes session immediately if no active run', () => {
      registry.attachConnection('sess-a', 'conn-1');
      registry.detachConnection('sess-a', 'conn-1');
      assert(!registry.has('sess-a'));
    });

    it('starts orphan grace if session has active run', () => {
      registry.attachConnection('sess-a', 'conn-1');
      registry.registerRun('sess-a', {
        runId: 'run-1',
        sessionKey: 'sess-a',
        abort: () => {},
        startedAt: Date.now(),
        state: 'running',
      });
      registry.detachConnection('sess-a', 'conn-1');
      // Session still exists (in grace)
      assert(registry.has('sess-a'));
    });
  });

  describe('detachAllForConnection', () => {
    it('detaches all sessions controlled by a connection', () => {
      registry.attachConnection('sess-a', 'conn-1');
      registry.attachConnection('sess-b', 'conn-1');
      registry.attachConnection('sess-c', 'conn-2');
      registry.detachAllForConnection('conn-1');
      assert(!registry.isController('sess-a', 'conn-1'));
      assert(!registry.isController('sess-b', 'conn-1'));
      assert(registry.isController('sess-c', 'conn-2'));
    });
  });

  // ---- Controller ----

  describe('isController', () => {
    it('returns true for attached controller', () => {
      registry.attachConnection('sess-a', 'conn-1');
      assert(registry.isController('sess-a', 'conn-1'));
    });

    it('returns false for unknown session', () => {
      assert(!registry.isController('sess-unknown', 'conn-1'));
    });

    it('returns false after handoff', () => {
      registry.attachConnection('sess-a', 'conn-1');
      registry.attachConnection('sess-a', 'conn-2');
      assert(!registry.isController('sess-a', 'conn-1'));
      assert(registry.isController('sess-a', 'conn-2'));
    });
  });

  // ---- Active Run ----

  describe('registerRun', () => {
    it('registers an active run for a session', () => {
      registry.attachConnection('sess-a', 'conn-1');
      registry.registerRun('sess-a', {
        runId: 'run-1',
        sessionKey: 'sess-a',
        abort: () => {},
        startedAt: Date.now(),
        state: 'running',
      });
      const run = registry.getActiveRun('sess-a');
      assert.equal(run?.runId, 'run-1');
      assert.equal(run?.state, 'running');
    });
  });

  describe('isSessionBusy', () => {
    it('returns false when no active run', () => {
      registry.attachConnection('sess-a', 'conn-1');
      assert(!registry.isSessionBusy('sess-a'));
    });

    it('returns true when run is running', () => {
      registry.attachConnection('sess-a', 'conn-1');
      registry.registerRun('sess-a', {
        runId: 'run-1',
        sessionKey: 'sess-a',
        abort: () => {},
        startedAt: Date.now(),
        state: 'running',
      });
      assert(registry.isSessionBusy('sess-a'));
    });

    it('returns true when run is paused_for_interaction', () => {
      registry.attachConnection('sess-a', 'conn-1');
      registry.registerRun('sess-a', {
        runId: 'run-1',
        sessionKey: 'sess-a',
        abort: () => {},
        startedAt: Date.now(),
        state: 'paused_for_interaction',
      });
      assert(registry.isSessionBusy('sess-a'));
    });
  });

  describe('updateRunState', () => {
    it('updates run state to paused_for_interaction', () => {
      registry.attachConnection('sess-a', 'conn-1');
      registry.registerRun('sess-a', {
        runId: 'run-1',
        sessionKey: 'sess-a',
        abort: () => {},
        startedAt: Date.now(),
        state: 'running',
      });
      registry.updateRunState('sess-a', 'paused_for_interaction');
      assert.equal(registry.getActiveRun('sess-a')?.state, 'paused_for_interaction');
    });

    it('updates run state back to running', () => {
      registry.attachConnection('sess-a', 'conn-1');
      registry.registerRun('sess-a', {
        runId: 'run-1',
        sessionKey: 'sess-a',
        abort: () => {},
        startedAt: Date.now(),
        state: 'paused_for_interaction',
      });
      registry.updateRunState('sess-a', 'running');
      assert.equal(registry.getActiveRun('sess-a')?.state, 'running');
    });
  });

  describe('completeRun', () => {
    it('removes the active run from the session', () => {
      registry.attachConnection('sess-a', 'conn-1');
      registry.registerRun('sess-a', {
        runId: 'run-1',
        sessionKey: 'sess-a',
        abort: () => {},
        startedAt: Date.now(),
        state: 'running',
      });
      registry.completeRun('sess-a', 'run-1');
      assert(!registry.isSessionBusy('sess-a'));
    });

    it('cleans up session entry if no controller', () => {
      registry.attachConnection('sess-a', 'conn-1');
      registry.registerRun('sess-a', {
        runId: 'run-1',
        sessionKey: 'sess-a',
        abort: () => {},
        startedAt: Date.now(),
        state: 'running',
      });
      registry.detachConnection('sess-a', 'conn-1');
      // Session still exists (orphan with active run)
      assert(registry.has('sess-a'));
      registry.completeRun('sess-a', 'run-1');
      // Session should be cleaned up now
      assert(!registry.has('sess-a'));
    });

    it('ignores unknown runId', () => {
      registry.attachConnection('sess-a', 'conn-1');
      registry.registerRun('sess-a', {
        runId: 'run-1',
        sessionKey: 'sess-a',
        abort: () => {},
        startedAt: Date.now(),
        state: 'running',
      });
      registry.completeRun('sess-a', 'run-unknown');
      assert(registry.isSessionBusy('sess-a'));
    });
  });

  describe('getActiveRunByRunId', () => {
    it('finds a run by runId across all sessions', () => {
      registry.attachConnection('sess-a', 'conn-1');
      registry.registerRun('sess-a', {
        runId: 'run-1',
        sessionKey: 'sess-a',
        abort: () => {},
        startedAt: Date.now(),
        state: 'running',
      });
      const run = registry.getActiveRunByRunId('run-1');
      assert.equal(run?.runId, 'run-1');
      assert.equal(run?.sessionKey, 'sess-a');
    });

    it('returns undefined for unknown runId', () => {
      assert.equal(registry.getActiveRunByRunId('run-unknown'), undefined);
    });
  });

  // ---- Orphan Grace ----

  describe('orphan grace period', () => {
    it('aborts active run and cleans up after grace expires', async () => {
      let aborted = false;
      registry.attachConnection('sess-a', 'conn-1');
      registry.registerRun('sess-a', {
        runId: 'run-1',
        sessionKey: 'sess-a',
        abort: () => { aborted = true; },
        startedAt: Date.now(),
        state: 'running',
      });
      registry.detachConnection('sess-a', 'conn-1');
      assert(registry.has('sess-a'), 'session should exist during grace');
      assert(!aborted, 'run should not be aborted during grace');

      await new Promise(r => setTimeout(r, 250));
      assert(aborted, 'run should be aborted after grace');
      assert(!registry.has('sess-a'), 'session should be cleaned up after grace');
    });

    it('does not abort if session is re-attached during grace', async () => {
      let aborted = false;
      registry.attachConnection('sess-a', 'conn-1');
      registry.registerRun('sess-a', {
        runId: 'run-1',
        sessionKey: 'sess-a',
        abort: () => { aborted = true; },
        startedAt: Date.now(),
        state: 'running',
      });
      registry.detachConnection('sess-a', 'conn-1');

      // Re-attach before grace expires
      await new Promise(r => setTimeout(r, 30));
      registry.attachConnection('sess-a', 'conn-2');

      await new Promise(r => setTimeout(r, 250));
      assert(!aborted, 'run should NOT be aborted after re-attach');
      assert(registry.has('sess-a'), 'session should survive after re-attach');
    });

    it('invokes onOrphanCleanup callback', async () => {
      let cleanupCalled = false;
      const reg = new SessionRuntimeRegistry({
        orphanGraceMs: 100,
        onOrphanCleanup: (sessionKey: string) => {
          cleanupCalled = true;
          assert.equal(sessionKey, 'sess-a');
        },
      });
      reg.attachConnection('sess-a', 'conn-1');
      reg.registerRun('sess-a', {
        runId: 'run-1',
        sessionKey: 'sess-a',
        abort: () => {},
        startedAt: Date.now(),
        state: 'running',
      });
      reg.detachConnection('sess-a', 'conn-1');
      await new Promise(r => setTimeout(r, 250));
      assert(cleanupCalled, 'onOrphanCleanup should be called');
      reg.shutdown();
    });
  });

  // ---- Status ----

  describe('getStatus', () => {
    it('returns correct status for idle session with connId', () => {
      registry.attachConnection('sess-a', 'conn-1');
      const status = registry.getStatus('sess-a', 0, 'conn-1');
      assert.equal(status.sessionKey, 'sess-a');
      assert.equal(status.attached, true);
      assert.equal(status.controller, true);
      assert.equal(status.processing, false);
      assert.equal(status.activeRun, undefined);
      assert.equal(status.pendingInteractionCount, 0);
    });

    it('returns controller=false for non-controller connId', () => {
      registry.attachConnection('sess-a', 'conn-1');
      const status = registry.getStatus('sess-a', 0, 'conn-2');
      assert.equal(status.attached, false);
      assert.equal(status.controller, false);
    });

    it('returns correct status for busy session', () => {
      registry.attachConnection('sess-a', 'conn-1');
      registry.registerRun('sess-a', {
        runId: 'run-1',
        sessionKey: 'sess-a',
        abort: () => {},
        startedAt: Date.now(),
        state: 'paused_for_interaction',
      });
      const status = registry.getStatus('sess-a', 2, 'conn-1');
      assert.equal(status.processing, true);
      assert.equal(status.activeRun?.state, 'paused_for_interaction');
      assert.equal(status.pendingInteractionCount, 2);
    });

    it('returns detached status for unknown session', () => {
      const status = registry.getStatus('sess-unknown', 0, 'conn-1');
      assert.equal(status.attached, false);
      assert.equal(status.controller, false);
      assert.equal(status.processing, false);
    });

    it('without connId returns has-any-controller status', () => {
      registry.attachConnection('sess-a', 'conn-1');
      const status = registry.getStatus('sess-a', 0);
      assert.equal(status.attached, true);
      assert.equal(status.controller, true);
    });
  });

  // ---- Multi-session concurrency ----

  describe('multi-session concurrency', () => {
    it('one connection can control multiple sessions', () => {
      registry.attachConnection('sess-a', 'conn-1');
      registry.attachConnection('sess-b', 'conn-1');
      assert(registry.isController('sess-a', 'conn-1'));
      assert(registry.isController('sess-b', 'conn-1'));
    });

    it('different sessions can have concurrent active runs', () => {
      registry.attachConnection('sess-a', 'conn-1');
      registry.attachConnection('sess-b', 'conn-1');
      registry.registerRun('sess-a', {
        runId: 'run-a',
        sessionKey: 'sess-a',
        abort: () => {},
        startedAt: Date.now(),
        state: 'running',
      });
      registry.registerRun('sess-b', {
        runId: 'run-b',
        sessionKey: 'sess-b',
        abort: () => {},
        startedAt: Date.now(),
        state: 'running',
      });
      assert(registry.isSessionBusy('sess-a'));
      assert(registry.isSessionBusy('sess-b'));
    });

    it('single session cannot have two active runs — second overwrites', () => {
      registry.attachConnection('sess-a', 'conn-1');
      registry.registerRun('sess-a', {
        runId: 'run-1',
        sessionKey: 'sess-a',
        abort: () => {},
        startedAt: Date.now(),
        state: 'running',
      });
      registry.registerRun('sess-a', {
        runId: 'run-2',
        sessionKey: 'sess-a',
        abort: () => {},
        startedAt: Date.now(),
        state: 'running',
      });
      // Second registerRun overwrites the first
      const run = registry.getActiveRun('sess-a');
      assert.equal(run?.runId, 'run-2');
    });
  });

  // ---- Shutdown ----

  describe('shutdown', () => {
    it('clears all sessions and grace timers', () => {
      let aborted = false;
      registry.attachConnection('sess-a', 'conn-1');
      registry.registerRun('sess-a', {
        runId: 'run-1',
        sessionKey: 'sess-a',
        abort: () => { aborted = true; },
        startedAt: Date.now(),
        state: 'running',
      });
      registry.shutdown();
      assert(aborted, 'shutdown should abort active runs');
      assert.equal(registry.size(), 0);
    });
  });

  // ---- Pending message queue (R2) ----

  describe('pending message queue', () => {
    it('enqueues messages and reports count', () => {
      const n1 = registry.enqueuePendingMessage('sess-a', { params: { message: 'a' }, enqueuedAt: 1 });
      const n2 = registry.enqueuePendingMessage('sess-a', { params: { message: 'b' }, enqueuedAt: 2 });
      assert.equal(n1, 1);
      assert.equal(n2, 2);
      assert.equal(registry.pendingMessageCount('sess-a'), 2);
    });

    it('drains messages in FIFO order and empties the queue', () => {
      registry.enqueuePendingMessage('sess-a', { params: { message: 'first' }, enqueuedAt: 1 });
      registry.enqueuePendingMessage('sess-a', { params: { message: 'second' }, enqueuedAt: 2 });
      const drained = registry.drainPendingMessages('sess-a');
      assert.deepEqual(drained.map(d => d.params.message), [ 'first', 'second' ]);
      assert.equal(registry.pendingMessageCount('sess-a'), 0);
      // Second drain is empty
      assert.deepEqual(registry.drainPendingMessages('sess-a'), []);
    });

    it('clearPendingMessages drops all queued messages and returns count', () => {
      registry.enqueuePendingMessage('sess-a', { params: { message: 'x' }, enqueuedAt: 1 });
      registry.enqueuePendingMessage('sess-a', { params: { message: 'y' }, enqueuedAt: 2 });
      const dropped = registry.clearPendingMessages('sess-a');
      assert.equal(dropped, 2);
      assert.equal(registry.pendingMessageCount('sess-a'), 0);
    });

    it('clear/drain on unknown session is a safe no-op', () => {
      assert.equal(registry.clearPendingMessages('nope'), 0);
      assert.deepEqual(registry.drainPendingMessages('nope'), []);
      assert.equal(registry.pendingMessageCount('nope'), 0);
    });
  });
});
