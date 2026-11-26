import { expect, it, vi } from 'vitest';
import { notificationBotConfig } from './bot-config-fixture.js';
import { recoveryCardConfiguration, recoveryExecutionConfiguration, requireRecoveryEnabled, SESSION_RECOVERY_CARDS_KEY } from '../bot-configuration.js';
import { SESSION_RECOVERY_OWNER_NOTIFICATIONS_KEY } from '../legacy-configuration.js';
import type { RecoveryAppConfigStore } from '../configuration-contracts.js';
import type { RecoveryStoredTask, RecoveryTimelineEvent } from '../task-contracts.js';
import { SessionRecoveryGroupNotifier, SessionRecoveryNotifier } from '../notification.js';
import { confirmOwnerRequest } from '../owner-cooperation.js';

const card = { template_id: 'published-template', callback_route_key: 'existing-callback-route' };
const row = (value: unknown, enabled = 1) => ({ enabled, config_json: JSON.stringify(value) });
const legacy = row({ card: { pre: card } });
const task = (): RecoveryStoredTask => ({ schemaVersion: 'session-recovery-task/v1', taskId: 'SR-one', version: 1,
  request: { event_id: 'event', delivery_key: 'delivery', session_key: 'original', diagnosis: 'Permission denied', tc_fault_label: 'TC.MCP.PERMISSION',
    target: { bot_id: 'bot', entity_id: 'owner', env: 'prod', engine: 'OC' } },
  level: 'approval', status: 'waiting_owner', phase: 'assess', controlPlaneEnvironment: 'pre',
  observationDeadlineAt: null, ownerDeadlineAt: null, plan: null, judgment: null, evidence: [], timeline: [],
  ownerRequest: { requestId: 'request-1', revision: 1, status: 'pending', reason: 'permission', summary: 'Read access missing',
    instructions: 'Request document read access', evidence_ids: [], resumePhase: 'assess', createdAtMs: 1 },
});

it('uses enabled legacy card resources only while the new row is absent', async () => {
  const config = { findByKey: vi.fn<RecoveryAppConfigStore['findByKey']>(async key => key === SESSION_RECOVERY_OWNER_NOTIFICATIONS_KEY ? legacy : null) };
  const sender = { sendNotification: vi.fn(), sendCard: vi.fn(), updateCard: vi.fn() };
  const notifier = new SessionRecoveryNotifier(sender, 'https://cw.invalid', config, 'pre', notificationBotConfig());
  expect(await notifier.notifyOwnerRequest({ task: task(), updateOnly: false })).toBe(true);
  expect(sender.sendCard).toHaveBeenCalledWith(expect.objectContaining({ templateId: card.template_id, callbackRouteKey: card.callback_route_key }));
  // Copy the card resource unchanged, then retire the entire legacy row. Existing card IDs survive.
  config.findByKey.mockImplementation(async key => key === SESSION_RECOVERY_CARDS_KEY ? row({ pre: card }) : null);
  expect(await notifier.notifyOwnerRequest({ task: task(), updateOnly: true })).toBe(true);
  expect(sender.updateCard).toHaveBeenCalledWith(expect.objectContaining({ outTrackId: sender.sendCard.mock.calls[0][0].outTrackId }));
  expect(sender.sendCard).toHaveBeenCalledOnce();
});

it('keeps legacy-only Owner overrides and card delivery usable before migration', async () => {
  const config = { findByKey: vi.fn<RecoveryAppConfigStore['findByKey']>(async key => key === SESSION_RECOVERY_OWNER_NOTIFICATIONS_KEY
    ? row({ card: { pre: card }, bot_overrides: [{ bot_id: 'bot', entity_id: 'owner', env: 'prod', enabled: true }] }) : null) };
  const bots = notificationBotConfig(); bots.find.mockResolvedValue(null);
  const sender = { sendNotification: vi.fn(), sendCard: vi.fn(), updateCard: vi.fn() };
  const notifier = new SessionRecoveryNotifier(sender, 'https://cw.invalid', config, 'pre', bots);
  expect(await notifier.notifyOwnerRequest({ task: task(), updateOnly: false })).toBe(true);
  expect(sender.sendCard).toHaveBeenCalledWith(expect.objectContaining({ templateId: card.template_id, callbackRouteKey: card.callback_route_key }));
});


