import { defaultCapabilities, extendCapabilities } from '@/capabilities';
import { listBots } from '@/services/backendApi/bots/botController';
import { listMyBots } from '@/services/backendApi/collaboration/collaborationBotController';
import { identityService } from '@/services/workspace/identityService';

jest.mock('@/services/backendApi/bots/botController', () => ({ listBots: jest.fn() }));
jest.mock('@/services/backendApi/collaboration/collaborationBotController', () => ({ listMyBots: jest.fn() }));

const mine = jest.mocked(listMyBots);
const catalog = jest.mocked(listBots);

beforeEach(() => {
  jest.resetAllMocks();
  mine.mockResolvedValue({
    code: 20000,
    data: {
      items: [
        { kind: 'human', bot_id: '1234', name: '测试员工' },
        { kind: 'bot', bot_id: 'bot_external', name: '外部 Bot', engine: 'openclaw' },
      ],
      total: 2,
    },
  });
});
afterEach(() => extendCapabilities(defaultCapabilities));

it('BCN-only identity startup skips the unavailable engine catalog and preserves mine metadata', async () => {
  extendCapabilities({ getCollaborationBotEngineLabelEnabled: () => ({ status: 'available', value: false }) });
  catalog.mockRejectedValue(new Error('engine service is not deployed'));
  await expect(identityService.loadIdentities()).resolves.toMatchObject({
    ok: true,
    data: {
      identities: expect.arrayContaining([
        {
          id: 'bot_external',
          kind: 'bot',
          displayName: '外部 Bot',
          engine: 'openclaw',
          online: true,
          status: 'online',
          reachability: 'reachable',
          avatarUrl: undefined,
          botType: undefined,
        },
      ]),
    },
  });
  expect(catalog).not.toHaveBeenCalled();
});

it('Open Core/full internal identity startup retains engine enrichment', async () => {
  extendCapabilities(defaultCapabilities);
  catalog.mockResolvedValue({
    code: 20000,
    data: { items: [{ bot_id: 'bot_external', bot_name: '外部 Bot', engine: 'hermes', status: 'ACTIVE' }], total: 1 },
  });
  const result = await identityService.loadIdentities();
  expect(result).toMatchObject({
    ok: true,
    data: { identities: expect.arrayContaining([expect.objectContaining({ id: 'bot_external', engine: 'hermes' })]) },
  });
  expect(catalog).toHaveBeenCalledWith({ page: 1, page_size: 100, user_id: '1234' });
});
