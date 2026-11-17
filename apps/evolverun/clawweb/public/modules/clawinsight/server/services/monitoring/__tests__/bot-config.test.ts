import { afterEach, beforeEach, expect, it, vi } from 'vitest';
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
import { requireRecoveryEnabled, recoveryNotifications, recoveryConfigurations, recoveryExecutionConfiguration } from '../../repair/session-recovery/bot-configuration.js';

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
    bot_name TEXT DEFAULT 'Bot', owner_id TEXT, owner_name TEXT, avernet_tenant TEXT DEFAULT 'one', bot_type TEXT DEFAULT 'personal', is_delete INTEGER DEFAULT 0)`);
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

it('reads the Bot type in the existing directory query', async () => {
  await bot();
  await db.exec("UPDATE ac_bots SET bot_type = 'service'");
  const query = vi.spyOn(db, 'query');
  expect(await directory.get(target, scope)).toMatchObject({ botType: 'service' });
  expect(query).toHaveBeenCalledTimes(1);
  expect(query.mock.calls[0][0]).toContain('FROM ac_bots WHERE');
  query.mockRestore();
});

it('bulk updates all owner pages, excludes other owners and refuses admin write escalation', async () => {
  for (let i = 0; i < 61; i++) await bot(`bot-${i}`);
  await bot('other', 'prod', '002');
  await bot('service');
  await db.exec("UPDATE ac_bots SET bot_type = 'service' WHERE bot_id = 'service'");
  const service = { ...target, botId: 'service' };
  await configs.setSwitches(service, switches(0));
  await expect(configureBots(configs, directory, refs, principal, { botRef: refs.encode(service, 'one'), enabled: false, version: 1 })).rejects.toMatchObject({ code: 'FORBIDDEN' });
  const result = await configureBots(configs, directory, refs, principal, { all: true, enabled: true });
  expect(result).toEqual({ updated: 61, failed: [] });
  const [count] = await db.query<{ count: number }>('SELECT COUNT(*) AS count FROM ce_bot_diagnosis_recovery_config');
  expect(count.count).toBe(62);
  await expect(configureBots(configs, directory, refs, principal, { botRef: refs.encode({ ...target, botId: 'other' }, 'one'), enabled: true, version: 0 })).rejects.toMatchObject({ code: 'FORBIDDEN' });
  expect(await configureBots(configs, directory, refs, principal, { all: true, enabled: false })).toEqual({ updated: 61, failed: [] });
  expect((await configs.find({ ...target, botId: 'bot-60' }))?.recoveryEnabled).toBe(false);
  expect(await configs.find(service)).toMatchObject({ diagnosisEnabled: true, recoveryEnabled: true, version: 1 });
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
  expect(dry).toMatchObject({ applied: false, routeCount: 1, skipped: 0, warnings: [] });
  expect(dry.planned).toHaveLength(2);
  expect(await configs.find({ ...target, botId: 'a' })).toBeNull();
  expect(dry.planned.map(item => item.botId)).toEqual(['a', 'b']);
  expect(JSON.stringify(dry)).not.toMatch(/https:/);
  await migrateBotDiagnosisRecoveryConfigs(db, directory, scope, { updatedBy: '001', apply: true });
  const a = (await configs.find({ ...target, botId: 'a' }))!, b = (await configs.find({ ...target, botId: 'b' }))!;
  expect(a.recoveryEnabled).toBe(true); expect(a.diagnosisEnabled).toBe(false);
  expect(parseBotNotifications(a.configJson).owner).toBe(true); expect(parseBotNotifications(b.configJson).owner).toBe(false);
  expect(parseBotNotifications(a.configJson).group).toEqual(parseBotNotifications(b.configJson).group);
  await configs.setSwitches(a, switches(1, false));
  const repeat = await migrateBotDiagnosisRecoveryConfigs(db, directory, scope, { updatedBy: '001', apply: true });
  expect(repeat.planned).toEqual([]); expect(repeat.skipped).toBe(2);
  expect((await configs.find(a))?.recoveryEnabled).toBe(false);
});


async function legacy() {
  await bot();
  const app = new AppConfigRepository(db);
  await app.create({ config_key: 'session_recovery_allowlist', config_json: JSON.stringify({ allowed_bots: [
    { bot_id: target.botId, entity_id: target.entityId, env: target.env },
  ] }) });
  await app.create({ config_key: 'session_recovery_owner_notifications', config_json: JSON.stringify({
    bot_overrides: [{ bot_id: 'default', entity_id: '001', env: 'prod', enabled: true }],
  }) });
  await app.update('session_recovery_owner_notifications', { enabled: 0 });
  await app.create({ config_key: 'session_recovery_notifications', config_json: JSON.stringify({
    webhook: 'https://business.invalid/send', timeout_seconds: 10,
    bot_webhooks: [{ bot_id: 'default', entity_id: '001', webhook: 'https://private.invalid/send' }],
  }) });
  return app;
}
const recoveryRequest = { target: { bot_id: 'default', entity_id: '001', env: 'prod', engine: 'OC' } } as Parameters<typeof requireRecoveryEnabled>[1];
const disabledNotifications = { owner: false, group: null };

it('reads legacy admission and exact Owner notification overrides until a new row exists', async () => {
  const app = await legacy();
  const source = await app.findByKey('session_recovery_allowlist');
  await expect(requireRecoveryEnabled(configs, recoveryRequest, app)).resolves.toBeUndefined();
  expect(await recoveryNotifications(configs, recoveryRequest.target, app)).toEqual({ owner: true,
    group: { webhook: 'https://private.invalid/send', timeout_seconds: 10 } });
  expect((await recoveryConfigurations(configs, [target], app))[0]).toMatchObject({ diagnosisEnabled: false, recoveryEnabled: true, version: 0 });
  // The single UI action writes only the new table. Explicit false wins over the legacy allowlist.
  await configureBots(configs, directory, refs, principal, { botRef: refs.encode(target, 'one'), enabled: false, version: 0 });
  await expect(requireRecoveryEnabled(configs, recoveryRequest, app)).rejects.toMatchObject({ code: 'recovery_disabled' });
  expect(await recoveryNotifications(configs, recoveryRequest.target, app)).toEqual(disabledNotifications);
  expect((await recoveryConfigurations(configs, [target], app))[0]).toMatchObject({ recoveryEnabled: false, version: 1 });
  expect(await app.findByKey('session_recovery_allowlist')).toEqual(source);
});

it('preserves Bot, directory entity, Owner and environment boundaries for legacy admission', async () => {
  const app = await legacy();
  for (const patch of [{ bot_id: 'Default' }, { entity_id: '1' }, { env: 'pre' }]) {
    const request = { target: { ...recoveryRequest.target, ...patch } } as typeof recoveryRequest;
    await expect(requireRecoveryEnabled(configs, request, app)).rejects.toMatchObject({ code: 'recovery_disabled' });
    expect(await recoveryNotifications(configs, request.target, app)).toEqual(disabledNotifications);
  }
  await app.update('session_recovery_allowlist', { config_json: JSON.stringify({ allowed_bots: [
    { bot_id: target.botId, entity_id: '001', env: 'prod' }, // Owner is not the directory entity.
  ] }) });
  await expect(requireRecoveryEnabled(configs, recoveryRequest, app)).rejects.toMatchObject({ code: 'recovery_disabled' });
  await bot(); // Ambiguous directory identity must not fall back, even with matching legacy notifications.
  expect(await recoveryNotifications(configs, recoveryRequest.target, app)).toEqual(disabledNotifications);
});

it('never turns new-table errors into absence or falls back after invalid new notification settings', async () => {
  const app = await legacy();
  const read = vi.spyOn(app, 'findByKey');
  const fail = vi.spyOn(configs, 'find').mockRejectedValue(new Error('new table unavailable'));
  await expect(requireRecoveryEnabled(configs, recoveryRequest, app)).rejects.toMatchObject({ code: 'recovery_configuration_unavailable' });
  expect(await recoveryNotifications(configs, recoveryRequest.target, app)).toEqual(disabledNotifications);
  expect(read).not.toHaveBeenCalled();
  fail.mockRestore();
  await configs.setSwitches(target, switches(0));
  for (const raw of ['{}', '{', '{"notifications":{"owner":{"enabled":"true"}}}', '{"notifications":{"group":{"enabled":true,"route_id":"missing"}}}']) {
    await db.exec('UPDATE ce_bot_diagnosis_recovery_config SET config_json = ?', [raw]);
    expect(await recoveryNotifications(configs, recoveryRequest.target, app)).toEqual(disabledNotifications);
  }
  expect(read.mock.calls.flat()).not.toContain('session_recovery_notifications');
  expect(read.mock.calls.flat()).not.toContain('session_recovery_owner_notifications');
});

it('keeps the migrated Bot independent of legacy availability and uses only its referenced group', async () => {
  const app = await legacy();
  await configs.setSwitches(target, switches(0));
  await db.exec('UPDATE ce_bot_diagnosis_recovery_config SET config_json = ?', [JSON.stringify({
    notifications: { owner: { enabled: true }, group: { enabled: true, route_id: 'test_group' } },
  })]);
  await app.create({ config_key: 'session_recovery_notification_routes', config_json: JSON.stringify({ routes: {
    test_group: { webhook: 'https://new-private.invalid/send', timeout_seconds: 5 },
  } }) });
  await db.exec('UPDATE ce_app_config SET config_json = ? WHERE config_key = ?', ['{', 'session_recovery_allowlist']);
  await db.exec('UPDATE ce_app_config SET config_json = ? WHERE config_key = ?', ['{', 'session_recovery_notifications']);
  await expect(requireRecoveryEnabled(configs, recoveryRequest, app)).resolves.toBeUndefined();
  expect(await recoveryNotifications(configs, recoveryRequest.target, app)).toEqual({ owner: true,
    group: { webhook: 'https://new-private.invalid/send', timeout_seconds: 5 } });
  await configs.setSwitches(target, switches(1, false));
  await expect(requireRecoveryEnabled(configs, recoveryRequest, app)).rejects.toMatchObject({ code: 'recovery_disabled' });
  // Closing admission does not cancel notification work for an existing task.
  expect((await recoveryNotifications(configs, recoveryRequest.target, app)).group?.webhook).toBe('https://new-private.invalid/send');
  await app.update('session_recovery_notification_routes', { enabled: 0 });
  expect((await recoveryNotifications(configs, recoveryRequest.target, app)).group).toBeNull();
});

it('does not write diagnosis-only, notification-only or historical targets during migration', async () => {
  const app = await legacy();
  await bot('diagnosed'); await bot('notified'); await bot('historical');
  await db.exec("INSERT INTO ce_tasks VALUES ('SR-old', 'session_recovery', ?)", [JSON.stringify({ request: {
    target: { bot_id: 'historical', entity_id: '001', env: 'prod' },
  } })]);
  await app.update('session_recovery_owner_notifications', { config_json: JSON.stringify({ bot_overrides: [
    { bot_id: 'notified', entity_id: '001', env: 'prod', enabled: true },
  ] }) });
  const read = vi.spyOn(db, 'query');
  const result = await migrateBotDiagnosisRecoveryConfigs(db, directory, scope, { updatedBy: '001', apply: true });
  expect(result.planned.map(item => item.botId)).toEqual(['default']);
  expect(result.planned[0]).toMatchObject({ diagnosisEnabled: false, recoveryEnabled: true });
  expect(read.mock.calls.some(([sql]) => sql.includes('insight_monitoring') || sql.includes('FROM ce_tasks'))).toBe(false);
  expect(await configs.findMany(['diagnosed', 'notified', 'historical'].map(botId => ({ ...target, botId })))).toEqual([null, null, null]);
});

it('keeps legacy ownership until an explicit team is saved and resolves team resources in one boundary', async () => {
  const app = await legacy();
  await app.create({ config_key: 'session_recovery_handbooks', config_json: JSON.stringify({
    bot_teams: { old: ['prod|default|001'] }, team_routes: { old: { 'TC.MCP': ['old/handbook.md'] },
      '97': { 'TC.MCP': ['skill/handbook.md'] } },
  }) });
  await app.create({ config_key: 'session_recovery_teams', config_json: JSON.stringify({ teams: {
    '97': { notification_route_id: 'team_97', llm: { api_key_mist_key: 'team-model' } },
  } }) });
  await app.create({ config_key: 'session_recovery_notification_routes', config_json: JSON.stringify({ routes: {
    team_97: { webhook: 'https://team.invalid/send', timeout_seconds: 5 },
    own: { webhook: 'https://own.invalid/send', timeout_seconds: 5 },
  } }) });
  const request = { ...recoveryRequest, tc_fault_label: 'TC.MCP.PERMISSION' };
  await configs.setSwitches(target, switches(0));
  expect(await recoveryExecutionConfiguration(request, app, configs)).toMatchObject({ handbook: { teamKey: 'old' }, modelApiKeyRef: null });
  const config = { team_key: '97', notifications: { owner: { enabled: true }, group: { enabled: true } } };
  await configs.replaceConfiguration(target, { ...switches(1), configJson: JSON.stringify(config) });
  expect(await recoveryExecutionConfiguration(request, app, configs)).toMatchObject({
    handbook: { teamKey: '97', chapters: expect.arrayContaining(['custom/97/skill/handbook.md']) }, modelApiKeyRef: 'team-model',
  });
  expect(await recoveryNotifications(configs, request.target, app)).toEqual({ owner: true, group: { webhook: 'https://team.invalid/send', timeout_seconds: 5 } });
  for (const [route, expected] of [['own', 'https://own.invalid/send'], ['missing', null]] as const) {
    const current = (await configs.find(target))!;
    await configs.replaceConfiguration(target, { ...switches(current.version), configJson: JSON.stringify({ ...config,
      notifications: { ...config.notifications, group: { enabled: true, route_id: route } } }) });
    expect((await recoveryNotifications(configs, request.target, app)).group?.webhook ?? null).toBe(expected);
  }
  const current = (await configs.find(target))!;
  await configs.replaceConfiguration(target, { ...switches(current.version), configJson: JSON.stringify({ ...config, team_key: 'default' }) });
  expect(await recoveryNotifications(configs, request.target, app)).toEqual({ owner: true, group: null });
  expect(await recoveryExecutionConfiguration(request, app, configs)).toMatchObject({ handbook: { teamKey: null }, modelApiKeyRef: null });
  await configureBots(configs, directory, refs, principal, { all: true, enabled: false });
  expect((await configs.find(target))?.configJson).toBe(JSON.stringify({ ...config, team_key: 'default' }));
});

it('does not recover malformed team configuration through legacy resources', async () => {
  const app = await legacy();
  await configs.replaceConfiguration(target, { ...switches(0), configJson: JSON.stringify({ team_key: '97',
    notifications: { owner: { enabled: true }, group: { enabled: true } } }) });
  await expect(recoveryExecutionConfiguration(recoveryRequest, app, configs)).rejects.toMatchObject({ code: 'recovery_configuration_invalid' });
  expect(await recoveryNotifications(configs, recoveryRequest.target, app)).toEqual(disabledNotifications);
  // Admission is independent of the availability of team resources.
  await expect(requireRecoveryEnabled(configs, recoveryRequest, app)).resolves.toBeUndefined();
});
