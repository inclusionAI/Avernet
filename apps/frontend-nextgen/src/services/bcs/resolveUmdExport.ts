// @sdd: UMD 副屏导出点路径重建（纯逻辑，无 SDK 依赖，便于单测）。
//
// 修复引擎 `@tc-chat/ui` resolveBusinessEntry 的缺陷：CDN 分支把 entry 算成
// componentName（如 `StateMachineRunView`）丢了 `libraryName.` 前缀，致 loadCDNComponent
// 在 window 顶层找不到导出。本函数按 resolveBusinessEntry 注入的 finalPayload 字段
// 重建完整点路径 exportName（如 `bcsPanel.StateMachineRunView`）。

/** UMD 数据平铺时需剔除的保留键，避免覆盖回调/框架字段（对齐引擎 CDNMount RESERVED_KEYS）。 */
export const RESERVED_DATA_KEYS = ['onAction', 'onInteraction', 'eventEmitter', 'tab', 'params', 'payload'];

/**
 * 从副屏 tab params（= resolveBusinessEntry finalPayload 或命令式 openTab params）重建 UMD 导出点路径。
 *
 * 字段来源：
 * - `_componentKey`：resolveBusinessEntry 注入的完整 `lib.Component`（声明式 `<AixUI component="lib.Comp">` 主路径）
 * - `_libraryName`：库名段
 * - `entry`：导出末段或完整点路径
 *
 * 优先级：
 * 1. `_componentKey`（声明式主路径，完整点路径）
 * 2. `_libraryName` + `entry` 拼接（命令式传了库名 + 末段，但无完整 key；entry===libraryName 时不拼接避免 `lib.lib`）
 * 3. `entry` 本身（命令式已传完整点路径，或顶层导出名）
 * 4. `_libraryName`（库默认导出）
 * 5. 'default'
 */
export function resolveExportName(params: Record<string, unknown>): string {
  const componentKey = params._componentKey as string | undefined;
  const libraryName = params._libraryName as string | undefined;
  const entry = params.entry as string | undefined;
  if (componentKey) return componentKey;
  if (libraryName && entry && entry !== libraryName) return `${libraryName}.${entry}`;
  return entry || libraryName || 'default';
}
