import { getBotClassification } from '@/services/backendApi/bots/botClassificationController';
import { isEnvelopeSuccess } from '@/services/backendApi/types';
import { useErrorNotifyStore } from '@/stores/errorNotifyStore';

export async function queryIsServiceBot(actorId: string): Promise<boolean> {
  if (actorId.startsWith('human_')) return false;
  const separator = actorId.indexOf(':');
  const botId = separator === -1 ? actorId : actorId.slice(0, separator);
  const entityId = separator === -1 ? undefined : actorId.slice(separator + 1);
  if (!botId) throw new Error('Bot ID 不能为空');
  if (botId === 'default') return false;
  try {
    const response = await getBotClassification(botId, entityId);
    if (!isEnvelopeSuccess(response) || !response.data?.bot_type) {
      throw new Error(response?.message || 'Bot 类型查询失败');
    }
    return response.data.bot_type === 'service';
  } catch (error) {
    const failure = error as { status?: number; toastKey?: string };
    // BCS 中存在不受 Backend 管理的 Bot，404 是正常分类结果，不弹默认错误提示。
    if (failure.status === 404) {
      if (failure.toastKey) useErrorNotifyStore.getState().cancel(failure.toastKey);
      return false;
    }
    throw error;
  }
}
