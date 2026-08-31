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
    return (response.data?.items ?? []).map((item) => ({
      id: item.id,
      type: item.type,
      description: item.description,
      status: item.status,
      clientId: item.config.client_id,
      hasSecret: item.config.has_client_secret,
    }));
  },
  createChannel: (botId: string, input: BotChannelInput) =>
    botEditorController.createChannel(botId, {
      type: 'dingding',
      description: input.description || null,
      config: { client_id: input.clientId, client_secret: input.clientSecret },
    }),
  setChannelStatus: (botId: string, channel: BotChannel) =>
    botEditorController.setChannelStatus(botId, channel.id, channel.status === 'active' ? 'inactive' : 'active'),
  deleteChannel: (botId: string, channelId: number) => botEditorController.deleteChannel(botId, channelId),
};
