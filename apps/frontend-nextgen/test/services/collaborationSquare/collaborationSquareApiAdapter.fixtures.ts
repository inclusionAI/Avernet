// Shared transport doubles and per-test reset for collaboration square adapter suites.
import * as bbsTaskController from '@/services/backendApi/collaboration/bbsTaskController';
import * as collaborationBotController from '@/services/backendApi/collaboration/collaborationBotController';
import * as friendConnectionController from '@/services/backendApi/collaboration/collaborationFriendConnectionController';
import * as groupController from '@/services/backendApi/collaboration/collaborationGroupController';
import * as publicBotController from '@/services/backendApi/collaboration/publicBotController';
import * as sessionController from '@/services/backendApi/collaboration/sessionController';
import * as botSessionController from '@/services/backendApi/bots/privateBotSessionController';
import * as backendBotController from '@/services/backendApi/bots/botController';
import * as bcsCandidatesModule from '@/services/bcs/botCandidatesController';
import { useErrorNotifyStore } from '@/stores/errorNotifyStore';
import type { BotMetadataDto, BotMetadataQueryBody } from '@/services/backendApi/bots/botController';
import type { PublicBot } from '@/domain/collaborationSquare/types';
import { afterEach, beforeEach, jest } from '@jest/globals';

// 注意：本文件使用 @jest/globals 名义 import（既有约定），与 babel 的 jest.mock 工厂提升
// 不兼容（工厂执行早于命名绑定初始化），故对依赖模块统一采用属性 spy 实现同态 mock。
export const mockedListBcsBotCandidates = jest.spyOn(bcsCandidatesModule, 'listBcsBotCandidates');
export const mockedSearchBcsCandidates = jest.spyOn(bcsCandidatesModule, 'searchBcsCandidates');
export const mockedListBotMetadata = jest.spyOn(backendBotController, 'listBotMetadata');
export const mockedBackendCatalogSearch = jest.spyOn(publicBotController, 'searchPublicBots');
export const mockedBackendCatalogDiscovery = jest.spyOn(publicBotController, 'discoverPublicBots');
export const mockedGetPublicBotProfile = jest.spyOn(publicBotController, 'getPublicBotProfile');
export const mockedListPublicGroups = jest.spyOn(groupController, 'listPublicGroups');
export const mockedQueryCollaborationBots = jest.spyOn(collaborationBotController, 'queryCollaborationBots');
export const mockedListFriendConnections = jest.spyOn(friendConnectionController, 'listFriendConnections');
export const mockedListFriendConnectionRequests = jest.spyOn(friendConnectionController, 'listFriendConnectionRequests');
export const mockedCreateFriendConnectionRequest = jest.spyOn(friendConnectionController, 'createFriendConnectionRequest');
export const mockedCreateBotSession = jest.spyOn(botSessionController, 'createBotSession');
export const mockedCreateGroupSession = jest.spyOn(sessionController, 'createSession');
export const mockedListBbsTasks = jest.spyOn(bbsTaskController, 'listBbsTasks');

export const humanContext = { actorId: 'human_900003', userId: '900003' };

/** 测试内显式开关 Backend 可选增强（defaultCapabilities 读取 window.AVERNET_RUNTIME_CONFIG）。 */
export function setPublicBotBackendEnabled(value: boolean) {
  Object.defineProperty(window, 'AVERNET_RUNTIME_CONFIG', {
    value: { PUBLIC_BOT_BACKEND_ENABLED: value },
    configurable: true,
  });
}

interface CandidateFixtureOptions {
  bot_id?: string;
  name?: string;
  status?: 'online' | 'hidden';
  is_friend?: boolean;
}

