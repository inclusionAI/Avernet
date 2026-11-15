// URL 深链会话冷启动兜底：选中态只记录 bot/sessionId，不负责按 sessionId 精确加载会话。
// 当列表页没有覆盖该会话时，这里主动拉取详情并写入选择快照，避免深链页面由于
// 列表缓存不可见而无法解析会话。
import { resolveCodingSessionOrigin, type ConversationBotView } from '@/domain/conversation';
import { isManagedConversationSection } from '@/domain/conversation/types';
import { botContainerService } from '@/services/botWorkshop/botContainerService';
import {
  buildConversationSessionRequestRange,
  resolveUserId,
  supportsConversationInstanceScope,
  type BotChatSessionView,
  type ChatBotView,
} from '@/services/workspace/botSessionService';
import { botSessionService } from '@/services/workspace/botSessionService';
import type { DomainResult } from '@/services/workspace/identityService';
import { useConversationStore } from '@/stores/conversationStore';
import { selectedSnapshotMatches } from '@/stores/conversationStoreSnapshot';
import { useEffect, useMemo, useRef } from 'react';

export interface UseConversationDeepLinkSessionHydrationOptions {
  userId: string | null;
  managedBots: ConversationBotView[];
  friendBots: ConversationBotView[];
  hydrated: boolean;
}

type SessionDetailResult = DomainResult<BotChatSessionView>;

function canProbeByInstance(bot: ChatBotView): boolean {
  return supportsConversationInstanceScope(bot) && !bot.isAgentCodingBot;
}

async function resolveSessionFromContainer(
  bot: ChatBotView,
  userId: string,
  sessionId: string,
  preferredDeviceUuid?: string,
): Promise<{ result: SessionDetailResult; deviceUuid?: string }> {
  if (!canProbeByInstance(bot)) {
    return { result: await botSessionService.getSessionDetail(bot, userId, sessionId) };
  }

  const tried = new Set<string>();
  if (preferredDeviceUuid) {
    tried.add(preferredDeviceUuid);
    const range = buildConversationSessionRequestRange(bot, preferredDeviceUuid);
    const result = await botSessionService.getSessionDetail(bot, userId, sessionId, range);
    if (result.ok && result.data?.sessionId === sessionId && result.data.botId === bot.botId) {
      return { result, deviceUuid: preferredDeviceUuid };
    }
  }

  try {
    const snapshot = await botContainerService.list(
      bot.realBotId,
      resolveUserId(userId),
      bot.ownerId,
    );
    for (const instance of snapshot.instances) {
      if (!instance.id || tried.has(instance.id)) continue;
      tried.add(instance.id);
      const range = buildConversationSessionRequestRange(bot, instance.id);
      const result = await botSessionService.getSessionDetail(bot, userId, sessionId, range);
      if (result.ok && result.data?.sessionId === sessionId && result.data.botId === bot.botId) {
        return { result, deviceUuid: instance.id };
      }
    }
  } catch {
    // fall through to no-deviceUuid query below on multi-instance lookup failure
  }

  return {
    result: { ok: false, error: { code: 'NOT_FOUND', friendlyMessage: '目标会话未找到', canRetry: true } },
  };
}

