import Database from 'better-sqlite3';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db/sqlite-database';
import { dialectFor, sqliteDialect, zdasDialect } from '@avernet/clawweb-shared/server/db/dialect';
import { migrations } from '@avernet/clawweb-shared/server/schema';
import { AIStudioTrainWatchRepository } from '../aistudio-train-watch-repository.js';
import { AIStudioTrainManualAuditRepository } from '../aistudio-train-manual-audit-repository.js';
import { watchFixture } from '../../contracts/__tests__/aistudio-train.fixture.js';
import { manualAuditEvent } from './aistudio-train-manual-audit.fixture.js';

const opened: Database.Database[] = [];
afterEach(() => { for (const db of opened.splice(0)) db.close(); });
function fixture() {
  const raw = new Database(':memory:'); opened.push(raw);
  // Execute the REAL v71 ZDAS render (round-tripped to SQLite for this fixture).
  // Its standalone indexes become SELECT 0, leaving all columns but no key UNIQUE.
  const ddl = migrations.find(m => m.version === 71)!.sql.map(sql => zdasDialect.renderDdl(sql));
  expect(ddl[1]).toContain('standalone CREATE INDEX skipped');
  for (const sql of ddl) raw.exec(sqliteDialect.renderDdl(sql));
  raw.exec(`CREATE TABLE flow_events(id INTEGER PRIMARY KEY AUTOINCREMENT,event_id TEXT,flow_id TEXT,workflow_id TEXT,
    node_id TEXT,event_type TEXT,attempt INTEGER,time BIGINT,data_json TEXT,error_text TEXT,gmt_create INTEGER,gmt_modified INTEGER)`);
  const db = new SqliteDatabase(raw);
  return {raw, db, watches: new AIStudioTrainWatchRepository(db), audits: new AIStudioTrainManualAuditRepository(db)};
}

describe('existing config schema uniqueness is a read-only AIS prerequisite', () => {
  it('does not bless actual ZDAS v71 rendering without its skipped unique index', async () => {
    const {raw, watches, audits} = fixture();
    expect(await watches.isReady()).toBe(false);
    expect(await audits.isReady()).toBe(false);
    // Direct repository calls must fail too, rather than ACK duplicate watches/receipts.
    for (let replay = 0; replay < 2; replay++) {
      await expect(watches.create(watchFixture())).rejects.toThrow(/unique/i);
      await expect(audits.insertAIStudioTrainManualAudit(manualAuditEvent())).rejects.toThrow(/unique/i);
    }
    expect(raw.prepare('SELECT COUNT(*) AS n FROM cm_app_config').get()).toEqual({n: 0});
    expect(raw.prepare('SELECT COUNT(*) AS n FROM flow_events').get()).toEqual({n: 0});
    expect(raw.prepare("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='cm_app_config'").all()).toEqual([]);
  });
  it.each([
    'CREATE INDEX wrong ON cm_app_config(config_key)',
    'CREATE UNIQUE INDEX wrong ON cm_app_config(config_key,version)',
    'CREATE UNIQUE INDEX wrong ON cm_app_config(config_key) WHERE enabled=1',
    'CREATE UNIQUE INDEX wrong ON cm_app_config(lower(config_key))',
  ])('rejects insufficient uniqueness: %s', async sql => {
    const {raw, watches, audits} = fixture(); raw.exec(sql);
    expect(await watches.isReady()).toBe(false); expect(await audits.isReady()).toBe(false);
  });
  it('accepts an already existing full single-column key and preserves exact replay', async () => {
    const {raw, watches, audits} = fixture();
    raw.exec('CREATE UNIQUE INDEX existing_config_key ON cm_app_config(config_key)');
    expect(await watches.isReady()).toBe(true); expect(await audits.isReady()).toBe(true);
    const w = watchFixture(); expect(await watches.create(w)).toEqual(w);
    expect(await watches.create({...w, watchId: 'replay'})).toEqual(w);
    expect(await audits.insertAIStudioTrainManualAudit(manualAuditEvent())).toBe(true);
    expect(await audits.insertAIStudioTrainManualAudit(manualAuditEvent())).toBe(true);
    expect(raw.prepare('SELECT COUNT(*) AS n FROM cm_app_config').get()).toEqual({n: 2});
    expect(raw.prepare('SELECT COUNT(*) AS n FROM flow_events').get()).toEqual({n: 1});
  });
  it.each(['mysql', 'zdas'] as const)('requires full-column unique metadata for %s and fails closed on metadata errors', async dbType => {
    let rows: unknown[] = [];
    const query = vi.fn(async (sql: string) => sql.includes('information_schema.STATISTICS') ? rows : []);
    const db = {dbType, dialect: dialectFor(dbType), query} as any;
    const watches = new AIStudioTrainWatchRepository(db), audits = new AIStudioTrainManualAuditRepository(db);
    for (const invalid of [[], [{index_name:'key',column_name:'config_key',prefix_length:10}],
      [{index_name:'key',column_name:'config_key',prefix_length:null}, {index_name:'key',column_name:'version',prefix_length:null}]]) {
      rows = invalid;
      expect(await watches.isReady()).toBe(false); expect(await audits.isReady()).toBe(false);
    }
    rows = [{index_name:'key',column_name:'config_key',prefix_length:null}];
    expect(await watches.isReady()).toBe(true); expect(await audits.isReady()).toBe(true);
    expect(query.mock.calls.some(([sql]) => /NON_UNIQUE = 0/.test(sql))).toBe(true);
    query.mockRejectedValue(new Error('metadata unavailable'));
    expect(await watches.isReady()).toBe(false); expect(await audits.isReady()).toBe(false);
  });
  it('blocks later mutations if the required unique key is lost, without repairing schema', async () => {
    const {raw, watches} = fixture(); raw.exec('CREATE UNIQUE INDEX existing_config_key ON cm_app_config(config_key)');
    const w = await watches.create(watchFixture()); raw.exec('DROP INDEX existing_config_key');
    await expect(watches.compareAndSwap(1, {...w, version:2})).rejects.toThrow(/unique/i);
    await expect(watches.requestManualPoll(1, {...w, version:2,manualRequestedAt:200,lastManualRequestedAt:200,manualRequestedBy:'owner'})).rejects.toThrow(/unique/i);
    expect(await watches.find(w.flowId,w.nodeId,w.attempt)).toEqual(w);
  });
});
