import { strict as assert } from 'node:assert';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { SessionStore } from '../src/store.js';
import type { SessionBinding } from '../src/types.js';

function makeBinding(key: string, acp: string, extra: Partial<SessionBinding> = {}): SessionBinding {
  return {
    gatewaySessionKey: key,
    acpSessionId: acp,
    cwd: '/tmp',
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    history: [],
    ...extra,
  };
}

describe('SessionStore', () => {
  let storePath: string;
  let store: SessionStore;

  beforeEach(() => {
    storePath = path.join(os.tmpdir(), `teamclaw-relay-test-${Date.now()}-${Math.random().toString(36).slice(2, 6)}.json`);
    store = new SessionStore(storePath, { writeDebounceMs: 5 });
  });

  afterEach(async () => {
    await store.flush();
    if (fs.existsSync(storePath)) fs.unlinkSync(storePath);
  });

  it('sets and retrieves by gateway key in O(1)', () => {
    store.set(makeBinding('test-key', 'acp-1'));
    const found = store.getByGatewaySessionKey('test-key');
    assert(found);
    assert.equal(found.acpSessionId, 'acp-1');
  });

  it('retrieves by acp session id', () => {
    store.set(makeBinding('k', 'acp-x'));
    const found = store.get('acp-x');
    assert(found);
    assert.equal(found.gatewaySessionKey, 'k');
  });

  it('appends history', () => {
    store.set(makeBinding('hist-key', 'acp-2'));
    store.appendHistory('hist-key', {
      id: 'msg-1',
      role: 'user',
      text: 'hello',
      timestamp: new Date().toISOString(),
    });
    const found = store.getByGatewaySessionKey('hist-key');
    assert.equal(found?.history?.length, 1);
    assert.equal(found?.history?.[0].text, 'hello');
  });

  it('deletes by gateway key and clears both indices', () => {
    store.set(makeBinding('del-key', 'acp-3'));
    store.deleteByGatewaySessionKey('del-key');
    assert.equal(store.getByGatewaySessionKey('del-key'), undefined);
    assert.equal(store.get('acp-3'), undefined);
  });

  it('lists sessions sorted by updatedAt descending', () => {
    store.set(makeBinding('a', 'acp-a', { createdAt: '2024-01-01T00:00:00Z', updatedAt: '2024-01-01T00:00:00Z' }));
    store.set(makeBinding('b', 'acp-b', { createdAt: '2024-01-02T00:00:00Z', updatedAt: '2024-01-02T00:00:00Z' }));
    const list = store.list();
    assert.equal(list.length, 2);
    assert.equal(list[0].gatewaySessionKey, 'b');
    assert.equal(list[1].gatewaySessionKey, 'a');
  });

  it('flush persists to disk and is loadable by a new store instance', async () => {
    store.set(makeBinding('persist-k', 'acp-p'));
    store.appendHistory('persist-k', { id: 'm', role: 'user', text: 'hi', timestamp: new Date().toISOString() });
    await store.flush();
    assert(fs.existsSync(storePath));

    const reopened = new SessionStore(storePath, { writeDebounceMs: 5 });
    const found = reopened.getByGatewaySessionKey('persist-k');
    assert(found);
    assert.equal(found.history?.[0].text, 'hi');
  });

  it('batches many mutations into a single atomic disk write (debounce)', async () => {
    store.set(makeBinding('b-1', 'acp-1'));
    for (let i = 0; i < 100; i++) {
      store.appendHistory('b-1', {
        id: `m-${i}`, role: 'user', text: `msg ${i}`, timestamp: new Date().toISOString(),
      });
    }
    await store.flush();
    const persisted = JSON.parse(fs.readFileSync(storePath, 'utf8')) as SessionBinding[];
    assert.equal(persisted.length, 1);
    assert.equal(persisted[0].history?.length, 100);
  });

  it('rebinds the same acpSessionId to a new gateway key', () => {
    store.set(makeBinding('gw-a', 'acp-shared'));
    store.set(makeBinding('gw-b', 'acp-shared'));
    assert.equal(store.getByGatewaySessionKey('gw-a'), undefined);
    const found = store.getByGatewaySessionKey('gw-b');
    assert(found);
    assert.equal(found.acpSessionId, 'acp-shared');
  });

  it('appendHistory on unknown key is a no-op', () => {
    store.appendHistory('missing', { id: 'x', role: 'user', text: 't', timestamp: new Date().toISOString() });
    assert.equal(store.getByGatewaySessionKey('missing'), undefined);
  });
});
