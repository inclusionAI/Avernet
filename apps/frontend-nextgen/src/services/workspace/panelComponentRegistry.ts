/**
 * 应用副屏组件注册表（app-local）。
 *
 * SDK `@tc-chat/ui` 的 `registerPanelContent(type, Component)` 注册供 live `<AixUI type="panel"
 * component="type">` 经 resolveAixUi/openBusinessPanel 渲染。但 SDK 不暴露按 type 反查组件的公共
 * 访问器——PanelAppend 需要按 block.component 派发到已注册组件，故本表在 SDK 注册之外同步保留
 * 一份 app-local Map，供 PanelAppend 解析。
 *
 * 用法：应用自有的副屏组件一律通过本模块的 `registerPanelComponent` 注册（双写 SDK + 本表）；
 * 第三方/SDK 内部注册（如 `registerUmdPanelHandler`/`registerTaskPanel` 内部直接调
 * `registerPanelContent`）不经过本表，PanelAppend 对其回落 content/text 渲染。
 */
import type { ComponentType } from 'react';
import type { PanelContentProps } from '@tc-chat/ui/es/SidePanelContent';
import { registerPanelContent } from '@tc-chat/ui/es/SidePanelContent';

const APP_PANEL_COMPONENTS = new Map<string, ComponentType<PanelContentProps>>();

/**
 * 双写注册：SDK 注册（供 live `<AixUI>` 解析 + openBusinessPanel 渲染）+ app-local Map
 * （供 PanelAppend 按 block.component 派发）。与 SDK 行为一致——同 type 后注册覆盖先注册。
 */
export function registerPanelComponent(type: string, Component: ComponentType<PanelContentProps>): void {
  registerPanelContent(type, Component);
  APP_PANEL_COMPONENTS.set(type, Component);
}

/** 按 component 名解析 app-local 注册的副屏组件；未注册返回 undefined（PanelAppend 回落 text/markdown）。 */
export function resolvePanelComponent(type: string): ComponentType<PanelContentProps> | undefined {
  return APP_PANEL_COMPONENTS.get(type);
}
