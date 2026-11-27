import { describe, expect, it, vi } from 'vitest';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { RunSubworkflowsRepository } from '../run-subworkflows-repository.js';

function fixture(missing: string[] = [], message = (column: string) => `no such column: ${column}`) {
  const query = vi.fn(async (sql: string, _params?: unknown[]) => {
    const column = missing.find(name => sql.slice(0, sql.indexOf(' FROM ')).split(/[, ]+/).includes(name));
    if (column) throw new Error(message(column));
    return [{ flow_id: 'child', parent_flow_id: 'parent', root_flow_id: 'root' }];
  });
  return { query, repo: new RunSubworkflowsRepository({ query } as unknown as IDatabase) };
}

describe('run snapshot lineage projection', () => {
  it('selects both stored columns only for detail, without credentials', async () => {
    const { repo, query } = fixture();
    expect(await repo.readRun('child')).toMatchObject({ parent_flow_id: 'parent', root_flow_id: 'root' });
    expect(query).toHaveBeenCalledTimes(1);
    const [[sql, params]] = query.mock.calls;
    expect(sql).toContain('parent_flow_id, root_flow_id FROM flow_runs WHERE flow_id = ?');
    expect(sql).not.toContain('credentials_json'); expect(sql).not.toContain('SELECT *');
    expect(params).toEqual(['child']);
  });

  it.each([
    (column: string) => `no such column: ${column}`,
    (column: string) => `Unknown column '${column}' in 'field list'`,
    (column: string) => `Unknown column '${column}'`,
  ])('retries only missing optional columns with compatible SQL error messages (%#)', async message => {
    const { repo, query } = fixture(['parent_flow_id', 'root_flow_id', 'ordinary_run_marker', 'evolution_analysis_status'], message);
    await repo.readRun('child');
    expect(query).toHaveBeenCalledTimes(5);
    const [sql] = query.mock.calls.at(-1)!;
    for (const column of ['parent_flow_id', 'root_flow_id', 'ordinary_run_marker', 'evolution_analysis_status', 'credentials_json']) {
      expect(sql).not.toContain(column);
    }
    expect(query.mock.calls.every(([, params]) => JSON.stringify(params) === '["child"]')).toBe(true);
  });

  it.each(['parent_flow_id', 'root_flow_id'])('retains the other lineage column when only %s is missing', async missing => {
    const { repo, query } = fixture([missing]); await repo.readRun('child');
    expect(query).toHaveBeenCalledTimes(2);
    const [sql] = query.mock.calls.at(-1)!;
    expect(sql).not.toContain(missing);
    expect(sql).toContain(missing === 'parent_flow_id' ? 'root_flow_id' : 'parent_flow_id');
  });

  it.each(['no such column: workflow_id', 'no such table: flow_runs', 'connection lost',
    'permission denied: parent_flow_id', 'no such column: unrelated_parent_flow_id'])('propagates storage failure %s without compatibility fallback', async message => {
    const { repo, query } = fixture(); const error = new Error(message);
    query.mockRejectedValue(error);
    await expect(repo.readRun('child')).rejects.toBe(error);
    expect(query).toHaveBeenCalledTimes(1);
  });

  it('does not add lineage or credentials to list SELECTs', async () => {
    const { repo, query } = fixture();
    query.mockResolvedValue([]);
    await repo.listRuns({ limit: 1, offset: 0 }, null);
    expect(query).toHaveBeenCalledTimes(2);
    for (const [sql] of query.mock.calls) expect(sql).not.toMatch(/parent_flow_id|root_flow_id|credentials_json/);
  });
});
