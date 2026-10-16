// 对话菜单多实例服务 Bot 的实例范围订阅 Hook:把 capability 的同步快照转成响应式状态。
// Open Core 能力为 null → 返回 null(BotItem 不渲染实例行,行为不变);internal overlay 注入后生效。
// capability 在应用启动后冻结(构建期恒定),hook 调用顺序稳定。
import {
  getCapabilities,
  type ConversationInstanceScopeBot,
  type ConversationInstanceScopeCapability,
  type ConversationInstanceScopeState,
} from '@/capabilities';
import { useConversationStore } from '@/stores/conversationStore';
import { useEffect, useState } from 'react';

export interface ConversationInstanceScopeView {
  capability: ConversationInstanceScopeCapability;
  /** 订阅到的最新状态快照。 */
  state: ConversationInstanceScopeState;
}

export function useConversationInstanceScope(bot: ConversationInstanceScopeBot): ConversationInstanceScopeView | null {
  const capability = getCapabilities().getConversationInstanceScope().value;
  const [state, setState] = useState<ConversationInstanceScopeState | null>(() => capability?.getState(bot) ?? null);
  useEffect(() => {
    if (!capability) return;
    const sync = (): void => setState(capability.getState(bot));
    sync();
    return capability.subscribe(bot.botId, sync);
  }, [capability, bot]);
  if (!capability || !state) return null;
  return { capability, state };
}

/** Bot 行实例范围聚合视图:行渲染判定/检索输入禁用/圆点口径一次取齐(能力 null 时为全空态)。 */
export interface ConversationInstanceRowView {
  scope: ConversationInstanceScopeView | null;
  row: ConversationInstanceScopeView | null;
  inputDisabled: boolean;
  marked: boolean;
}

export function useConversationInstanceRow(
  bot: ConversationInstanceScopeBot,
  searchActive: boolean,
): ConversationInstanceRowView {
  const scope = useConversationInstanceScope(bot);
  const instanceSelection = useConversationStore((state) => state.instanceSelectionByBotId[bot.botId]);
  // 服务 Bot 清单未解析/失败时渲染实例行(加载/失败态)并禁用检索输入(AC-09);
  // 多实例时外部清除已提交条件保留输入草稿(AC-07)。
  const row = scope?.state.service && (scope.state.multiInstance || !scope.state.ready) ? scope : null;
  const inputDisabled = Boolean(scope && scope.state.service && !scope.state.ready);
  // 检索入口圆点口径:检索词生效,或多实例服务 Bot 已选中实例(含自动选中第一台)——按实例过滤同样
  // 构成「会话列表处于筛选状态」,入口圆点与图标激活色亮起。单实例/非服务 Bot 无实例过滤不参与;
  // 实例清单未就绪(加载中/失败)无范围不计。操作区钉住与检索输入显隐仍仅随检索词,收起态行为不变。
  const filterActive = Boolean(scope?.state.service && scope.state.multiInstance && instanceSelection);
  return { scope, row, inputDisabled, marked: searchActive || filterActive };
}