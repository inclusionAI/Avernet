import { afterEach, beforeEach, expect, it } from 'vitest';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { migrations } from '@avernet/clawweb-shared/server/schema';
import { database } from './test-database.js';
import { BotDiagnosisRecoveryConfigRepository } from '../../../repositories/bot-diagnosis-recovery-config-repository.js';
import { SqlMonitoringBotDirectory } from '../../../repositories/monitoring-bot-directory.js';
import { AppConfigRepository } from '@avernet/clawevolve/server/repositories/app-config-repository';
import { initializeMonitoringSqlite } from '../schema.js';
import { configureBots } from '../bot-config-service.js';
import { createMonitoringReferences } from '../target-ref.js';
import { parseBotNotifications } from '../bot-config-contracts.js';
import { migrateBotDiagnosisRecoveryConfigs } from '../bot-config-migration.js';
import { requireRecoveryEnabled, recoveryNotifications } from '../../repair/session-recovery/bot-configuration.js';

let db: IDatabase, configs: BotDiagnosisRecoveryConfigRepository, directory: SqlMonitoringBotDirectory;
const scope = { tenant: 'one', allowedTargetEnvs: ['pre', 'prod'] };
const principal = { ...scope, staffId: '001', isAuthenticated: true, isClawInsightAdmin: true };
const target = { botId: 'default', entityId: 'service-entity', env: 'prod' };
const refs = createMonitoringReferences();
beforeEach(async () => {
  db = database(':memory:');
  for (const version of [134, 154]) {
    const migration = migrations.find(m => m.version === version)!;
    await migration.migrate?.(db);
    for (const ddl of migration.sql) await db.exec(db.dialect.renderDdl(ddl));
  }
  await initializeMonitoringSqlite(db);
  await db.exec(`CREATE TABLE ac_bots (id INTEGER PRIMARY KEY AUTOINCREMENT, bot_id TEXT, entity_id TEXT, env TEXT, active_engine TEXT DEFAULT 'openclaw',
    bot_name TEXT DEFAULT 'Bot', owner_id TEXT, owner_name TEXT, avernet_tenant TEXT DEFAULT 'one', is_delete INTEGER DEFAULT 0)`);
  await db.exec("CREATE TABLE ce_tasks (task_id TEXT, task_type TEXT, config_json TEXT)");
  configs = new BotDiagnosisRecoveryConfigRepository(db); directory = new SqlMonitoringBotDirectory(db, true);
});
afterEach(async () => { await db.close(); });
async function bot(botId = target.botId, env = 'prod', owner = '001', entity = target.entityId) {
  await db.exec('INSERT INTO ac_bots (bot_id, entity_id, env, owner_id) VALUES (?, ?, ?, ?)', [botId, entity, env, owner]);
}
const switches = (expectedVersion: number, enabled = true) => ({ diagnosisEnabled: enabled, recoveryEnabled: enabled, expectedVersion, updatedBy: '001' });

it('isolates complete target identities and rejects stale changes without losing JSON', async () => {
  await configs.setSwitches(target, switches(0));
  const raw = '{"notifications":{"owner":{"enabled":true}},"future":{"value":42}}';
  await db.exec('UPDATE ce_bot_diagnosis_recovery_config SET config_json = ?', [raw]);
  await configs.setSwitches(target, switches(1, false));
  await expect(configs.setSwitches(target, switches(1))).rejects.toMatchObject({ code: 'EVENT_CONFLICT' });
  await expect(configs.setSwitches(target, switches(0))).rejects.toMatchObject({ code: 'EVENT_CONFLICT' });
  expect(await configs.find(target)).toMatchObject({ configJson: raw, version: 2, recoveryEnabled: false });
  for (const other of [{ ...target, env: 'pre' }, { ...target, entityId: 'another' }, { ...target, botId: 'Default' }]) {
    expect(await configs.find(other)).toBeNull(); await configs.setSwitches(other, switches(0));
  }
  expect(await configs.findMany([])).toEqual([]);
});

it('resolves recovery Owner to the directory entity and fails closed after ownership changes', async () => {
  await bot(); await configs.setSwitches(target, switches(0));
  const request = { target: { bot_id: 'default', entity_id: '001', env: 'prod', engine: 'OC' } } as Parameters<typeof requireRecoveryEnabled>[1];
  await expect(requireRecoveryEnabled(configs, request)).resolves.toBeUndefined();
  await db.exec('UPDATE ce_bot_diagnosis_recovery_config SET config_json = ?', ['{"notifications":{"owner":{"enabled":true}}}']);
  expect((await recoveryNotifications(configs, request.target)).owner).toBe(true);
  await db.exec("UPDATE ac_bots SET owner_id = '002'");
  await expect(requireRecoveryEnabled(configs, request)).rejects.toMatchObject({ code: 'recovery_disabled' });
  expect((await recoveryNotifications(configs, request.target)).owner).toBe(false);
});

