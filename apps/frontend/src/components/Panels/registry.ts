import { registerPanelContent } from '@aix-chat/ui';
import type { PanelPlugin } from './types';

const _registry: PanelPlugin[] = [];

/**
 * registerPanel
 *
 * oc-web 的副屏插件注册入口。
 * 同时写入 SDK 的全局内容路由表（registerPanelContent），
 * 并在本地 registry 保留元信息，供未来按 botId 过滤等扩展使用。
 */
export function registerPanel(plugin: PanelPlugin): void {
  if (_registry.some((p) => p.type === plugin.type)) {
    console.warn(`[Panels] type "${plugin.type}" 已注册，跳过重复注册`);
    return;
  }
  _registry.push(plugin);
  registerPanelContent(plugin.type, plugin.component);
}

/**
 * getRegisteredPanels
 *
 * 返回所有已注册的 panel 元信息列表（只读）。
 */
export function getRegisteredPanels(): readonly PanelPlugin[] {
  return _registry;
}

/**
 * getPanelTypesForBot
 *
 * 根据 botId 返回该 bot 支持的 panel 类型列表。
 * 当前返回全部已注册类型；未来可按 bot 能力配置过滤。
 */
// eslint-disable-next-line @typescript-eslint/no-unused-vars
export function getPanelTypesForBot(_botId: string): string[] {
  return _registry.map((p) => p.type);
}
