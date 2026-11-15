// URL 入向装配子 Hook(从 useConversationSelection 拆出,文件大小阈值):
// - URL → Store(展开 Bot/好友分组、记忆归属/范围、单选会话);
// - section 缺失时按目录补齐;旧版纯 bot_id 在完整目录唯一匹配后转换为规范地址;
// - 完整目录到达后按管理→团队→好友归类;不能拿旧 friend 缓存搭配 team 请求身份。
import {
  extractCodingSessionCreatorUserId,
  resolveCodingSessionOrigin,
  type ConversationBotView,
  type ConversationRouteState,
} from '@/domain/conversation';
import { resolveConversationRouteTarget } from '@/domain/conversation/resolveRouteTarget';
import { isManagedConversationSection } from '@/domain/conversation/types';
import { useConversationStore, type ConversationState } from '@/stores/conversationStore';
import { useCallback, useEffect, useRef } from 'react';

export function useConversationRouteResolver(options: {
  userId?: string | null;
  managedBots: ConversationBotView[];
  friendBots: ConversationBotView[];
  /** 目录 hydration 是否完成(完成后才按目录重解析未决 section)。 */
  hydrated: boolean;
  /** 目录 key(managed 形如 section=botId / friend 为纯 botId),变化触发重解析。 */
  managedBotIdKey: string;
  /** managed/team 目录中的 Coding Bot key(section=botId)，仅内容变化时重建解析器。 */
  managedCodingBotIdKey: string;
  friendBotIdKey: string;
  /** 当前选中是否仍在目录(丢失时按完整目录归类重解析)。 */
  selectedInDirectory: boolean;
  /** 当前选中状态的 URL 投影(重解析补 section 用)。 */
  selection: ConversationRouteState;
  /** 会话 Store 订阅态(丢失类重解析依赖 selectedBotId 变化触发)。 */
  store: ConversationState;
}): (route: ConversationRouteState) => void {
  const {
    userId,
    managedBots,
    friendBots,
    hydrated,
    managedBotIdKey,
    managedCodingBotIdKey,
    friendBotIdKey,
    selectedInDirectory,
    selection,
    store,
  } = options;
  const selectedBotId = store.selectedBotId;

  // 入向:URL → Store。section 缺失且目录可匹配时补齐;目录无匹配保持 null,
  // 待 hydration 完成后由下方 effect 重解析(旧/外部链接原样透传)。
  const lastRouteRef = useRef<ConversationRouteState | null>(null);
  const onRouteSelection = useCallback(
    (inputRoute: ConversationRouteState) => {
      const target = resolveConversationRouteTarget(inputRoute, [...managedBots, ...friendBots], hydrated);
      const route = target ? { ...inputRoute, botId: target.bot.botId, section: target.section } : inputRoute;
      // 完全空的解析(无参数,如同路由直进 /workspace/chat)不动选中:
      // 挂载清空会误清除页内已有选中;URL 投影对空选中本就产出空串。
      if (!route.botId && !route.section && !route.sessionId) return;
      lastRouteRef.current = route;
      const storeNow = useConversationStore.getState();
      if (!route.botId) {
        storeNow.selectConversation({
          botId: null,
          section: null,
          origin: 'mine',
          scope: 'all',
          friendUserId: null,
          sessionId: null,
        });
        return;
      }
      const section =
        route.section ??
        (managedBotIdKey.split('|').includes(`managed=${route.botId}`)
          ? 'managed'
          : managedBotIdKey.split('|').includes(`team=${route.botId}`)
          ? 'team'
          : friendBotIdKey.split('|').includes(route.botId)
          ? 'friend'
          : null);
      const selectedIsCodingBot =
        isManagedConversationSection(section) && managedCodingBotIdKey.split('|').includes(`${section}=${route.botId}`);
      const codingOrigin =
        selectedIsCodingBot && route.sessionId ? resolveCodingSessionOrigin(route.sessionId, userId) : null;
      // Coding Session 的创建者由 sessionId 权威表达；深链缺失或携带了错误 origin 时，
      // 与 AIXCoding 路由模式一样先切到正确来源，再解析会话。
      const managedOrigin = isManagedConversationSection(section) ? codingOrigin ?? route.origin ?? 'mine' : 'mine';
      const scope =
        isManagedConversationSection(section) && managedOrigin === 'mine'
          ? route.scope ?? storeNow.effectiveScopeByManagedBotId[route.botId] ?? 'all'
          : 'all';
      const friendUserId =
        isManagedConversationSection(section) && managedOrigin === 'others'
          ? route.friendUserId ??
            (selectedIsCodingBot ? extractCodingSessionCreatorUserId(route.sessionId ?? '') : null)
          : null;
      // 普通 Bot 的 others 仍要求好友分组身份；Coding Bot 可直接由 sessionId 解析创建者。
      const sessionId =
        isManagedConversationSection(section) && managedOrigin === 'others' && !friendUserId && !selectedIsCodingBot
          ? null
          : route.sessionId ?? null;
      storeNow.setExpandedBot(route.botId, true);
      if (isManagedConversationSection(section)) {
        if ((storeNow.originByManagedBotId[route.botId] ?? 'mine') !== managedOrigin) {
          storeNow.setManagedBotOrigin(route.botId, managedOrigin);
        }
        if ((storeNow.effectiveScopeByManagedBotId[route.botId] ?? 'all') !== scope) {
          storeNow.setManagedBotScope(route.botId, scope);
        }
        if (friendUserId) storeNow.setExpandedFriend(route.botId, friendUserId, true);
      }
      const nextSelection = {
        botId: route.botId,
        section,
        origin: managedOrigin,
        scope,
        friendUserId,
        sessionId,
      };
      if (
        storeNow.selectedBotId === nextSelection.botId &&
        storeNow.selectedSection === section &&
        storeNow.selectedOrigin === managedOrigin &&
        storeNow.selectedScope === scope &&
        storeNow.selectedFriendUserId === friendUserId &&
        storeNow.selectedSessionId === sessionId
      ) return; // Re-parsing an unresolved or echoed route must not trigger another store update.
      storeNow.selectConversation(nextSelection);
    },
    [friendBotIdKey, managedBotIdKey, managedCodingBotIdKey, userId, managedBots, friendBots, hydrated],
  );

  // 目录 hydration 完成后,对仍未解析 section 的待定路由重解析一次。
  useEffect(() => {
    if (!hydrated || !lastRouteRef.current) return;
    const storeNow = useConversationStore.getState();
    if (!storeNow.selectedBotId || storeNow.selectedSection) return;
    onRouteSelection(lastRouteRef.current);
  }, [friendBotIdKey, hydrated, managedBotIdKey, onRouteSelection]);

  // 完整目录到达后按管理→团队→好友归类；不能拿旧 friend 缓存搭配 team 请求身份。
  useEffect(() => {
    if (!hydrated || selectedInDirectory || !selectedBotId) return;
    const target = resolveConversationRouteTarget(selection, [...managedBots, ...friendBots], true);
    if (target && target.bot.botId !== selectedBotId) {
      onRouteSelection({ ...selection, botId: target.bot.botId, section: target.section });
      return;
    }
    const section =
      managedBots.find((view) => view.bot.botId === selectedBotId)?.section ??
      friendBots.find((view) => view.bot.botId === selectedBotId)?.section;
    if (section) onRouteSelection({ ...selection, section });
  }, [hydrated, selectedInDirectory, selectedBotId, managedBots, friendBots, onRouteSelection, selection]);

  return onRouteSelection;
}