it.each([0, 2])('never sends or updates cards from a legacy row with enabled=%s', async enabled => {
  const config = { findByKey: vi.fn<RecoveryAppConfigStore['findByKey']>(async key => key === SESSION_RECOVERY_OWNER_NOTIFICATIONS_KEY
    ? row({ card: { pre: card }, bot_overrides: [{ bot_id: 'bot', entity_id: 'owner', env: 'prod', enabled: true }] }, enabled) : null) };
  const sender = { sendNotification: vi.fn(), sendCard: vi.fn(), updateCard: vi.fn() };
  const notifier = new SessionRecoveryNotifier(sender, 'https://cw.invalid', config, 'pre', notificationBotConfig());
  expect(await recoveryCardConfiguration(config, 'pre')).toBeNull();
  expect(await notifier.notifyOwnerRequest({ task: task(), updateOnly: false })).toBe(false);
  expect(await notifier.notifyOwnerRequest({ task: task(), updateOnly: true })).toBe(false);
  expect(await notifier.notify({ botId: 'bot', ownerId: 'owner', targetEnvironment: 'prod', taskId: 'SR-one', sessionKey: 'original', kind: 'result', summary: 'handoff' })).toBe(false);
  expect(sender.sendCard).not.toHaveBeenCalled(); expect(sender.updateCard).not.toHaveBeenCalled();
});

it.each([
  ['disabled', row({ pre: card }, 0)], ['invalid enabled', row({ pre: card }, 2)],
  ['empty', row({})], ['wrong environment', row({ prod: card })], ['null', row(null)], ['array', row([])],
  ['legacy shape', legacy], ['missing template', row({ pre: { callback_route_key: 'route' } })],
  ['empty template', row({ pre: { template_id: ' ' } })], ['invalid callback', row({ pre: { ...card, callback_route_key: 1 } })],
  ['invalid JSON', { enabled: 1, config_json: '{' }],
])('fails closed for an existing %s card configuration without consulting valid legacy resources', async (_name, current) => {
  const config = { findByKey: vi.fn<RecoveryAppConfigStore['findByKey']>(async key => key === SESSION_RECOVERY_CARDS_KEY ? current : legacy) };
  expect(await recoveryCardConfiguration(config, 'pre')).toBeNull();
  expect(config.findByKey.mock.calls).toEqual([[SESSION_RECOVERY_CARDS_KEY]]);
  const sender = { sendNotification: vi.fn(), sendCard: vi.fn(), updateCard: vi.fn() };
  const notifier = new SessionRecoveryNotifier(sender, 'https://cw.invalid', config, 'pre', notificationBotConfig('{"notifications":{"owner":{"enabled":true}}}'));
  expect(await notifier.notifyOwnerRequest({ task: task(), updateOnly: false })).toBe(false);
  expect(await notifier.notify({ botId: 'bot', ownerId: 'owner', targetEnvironment: 'prod', taskId: 'SR-one', sessionKey: 'original', kind: 'result', summary: 'handoff' })).toBe(false);
  expect(sender.sendCard).not.toHaveBeenCalled(); expect(sender.sendNotification).not.toHaveBeenCalled();
});

it('does not reinterpret failed reads as absence or send cards when neither source exists', async () => {
  const config = { findByKey: vi.fn<RecoveryAppConfigStore['findByKey']>().mockRejectedValue(new Error('unavailable')) };
  expect(await recoveryCardConfiguration(config, 'pre')).toBeNull();
  expect(config.findByKey.mock.calls).toEqual([[SESSION_RECOVERY_CARDS_KEY]]);
  config.findByKey.mockResolvedValue(null);
  expect(await recoveryCardConfiguration(config, 'pre')).toBeNull();
  config.findByKey.mockImplementation(async key => key === SESSION_RECOVERY_CARDS_KEY ? null : { enabled: 1, config_json: '{' });
  expect(await recoveryCardConfiguration(config, 'pre')).toBeNull();
});

it('keeps Markdown plan notices independent of unavailable card resources', async () => {
  const config = { findByKey: vi.fn<RecoveryAppConfigStore['findByKey']>().mockRejectedValue(new Error('unavailable')) };
  const sender = { sendNotification: vi.fn(), sendCard: vi.fn(), updateCard: vi.fn() };
  const notifier = new SessionRecoveryNotifier(sender, 'https://cw.invalid', config, 'pre', notificationBotConfig('{"notifications":{"owner":{"enabled":true}}}'));
  expect(await notifier.notify({ botId: 'bot', ownerId: 'owner', targetEnvironment: 'prod', taskId: 'SR-one', sessionKey: 'original', kind: 'plan', summary: 'Review plan' })).toBe(true);
  expect(sender.sendNotification).toHaveBeenCalledOnce(); expect(sender.sendCard).not.toHaveBeenCalled();
  expect(config.findByKey).not.toHaveBeenCalled();
});

