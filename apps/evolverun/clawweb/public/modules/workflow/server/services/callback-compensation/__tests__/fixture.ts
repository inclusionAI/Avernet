import Database from 'better-sqlite3';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db/sqlite-database';
import { sqliteDialect } from '@avernet/clawweb-shared/server/db/dialect';
import { migrationsV37To74 } from '@avernet/clawweb-shared/server/schema-v37-74';
import { callbackCompensationDdl } from '@avernet/clawweb-shared/server/schema-callback-compensation';
import { CallbackCompensationSource, nodeFields, runFields } from '../../../repositories/callback-compensation-source.js';
import { CallbackCompensationService } from '../service.js';
import type { CompensationEvidence, CompensationEvidenceProvider, CompensationOptions, CompensationTransportRequest } from '../types.js';

export const NOW = Date.parse('2026-11-28T04:00:00.000Z');
export const key = Buffer.alloc(32, 42);
export function fixture(path = ':memory:') {
  const raw = new Database(path); const db = new SqliteDatabase(raw);
  const fields = (s: string) => s.split(',').map(s => s.trim());
  const numeric = new Set(['id','retry_epoch','attempt','started_at','completed_at','gmt_create','gmt_modified',
    'node_count','succeeded_count','failed_count','total_duration_ms','total_token_usage','duration_ms']);
  for (const [table, cols] of [['flow_runs', [...fields(runFields), 'env', 'retry_epoch', 'state_json', 'credentials_json']],
    ['node_executions', fields(nodeFields)]] as const) {
    raw.exec(`CREATE TABLE ${table} (${cols.map(c => `${c} ${c === 'id' ? 'INTEGER PRIMARY KEY AUTOINCREMENT' : numeric.has(c) ? 'INTEGER' : 'TEXT'}`).join(',')})`);
  }
  raw.exec('CREATE TABLE workflow_repair_release_records (record_id TEXT, record_kind TEXT)');
  for (const version of [63, 69]) for (const sql of migrationsV37To74.find(m => m.version === version)!.sql) raw.exec(sql);
  raw.exec("ALTER TABLE http_callback_logs ADD COLUMN callback_source TEXT NOT NULL DEFAULT 'workflow-level'");
  for (const sql of callbackCompensationDdl) raw.exec(sqliteDialect.renderDdl(sql));
  let clock = NOW; let outcome = 204;
  const calls: CompensationTransportRequest[] = [];
  const proofs = new Map<string, CompensationEvidence>();
  const evidence: CompensationEvidenceProvider = { async resolve(q) { return structuredClone(proofs.get(q.flowId) ?? null); } };
  const defaults: CompensationOptions = { environment: 'pre', protectionKey: key, evidence,
    now: () => clock, transport: async request => { calls.push(request); return { status: outcome }; } };
  const services: CallbackCompensationService[] = []; let closed = false;
  function service(overrides: Partial<CompensationOptions> = {}) {
    const value = new CallbackCompensationService(db, { ...defaults, ...overrides }); services.push(value); return value;
  }
  function run(flowId = 'flow-1', changes: Record<string, unknown> = {}) {
    const data = { flow_id: flowId, workflow_id: 'wf', status: 'succeeded', env: 'pre', retry_epoch: 0,
      params_json: '{"secret_param":"SENSITIVE-PARAM"}', input_json: '{"body":"SENSITIVE-INPUT"}',
      result_json: '{"result":"SENSITIVE-RESULT"}', current_phase: null, credentials_json: '{"secret":"fake"}',
      node_count: 1, succeeded_count: 1, failed_count: 0, started_at: NOW / 1000 - 60, completed_at: NOW / 1000 - 10,
      gmt_create: NOW / 1000 - 60, gmt_modified: NOW / 1000 - 10, ...changes };
    raw.prepare(`INSERT INTO flow_runs (${Object.keys(data).join(',')}) VALUES (${Object.keys(data).map(() => '?').join(',')})`).run(...Object.values(data));
    raw.prepare(`INSERT INTO node_executions (flow_id,workflow_id,node_id,status,attempt,input_json,output_json,started_at,completed_at)
      VALUES (?,?,'node-1','succeeded',1,'{"input":"SENSITIVE-NODE-INPUT"}','{"output":"SENSITIVE-OUTPUT"}',?,?)`)
      .run(flowId, data.workflow_id, NOW / 1000 - 50, NOW / 1000 - 20);
  }
  function config(configId = 'callback-string-id', changes: Record<string, unknown> = {}) {
    const data = { config_id: configId, workflow_id: 'wf', name: 'Cortex', url: 'https://cortex.example.test/exact',
      secret: 'TEST-TOKEN', enabled: 1, notify_on: '["workflow_succeeded"]',
      timeout_ms: 1000, include_node_output: 0, ...changes };
    raw.prepare(`INSERT INTO http_callback_configs (${Object.keys(data).join(',')}) VALUES (${Object.keys(data).map(() => '?').join(',')})`).run(...Object.values(data));
  }
  async function authorize(flowId = 'flow-1', changes: Partial<CompensationEvidence> = {}) {
    const snapshot = await new CallbackCompensationSource(db).snapshot('wf', flowId);
    const entry: CompensationEvidence = {
      environment: 'pre', workflowId: 'wf', flowId, receiverId: 'cortex-logical-receiver', generation: 'reviewed-independent-generation:1',
      retryEpoch: snapshot!.run.retry_epoch, snapshotHash: snapshot!.snapshotHash, generationEvidenceRef: 'review:generation:immutable-record-42',
      configIds: ['callback-string-id', 'old-config-version'], urls: ['https://cortex.example.test/exact','https://cortex-old.example.test/legacy'],
      url: 'https://cortex.example.test/exact', historicalRoute: { evidenceRef: 'review:historical-route',
        validFrom: '2020-01-01T00:00:00Z', validUntil: '2026-12-01T00:00:00Z' },
      legacyIdempotency: { evidenceRef: 'review:receiver-contract', keyContract: 'business-key-contract:workflow+run+generation',
        legacySenderContract: 'legacy-body-identity-v1', generationContract: 'completion-generation-v2', lateArrivalContract: 'late-events-v1',
        earliestCompletion: '2020-01-01T00:00:00Z', latestCompletion: '2026-11-29T00:00:00Z',
        lateArrivalUntil: '2026-12-01T00:00:00Z', retentionUntil: '2027-01-01T00:00:00Z' },
      evidenceRef: 'review:authorization-42', validFrom: '2026-11-27T00:00:00Z', validUntil: '2026-11-29T00:00:00Z',
      phaseNullable: true, ...changes,
    };
    proofs.set(flowId, entry); return entry;
  }
  async function queued(s = service(), flowIds = ['flow-1'], actor = 'admin') {
    const preview = await s.preview('wf', actor, { configId: 'callback-string-id', flowIds });
    const input = { previewId: preview.previewId, itemIds: preview.items.map(i => i.itemId), reason: 'Reviewed notification recovery', requestKey: 'request-key-12345' };
    return { preview, input, batch: await s.create('wf', actor, input), service: s };
  }
  function log(changes: Record<string, unknown> = {}) {
    const row = { flow_id: 'flow-1', workflow_id: 'wf', config_id: 'old-config-version',
      callback_url: 'https://cortex-old.example.test/legacy', notify_event: 'workflow_succeeded', status: 'delivered', ...changes };
    raw.prepare(`INSERT INTO http_callback_logs (${Object.keys(row).join(',')}) VALUES (${Object.keys(row).map(() => '?').join(',')})`).run(...Object.values(row));
  }
  return { raw, db, calls, proofs, evidence, run, config, authorize, service, queued, log,
    advance: (ms: number) => { clock += ms; }, outcome: (status: number) => { outcome = status; },
    async close() { if (closed) return; closed = true; for (const s of services) await s.close(); await db.close(); } };
}
export type Fixture = ReturnType<typeof fixture>;
