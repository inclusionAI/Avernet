import { defaultCapabilities, extendCapabilities } from '@/capabilities';
import { createBot, listBotInventory, pollBotAuthStatus } from '@/services/backendApi/bots/botController';
import { BackendRequestError } from '@/services/backendApi/httpClient';
import { mapBotDto } from '@/services/botWorkshop/botMapper';
import { botWorkshopService } from '@/services/botWorkshop/botWorkshopService';
import { afterEach, describe, expect, test } from '@jest/globals';

jest.mock('@/services/backendApi/bots/botController', () => ({
  createBot: jest.fn(),
  listBotInventory: jest.fn(),
  pollBotAuthStatus: jest.fn(),
}));

const mockedCreateBot = createBot as jest.MockedFunction<typeof createBot>;
const mockedListBotInventory = listBotInventory as jest.MockedFunction<typeof listBotInventory>;
const mockedPollBotAuthStatus = pollBotAuthStatus as jest.MockedFunction<typeof pollBotAuthStatus>;

afterEach(() => {
  // 覆盖过引擎清单的用例退出时还原 Open 默认，避免污染同文件后续用例。
  extendCapabilities({ getBotEngineOptions: defaultCapabilities.getBotEngineOptions });
});

describe('botWorkshopService', () => {
  test.each([
    ['service', true],
    ['non-service', false],
  ] as const)('maps %s filtering to the backend before pagination', async (serviceMode, isService) => {
    mockedListBotInventory.mockResolvedValue({
      code: 200000,
      data: { total: 21, page: 2, page_size: 20, items: [] },
    });

    const result = await botWorkshopService.list({ serviceMode, page: 2, pageSize: 20 });

    expect(mockedListBotInventory).toHaveBeenCalledWith(
      expect.objectContaining({ is_service: isService, page: 2, page_size: 20 }),
      undefined,
    );
    expect(result).toMatchObject({ total: 21, page: 2, pageSize: 20 });
  });
  test('maps Avernet OpenAPI bot fields into the domain model', () => {
    const { item } = mapBotDto({
      bot_id: 'bot-openapi-1',
      bot_name: '知识整理助手',
      bot_desc: '整理项目资料',
      engine: 'openclaw',
      bot_type: 'personal',
      status: 'ACTIVE',
      owner_entity_id: 'personal-space',
    });

    expect(item).toMatchObject({
      id: 'bot-openapi-1',
      name: '知识整理助手',
      description: '整理项目资料',
      deployment: 'cloud',
      serviceMode: 'non-service',
      lifecycle: 'running',
    });
    expect(item.runtime.engine).toBe('openclaw');
  });

  test('详情页使用网关寻址 Bot ID，不被运行时 default 覆盖', () => {
    const { item } = mapBotDto(
      {
        bot_id: 'default',
        bot_name: '调试 Bot',
        engine: 'openclaw',
        status: 'ACTIVE',
      },
      '20260806_wg6wkrk4',
    );

    expect(item.id).toBe('20260806_wg6wkrk4');
  });

  test('does not fake local creation before device workflow is available', async () => {
    await expect(
      botWorkshopService.create({
        scenario: 'local',
        name: '本地资料助手',
        description: '只在本机运行',
        engine: 'openclaw',
        spaceId: 'team-space',
        ownership: 'team',
        serviceMode: 'service',
        initialize: true,
      }),
    ).rejects.toThrow('需要先选择已绑定设备和工作目录');
  });

  test('maps a cloud service bot to the Avernet-compatible contract', async () => {
    const request = botWorkshopService.toCreateRequest({
      scenario: 'cloud',
      name: '云端发布助手',
      description: '提供稳定服务',
      engine: 'teclaw',
      spaceId: 'team-space',
      ownership: 'team',
      serviceMode: 'service',
      initialize: true,
    });

    expect(request).toEqual({
      bot_name: '云端发布助手',
      bot_desc: '提供稳定服务',
      engine: 'teclaw',
      cluster_name: 'ANDC',
      bot_type: 'service',
      space_id: 'team-space',
    });
  });

  test('Open Core（阿里云部署）默认清单允许原生 Claude Code 个人云端直建', () => {
    const request = botWorkshopService.toCreateRequest({
      scenario: 'cloud',
      name: 'claude_code Bot',
      description: 'AI Coding',
      engine: 'claude_code',
      spaceId: '10001',
      ownership: 'personal',
      serviceMode: 'non-service',
      initialize: true,
    });
    expect(request).toEqual({
      bot_name: 'claude_code Bot',
      bot_desc: 'AI Coding',
      engine: 'claude_code',
      cluster_name: 'ACRA',
      bot_type: 'personal',
      space_id: '10001',
    });
  });

  test('rejects creating an ordinary personal Claude Code bot when the form offers no native entry', () => {
    // internal overlay（AgentCoding 接管 CC 创建）：引擎清单不含 claude_code，普通 CC 直建仍拦截。
    extendCapabilities({
      getBotEngineOptions: () => ({
        status: 'available',
        value: [
          { value: 'openclaw', label: 'OpenClaw' },
          { value: 'aicoding', label: 'AgentCoding' },
          { value: 'hermes', label: 'Hermes' },
          { value: 'teclaw', label: 'TEClaw' },
        ],
      }),
    });
    expect(() =>
      botWorkshopService.toCreateRequest({
        scenario: 'cloud',
        name: 'claude_code Bot',
        description: 'AI Coding',
        engine: 'claude_code',
        spaceId: '10001',
        ownership: 'personal',
        serviceMode: 'service',
        initialize: true,
      }),
    ).toThrow('普通 Claude Code');
  });

  test('maps an AgentCoding template while retaining its declared engine', () => {
    const request = botWorkshopService.toCreateRequest({
      scenario: 'cloud',
      name: 'AgentCoding Bot',
      description: 'AI Coding',
      engine: 'aicoding',
      spaceId: '10001',
      ownership: 'personal',
      serviceMode: 'non-service',
      initialize: true,
      agentCoding: {
        kind: 'template',
        template: {
          key: 'architect',
          versionId: 'v1',
          name: '架构 Bot',
          engine: 'claude_code',
          source: 'official',
          templateType: 'architect',
          fields: [],
          config: {},
        },
        values: { architect_name: '平台架构' },
      },
    });
    expect(request).toEqual(
      expect.objectContaining({
        engine: 'claude_code',
        engine_properties: {
          template_type: 'architect',
          template_config: expect.objectContaining({
            template_key: 'architect',
            template_version_id: 'v1',
          }),
        },
      }),
    );
    expect(
      ((request.engine_properties as Record<string, unknown>).template_config as Record<string, unknown>).envs,
    ).toEqual({
      AGENT_CONFIG: JSON.stringify({ architect_name: '平台架构', bot_name: 'AgentCoding Bot' }),
    });
  });

  test('allows service creation for a template that explicitly enables the service Bot capability', () => {
    const request = botWorkshopService.toCreateRequest({
      scenario: 'cloud',
      name: '架构服务 Bot',
      description: '',
      engine: 'aicoding',
      spaceId: '10001',
      ownership: 'personal',
      serviceMode: 'service',
      initialize: true,
      agentCoding: {
        kind: 'template',
        template: {
          key: 'architect',
          versionId: '2600004',
          name: '架构 Bot',
          engine: 'claude_code',
          source: 'official',
          templateType: 'architect',
          fields: [],
          config: { capabilities: { upgrade_service_bot: true } },
        },
        values: {},
      },
    });

    expect(request).toEqual(expect.objectContaining({ engine: 'claude_code', bot_type: 'service' }));
  });

  test('rejects service creation for a template without the service Bot capability', () => {
    expect(() =>
      botWorkshopService.toCreateRequest({
        scenario: 'cloud',
        name: '架构服务 Bot',
        description: '',
        engine: 'aicoding',
        spaceId: '10001',
        ownership: 'personal',
        serviceMode: 'service',
        initialize: true,
        agentCoding: {
          kind: 'template',
          template: {
            key: 'architect',
            versionId: '2600004',
            name: '架构 Bot',
            engine: 'claude_code',
            source: 'official',
            templateType: 'architect',
            fields: [],
            config: {},
          },
          values: {},
        },
      }),
    ).toThrow('当前模板未开启服务 Bot 能力');
  });

  test('rejects service creation when AgentCoding has no selected template', () => {
    expect(() =>
      botWorkshopService.toCreateRequest({
        scenario: 'cloud',
        name: 'AIcoding 服务 Bot',
        description: '',
        engine: 'aicoding',
        spaceId: '10001',
        ownership: 'personal',
        serviceMode: 'service',
        initialize: true,
      }),
    ).toThrow('请选择 AgentCoding 模板');
  });

  test('returns the AgentPass iframe authorization step from the OpenAPI 202 payload', async () => {
    mockedCreateBot.mockResolvedValue({
      code: 202000,
      message: 'Accepted',
      data: {
        bot_id: 'bot-pending-1',
        iframe_url: 'https://agentpass.example/authorize',
        redirect_url: '',
      },
    });
    const input = {
      scenario: 'cloud' as const,
      name: '授权助手',
      description: '等待 AgentPass 授权',
      engine: 'openclaw',
      spaceId: '10001',
      ownership: 'personal' as const,
      serviceMode: 'non-service' as const,
      initialize: true,
    };

    await expect(botWorkshopService.create(input)).resolves.toEqual({
      type: 'authorization_required',
      botId: 'bot-pending-1',
      iframeUrl: 'https://agentpass.example/authorize',
      redirectUrl: '',
      request: botWorkshopService.toCreateRequest(input),
    });
  });

  test('echoes the original create request when polling authorization and maps the issued Bot', async () => {
    const request = {
      bot_name: '授权助手',
      bot_desc: '等待 AgentPass 授权',
      engine: 'openclaw',
      cluster_name: 'ACRA' as const,
      bot_type: 'personal' as const,
      space_id: '10001',
    };
    mockedPollBotAuthStatus.mockResolvedValue({
      code: 200000,
      data: {
        status: 'ISSUED',
        bot: { bot_id: 'bot-pending-1', bot_name: '授权助手', engine: 'openclaw', status: 'PENDING' },
      },
    });

    const result = await botWorkshopService.pollCreateAuthorization('bot-pending-1', request);

    expect(mockedPollBotAuthStatus).toHaveBeenCalledWith('bot-pending-1', request);
    expect(result.status).toBe('ISSUED');
    expect(result.bot?.id).toBe('bot-pending-1');
  });

  test('preserves a terminal AgentPass status returned in the OpenAPI 400 envelope', async () => {
    mockedPollBotAuthStatus.mockRejectedValue(
      new BackendRequestError('Authorization did not complete', {
        status: 400,
        apiPath: '/openapi/v1/bots/bot-pending-1/auth-status',
        data: { data: { status: 'REJECTED', message: 'User rejected authorization', bot: null } },
      }),
    );

    await expect(
      botWorkshopService.pollCreateAuthorization('bot-pending-1', {
        bot_name: '授权助手',
        bot_desc: '',
        engine: 'openclaw',
        cluster_name: 'ACRA',
        bot_type: 'personal',
      }),
    ).resolves.toEqual({ status: 'REJECTED', message: 'User rejected authorization' });
  });

  test('uses the real singlebox personal space instead of a placeholder', () => {
    expect(botWorkshopService.getCreateSpaces('cloud', undefined, 'mock-user')[0]).toMatchObject({
      id: 'personal:mock-user',
      ownership: 'personal',
      canCreate: true,
    });
  });

  test('uses the selected global personal space as the cloud Bot default', () => {
    expect(
      botWorkshopService.getCreateSpaces('cloud', '10001', 'mock-user', {
        id: '10001',
        name: '风太的个人空间',
        ownership: 'personal',
        canCreate: true,
      }),
    ).toEqual([
      {
        id: '10001',
        name: '风太的个人空间',
        ownership: 'personal',
        canCreate: true,
      },
    ]);
  });

  test('rejects unsupported service combinations and invalid names', () => {
    expect(() =>
      botWorkshopService.validateCreate({
        scenario: 'cloud',
        name: 'invalid@bot',
        description: '',
        engine: 'hermes',
        spaceId: 'personal-space',
        ownership: 'personal',
        serviceMode: 'service',
        initialize: false,
      }),
    ).toThrow('Bot 名称不能包含 @');

    expect(() =>
      botWorkshopService.validateCreate({
        scenario: 'cloud',
        name: 'Hermes 服务',
        description: '',
        engine: 'hermes',
        spaceId: 'personal-space',
        ownership: 'personal',
        serviceMode: 'service',
        initialize: false,
      }),
    ).toThrow('Hermes 暂不支持服务化');
  });
});
