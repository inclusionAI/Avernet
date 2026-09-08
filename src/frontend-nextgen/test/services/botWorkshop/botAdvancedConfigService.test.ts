import { botEditorController } from '@/services/backendApi/bots/botEditorController';
import { botAdvancedConfigService } from '@/services/botWorkshop/botAdvancedConfigService';

jest.mock('@/services/backendApi/bots/botEditorController', () => ({
  botEditorController: { createChannel: jest.fn(), updateChannel: jest.fn(), listChannels: jest.fn() },
}));

test('创建钉钉渠道时透传流式卡片配置', async () => {
  const createChannel = botEditorController.createChannel as jest.Mock;
  createChannel.mockResolvedValue({ data: {} });

  await botAdvancedConfigService.createChannel('bot-1', {
    bindingMode: 'plugin',
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
  });

  expect(createChannel).toHaveBeenCalledWith('bot-1', {
    type: 'dingding',
    binding_mode: 'plugin',
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
  });
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
  });

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
    },
  });
});
