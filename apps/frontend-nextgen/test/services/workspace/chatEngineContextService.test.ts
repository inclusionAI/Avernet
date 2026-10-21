import { listSpaces } from '@/services/backendApi/admin/spaceController';
import { listBotInventory } from '@/services/backendApi/bots/botController';
import { loadChatEngineContext } from '@/services/workspace/chatEngineContextService';
import type { ChatBotView } from '@/services/workspace/botSessionService';

jest.mock('@/services/backendApi/admin/spaceController');
jest.mock('@/services/backendApi/bots/botController');
const spaces = jest.mocked(listSpaces);
const inventory = jest.mocked(listBotInventory);
const bot: ChatBotView = { botId: 'selected:entity', realBotId: 'selected', ownerId: 'entity',
  engineOwnerId: 'owner', isTeamBot: true, spaceId: '42', displayName: 'Selected Bot',
  online: true, chatable: true, engine: 'openclaw', runtimeStage: 'draft' };
const row = { bot_id: 'selected', owner_entity_id: 'owner', actions: ['restart'] };
const page = (items: unknown[], total = items.length) => ({ code: 200000, data: { items, total } });
const run = (value = bot, controller = new AbortController()) => loadChatEngineContext(value, 'caller', controller.signal);
beforeEach(() => {
  jest.resetAllMocks();
  spaces.mockResolvedValue(page([{ space_id: 42, space_type: 'TEAM' }]) as never);
  inventory.mockResolvedValue(page([row]) as never);
});

test('only supplements the selected Bot after its Space accessibility is established', async () => {
  expect(await run()).toMatchObject({ ...bot, actions: ['restart'] });
  expect(spaces).toHaveBeenCalledWith({ user_id: 'caller', scope: 'accessible', page_no: 1, page_size: 100 }, expect.any(AbortSignal));
  expect(inventory).toHaveBeenCalledTimes(1);
  expect(inventory).toHaveBeenCalledWith({ user_id: 'caller', keyword: 'Selected Bot', page: 1, page_size: 100 }, '42', expect.any(AbortSignal));
});

test.each([{ items: [] }, { items: [{ space_id: 43, space_type: 'TEAM' }] }])('does not query all without access to the selected Space: %j', async ({ items }) => {
  spaces.mockResolvedValue(page(items) as never);
  expect(await run()).toBeNull();
  expect(inventory).not.toHaveBeenCalled();
});

test('does not infer Space access from a collaboration or missing owner/Space', async () => {
  expect(await run({ ...bot, spaceId: undefined })).toBeNull();
  expect(spaces).not.toHaveBeenCalled();
  expect(inventory).not.toHaveBeenCalled();
});

test.each([
  { ...row, bot_id: 'other' }, { ...row, owner_entity_id: 'other' },
  { ...row, actions: ['engine_restart'] }, { ...row, actions: [] },
  { ...row, disabled_actions: { restart: 'locked' } },
])('does not borrow context or capability from %j', async (item) => {
  inventory.mockResolvedValue(page([item]) as never);
  expect(await run()).toBeNull();
});

test.each(['service_online', 'service_failed', undefined])('Service %s is not draft evidence', async (display_state) => {
  inventory.mockResolvedValue(page([{ ...row, display_state }]) as never);
  expect(await run({ ...bot, botType: 'service' })).toBeNull();
});

test('requires every matching Service card to be an explicit draft', async () => {
  inventory.mockResolvedValue(page([{ ...row, display_state: 'service_draft' }]) as never);
  expect(await run({ ...bot, botType: 'service' })).toMatchObject({ serviceLifecycle: 'draft', actions: ['restart'] });
  inventory.mockResolvedValue(page([{ ...row, display_state: 'service_draft' }, { ...row, display_state: 'service_online' }]) as never);
  expect(await run({ ...bot, botType: 'service' })).toBeNull();
});

test.each([
  { code: 500000, data: {} }, { code: 200000, data: {} },
])('rejects unavailable inventory %j', async (response) => {
  inventory.mockResolvedValue(response as never);
  await expect(run()).rejects.toThrow('Engine context page is unavailable');
});

test.each([page([], 1), page([row], 101)])('incomplete inventory %j cannot authorize inspection', async (response) => {
  inventory.mockResolvedValue(response as never);
  await expect(run()).resolves.toBeNull();
});

test('an aborted accessibility query never starts inventory', async () => {
  const controller = new AbortController();
  spaces.mockImplementation(async () => { controller.abort(); return page([{ space_id: 42, space_type: 'TEAM' }]) as never; });
  expect(await run(bot, controller)).toBeNull();
  expect(inventory).not.toHaveBeenCalled();
});

test('a later failing Service page does not publish partial draft evidence', async () => {
  inventory.mockResolvedValueOnce(page(Array.from({ length: 100 }, () => ({ ...row, display_state: 'service_draft' })), 101) as never)
    .mockRejectedValueOnce(new Error('unavailable'));
  await expect(run({ ...bot, botType: 'service' })).rejects.toThrow('unavailable');
});

test('finds the selected Space on a later accessible page', async () => {
  spaces.mockResolvedValueOnce(page(Array.from({ length: 100 }, (_, index) => ({ space_id: index + 100, space_type: 'TEAM' })), 101) as never)
    .mockResolvedValueOnce(page([{ space_id: 42, space_type: 'TEAM' }], 101) as never);
  expect(await run()).toMatchObject({ realBotId: 'selected', actions: ['restart'] });
  expect(spaces).toHaveBeenCalledTimes(2);
  expect(inventory).toHaveBeenCalledTimes(1);
});

test('scans every filtered Service page and rejects an online card on the last page', async () => {
  const draft = { ...row, display_state: 'service_draft' };
  inventory.mockResolvedValueOnce(page(Array.from({ length: 100 }, () => draft), 101) as never)
    .mockResolvedValueOnce(page([{ ...row, display_state: 'service_online' }], 101) as never);
  expect(await run({ ...bot, botType: 'service' })).toBeNull();
  expect(inventory).toHaveBeenCalledTimes(2);
});
