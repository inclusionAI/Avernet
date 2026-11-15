import Database from 'better-sqlite3';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db/sqlite-database';
import { workflowAiDraftMigrations } from '@avernet/clawweb-shared/server/schema-workflow-ai-draft';
import type { WorkflowAiDraftExecutionPort, WorkflowAiDraftResult } from '@avernet/workflow/server/contracts/workflow-ai-draft';
import { randomUUID } from 'node:crypto';
import { vi } from 'vitest';
export const overlayYaml = 'id: flow\nversion: 7\ntitle: Unsaved editor source\nnodes:\n  - id: n\n    executor: {type: agent}\n';
export const draftRequest = () => ({ requestId: randomUUID(), mode: 'modify' as const, prompt: 'Improve the workflow', currentYaml: overlayYaml });
export async function aiDraftFixture() {
  const raw = new Database(':memory:');
  const db = new SqliteDatabase(raw);
  for (const m of workflowAiDraftMigrations) for (const sql of m.sql) await db.exec(db.dialect.renderDdl(sql));
  raw.exec(`CREATE TABLE workflow_specs (workflow_id TEXT PRIMARY KEY, pack_id TEXT, spec_json TEXT);
    INSERT INTO workflow_specs VALUES ('flow', 'pack', '{}');
    CREATE TABLE workflow_deploy_history (workflow_id TEXT, pack_id TEXT, tag_name TEXT, deploy_number INTEGER, action TEXT, is_active INTEGER);
    CREATE TABLE bot_workflow_permissions (id INTEGER PRIMARY KEY, workflow_id TEXT, bot_id TEXT, bot_owner_id TEXT,
      can_view INTEGER, can_edit INTEGER, can_execute INTEGER, env TEXT, gmt_create INTEGER, gmt_modified INTEGER);
    INSERT INTO bot_workflow_permissions VALUES (1, 'flow', NULL, 'owner', 1, 1, 1, 'dev', 1, 1);`);
  let result: WorkflowAiDraftResult | null = null;
  const execution: WorkflowAiDraftExecutionPort = {
    resolveTarget: vi.fn(async () => ({ botId: 'autoheal:configured-owner' })),
    dispatch: vi.fn(async () => ({ jobId: 'job' })),
    poll: vi.fn(async () => result ? { status: 'completed' as const, result } : { status: 'running' as const }),
  };
  return { raw, db, execution, setResult: (value: typeof result) => { result = value; } };
}
