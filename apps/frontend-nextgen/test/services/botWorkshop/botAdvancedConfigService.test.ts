import { botEditorController } from '@/services/backendApi/bots/botEditorController';
import { botAdvancedConfigService } from '@/services/botWorkshop/botAdvancedConfigService';

jest.mock('@/services/backendApi/bots/botEditorController', () => ({
  botEditorController: {
    createChannel: jest.fn(),
    updateChannel: jest.fn(),
    listChannels: jest.fn(),
    listIdentityFiles: jest.fn(),
    getIdentityFile: jest.fn(),
    updateIdentityFile: jest.fn(),
  },
}));

test('配置初始请求透传 Bot Owner', async () => {
  const listIdentityFiles = botEditorController.listIdentityFiles as jest.Mock;
  const listChannels = botEditorController.listChannels as jest.Mock;
  listIdentityFiles.mockResolvedValue({ data: { files: [] } });
  listChannels.mockResolvedValue({ data: [] });

  await botAdvancedConfigService.listIdentityFiles('bot-1', 'owner-1');
  await botAdvancedConfigService.listChannels('bot-1', 'owner-1');

  expect(listIdentityFiles).toHaveBeenCalledWith('bot-1', 'owner-1', undefined);
  expect(listChannels).toHaveBeenCalledWith('bot-1', 'owner-1');
});

test.each(['draft', 'verify', 'online'] as const)('MD 读取透传真实 owner 与 %s', async (stage) => {
  jest.mocked(botEditorController.listIdentityFiles).mockResolvedValue({ code: 200000, data: { bot_id: 'shared', files: [] } });
  jest.mocked(botEditorController.getIdentityFile).mockResolvedValue({
    code: 200000, data: { bot_id: 'shared', type: 'RULES', file_path: '', content: '# rules' },
  });
  await botAdvancedConfigService.listIdentityFiles('shared', 'owner', stage);
  await expect(botAdvancedConfigService.getIdentityFile('shared', 'RULES', 'owner', stage)).resolves.toBe('# rules');
  expect(botEditorController.listIdentityFiles).toHaveBeenLastCalledWith('shared', 'owner', stage);
  expect(botEditorController.getIdentityFile).toHaveBeenLastCalledWith('shared', 'RULES', 'owner', stage);
});

test('保存传 owner，后端拒绝 MEMBER 写入时保留错误，不重试或降级权限', async () => {
  const refused = new Error('Not found');
  jest.mocked(botEditorController.updateIdentityFile).mockReset().mockRejectedValue(refused);
  await expect(botAdvancedConfigService.saveIdentityFile('shared', 'RULES', '# rules', 'owner')).rejects.toBe(refused);
  expect(botEditorController.updateIdentityFile).toHaveBeenCalledTimes(1);
  expect(botEditorController.updateIdentityFile).toHaveBeenCalledWith('shared', 'RULES', '# rules', 'owner');
});

test('创建钉钉渠道时透传流式卡片配置', async () => {
  const createChannel = botEditorController.createChannel as jest.Mock;
  createChannel.mockResolvedValue({ data: {} });

  await botAdvancedConfigService.createChannel('bot-1', {
    bindingMode: 'plugin',
    stage: 'verify',
    description: '研发群',
    clientId: 'client-1',
    clientSecret: 'secret-1',
    enableStreamingCards: true,
    cardTemplateId: 'tpl-1',
    cardTemplateKey: 'content',
    dmPolicy: 'open',
    allowlist: ['*'],
    replyToMessage: true,
    aixEnable: true,
    includeSenderName: true,
  }, 'owner-1');

  expect(createChannel).toHaveBeenCalledWith('bot-1', {
    type: 'dingding',
    binding_mode: 'plugin',
    stage: 'verify',
    description: '研发群',
    config: {
      client_id: 'client-1',
      client_secret: 'secret-1',
      enable_streaming_cards: true,
      card_template_id: 'tpl-1',
      card_template_key: 'content',
      dm_policy: 'open',
      allowlist: ['*'],
      reply_to_message: true,
      aix_enable: true,
      include_sender_name: true,
      robot_code: '',
    },
  }, 'owner-1');
});

test('渠道列表兼容 OpenAPI 直接数组并保留完整配置', async () => {
  const listChannels = botEditorController.listChannels as jest.Mock;
  listChannels.mockResolvedValue({
    data: [
      {
        id: 1,
        type: 'dingding',
        binding_mode: 'bcn_gateway',
        status: 'active',
        description: '研发群',
        stage: 'online',
        created_at: '2026-09-03T10:00:00Z',
        config: {
          client_id: 'client-1',
          has_client_secret: true,
          dm_policy: 'disabled',
          allowlist: ['1001'],
          reply_to_message: false,
          aix_enable: false,
          include_sender_name: false,
          robot_code: 'robot-1',
          group_chat_scope: 'conversation_shared',
          outbound_visibility: 'lead_only',
          forward_sender_identity: true,
        },
      },
    ],
  });

  await expect(botAdvancedConfigService.listChannels('bot-1')).resolves.toEqual([
    expect.objectContaining({
      id: 1,
      bindingMode: 'bcn_gateway',
      dmPolicy: 'disabled',
      allowlist: ['1001'],
      replyToMessage: false,
      aixEnable: false,
      includeSenderName: false,
      createdAt: '2026-09-03T10:00:00Z',
      robotCode: 'robot-1',
      groupChatScope: 'conversation_shared',
      outboundVisibility: 'lead_only',
      forwardSenderIdentity: true,
    }),
  ]);
});

test('创建 BCN 渠道时只提交 BCN 模式允许的配置', async () => {
  const createChannel = botEditorController.createChannel as jest.Mock;
  createChannel.mockResolvedValue({ data: {} });

  await botAdvancedConfigService.createChannel('bot-1', {
    bindingMode: 'bcn_gateway',
    description: '协作群',
    clientId: 'client-2',
    clientSecret: 'secret-2',
    robotCode: 'robot-2',
    enableStreamingCards: false,
    cardTemplateId: '',
    cardTemplateKey: '',
    dmPolicy: 'open',
    allowlist: ['*'],
    replyToMessage: true,
    aixEnable: true,
    includeSenderName: true,
    groupChatScope: 'per_sender',
    outboundVisibility: 'full_transcript',
    forwardSenderIdentity: true,
  }, 'owner-1');

  expect(createChannel).toHaveBeenCalledWith('bot-1', {
    type: 'dingding',
    binding_mode: 'bcn_gateway',
    description: '协作群',
    config: {
      client_id: 'client-2',
      client_secret: 'secret-2',
      robot_code: 'robot-2',
      enable_streaming_cards: false,
      card_template_id: null,
      group_chat_scope: 'per_sender',
      outbound_visibility: 'full_transcript',
      forward_sender_identity: true,
    },
  }, 'owner-1');
});
