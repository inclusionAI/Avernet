import { botEditorController } from '@/services/backendApi/bots/botEditorController';
import { botAdvancedConfigService } from '@/services/botWorkshop/botAdvancedConfigService';

jest.mock('@/services/backendApi/bots/botEditorController', () => ({
  botEditorController: { createChannel: jest.fn(), listChannels: jest.fn() },
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
    },
  });
});
