import { expect, it } from 'vitest';
import Database from 'better-sqlite3';
import { migrations } from '../schema.js';

it('adds retry epoch to existing flow runs without changing their generation', () => {
  const raw = new Database(':memory:');
  try {
    raw.exec("CREATE TABLE flow_runs (flow_id TEXT PRIMARY KEY, status TEXT); INSERT INTO flow_runs VALUES ('old', 'running')");
    const migration = migrations.find(item => item.version === 140);
    expect(migration?.sql).toHaveLength(1);
    raw.exec(migration!.sql[0]);
    expect(raw.prepare('SELECT status, retry_epoch FROM flow_runs WHERE flow_id = ?').get('old'))
      .toEqual({ status: 'running', retry_epoch: 0 });
  } finally { raw.close(); }
});
