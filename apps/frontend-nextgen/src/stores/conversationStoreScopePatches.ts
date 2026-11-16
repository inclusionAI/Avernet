// Conversation Store 的管理 Bot 来源/范围切换 patch。拆出该文件既守住 Store
// 文件大小门禁，也让“归档浏览不清空右侧会话”的语义与普通筛选语义分开表达。
import type { ConversationOrigin, ConversationSessionScope } from '@/domain/conversation';
import { isManagedConversationSection } from '@/domain/conversation/types';
import {
  clearSessionKeyPatch,
  revisionPatch,
  selectedSnapshotScopePatch,
} from './conversationStoreHelpers';
import type { ConversationState } from './conversationStoreState';

export type ConversationScopePatch = ConversationState | Partial<ConversationState>;

export function managedBotOriginPatch(
  state: ConversationState,
  botId: string,
  origin: ConversationOrigin,
  options?: { supportsScopesOutsideMine?: boolean },
): ConversationScopePatch {
  if (state.originByManagedBotId[botId] === origin) return state;
  // effective 读取范围:Coding Bot 支持 mine/others/collab 三来源复用范围;
  // 普通 Bot 的 others 保持历史行为强制 all。
  const effectiveScope: ConversationSessionScope =
    origin === 'others' && options?.supportsScopesOutsideMine !== true
      ? 'all'
      : state.scopeByManagedBotId[botId] ?? 'all';
  const selectedHere = state.selectedBotId === botId && isManagedConversationSection(state.selectedSection);
  return {
    originByManagedBotId: { ...state.originByManagedBotId, [botId]: origin },
    effectiveScopeByManagedBotId: { ...state.effectiveScopeByManagedBotId, [botId]: effectiveScope },
    // 发起归属切换后该 Bot 的 Session 列表整体失效,清除其选中的 Session;
    // 所选 Bot 的选中态同步跟随筛选(AC-6/AC-7:主舞台与 URL 投影随切换一致)。
    ...(selectedHere
      ? {
          selectedOrigin: origin,
          selectedScope: effectiveScope,
          // 来源切换后好友只读选中不再成立,一并无效化(与 selectedSessionId 同语义)。
          selectedFriendUserId: null,
          selectedSessionId: null,
        }
      : {}),
    // 归属切换是实际过滤变化:revision 递增;切回 mine 且记忆范围为 favorite 时清空搜索。
    ...revisionPatch(state, botId),
    ...(effectiveScope === 'favorite' ? clearSessionKeyPatch(state, botId) : {}),
  };
}

export function managedBotScopePatch(
  state: ConversationState,
  botId: string,
  scope: ConversationSessionScope,
  options?: { preserveSelection?: boolean },
): ConversationScopePatch {
  // 生效范围实际变化才算一次过滤变化;缺省 all 的等值提交不递增 revision。
  const previousScope = state.effectiveScopeByManagedBotId[botId] ?? 'all';
  const effectiveChanged = previousScope !== scope;
  const selectedHere = state.selectedBotId === botId && isManagedConversationSection(state.selectedSection);
  // 显式要求保留选中会话(工程能力修正)与归档浏览性筛选都不得卸载已打开的主会话;
  // 从归档切回 all 同样保留,避免浏览一次归档导致右侧会话被关闭。
  const preserveSelection =
    options?.preserveSelection === true ||
    scope === 'archived' ||
    (previousScope === 'archived' && scope === 'all');
  return {
    scopeByManagedBotId: { ...state.scopeByManagedBotId, [botId]: scope },
    effectiveScopeByManagedBotId: { ...state.effectiveScopeByManagedBotId, [botId]: scope },
    // 所选管理 Bot 的选中范围同步跟随筛选(AC-7:URL 仍带上 scope/session)。
    ...(selectedHere ? { selectedScope: scope } : {}),
    ...(effectiveChanged
      ? {
          ...revisionPatch(state, botId),
          ...(!preserveSelection && selectedHere ? { selectedSessionId: null } : {}),
          ...(preserveSelection && selectedHere ? selectedSnapshotScopePatch(state, botId, scope) : {}),
          // 进入 favorite 清空已生效搜索;列表失效沿用既有 origin/scope 缓存比对,不额外删除。
          ...(scope === 'favorite' ? clearSessionKeyPatch(state, botId) : {}),
        }
      : {}),
  };
}
