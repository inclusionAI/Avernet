import { getWorkerConfig, postFuse, type FuseRequestBody } from '@/services/backendApi/bcsfuse/bcsfuseController';
import type { DomainError, DomainResult } from './identityService';

function toDomainError(code: string, friendlyMessage: string): DomainError {
  return { code, friendlyMessage, canRetry: false };
}

export interface FusionBotInfo {
  botUuid: string;
  name: string;
  avatar?: string;
  fusionEnable: boolean;
}

export interface FuseResult {
  summary: string;
  success: boolean;
  error?: string;
}

export const bcsfuseService = {
  async getFusionEnable(workerId: string): Promise<DomainResult<boolean>> {
    try {
      const resp = await getWorkerConfig(workerId);
      return { ok: true, data: resp.fusion_enable === true };
    } catch {
      return { ok: false, error: toDomainError('BCSFUSE_CONFIG_FAILED', '获取画像公开配置失败') };
    }
  },

  async getFusionBots(
    participants: Array<{ actorId: string; kind: string; name: string; avatarUrl?: string }>,
  ): Promise<DomainResult<FusionBotInfo[]>> {
    const botParticipants = participants.filter((p) => p.kind === 'bot');
    if (botParticipants.length === 0) return { ok: true, data: [] };
    try {
      const results = await Promise.allSettled(
        botParticipants.map(async (bot) => {
          try {
            const resp = await getWorkerConfig(bot.actorId);
            return {
              botUuid: bot.actorId,
              name: bot.name,
              avatar: bot.avatarUrl,
              fusionEnable: resp.fusion_enable === true,
            };
          } catch {
            return { botUuid: bot.actorId, name: bot.name, avatar: bot.avatarUrl, fusionEnable: false };
          }
        }),
      );
      const bots: FusionBotInfo[] = results
        .filter((r) => r.status === 'fulfilled')
        .map((r) => (r as PromiseFulfilledResult<FusionBotInfo>).value);
      return { ok: true, data: bots };
    } catch {
      return {
        ok: true,
        data: botParticipants.map((bot) => ({
          botUuid: bot.actorId,
          name: bot.name,
          avatar: bot.avatarUrl,
          fusionEnable: false,
        })),
      };
    }
  },

  async postFuse(groupId: string, body: FuseRequestBody): Promise<DomainResult<FuseResult>> {
    try {
      const resp = await postFuse(groupId, body);
      if (resp?.recommendation?.summary) {
        return { ok: true, data: { summary: resp.recommendation.summary, success: true } };
      }
      return {
        ok: true,
        data: { summary: '', success: false, error: resp?.errors?.join('；') || resp?.error || '问答失败，请重试' },
      };
    } catch {
      return { ok: false, error: toDomainError('BCSFUSE_FUSE_FAILED', '网络异常，请重试') };
    }
  },
};
