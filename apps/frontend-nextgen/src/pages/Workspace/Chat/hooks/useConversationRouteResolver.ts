// URL 入向装配子 Hook(从 useConversationSelection 拆出,文件大小阈值):
// - URL → Store(展开 Bot/好友分组、记忆归属/范围、单选会话);
// - section 缺失且目录可匹配时补齐;目录无匹配保持 null,待 hydration 完成后重解析
//   (旧/外部链接原样透传);
// - 完整目录到达后按管理→团队→好友归类;不能拿旧 friend 缓存搭配 team 请求身份。
import type { ConversationBotView, ConversationRouteState } from '@/domain/conversation';
import { isManagedConversationSection } from '@/domain/conversation/types';
import { useConversationStore, type ConversationState } from '@/stores/conversationStore';
import { useCallback, useEffect, useRef } from 'react';

export function useConversationRouteResolver(options: {
  managedBots: ConversationBotView[];
  friendBots: ConversationBotView[];
  /** 目录 hydration 是否完成(完成后才按目录重解析未决 section)。 */
  hydrated: boolean;
  /** 目录 key(managed 形如 section=botId / friend 为纯 botId),变化触发重解析。 */
  managedBotIdKey: string;
  friendBotIdKey: string;
  /** 当前选中是否仍在目录(丢失时按完整目录归类重解析)。 */
  selectedInDirectory: boolean;
  /** 当前选中状态的 URL 投影(重解析补 section 用)。 */
  selection: ConversationRouteState;
  /** 会话 Store 订阅态(丢失类重解析依赖 selectedBotId 变化触发)。 */
  store: ConversationState;
}): (route: ConversationRouteState) => void {
  const { managedBots, friendBots, hydrated, managedBotIdKey, friendBotIdKey, selectedInDirectory, selection, store } =
    options;
  const selectedBotId = store.selectedBotId;

  // 入向:URL → Store。section 缺失且目录可匹配时补齐;目录无匹配保持 null,
  // 待 hydration 完成后由下方 effect 重解析(旧/外部链接原样透传)。
  const lastRouteRef = useRef<ConversationRouteState | null>(null);
  const onRouteSelection = useCallback(
    (route: ConversationRouteState) => {
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
      const managedOrigin = isManagedConversationSection(section) ? route.origin ?? 'mine' : 'mine';
      const scope =
        isManagedConversationSection(section) && managedOrigin === 'mine'
          ? route.scope ?? storeNow.effectiveScopeByManagedBotId[route.botId] ?? 'all'
          : 'all';
      const friendUserId =
        isManagedConversationSection(section) && managedOrigin === 'others' ? route.friendUserId ?? null : null;
      // origin=others 缺 friend 的非法 URL:sessionId 无法归属到某好友,从选中态剔除,
      // 避免投影回写 origin=others&session=… 残缺组合(AC-13 路由语法)。
      const sessionId =
        isManagedConversationSection(section) && managedOrigin === 'others' && !friendUserId
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
      storeNow.selectConversation({
        botId: route.botId,
        section,
        origin: managedOrigin,
        scope,
        friendUserId,
        sessionId,
      });
    },
    [friendBotIdKey, managedBotIdKey],
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
    const section =
      managedBots.find((view) => view.bot.botId === selectedBotId)?.section ??
      friendBots.find((view) => view.bot.botId === selectedBotId)?.section;
    if (section) onRouteSelection({ ...selection, section });
  }, [hydrated, selectedInDirectory, selectedBotId, managedBots, friendBots, onRouteSelection, selection]);

  return onRouteSelection;
}