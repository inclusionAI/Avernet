import { getBotAvatar, saveBotAvatar } from '@/services/backendApi/bots/botAvatarController';
export const botAvatarService = {
  async get(id: string) {
    const response = await getBotAvatar(id);
    if (!response.data) throw new Error('头像数据不可用');
    return response.data.avatar_url;
  },
  async save(id: string, value: string) {
    const response = await saveBotAvatar(id, value);
    if (!response.data) throw new Error('头像保存未返回结果');
    return response.data.avatar_url;
  },
};
