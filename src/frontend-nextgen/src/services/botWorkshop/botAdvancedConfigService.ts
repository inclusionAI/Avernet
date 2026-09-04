import type { BotChannel, BotChannelInput, BotIdentityFile } from '@/domain/botAdvancedConfig';
import { botEditorController } from '@/services/backendApi/bots/botEditorController';

export const botAdvancedConfigService = {
  async listIdentityFiles(botId: string): Promise<BotIdentityFile[]> {
    const response = await botEditorController.listIdentityFiles(botId);
    return (response.data?.files ?? []).map((file) => ({ type: file.type, exists: file.exists }));
  },
  async getIdentityFile(botId: string, type: string): Promise<string> {
    const response = await botEditorController.getIdentityFile(botId, type);
    return response.data?.content ?? '';
  },
  saveIdentityFile: (botId: string, type: string, content: string) =>
    botEditorController.updateIdentityFile(botId, type, content),
  async listChannels(botId: string): Promise<BotChannel[]> {
    const response = await botEditorController.listChannels(botId);
    const items = Array.isArray(response.data) ? response.data : response.data?.items ?? [];
    return items.map((item) => ({
      id: item.id,
      type: item.type,
      description: item.description,
      status: item.status,
      clientId: item.config.client_id,
      hasSecret: item.config.has_client_secret,
      enableStreamingCards: Boolean(item.config.enable_streaming_cards),
      cardTemplateId: item.config.card_template_id ?? undefined,
      cardTemplateKey: item.config.card_template_key ?? undefined,
      dmPolicy: item.config.dm_policy === 'disabled' ? 'disabled' : 'open',
      allowlist: Array.isArray(item.config.allowlist) ? item.config.allowlist : ['*'],
      replyToMessage: item.config.reply_to_message !== false,
      aixEnable: item.config.aix_enable !== false,
      includeSenderName: item.config.include_sender_name !== false,
      createdAt: item.created_at ?? undefined,
      updatedAt: item.updated_at ?? undefined,
    }));
  },
  createChannel: (botId: string, input: BotChannelInput) =>
    botEditorController.createChannel(botId, {
      type: 'dingding',
      description: input.description || null,
      config: {
        client_id: input.clientId,
        client_secret: input.clientSecret,
        enable_streaming_cards: input.enableStreamingCards,
        card_template_id: input.cardTemplateId.trim() || null,
        card_template_key: input.cardTemplateKey.trim() || null,
        dm_policy: input.dmPolicy,
        allowlist: input.allowlist,
        reply_to_message: input.replyToMessage,
        aix_enable: input.aixEnable,
        include_sender_name: input.includeSenderName,
      },
    }),
  updateChannel: (botId: string, channelId: number, input: BotChannelInput) =>
    botEditorController.updateChannel(botId, channelId, {
      description: input.description || null,
      config: {
        client_id: input.clientId,
        client_secret: input.clientSecret.trim() || null,
        enable_streaming_cards: input.enableStreamingCards,
        card_template_id: input.cardTemplateId.trim() || null,
        card_template_key: input.cardTemplateKey.trim() || null,
        dm_policy: input.dmPolicy,
        allowlist: input.allowlist,
        reply_to_message: input.replyToMessage,
        aix_enable: input.aixEnable,
        include_sender_name: input.includeSenderName,
      },
    }),
  setChannelStatus: (botId: string, channel: BotChannel) =>
    botEditorController.setChannelStatus(botId, channel.id, channel.status === 'active' ? 'inactive' : 'active'),
  deleteChannel: (botId: string, channelId: number) => botEditorController.deleteChannel(botId, channelId),
};
