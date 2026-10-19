// @vitest-environment node
import Database from 'better-sqlite3';
import { expect, it } from 'vitest';
import { SqliteDatabase, runMigrations } from '@avernet/clawweb-shared/server/db';
import { FlowRunRepository } from '../flow-run-repository.js';
import { DashboardRepository } from '../dashboard-repository.js';
import { NodeStatsRepository } from '../node-stats-repository.js';
import { HealthSnapshotRepository } from '../health-snapshot-repository.js';

it('excludes trial successes and failures while retaining legacy NULL and -1 formal versions', async () => {
  const db = new SqliteDatabase(new Database(':memory:'));
  await runMigrations(db, 'sqlite');
  const runs = new FlowRunRepository(db);
  const now = Math.floor(Date.now() / 1000);
  try {
    for (const [id, version, status] of [
      ['legacy-null', null, 'succeeded'], ['legacy-minus', -1, 'failed'],
      ['trial-success', -1, 'succeeded'], ['trial-failed', -1, 'failed'],
    ] as const) {
      expect(await runs.insert({ flowId: id, workflowId: id.startsWith('trial') ? 'trial-only' : 'formal',
        status, startedAt: now + (id === "legacy-minus" ? 1 : 0), workflowVersion: version })).toBe(true);
      if (id.startsWith('trial')) await db.exec(`INSERT INTO workflow_repair_release_records
        (record_id,workflow_id,task_id,revision,record_kind,state_version,request_key,request_digest,body_json,created_at_ms,updated_at_ms)
        VALUES (?, 'trial-only', 'task', 1, 'trial_run', 1, ?, 'digest', '{}', 1, 1)`, [id, id]);
    }
    for (const id of ['trial-success', 'trial-failed']) await db.exec(`INSERT INTO node_executions
      (flow_id, workflow_id, node_id, status, started_at, error_text, duration_ms)
      VALUES (?, 'trial-only', 'node', 'failed', ?, 'trial timeout', 999999)`, [id, now]);
    expect(await new NodeStatsRepository(db).getNodeStats('trial-only')).toEqual({ workflowId: 'trial-only', totalRuns: 0, nodes: [] });
    expect(await runs.countByStatus()).toEqual({ succeeded: 1, failed: 1 });
    expect(await runs.countRuns()).toBe(4);
    expect(await runs.countRuns({ purpose: 'trial' })).toBe(2);
    expect((await runs.findRuns({ purpose: 'production' })).map(run => run.flow_id).sort()).toEqual(['legacy-minus', 'legacy-null']);
    expect((await runs.findRunStatsByWorkflow()).map(run => [run.workflow_id, run.run_count])).toEqual([['formal', 2]]);
    expect(await new HealthSnapshotRepository(db).getDistinctWorkflowIds()).toEqual(['formal']);
    const dashboard = new DashboardRepository(db);
    expect((await dashboard.getOverview(now - 1, now + 1)).totalRuns).toBe(2);
    expect((await new NodeStatsRepository(db).getSuccessTrend('trial-only', 1)).every(day => day.totalRuns === 0)).toBe(true);
  } finally { await db.close(); }
});
