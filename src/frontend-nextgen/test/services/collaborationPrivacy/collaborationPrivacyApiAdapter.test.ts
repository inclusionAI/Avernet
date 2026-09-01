import type { BackendApiEnvelope, BackendApiPage, CollaborationBotDto, OwnedBotDto } from '@/services/backendApi';
import { createCollaborationPrivacyApiAdapter } from '@/services/collaborationPrivacy/collaborationPrivacyApiAdapter';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';

const listMyBots = jest.fn<(...args: any[]) => any>();
const getCollaborationBot = jest.fn<(...args: any[]) => any>();
const patchCollaborationBot = jest.fn<(...args: any[]) => any>();
const adapter = createCollaborationPrivacyApiAdapter({
  listMyBots,
  getCollaborationBot,
  patchCollaborationBot,
});

function createPhysicalBot(overrides: Partial<CollaborationBotDto> = {}): CollaborationBotDto {
  return {
    kind: 'bot',
    bot_id: 'bot-1',
    name: '测试 Bot',
    visibility: 'protected',
    status: 'online',
    env: 'pre',
    descriptor: { summary: '', domains: [], skills: [], scopes: [] },
    reachability: 'reachable',
    created_at: 1,
    updated_at: 2,
    ...overrides,
  };
}

function createHumanBot(overrides: Partial<CollaborationBotDto> = {}): CollaborationBotDto {
  return {
    kind: 'human',
    bot_id: 'human-1',
    name: '测试用户',
    visibility: 'private',
    status: 'online',
    env: 'pre',
    created_at: 1,
    updated_at: 2,
    ...overrides,
  };
}

beforeEach(() => {
  listMyBots.mockReset();
  getCollaborationBot.mockReset();
  patchCollaborationBot.mockReset();
});

