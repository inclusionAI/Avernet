import { backendRequest } from './httpClient';
export interface Bot {
  bot_id: string;
  bot_name?: string;
  owner_name?: string;
  owner_id?: string;
  ext?: Record<string, unknown>;
  [key: string]: unknown;
}
export const DOMAIN_BOTS_PAGE_SIZE = 200;
export async function searchDomainBots(params: { page: number; page_size: number }) {
  return backendRequest<{ success?: boolean; data?: { items?: Bot[] } }>('/api/bots/search/domain-bots', {
    method: 'GET',
    params,
    operation: 'search-domain-bots',
    target: 'legacy-agentclaw',
  });
}