/** 以 BCS 契约形状构造候选项（对齐 domain-models.yaml BotCandidate/PhysicalBot 消费字段）。 */
export function candidateAt(rawIndex: number, options: CandidateFixtureOptions = {}) {
  return {
    bot: {
      bot_id: options.bot_id ?? `assistant:owner-${rawIndex}`,
      name: options.name ?? `候选Bot${rawIndex}`,
      avatar_url: null,
      created_by: '9',
      descriptor: { summary: `第 ${rawIndex} 条候选摘要` },
      visibility: 'public' as const,
      user_visibility: 'public' as const,
      status: options.status ?? ('online' as const),
    },
    is_friend: options.is_friend ?? false,
  };
}

/** 以 BCS 原始 offset 提供候选页（upstream 固定 20 一批）。 */
export function mockBcsCandidatesPage(
  total: number,
  options: { candidateAt?: (rawIndex: number) => ReturnType<typeof candidateAt> } = {},
) {
  const buildCandidate = options.candidateAt ?? candidateAt;
  mockedListBcsBotCandidates.mockImplementation(
    async (_actorId: string, query: { offset: number; limit: number }) => {
    const count = Math.max(0, Math.min(query.limit, total - query.offset));
    return {
      code: 20000 as const,
      message: 'ok',
      data: {
        items: Array.from({ length: count }, (_, index) => buildCandidate(query.offset + index)),
        total,
        offset: query.offset,
        limit: query.limit,
      },
    };
  });
}

/** 模拟 metadata/queries：serviceFalseKeys（`bot_id:owner_id` 形态）标记为 service+false。 */
export function mockBotMetadata(serviceFalseKeys: Set<string>) {
  mockedListBotMetadata.mockImplementation(async (_params: unknown, body: BotMetadataQueryBody) => {
    const items: BotMetadataDto[] = body.bots.map((pair) => {
      const key = `${pair.bot_id}:${pair.owner_id}`;
      const serviceFalse = serviceFalseKeys.has(key);
      return {
        bcn_bot_id: key,
        bot_id: pair.bot_id,
        owner_id: pair.owner_id,
        bot_name: key,
        bot_type: serviceFalse ? 'service' : 'personal',
        online_available: serviceFalse ? false : null,
      };
    });
    return { code: 200000 as const, message: 'ok', data: { items, total: items.length, offset: 0, limit: items.length } };
  });
}

export const emptyFriendEnvelope = { code: 20000 as const, message: 'ok', data: { items: [], total: 0 } };
/** 列表路径的默认申请侧供给：无 pending 申请。 */
export function mockEmptySentPendingRequests() {
  mockedListFriendConnectionRequests.mockResolvedValue(emptyFriendEnvelope);
}

/** 智能搜索关系回填的空 BCS 页（connections + pending 均为空页）。 */
export function emptyFriendRequestsForDiscovery() {
  mockedListFriendConnections.mockResolvedValue(emptyFriendEnvelope);
  mockedListFriendConnectionRequests.mockResolvedValue(emptyFriendEnvelope);
}

export function idsOf(bots: PublicBot[]) {
  return bots.map((bot) => bot.id);
}

beforeEach(() => {
  mockedListBcsBotCandidates.mockReset();
  mockedSearchBcsCandidates.mockReset();
  mockedListBotMetadata.mockReset();
  mockedBackendCatalogSearch.mockReset();
  mockedBackendCatalogDiscovery.mockReset();
  mockedGetPublicBotProfile.mockReset();
  mockedListPublicGroups.mockReset();
  mockedQueryCollaborationBots.mockReset();
  mockedListFriendConnections.mockReset();
  mockedListFriendConnectionRequests.mockReset();
  mockedCreateFriendConnectionRequest.mockReset();
  mockedCreateBotSession.mockReset();
  mockedCreateGroupSession.mockReset();
  mockedListBbsTasks.mockReset();
  Reflect.deleteProperty(window, 'AVERNET_RUNTIME_CONFIG');
  useErrorNotifyStore.getState().reset();
});

afterEach(() => {
  Reflect.deleteProperty(window, 'AVERNET_RUNTIME_CONFIG');
});
