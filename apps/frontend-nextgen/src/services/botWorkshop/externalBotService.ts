import { listMyBots, type CollaborationBotDto } from '@/services/backendApi/collaboration/collaborationBotController';
import { isEnvelopeSuccessAnyDialect } from '@/services/backendApi/types';
import { mapBotDto } from './botMapper';
import type { BotDomain } from './types';

export interface ExternalBotDomain {
  id: string;
  name: string;
  description?: string;
  avatarUrl?: string;
  status?: string;
  reachability?: string;
  engine?: string;
}

interface ExternalBotListOptions {
  pageSize?: number;
  signal?: AbortSignal;
}

function requestMine(offset: number, limit: number, signal?: AbortSignal) {
  const params = { kind: 'bot' as const, offset, limit };
  return signal ? listMyBots(params, signal) : listMyBots(params);
}

async function loadCollaborationBots(pageSize: number, signal?: AbortSignal): Promise<CollaborationBotDto[]> {
  const items: CollaborationBotDto[] = [];
  for (let offset = 0; ; offset += pageSize) {
    const response = await requestMine(offset, pageSize, signal);
    if (!isEnvelopeSuccessAnyDialect(response)) throw new Error(response.message || '外部 Bot 列表加载失败');
    const pageItems = response.data?.items ?? [];
    items.push(...pageItems);
    const total = response.data?.total;
    if (!pageItems.length || total === undefined || items.length >= total || pageItems.length < pageSize) return items;
  }
}

function mapExternalBot(dto: CollaborationBotDto): ExternalBotDomain {
  return {
    id: dto.bot_id,
    name: dto.name?.trim() || dto.bot_id,
    description: dto.descriptor?.summary,
    avatarUrl: dto.avatar_url,
    status: dto.status,
    reachability: dto.reachability,
    engine: dto.engine,
  };
}

export function externalBotToGeneralConfig(bot: ExternalBotDomain): BotDomain {
  return mapBotDto(
    {
      bot_id: bot.id,
      bot_name: bot.name,
      bot_desc: bot.description,
      avatar_url: bot.avatarUrl,
      engine: bot.engine,
      status: bot.status,
      bot_type: 'personal',
    },
    bot.id,
  ).item;
}

export const externalBotService = {
  async list(options: ExternalBotListOptions = {}): Promise<ExternalBotDomain[]> {
    const pageSize = options.pageSize ?? 100;
    const collaborationBots = await loadCollaborationBots(pageSize, options.signal);
    // 外部 Bot 使用 bot_ 前缀，不以当前 TC Bot 目录的可见范围推断类型。
    return collaborationBots.filter((bot) => bot.bot_id.startsWith('bot_')).map(mapExternalBot);
  },
};
