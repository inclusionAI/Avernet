// @sdd: BCS 状态机副屏取数基址注入纯逻辑（无 SDK/React 依赖，便于单测）。
//
// 背景：远程 CDN UMD（@alipay/bcn-panel-asset v1.2.0，bench `src/StateMachineRunView`）
// 经 manifest 加载（bcsPanel 库），其 resolveBaseUrl 契约：
//   props.apiBaseUrl || props.baseUrl || props.data?.apiBaseUrl || props.data?.baseUrl || 默认
// 远程 UMD 的默认值为 `/bcnproxy`，故 CDN 加载时需注入 `/api/v1/collaboration`。部署态 Tern
// 同源反代表（config/internal/runtime/config.ts tern.proxy）只登记了 `/api/v1/collaboration`
// （→ teamclawgw-{pre,prod}），无 `/bcnproxy` 条目——故经 CDN 加载时若不注入 apiBaseUrl，
// 远程 UMD 会回退其 `/bcnproxy` 默认，相对路径落到前端站本身被 CORB 拦截。
//
// 解法：在 CDN 加载层（UmdPanel）对 BCS 状态机 panel 注入 gateway 同源反代基址
// `/api/v1/collaboration`，让远程 UMD 命中 `props.apiBaseUrl`。已显式提供 apiBaseUrl/baseUrl
// 时不干预（尊重调用方/后端显式配置）。非 BCS 状态机 panel 不注入（其它 UMD panel 有各自后端约定）。
import { BCS_STATE_MACHINE_API_BASE_URL } from './bcsManifestController';

/** BCS 状态机副屏所属 UMD 库名（vite lib.name='bcsPanel'）。 */
const BCS_PANEL_LIBRARY = 'bcsPanel';
/** BCS 状态机副屏导出末段名（含点路径前缀 bcsPanel.StateMachineRunView 或裸 StateMachineRunView）。 */
const STATE_MACHINE_EXPORT_NAME = 'StateMachineRunView';

/**
 * 判定副屏是否为 BCS 状态机 panel。命中条件（任一）：
 * - UMD 库名为 `bcsPanel`（BCS 状态机是当前 bcsPanel 库唯一导出，库维度命中即可）。
 * - 导出点路径末段为 `StateMachineRunView`（兼容声明式 / 命令式两种 entry 形态）。
 */
export function isBcsStateMachinePanel(libraryName: string | undefined, exportName: string | undefined): boolean {
  if (libraryName === BCS_PANEL_LIBRARY) return true;
  if (!exportName) return false;
  return (
    exportName === STATE_MACHINE_EXPORT_NAME ||
    exportName === `${BCS_PANEL_LIBRARY}.${STATE_MACHINE_EXPORT_NAME}` ||
    exportName.endsWith(`.${STATE_MACHINE_EXPORT_NAME}`)
  );
}

/**
 * 计算需注入到远程 UMD 组件 `props.apiBaseUrl` 的取数基址。
 *
 * 返回值语义：UmdPanel 仅在返回非 undefined 时写入 `flattened.apiBaseUrl`。
 * - 已显式提供 apiBaseUrl：返回 undefined，不覆盖（flattened 已含原值，resolveBaseUrl 命中它）。
 * - 已显式提供 baseUrl（无 apiBaseUrl）：返回 undefined，避免注入 apiBaseUrl 以更高优先级盖掉
 *   调用方意图的 baseUrl。
 * - 均未提供且为 BCS 状态机 panel：返回 `/api/v1/collaboration`。
 * - 均未提供且非 BCS 状态机 panel：返回 undefined，不干预其它 panel 的默认取数约定。
 */
export function resolveBcsStateMachineApiBaseUrl(
  libraryName: string | undefined,
  exportName: string | undefined,
  providedApiBaseUrl: unknown,
  providedBaseUrl: unknown,
): string | undefined {
  if (providedApiBaseUrl || providedBaseUrl) return undefined;
  if (!isBcsStateMachinePanel(libraryName, exportName)) return undefined;
  return BCS_STATE_MACHINE_API_BASE_URL;
}
