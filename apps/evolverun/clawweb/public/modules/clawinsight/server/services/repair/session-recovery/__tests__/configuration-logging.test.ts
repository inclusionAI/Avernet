import { beforeEach, expect, it, vi } from 'vitest';
import { logRepairEvent } from '../../common/logging.js';
import { recoveryCardConfiguration, recoveryExecutionConfiguration, recoveryModelCredentials, recoveryNotifications, requireRecoveryEnabled } from '../bot-configuration.js';
import { notificationBotConfig } from './bot-config-fixture.js';
import type { RecoveryRequest } from '../contracts.js';
import type { RecoveryStoredTask } from '../task-contracts.js';
vi.mock('../../common/logging.js', () => ({ logRepairEvent: vi.fn() }));
beforeEach(() => vi.clearAllMocks());
const request: RecoveryRequest = { event_id: 'event', delivery_key: 'delivery', session_key: 'session', tc_fault_label: 'TC.MCP', diagnosis: 'Denied', execution_level: 'approval',
  target: { bot_id: 'bot', entity_id: 'owner', env: 'prod', engine: 'OC' } };
const row = (value: unknown, enabled = 1) => ({ enabled, config_json: JSON.stringify(value) });
const card = { template_id: 'PRIVATE_TEMPLATE', callback_route_key: 'PRIVATE_CALLBACK' };
const recorded = () => vi.mocked(logRepairEvent).mock.calls.map(call => call[3]);

it.each([true, false])('logs the actual admission source with a new row present=%s', async present => {
  const bots = notificationBotConfig(); if (!present) bots.find.mockResolvedValue(null);
  const app = { findByKey: vi.fn(async () => row({ allowed_bots: [{ bot_id: 'bot', entity_id: 'owner', env: 'prod' }] })) };
  await requireRecoveryEnabled(bots, request, app);
  expect(recorded()).toContainEqual(expect.objectContaining({ configuration: 'admission', source: present ? 'new' : 'legacy', enabled: true, eventId: 'event' }));
  if (present) expect(app.findByKey).not.toHaveBeenCalled();
});

it('logs a failed Bot lookup as unresolved without claiming a legacy fallback', async () => {
  const bots = notificationBotConfig(); bots.find.mockRejectedValue(new Error('PRIVATE_DATABASE_ERROR'));
  const app = { findByKey: vi.fn() };
  await expect(requireRecoveryEnabled(bots, request, app)).rejects.toMatchObject({ code: 'recovery_configuration_unavailable' });
  expect(recorded()).toContainEqual(expect.objectContaining({ source: 'unresolved', status: 'unavailable' }));
  expect(app.findByKey).not.toHaveBeenCalled(); expect(JSON.stringify(recorded())).not.toContain('PRIVATE_');
});

it.each(['bot', 'team'])('records new notifications and %s route ownership without destinations', async routeSource => {
  const bots = notificationBotConfig(JSON.stringify({ team_key: '97', notifications: { owner: { enabled: true }, group: { enabled: true,
    ...(routeSource === 'bot' ? { route_id: 'own' } : {}) } } }));
  const app = { findByKey: vi.fn(async key => key === 'session_recovery_teams' ? row({ teams: { '97': { notification_route_id: 'own' } } })
    : row({ routes: { own: { webhook: 'https://PRIVATE_WEBHOOK.invalid/send', timeout_seconds: 10 } } })) };
  const value = await recoveryNotifications(bots, request.target, app, 'SR-one');
  expect(value.owner).toBe(true); expect(value.group).not.toBeNull();
  expect(recorded()).toContainEqual(expect.objectContaining({ configuration: 'notifications', source: 'new', routeSource, teamKey: '97',
    taskId: 'SR-one', ownerId: 'owner', targetEnvironment: 'prod', ownerEnabled: true, groupEnabled: true }));
  expect(JSON.stringify(recorded())).not.toContain('PRIVATE_');
});

it('records disabled legacy notification outcomes without confusing them with new policy', async () => {
  const bots = notificationBotConfig(); bots.find.mockResolvedValue(null);
  const app = { findByKey: vi.fn(async () => row({ bot_overrides: [{ bot_id: 'bot', entity_id: 'owner', env: 'prod', enabled: true }] }, 0)) };
  expect(await recoveryNotifications(bots, request.target, app, 'SR-one')).toEqual({ owner: false, group: null });
  expect(recorded()).toContainEqual(expect.objectContaining({ configuration: 'notifications', source: 'legacy', ownerEnabled: false, groupEnabled: false }));
});

