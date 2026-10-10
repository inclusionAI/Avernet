import { listBots } from '@/services/backendApi/bots/botController';
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

function baseBotId(id: string): string {
  const separator = id.lastIndexOf(':');
  return separator > 0 ? id.slice(0, separator) : id;
}

function requestMine(offset: number, limit: number, signal?: AbortSignal) {
  const params = { kind: 'bot' as const, offset, limit };
  return signal ? listMyBots(params, signal) : listMyBots(params);
}

function requestOwned(page: number, pageSize: number, signal?: AbortSignal) {
  const params = { page, page_size: pageSize };
  return signal ? listBots(params, signal) : listBots(params);
}

async function loadOwnedIds(pageSize: number, signal?: AbortSignal): Promise<Set<string>> {
  const ids = new Set<string>();
  let loaded = 0;
  for (let page = 1; ; page += 1) {
    const response = await requestOwned(page, pageSize, signal);
    if (!isEnvelopeSuccessAnyDialect(response)) throw new Error(response.message || 'TC Bot 列表加载失败');
    const items = response.data?.items ?? [];
    loaded += items.length;
    items.forEach((item) => {
      ids.add(item.bot_id);
      ids.add(baseBotId(item.bot_id));
    });
    const total = response.data?.total;
    if (!items.length || total === undefined || loaded >= total || items.length < pageSize) return ids;
  }
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
    const [ownedIds, collaborationBots] = await Promise.all([
      loadOwnedIds(pageSize, options.signal),
      loadCollaborationBots(pageSize, options.signal),
    ]);
    return collaborationBots
      .filter((bot) => !ownedIds.has(bot.bot_id) && !ownedIds.has(baseBotId(bot.bot_id)))
      .map(mapExternalBot);
  },
};
