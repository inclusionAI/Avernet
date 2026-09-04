import { botEditorController } from '@/services/backendApi/bots/botEditorController';
import { botAdvancedConfigService } from '@/services/botWorkshop/botAdvancedConfigService';

jest.mock('@/services/backendApi/bots/botEditorController', () => ({
  botEditorController: { createChannel: jest.fn(), updateChannel: jest.fn(), listChannels: jest.fn() },
}));

test('创建钉钉渠道时透传流式卡片配置', async () => {
  const createChannel = botEditorController.createChannel as jest.Mock;
  createChannel.mockResolvedValue({ data: {} });

  await botAdvancedConfigService.createChannel('bot-1', {
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
        },
      },
    ],
  });

  await expect(botAdvancedConfigService.listChannels('bot-1')).resolves.toEqual([
    expect.objectContaining({
      id: 1,
      dmPolicy: 'disabled',
      allowlist: ['1001'],
      replyToMessage: false,
      aixEnable: false,
      includeSenderName: false,
      createdAt: '2026-09-03T10:00:00Z',
    }),
  ]);
});
