import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope, BackendUnknownRecord } from '../types';
import { userScopedParams } from './botController';
import type { BotSkillDto } from './botSkillController';

export const botCapabilityDetailController = {
  skill(botId: string, skillId: string, ownerId?: string) {
    return backendRequest<BackendApiEnvelope<BotSkillDto>>(
      `/openapi/v1/bots/${encodeURIComponent(botId)}/skills/${encodeURIComponent(skillId)}`,
      { params: userScopedParams({ owner_id: ownerId }) },
    );
  },
  skillContent(botId: string, skillId: string, ownerId?: string) {
    return backendRequest<BackendApiEnvelope<{ content: string }>>(
      `/openapi/v1/bots/${encodeURIComponent(botId)}/skills/${encodeURIComponent(skillId)}/content`,
      { params: userScopedParams({ owner_id: ownerId }) },
    );
  },
  mcp(serverCode: string) {
    return backendRequest<BackendApiEnvelope<BackendUnknownRecord>>(
      `/openapi/v1/bots/mcp/servers/${encodeURIComponent(serverCode)}`,
    );
  },
};
