import Database from 'better-sqlite3';
import { describe,expect,it } from 'vitest';
import { allMigrations } from '../db.js';
import { aistudioTrainManualAuditDdl } from '../schema-aistudio-train-manual-audit.js';
describe('manual audit additive schema', () => {
  it('registers unique v156 and creates an exact marker without changing flow_events history', () => {
    expect(allMigrations.filter(m => m.version===156)).toHaveLength(1);
    const raw=new Database(':memory:');
    try {
      for(const ddl of aistudioTrainManualAuditDdl('sqlite')) raw.exec(ddl);
      const cols=raw.prepare('PRAGMA table_info(aistudio_train_manual_audits)').all() as {name:string}[];
      expect(cols.map(c => c.name)).toEqual(['id','event_id','identity_json','event_row_id','gmt_create','gmt_modified']);
      raw.exec("INSERT INTO aistudio_train_manual_audits(event_id,identity_json) VALUES('e','[]')");
      expect(() => raw.exec("INSERT INTO aistudio_train_manual_audits(event_id,identity_json) VALUES('e','[]')")).toThrow(/unique/i);
    } finally {raw.close();}
  });
  it.each(['mysql','zdas'] as const)('uses inline full identity uniqueness and native audit timestamps on %s', type => {
    const ddl=aistudioTrainManualAuditDdl(type).join(';');
    expect(ddl).toMatch(/event_id VARCHAR\(255\) CHARACTER SET ascii COLLATE ascii_bin NOT NULL/);
    expect(ddl).toMatch(/UNIQUE.*\(event_id\)/); expect(ddl).not.toMatch(/CREATE INDEX|flow_events|event_id\(\d+\)/);
    expect(ddl).toContain('event_row_id BIGINT'); expect(ddl).toContain('gmt_modified TIMESTAMP');
  });
});