it('bulk updates all owner pages, excludes other owners and refuses admin write escalation', async () => {
  for (let i = 0; i < 61; i++) await bot(`bot-${i}`);
  await bot('other', 'prod', '002');
  const result = await configureBots(configs, directory, refs, principal, { all: true, enabled: true });
  expect(result).toEqual({ updated: 61, failed: [] });
  const [count] = await db.query<{ count: number }>('SELECT COUNT(*) AS count FROM ce_bot_diagnosis_recovery_config');
  expect(count.count).toBe(61);
  await expect(configureBots(configs, directory, refs, principal, { botRef: refs.encode({ ...target, botId: 'other' }, 'one'), enabled: true, version: 0 })).rejects.toMatchObject({ code: 'FORBIDDEN' });
  expect(await configureBots(configs, directory, refs, principal, { all: true, enabled: false })).toEqual({ updated: 61, failed: [] });
  expect((await configs.find({ ...target, botId: 'bot-60' }))?.recoveryEnabled).toBe(false);
});

it.each(['', '{}', 'null', '[]', '{', '{"notifications":null}', '{"notifications":{"owner":{"enabled":"true"}}}',
  '{"notifications":{"owner":{"enabled":true},"group":null}}', '{"notifications":{"owner":{"enabled":true},"group":{"enabled":true,"route_id":7}}}'])('disables both channels for absent or invalid notification JSON %s', raw => {
  expect(parseBotNotifications(raw)).toEqual({ owner: false, group: null });
});
it('allows independent explicit channels and extensible top-level config', () => {
  expect(parseBotNotifications('{"future":{},"notifications":{"owner":{"enabled":true},"group":{"enabled":true,"route_id":"team_a"}}}'))
    .toEqual({ owner: true, group: { routeId: 'team_a' } });
});

it('dry-runs migration, deduplicates routes and preserves edited rows on rerun', async () => {
  await bot('a'); await bot('b'); await bot('c', 'pre');
  const app = new AppConfigRepository(db);
  await app.create({ config_key: 'session_recovery_allowlist', config_json: JSON.stringify({ allowed_bots: ['a', 'b'].map(bot_id => ({ bot_id, entity_id: target.entityId, env: 'prod' })) }) });
  await app.create({ config_key: 'session_recovery_owner_notifications', config_json: JSON.stringify({ bot_overrides: [{ bot_id: 'a', entity_id: '001', env: 'prod', enabled: true }] }) });
  await app.update('session_recovery_owner_notifications', { enabled: 0 });
  await app.create({ config_key: 'session_recovery_notifications', config_json: JSON.stringify({ webhook: 'https://business.invalid/send', timeout_seconds: 10,
    bot_webhooks: ['a', 'b'].map(bot_id => ({ bot_id, entity_id: '001', webhook: 'https://private.invalid/send' })) }) });
  await db.exec("INSERT INTO ce_tasks VALUES ('SR-history', 'session_recovery', ?)", [JSON.stringify({ request: { target: { bot_id: 'c', entity_id: '001', env: 'pre' } } })]);
  const dry = await migrateBotDiagnosisRecoveryConfigs(db, directory, scope, { updatedBy: '001' });
  expect(dry).toMatchObject({ applied: false, routeCount: 2, skipped: 0, warnings: [] });
  expect(dry.planned).toHaveLength(3);
  expect(await configs.find({ ...target, botId: 'a' })).toBeNull();
  expect(JSON.stringify(dry)).not.toMatch(/https:/);
  await migrateBotDiagnosisRecoveryConfigs(db, directory, scope, { updatedBy: '001', apply: true });
  const a = (await configs.find({ ...target, botId: 'a' }))!, b = (await configs.find({ ...target, botId: 'b' }))!;
  expect(a.recoveryEnabled).toBe(true); expect(a.diagnosisEnabled).toBe(false);
  expect(parseBotNotifications(a.configJson).owner).toBe(true); expect(parseBotNotifications(b.configJson).owner).toBe(false);
  expect(parseBotNotifications(a.configJson).group).toEqual(parseBotNotifications(b.configJson).group);
  await configs.setSwitches(a, switches(1, false));
  const repeat = await migrateBotDiagnosisRecoveryConfigs(db, directory, scope, { updatedBy: '001', apply: true });
  expect(repeat.planned).toEqual([]); expect(repeat.skipped).toBe(3);
  expect((await configs.find(a))?.recoveryEnabled).toBe(false);
});