it.each(['new', 'legacy'])('records %s card selection with task and control-plane environment', async source => {
  const app = { findByKey: vi.fn(async key => key === 'session_recovery_cards' ? source === 'new' ? row({ pre: card }) : null : row({ card: { pre: card } })) };
  expect(await recoveryCardConfiguration(app, 'pre', { taskId: 'SR-one', botId: 'bot', ownerId: 'owner', targetEnvironment: 'prod' })).toEqual(card);
  expect(recorded()).toContainEqual(expect.objectContaining({ configuration: 'cards', source, environment: 'pre', targetEnvironment: 'prod', taskId: 'SR-one', enabled: true }));
  expect(JSON.stringify(recorded())).not.toContain('PRIVATE_');
});

it.each([['disabled', row({ pre: card }, 0)], ['read_or_parse_failed', { enabled: 1, config_json: '{' }]])('records unusable new card config as new, reason=%s', async (reason, config) => {
  const app = { findByKey: vi.fn(async () => config) };
  expect(await recoveryCardConfiguration(app, 'pre')).toBeNull();
  expect(app.findByKey).toHaveBeenCalledExactlyOnceWith('session_recovery_cards');
  expect(recorded()).toContainEqual(expect.objectContaining({ configuration: 'cards', source: 'new', enabled: false, reason }));
});

it.each(['new', 'legacy'])('records %s team ownership and the default credential selection', async source => {
  const bots = notificationBotConfig('{"team_key":"default"}'); if (source === 'legacy') bots.find.mockResolvedValue(null);
  const app = { findByKey: vi.fn(async () => null) };
  await recoveryExecutionConfiguration(request, app, bots);
  expect(recorded()).toContainEqual(expect.objectContaining({ configuration: 'execution', source, eventId: 'event', reason: 'default_model_credentials' }));
});

it('logs only the MIST key name after actual resolution, and never logs returned values or resolver errors', async () => {
  const task = { taskId: 'SR-one', request, modelApiKeyRef: 'teams.97.model' } as RecoveryStoredTask;
  const resolve = vi.fn(async () => 'PRIVATE_API_KEY');
  expect(await recoveryModelCredentials(task, resolve)).toEqual({ modelApiKey: 'PRIVATE_API_KEY' });
  expect(resolve).toHaveBeenCalledExactlyOnceWith('teams.97.model');
  expect(recorded()).toContainEqual(expect.objectContaining({ configuration: 'model_credentials', source: 'mist', mistKey: 'teams.97.model', status: 'resolved', taskId: 'SR-one' }));
  resolve.mockRejectedValue(new Error('PRIVATE_API_KEY in provider error'));
  await expect(recoveryModelCredentials(task, resolve)).rejects.toMatchObject({ code: 'recovery_model_credentials_unavailable' });
  expect(recorded().at(-1)).toMatchObject({ mistKey: 'teams.97.model', status: 'unavailable' });
  expect(JSON.stringify(recorded())).not.toContain('PRIVATE_');
  resolve.mockClear();
  expect(await recoveryModelCredentials({ ...task, modelApiKeyRef: undefined }, resolve)).toEqual({});
  expect(resolve).not.toHaveBeenCalled();
  expect(recorded().at(-1)).toMatchObject({ configuration: 'model_credentials', source: 'default', status: 'resolved' });
  expect(recorded().at(-1)).not.toHaveProperty('mistKey');
});


it('logs invalid new notification JSON without falling back to legacy or leaking the payload', async () => {
  const bots = notificationBotConfig('{PRIVATE_CONFIG');
  const app = { findByKey: vi.fn() };
  expect(await recoveryNotifications(bots, request.target, app, 'SR-one')).toEqual({ owner: false, group: null });
  expect(recorded()).toContainEqual(expect.objectContaining({ source: 'new', configuration: 'notifications', reason: 'invalid_configuration' }));
  expect(app.findByKey).not.toHaveBeenCalled(); expect(JSON.stringify(recorded())).not.toContain('PRIVATE_');
});
