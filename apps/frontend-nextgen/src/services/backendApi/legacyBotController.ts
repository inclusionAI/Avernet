import { backendRequest } from './httpClient';
export interface Bot {
  bot_id: string;
  bot_name?: string;
  owner_name?: string;
  owner_id?: string;
  ext?: Record<string, unknown>;
  [key: string]: unknown;
}
// 关联架构 Bot 全量查询：不再透传 page / page_size，由后端直接返回全部候选。
export async function searchDomainBots() {
  return backendRequest<{ success?: boolean; data?: { items?: Bot[] } }>('/api/bots/search/domain-bots', {
    method: 'GET',
    operation: 'search-domain-bots',
    target: 'legacy-agentclaw',
  });
}