describe('collaborationPrivacyApiAdapter', () => {
  it('reads only the managed Bot list from mine and forwards cancellation', async () => {
    listMyBots.mockResolvedValue({
      code: 20000,
      data: { items: [createPhysicalBot()], total: 1, offset: 0, limit: 20 },
    });
    const signal = new AbortController().signal;

    await expect(adapter.listManagedBots({ offset: 0, limit: 20 }, signal)).resolves.toEqual({
      items: [createPhysicalBot()],
      total: 1,
      offset: 0,
      limit: 20,
    });
    expect(listMyBots).toHaveBeenCalledWith({ offset: 0, limit: 20 }, signal);
  });

  it('joins the ready /bots engine field onto mine rows, including composite Bot ids', async () => {
    const listBots = jest.fn(
      async (
        ..._args: [Record<string, unknown>?, AbortSignal?]
      ): Promise<BackendApiEnvelope<BackendApiPage<OwnedBotDto>>> => {
        void _args;
        return {
          code: 200000,
          data: {
            total: 2,
            items: [
              {
                bot_id: 'bot-1',
                bot_name: '测试 Bot',
                engine: 'openclaw',
                status: 'ACTIVE',
              },
              {
                bot_id: 'bot-2',
                bot_name: '未匹配 Bot',
                engine: 'claude_code',
                status: 'ACTIVE',
              },
            ] satisfies OwnedBotDto[],
          },
        };
      },
    );
    const enrichedAdapter = createCollaborationPrivacyApiAdapter({
      listMyBots,
      getCollaborationBot,
      patchCollaborationBot,
      listBots,
    });
    listMyBots.mockResolvedValue({
      code: 20000,
      data: {
        items: [createPhysicalBot({ bot_id: 'bot-1:447147' })],
        total: 1,
        offset: 0,
        limit: 20,
      },
    });

    await expect(
      enrichedAdapter.listManagedBots({ user_id: '447147' }, new AbortController().signal),
    ).resolves.toMatchObject({
      items: [expect.objectContaining({ bot_id: 'bot-1:447147', engine: 'openclaw' })],
    });
    expect(listBots).toHaveBeenCalledWith({ page: 1, page_size: 100, user_id: '447147' }, expect.any(AbortSignal));
  });

  it('keeps the managed Bot list usable when the optional /bots engine enrichment fails', async () => {
    const listBots = jest.fn(
      async (
        ..._args: [Record<string, unknown>?, AbortSignal?]
      ): Promise<BackendApiEnvelope<BackendApiPage<OwnedBotDto>>> => {
        void _args;
        return {
          code: 200000,
          data: {
            total: 1,
            items: [{ bot_id: 'bot-1', engine: 'openclaw', status: 'ACTIVE' } as OwnedBotDto],
          },
        };
      },
    );
    const enrichedAdapter = createCollaborationPrivacyApiAdapter({
      listMyBots,
      getCollaborationBot,
      patchCollaborationBot,
      listBots,
    });
    listMyBots.mockResolvedValue({
      code: 20000,
      data: { items: [createPhysicalBot()], total: 1, offset: 0, limit: 20 },
    });

    await expect(enrichedAdapter.listManagedBots({ user_id: '447147' })).resolves.toMatchObject({
      items: [expect.objectContaining({ bot_id: 'bot-1' })],
      total: 1,
      offset: 0,
      limit: 20,
    });
  });

  it('accepts the real dual-audience visibility fields returned by mine', async () => {
    const bot = createPhysicalBot({ visibility: 'public', user_visibility: 'private', friend_ext: {} });
    listMyBots.mockResolvedValue({
      code: 20000,
      data: { items: [bot], total: 1, offset: 0, limit: 20 },
    });

    await expect(adapter.listManagedBots()).resolves.toEqual({
      items: [bot],
      total: 1,
      offset: 0,
      limit: 20,
    });
  });

  it('loads one managed Bot detail and forwards cancellation', async () => {
    const bot = createPhysicalBot();
    getCollaborationBot.mockResolvedValue({ code: 20000, data: bot });
    const signal = new AbortController().signal;

    await expect(adapter.getManagedBot('bot-1', signal)).resolves.toEqual(bot);
    expect(getCollaborationBot).toHaveBeenCalledWith('bot-1', signal);
  });

  it('passes only Swagger-confirmed PATCH fields through without filling unsupported fields', async () => {
    const patchedBot = createPhysicalBot({ status: 'hidden', visibility: 'public' });
    patchCollaborationBot.mockResolvedValue({ code: 20000, data: patchedBot });
    const signal = new AbortController().signal;

    await expect(
      adapter.patchManagedBot(
        'bot-1',
        {
          status: 'hidden',
          visibility: 'public',
          descriptor: { summary: 'updated' },
        },
        signal,
      ),
    ).resolves.toEqual(patchedBot);
    expect(patchCollaborationBot).toHaveBeenCalledWith(
      'bot-1',
      {
        status: 'hidden',
        visibility: 'public',
        descriptor: { summary: 'updated' },
      },
      signal,
    );
  });

  it('passes the deployed friend approval PATCH fields through and accepts the confirmed response', async () => {
    const body = {
      friend_ext: {
        public_user_approval: { puid: 'p-user', status: 'AGREE' },
        no_check_scope_friend_deps: ['A4195'],
      },
      friend_check_in_strategy: 'DEPT_FREE' as const,
    };
    const patchedBot = createPhysicalBot(body);
    patchCollaborationBot.mockResolvedValue({ code: 20000, data: patchedBot });
    const signal = new AbortController().signal;

    await expect(adapter.patchManagedBot('bot-1', body, signal)).resolves.toEqual(patchedBot);
    expect(patchCollaborationBot).toHaveBeenCalledWith('bot-1', body, signal);
  });

  it('rejects malformed friend approval fields in Bot responses', async () => {
    patchCollaborationBot.mockResolvedValueOnce({
      code: 20000,
      data: createPhysicalBot({ friend_check_in_strategy: 'UNKNOWN' as 'APPROVAL' }),
    });
    await expect(adapter.patchManagedBot('bot-1', { status: 'online' })).rejects.toThrow(
      'Bot 更新接口返回了无法识别的数据',
    );

    patchCollaborationBot.mockResolvedValueOnce({
      code: 20000,
      data: createPhysicalBot({ friend_ext: [] as unknown as Record<string, unknown> }),
    });
    await expect(adapter.patchManagedBot('bot-1', { status: 'online' })).rejects.toThrow(
      'Bot 更新接口返回了无法识别的数据',
    );
  });

  it('rejects malformed success envelopes instead of treating missing data as an empty success', async () => {
    listMyBots.mockResolvedValue({ code: 20000, data: { items: 'invalid' } });
    getCollaborationBot.mockResolvedValue({ code: 20000 });
    patchCollaborationBot.mockResolvedValue({ code: 20000 });

    await expect(adapter.listManagedBots()).rejects.toThrow('Bot 列表接口返回了无法识别的数据');
    await expect(adapter.getManagedBot('bot-1')).rejects.toThrow('Bot 详情接口返回了无法识别的数据');
    await expect(adapter.patchManagedBot('bot-1', { status: 'online' })).rejects.toThrow(
      'Bot 更新接口返回了无法识别的数据',
    );
  });

  it('rejects mine responses that omit the confirmed pagination envelope', async () => {
    listMyBots.mockResolvedValue({
      code: 20000,
      data: { items: [createPhysicalBot()] },
    });

    await expect(adapter.listManagedBots()).rejects.toThrow('Bot 列表接口返回了无法识别的数据');
  });

  it('accepts the confirmed Human projection without requiring physical Bot-only fields', async () => {
    const human = createHumanBot();
    listMyBots.mockResolvedValue({
      code: 20000,
      data: { items: [human], total: 1, offset: 0, limit: 20 },
    });

    await expect(adapter.listManagedBots()).resolves.toEqual({
      items: [human],
      total: 1,
      offset: 0,
      limit: 20,
    });
  });

  it('rejects physical Bot rows that omit fields required by the confirmed response schema', async () => {
    const requiredFields: Array<keyof CollaborationBotDto> = [
      'name',
      'visibility',
      'status',
      'env',
      'descriptor',
      'reachability',
      'created_at',
      'updated_at',
    ];

    for (const field of requiredFields) {
      const bot = createPhysicalBot();
      delete bot[field];
      listMyBots.mockResolvedValue({
        code: 20000,
        data: { items: [bot], total: 1, offset: 0, limit: 20 },
      });

      await expect(adapter.listManagedBots()).rejects.toThrow('Bot 列表接口返回了无法识别的数据');
    }
  });

  it('rejects malformed descriptor and Human rows carrying physical Bot-only projections', async () => {
    const malformedDescriptor = createPhysicalBot({
      descriptor: { summary: '', domains: [], scopes: [], skills: [{ name: '' }] },
    });
    const malformedHuman = createHumanBot({ reachability: 'reachable' });

    listMyBots.mockResolvedValue({
      code: 20000,
      data: { items: [malformedDescriptor], total: 1, offset: 0, limit: 20 },
    });
    await expect(adapter.listManagedBots()).rejects.toThrow('Bot 列表接口返回了无法识别的数据');

    listMyBots.mockResolvedValue({
      code: 20000,
      data: { items: [malformedHuman], total: 1, offset: 0, limit: 20 },
    });
    await expect(adapter.listManagedBots()).rejects.toThrow('Bot 列表接口返回了无法识别的数据');
  });

  it('rejects detail and PATCH responses for a different Bot instead of updating the wrong target', async () => {
    getCollaborationBot.mockResolvedValue({ code: 20000, data: createPhysicalBot({ bot_id: 'bot-2' }) });
    patchCollaborationBot.mockResolvedValue({ code: 20000, data: createPhysicalBot({ bot_id: 'bot-2' }) });

    await expect(adapter.getManagedBot('bot-1')).rejects.toThrow('Bot 详情接口返回了不匹配的 Bot');
    await expect(adapter.patchManagedBot('bot-1', { status: 'hidden' })).rejects.toThrow(
      'Bot 更新接口返回了不匹配的 Bot',
    );
  });

  it('rejects unknown business codes instead of exposing a false success', async () => {
    listMyBots.mockResolvedValue({ code: 1, data: { items: [] } });

    await expect(adapter.listManagedBots()).rejects.toThrow('Bot 列表接口返回了无法识别的业务码');
  });
});
