// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { FanoutStateRepository } from '../fanout-state-repository.js';
import { childInput, fixture, group } from './fanout-state-fixture.js';

let f: ReturnType<typeof fixture>;
let repo: FanoutStateRepository;


const columns = 'child_flow_id, parent_flow_id, parent_node_id, parent_attempt, child_workflow_id';

beforeEach(() => {
  f = fixture();
  repo = new FanoutStateRepository(f.db);
});

afterEach(async () => {
  await f.db.close();
});

const saveGroup = async () => {
  await repo.saveGroup('parent', 'fanout', { group: group(), expectedRevision: null });
};

const start = () => repo.startOrGetChild('parent', 'child-fanout', childInput());

const linkRow = () =>
  f.raw.prepare(`SELECT ${columns} FROM flow_run_links WHERE child_flow_id = ?`).get('child-fanout');

const childRow = () =>
  f.raw.prepare('SELECT flow_id FROM flow_runs WHERE flow_id = ?').get('child-fanout');

describe('fanout child creation and run link persistence', () => {
  it('writes the child run and its link in the same transaction', async () => {
    await saveGroup();
    const started = await start();
    expect(started.created).toBe(true);
    expect(childRow()).toEqual({ flow_id: 'child-fanout' });
    expect(linkRow()).toEqual({
      child_flow_id: 'child-fanout',
      parent_flow_id: 'parent',
      parent_node_id: 'fanout',
      parent_attempt: null,
      child_workflow_id: 'child-wf',
    });
  });

  it('repairs a missing link when an existing child is rejoined', async () => {
    await saveGroup();
    await start();
    f.raw.exec('DELETE FROM flow_run_links');
    expect(linkRow()).toBeUndefined();
    const rejoined = await start();
    expect(rejoined.created).toBe(false);
    expect(linkRow()).toEqual({
      child_flow_id: 'child-fanout',
      parent_flow_id: 'parent',
      parent_node_id: 'fanout',
      parent_attempt: null,
      child_workflow_id: 'child-wf',
    });
  });

  it('rejects a mismatched link before creating an orphan child', async () => {
    await saveGroup();
    f.raw.prepare(
      'INSERT INTO flow_run_links (child_flow_id, parent_flow_id, parent_node_id, parent_attempt, child_workflow_id) VALUES (?, ?, ?, ?, ?)',
    ).run('child-fanout', 'other-parent', 'fanout', null, 'child-wf');
    await expect(start()).rejects.toMatchObject({ statusCode: 409 });
    expect(childRow()).toBeUndefined();
  });

  it('rolls back a child run when its link insert fails', async () => {
    await saveGroup();
    f.raw.exec(
      `CREATE TRIGGER refuse_link BEFORE INSERT ON flow_run_links
       BEGIN SELECT RAISE(ABORT, 'link persistence failed'); END`,
    );
    await expect(start()).rejects.toThrow('link persistence failed');
    expect(childRow()).toBeUndefined();
    expect(linkRow()).toBeUndefined();
  });

  it('concurrent duplicate starts produce one child and one link', async () => {
    await saveGroup();
    const [first, second] = await Promise.allSettled([start(), start()]);
    expect(first.status).toBe('fulfilled');
    expect(second.status).toBe('fulfilled');
    const firstValue = (first.value as { created: boolean }).created;
    const secondValue = (second.value as { created: boolean }).created;
    expect([firstValue, secondValue].sort()).toEqual([false, true]);
    expect(
      f.raw.prepare('SELECT COUNT(*) AS n FROM flow_runs WHERE flow_id = ?').get('child-fanout'),
    ).toEqual({ n: 1 });
    expect(
      f.raw.prepare('SELECT COUNT(*) AS n FROM flow_run_links WHERE child_flow_id = ?').get('child-fanout'),
    ).toEqual({ n: 1 });
  });
});