it.each(['absent', 'disabled'])('admits, resolves handbooks, sends/updates cards, and audits group delivery with retired entries %s', async retired => {
  const rows: Record<string, ReturnType<typeof row>> = {
    session_recovery_cards: row({ pre: card }),
    session_recovery_teams: row({ teams: { '97': { notification_route_id: 'team_97' } } }),
    session_recovery_handbooks: row({ team_routes: { '97': { 'TC.MCP': ['permission/handbook.md'] } } }),
    session_recovery_notification_routes: row({ routes: { team_97: { webhook: 'https://group.invalid/send', timeout_seconds: 10 } } }),
  };
  const config = { findByKey: vi.fn<RecoveryAppConfigStore['findByKey']>(async key => rows[key] ?? (retired === 'disabled' ? row({}, 0) : null)) };
  const bots = notificationBotConfig(JSON.stringify({ team_key: '97', notifications: { owner: { enabled: true }, group: { enabled: true } } }));
  let current = task();
  await expect(requireRecoveryEnabled(bots, current.request, config)).resolves.toBeUndefined();
  const execution = await recoveryExecutionConfiguration(current.request, config, bots);
  expect(execution.handbook).toMatchObject({ teamKey: '97' });
  expect(JSON.stringify(execution.handbook)).toContain('custom/97/permission/handbook.md');
  const sender = { sendNotification: vi.fn(), sendCard: vi.fn(), updateCard: vi.fn() };
  const notifier = new SessionRecoveryNotifier(sender, 'https://cw.invalid', config, 'pre', bots);
  expect(await notifier.notifyOwnerRequest({ task: current, updateOnly: false })).toBe(true);
  const sent = sender.sendCard.mock.calls[0][0];
  current = { ...current, ...confirmOwnerRequest(current, sent.cardParamMap.request_id, 'owner', 'Done', 2) };
  expect(current).toMatchObject({ status: 'running', phase: 'assess', requiresFreshObservation: true });
  expect(await notifier.notifyOwnerRequest({ task: current, updateOnly: true })).toBe(true);
  expect(sender.updateCard).toHaveBeenCalledWith(expect.objectContaining({ outTrackId: sent.outTrackId,
    cardParamMap: expect.objectContaining({ confirm_enabled: 'false' }) }));
  const receipt: RecoveryTimelineEvent = { event_id: 'card-sent', at_ms: 1, kind: 'notification', actor: 'service', summary: 'Owner card sent',
    payload: { channel: 'owner_card', operation: 'create', state: 'sent', sent: true, owner_request_id: 'request-1' } };
  current.timeline.push(receipt);
  const groupSender = { send: vi.fn() };
  const appendNotification = vi.fn(async (_id: string, event: RecoveryTimelineEvent) => { current.timeline.push(event); return true; });
  new SessionRecoveryGroupNotifier({ botConfigurations: bots, config, sender: groupSender, publicBaseUrl: 'https://cw.invalid', controlPlaneEnvironment: 'pre',
    tasks: { find: async () => current, appendNotification } }).after({ taskId: current.taskId, eventId: receipt.event_id, kind: 'owner_card_sent' });
  await vi.waitFor(() => expect(current.timeline.some(event => event.payload.channel === 'group_webhook' && event.payload.sent === true)).toBe(true));
  expect(groupSender.send).toHaveBeenCalledWith(expect.objectContaining({ webhook: 'https://group.invalid/send' }));
  expect(await notifier.notify({ botId: 'bot', ownerId: 'owner', targetEnvironment: 'prod', taskId: current.taskId, sessionKey: 'original', kind: 'result', summary: 'Owner follow-up' })).toBe(true);
  expect(sender.sendCard).toHaveBeenLastCalledWith(expect.objectContaining({ templateId: card.template_id, outTrackId: 'sr-owner-handoff:pre:SR-one' }));
  expect(config.findByKey.mock.calls.every(([key]) => Object.hasOwn(rows, key))).toBe(true);
});
