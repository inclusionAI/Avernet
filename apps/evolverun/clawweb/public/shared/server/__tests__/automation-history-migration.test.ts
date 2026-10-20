import { expect, it, vi } from 'vitest';
import Database from 'better-sqlite3';
import { SqliteDatabase, type IDatabase } from '../db.js';
import { dialectFor } from '../db/dialect.js';
import { repairReleaseMigrations } from '../schema-repair-release.js';
import { automationHistoryMigration } from '../schema-automation-history.js';
it('indexes historical JSON statuses without application backfill and tolerates a repeated migration', async () => {
  const raw = new Database(':memory:'), db = new SqliteDatabase(raw);
  try {
    for (const migration of repairReleaseMigrations) for (const sql of migration.sql) await db.exec(db.dialect.renderDdl(sql));
    raw.prepare(`INSERT INTO workflow_repair_release_records (record_id,workflow_id,task_id,revision,record_kind,state_version,request_key,request_digest,body_json,created_at_ms,updated_at_ms)
      VALUES ('old','wf','scope',0,'auto_run',0,'old','digest','{"status":"waiting"}',1,1)`).run();
    for (let i = 0; i < 2; i++) {
      await automationHistoryMigration.migrate!(db);
      for (const sql of automationHistoryMigration.sql) await db.exec(db.dialect.renderDdl(sql));
    }
    expect(raw.prepare('SELECT record_status,body_json,state_version FROM workflow_repair_release_records').get()).toEqual({record_status:'waiting',body_json:'{"status":"waiting"}',state_version:0});
    raw.prepare("UPDATE workflow_repair_release_records SET record_kind='other',body_json=? WHERE record_id='old'").run(JSON.stringify({status:'an_unrelated_status_longer_than_sixteen'}));
    expect(raw.prepare("SELECT record_status FROM workflow_repair_release_records WHERE record_id='old'").get()).toEqual({record_status:null});
    expect(raw.prepare('PRAGMA index_list(workflow_repair_release_records)').all()).toEqual(expect.arrayContaining([
      expect.objectContaining({name:'idx_repair_history_time'}),expect.objectContaining({name:'idx_repair_history_status'}),
    ]));
  } finally { await db.close(); }
});
it.each(['mysql','zdas'] as const)('renders the generated projection and history indexes for %s', async type => {
  const dialect = dialectFor(type), exec = vi.fn().mockResolvedValue({affectedRows:0});
  await automationHistoryMigration.migrate!({dialect,exec} as unknown as IDatabase);
  expect(exec.mock.calls[0][0]).toContain("JSON_UNQUOTE(JSON_EXTRACT(body_json, '$.status'))");
  expect(exec.mock.calls[0][0]).toContain("ELSE NULL END) VIRTUAL");
  const ddl = automationHistoryMigration.sql.map(sql => dialect.renderDdl(sql)).join('\n');
  const indexes = type === 'zdas' ? exec.mock.calls.map(call => call[0]).join('\n') : ddl;
  expect(indexes).toContain('idx_repair_history_time'); expect(indexes).toContain('idx_repair_history_status');
  if (type === 'zdas') expect(indexes).toContain('ADD INDEX');
  expect(ddl).not.toContain('IF NOT EXISTS');
  // Largest compound key: task(64), kind(32), status(16), record(64), timestamp(8 bytes).
  expect((64+32+16+64)*4+8).toBeLessThan(767);
});
it('propagates actual migration errors', async () => {
  await expect(automationHistoryMigration.migrate!({dialect:dialectFor('mysql'),exec:async()=>{throw new Error('DDL denied')}} as unknown as IDatabase)).rejects.toThrow('DDL denied');
});
