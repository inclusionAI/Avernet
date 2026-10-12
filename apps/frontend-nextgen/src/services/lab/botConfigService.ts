import type { BrowseSubscription, UpsertSubscriptionInput } from '@/domain/lab/types';
import type { BotConfigGateway } from './botConfigGateway';

/** 备注最大字符数（对齐后端 MAX_BROWSE_SUBSCRIPTION_NOTE_LENGTH=512）。 */
export const BROWSE_NOTE_MAX_LENGTH = 512;

/**
 * Bot 逛社区配置 Service。镜像 CommunityService：领域校验在前端兜底，Gateway 仅做传输。
 * - enable / updateNote 复用同一 upsert（B 方案触发模式由后端固定 openclaw；前端不发 mode，note 随提交更新）。
 * - disable 调 DELETE，后端卸下定时触发并幂等。
 */
export class BotConfigService {
  constructor(private readonly gateway: BotConfigGateway) {}

  listSubscriptions(ownerUserId: string, signal?: AbortSignal) {
    return this.gateway.listSubscriptions(ownerUserId, signal);
  }

  async enableSubscription(botId: string, ownerUserId: string, note?: string | null, signal?: AbortSignal) {
    if (!botId) throw new Error('缺少 Bot');
    if (!ownerUserId) throw new Error('缺少当前用户身份');
    const input: UpsertSubscriptionInput = {
      botId,
      ownerUserId,
      note: this.normalizeNote(note),
    };
    return this.gateway.upsertSubscription(input, signal);
  }

  /** 备注更新沿用同一 upsert（仅在已订阅时有意义）。 */
  updateNote(botId: string, ownerUserId: string, note: string | null, signal?: AbortSignal) {
    return this.enableSubscription(botId, ownerUserId, note, signal);
  }

  async disableSubscription(botId: string, signal?: AbortSignal) {
    if (!botId) throw new Error('缺少 Bot');
    await this.gateway.deleteSubscription(botId, signal);
  }

  private normalizeNote(note?: string | null): string | null {
    const trimmed = (note ?? '').trim();
    return trimmed ? trimmed.slice(0, BROWSE_NOTE_MAX_LENGTH) : null;
  }
}

export type { BrowseSubscription };
