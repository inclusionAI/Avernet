import { resolveBotRuntime } from '@/adapters/bot-runtime/resolveBotRuntime';
import { listCollaboratingBots, type CollaboratingBotDto } from '@/services/backendApi/bots/collaboratingBotController';
import { resolveUserId, type ChatBotView } from './botSessionService';
import type { DomainResult } from './identityService';

const PAGE_SIZE = 100;

function toTeamBot(dto: CollaboratingBotDto): ChatBotView {
  const realBotId = dto.bot_id.trim();
  const ownerId = dto.entity_id?.trim() || undefined;
  const botId = ownerId ? `${realBotId}:${ownerId}` : realBotId;
  const runtime = resolveBotRuntime({ engine: dto.engine, botType: dto.bot_type, botId });
  return {
    botId,
    realBotId,
    ownerId,
    isTeamBot: true,
    displayName: dto.bot_name?.trim() || realBotId,
    engine: dto.engine,
    botType: dto.bot_type,
    online: dto.status === 'ACTIVE' || dto.status === 'online',
    reachability: 'reachable',
    chatable: Boolean(realBotId && ownerId),
    isAgentCodingBot: runtime.isAgentCodingBot,
  };
}

export const teamBotConversationService = {
  /** 取全目录后供本地搜索；不向该端点发送 keyword/owner 等不支持的参数。 */
  async listBots(userId: string, signal?: AbortSignal): Promise<DomainResult<ChatBotView[]>> {
    try {
      const requester = resolveUserId(userId);
      if (!requester) throw new Error('登录用户不可用');
      const bots = new Map<string, ChatBotView>();
      for (let page = 1; ; page += 1) {
        if (signal?.aborted) throw new Error('请求已取消');
        const response = await listCollaboratingBots({ user_id: requester, page, page_size: PAGE_SIZE }, signal);
        const data = response.data;
        if (
          response.code !== 200000 ||
          !data ||
          !Array.isArray(data.items) ||
          !Number.isFinite(data.total) ||
          data.total < 0
        ) {
          throw new Error(response.message || '团队 Bot 接口返回格式异常');
        }
        for (const dto of data.items) {
          if (typeof dto.bot_id !== 'string' || !dto.bot_id.trim()) continue;
          const bot = toTeamBot(dto);
          if (!bots.has(bot.botId)) bots.set(bot.botId, bot);
        }
        if (!data.items.length || page * PAGE_SIZE >= data.total) break;
      }
      return { ok: true, data: [...bots.values()] };
    } catch {
      return {
        ok: false,
        error: { code: 'TEAM_BOTS_LOAD_FAILED', friendlyMessage: '加载团队 Bot 失败，请稍后重试。', canRetry: true },
      };
    }
  },
};
