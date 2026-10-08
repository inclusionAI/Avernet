import type { BotLink, BotLinkInput } from '@/domain/botLinks';
import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope } from '../types';
const path = (botId: string) => `/openapi/v1/bots/${encodeURIComponent(botId)}/links`;
export const botLinkController = {
  list: (botId: string) => backendRequest<BackendApiEnvelope<BotLink[]>>(path(botId)),
  create: (botId: string, links: BotLinkInput[]) =>
    backendRequest<BackendApiEnvelope<BotLink[]>>(path(botId), { method: 'POST', data: { links } }),
  update: (botId: string, id: string, data: Partial<BotLinkInput>) =>
    backendRequest<BackendApiEnvelope<BotLink>>(`${path(botId)}/${encodeURIComponent(id)}`, { method: 'PUT', data }),
  remove: (botId: string, id: string) =>
    backendRequest(`${path(botId)}/${encodeURIComponent(id)}`, { method: 'DELETE' }),
};