export function useConversationDeepLinkSessionHydration(options: UseConversationDeepLinkSessionHydrationOptions): void {
  const { userId, managedBots, friendBots, hydrated } = options;

  const selectedBotId = useConversationStore((state) => state.selectedBotId);
  const selectedSection = useConversationStore((state) => state.selectedSection);
  const selectedOrigin = useConversationStore((state) => state.selectedOrigin);
  const selectedScope = useConversationStore((state) => state.selectedScope);
  const selectedFriendUserId = useConversationStore((state) => state.selectedFriendUserId);
  const selectedSessionId = useConversationStore((state) => state.selectedSessionId);
  const selectedSnapshot = useConversationStore((state) => state.selectedSessionSnapshot);
  const sessionList = useConversationStore((state) => {
    const botId = state.selectedBotId;
    if (!botId) return null;
    if (state.selectedSection === 'friend') return state.friendBotSessionsByBotId[botId] ?? null;
    if (!isManagedConversationSection(state.selectedSection)) return null;
    const cache = state.sessionsByBotId[botId];
    if (cache?.origin === state.selectedOrigin && cache.scope === state.selectedScope) return cache.sessions;
    if (state.selectedOrigin === 'others') {
      return cache?.friendGroups[state.selectedFriendUserId ?? '']?.sessions ?? null;
    }
    return null;
  });

  const botById = useMemo(() => {
    const map = new Map<string, ChatBotView>();
    for (const view of managedBots) map.set(view.bot.botId, view.bot);
    for (const view of friendBots) map.set(view.bot.botId, view.bot);
    return map;
  }, [managedBots, friendBots]);

  const completedRef = useRef<Set<string>>(new Set());
  const inflightRef = useRef<Set<string>>(new Set());

  // 普通 Bot 的 others 仍走好友分组只读链路；Coding Bot 的来源由 sessionId 判定，
  // mine/others 都允许精确拉详情并交给 Embedded Stage。
  useEffect(() => {
    if (!hydrated || !userId || !selectedBotId || !selectedSection || !selectedSessionId) return;
    if (!isManagedConversationSection(selectedSection) && selectedSection !== 'friend') return;

    const bot = botById.get(selectedBotId);
    if (!bot || (selectedOrigin === 'others' && !bot.isAgentCodingBot)) return;

    const identity = {
      botId: selectedBotId,
      section: selectedSection,
      origin: selectedOrigin,
      scope: selectedScope,
      friendUserId: selectedOrigin === 'others' ? selectedFriendUserId : null,
      sessionId: selectedSessionId,
    };
    if (selectedSnapshotMatches(selectedSnapshot, identity)) return;
    if (sessionList?.items.some((item) => item.sessionId === selectedSessionId)) return;

    const inferredOrigin = bot.isAgentCodingBot ? resolveCodingSessionOrigin(selectedSessionId, userId) : 'mine';
    if (inferredOrigin && inferredOrigin !== selectedOrigin) return;

    const requestKey = [
      userId,
      selectedBotId,
      selectedSection,
      selectedOrigin,
      selectedScope,
      selectedFriendUserId ?? '',
      selectedSessionId,
    ].join(':');
    if (completedRef.current.has(requestKey) || inflightRef.current.has(requestKey)) return;

    inflightRef.current.add(requestKey);
    const run = async () => {
      const preferredDeviceUuid = useConversationStore
        .getState()
        .instanceSelectionByBotId[selectedBotId];
      const { result, deviceUuid } = await resolveSessionFromContainer(
        bot,
        userId,
        selectedSessionId,
        preferredDeviceUuid,
      );
      const store = useConversationStore.getState();
      const currentIdentity = {
        botId: selectedBotId,
        section: selectedSection,
        origin: selectedOrigin,
        scope: selectedScope,
        friendUserId: selectedOrigin === 'others' ? selectedFriendUserId : null,
        sessionId: selectedSessionId,
      };

      if (
        !result.ok ||
        !result.data ||
        result.data.sessionId !== selectedSessionId ||
        result.data.botId !== selectedBotId
      ) {
        return;
      }

      const detailOrigin = bot.isAgentCodingBot ? resolveCodingSessionOrigin(result.data.sessionId, userId) : 'mine';
      if (detailOrigin && detailOrigin !== selectedOrigin) return;

      if (
        store.selectedBotId !== selectedBotId ||
        store.selectedSection !== selectedSection ||
        store.selectedOrigin !== selectedOrigin ||
        store.selectedScope !== selectedScope ||
        store.selectedFriendUserId !== currentIdentity.friendUserId ||
        store.selectedSessionId !== selectedSessionId
      ) {
        return;
      }

      if (selectedSnapshotMatches(store.selectedSessionSnapshot, currentIdentity)) return;

      if (deviceUuid && deviceUuid !== store.instanceSelectionByBotId[selectedBotId]) {
        store.setBotInstanceSelection(selectedBotId, deviceUuid);
      }

      store.setSelectedSessionSnapshot({ ...currentIdentity, session: result.data, friend: null });
    };

    void run().finally(() => {
      completedRef.current.add(requestKey);
      inflightRef.current.delete(requestKey);
    });
  }, [
    userId,
    selectedBotId,
    selectedSection,
    selectedOrigin,
    selectedScope,
    selectedFriendUserId,
    selectedSessionId,
    selectedSnapshot,
    botById,
    hydrated,
    sessionList,
  ]);
}

export { resolveSessionFromContainer };
