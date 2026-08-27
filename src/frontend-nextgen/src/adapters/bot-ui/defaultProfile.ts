import type { BotRuntime } from '@/adapters/bot-runtime';
import type { BotUiProfile } from './types';

export function resolveDefaultBotUiProfile(runtime: BotRuntime): BotUiProfile {
  return {
    displayName: runtime.engine === 'unknown' ? '未知引擎 Bot' : `${runtime.engine} Bot`,
    badgeLabel: runtime.isDefaultBot ? '系统默认' : runtime.templateType,
    iconKey: runtime.engine,
    // UI 动作入口由画像统一给出，页面只负责渲染动作列表。
    visibleActions: runtime.isDefaultBot ? ['chat', 'view'] : ['chat', 'view', 'configure', 'publish'],
  };
}
