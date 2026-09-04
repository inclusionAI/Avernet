// BCS 副屏资源清单（manifest）已从 ocb /bcnproxy 直连 BCN 迁至 TeamClaw Gateway
// /api/v1/collaboration/manifest（信封响应）。本文件迁移自 ocb `BcnController.ts`（getManifest）
// + `BotRenderController.ts`（listBotRenderScreens）；Bot render screens 已从 ocb
// /api/bot-render-screens 迁至 openapi /openapi/v1/bots/{bot_id}/render-screens（复用
// Bot 工坊 botEditorController.listRenderScreens 同路径、同 user_id 注入）。
import { botEditorController } from '@/services/backendApi/bots/botEditorController';
import { backendRequest } from '@/services/backendApi/httpClient';
import type { BackendApiEnvelope, BackendUnknownRecord } from '@/services/backendApi/types';

// === BCS 前端资源清单（manifest） ===

/** BCS 前端资源包：副屏 CDN 库映射条目（manifest 返回的 CDN 库，含 bcsPanel 等）。 */
export interface BcsManifestBundle {
  /** 库名（如 'asfui'），对应 <AixUI component="asfui.X"> 的 libraryName 段。 */
  name: string;
  /** CDN URL（UMD bundle）。 */
  url: string;
}

/** BCS 前端资源清单响应（GET manifest）。 */
export interface BcsManifest {
  schema_version: number;
  env: string;
  /** CDN 库映射条目；manifest 返回的 CDN 库（含 bcsPanel，由 UmdPanel 加载远程 UMD 渲染）。 */
  bundles: BcsManifestBundle[];
}

// === Bot 副屏 CDN 配置（Bot render screens） ===

/** Bot 在副屏配置页配的 CDN 库记录。 */
export interface BotRenderScreen extends BackendUnknownRecord {
  /** 记录 ID。 */
  id: number;
  /** 归属 Bot ID。 */
  bot_id: string;
  /** 库名（如 'asfui'）。 */
  name: string;
  /** CDN URL（UMD bundle）。 */
  cdn_url: string;
}

// === Endpoint（已迁至 TeamClaw Gateway /api/v1/collaboration/**） ===
//
// manifest 已从 ocb /bcnproxy 直连 BCN 迁到 teamclaw gateway：
//   GET /api/v1/collaboration/manifest → 信封 {code,message,data:BcsManifest}
// gateway 响应统一包在 data 里，getManifest 解包后返回 BcsManifest。
// Bot render screens 走 openapi /openapi/v1/bots/{bot_id}/render-screens（见
// listBotRenderScreens，经 botEditorController），不再单独占用 endpoint 常量。
export const BCS_MANIFEST_ENDPOINTS = {
  /** GET → 信封 {code,message,data:BcsManifest}。协作群场景的前端资源清单，不依赖单 bot。 */
  manifest: '/api/v1/collaboration/manifest',
} as const;

/**
 * BCS 状态机副屏（bcsPanel.StateMachineRunView）取数的 gateway 同源反代基址。
 *
 * 远程 CDN UMD（@alipay/bcn-panel-asset）与本地 fallback 副屏均按
 * `resolveBaseUrl(props) = props.apiBaseUrl || props.baseUrl || props.data?.apiBaseUrl || props.data?.baseUrl || 默认`
 * 解析取数基址，且远程 UMD 的默认值为 `/bcnproxy`。部署态 Tern 同源反代表无 `/bcnproxy` 条目，
 * 相对 `/bcnproxy/...` 会落到前端站本身被 CORB 拦截，故 teamclaw 在 CDN 加载层（UmdPanel）对
 * BCS 状态机 panel 注入本基址，使远程 UMD 命中 `props.apiBaseUrl` 走 `/api/v1/collaboration/**`
 * （与 manifest 同源反代一致）。调用方/后端已显式提供 apiBaseUrl/baseUrl 时不覆盖。
 */
export const BCS_STATE_MACHINE_API_BASE_URL = '/api/v1/collaboration';

/**
 * 拉 BCS 前端资源清单（GET /api/v1/collaboration/manifest）。
 * gateway 响应包在 {code,message,data} 信封里，这里解包取 data；data 缺失时回退空清单。
 */
export async function getManifest(): Promise<BcsManifest> {
  const res = await backendRequest<BackendApiEnvelope<BcsManifest>>(BCS_MANIFEST_ENDPOINTS.manifest, {
    method: 'GET',
    retryOnTransient: true,
  });
  return res.data ?? { schema_version: 0, env: '', bundles: [] };
}

/**
 * 查询 Bot 副屏 CDN 库配置（GET /openapi/v1/bots/{bot_id}/render-screens）。
 * 复用 openapi 新接口（与 Bot 工坊 botEditorController.listRenderScreens 同路径、
 * 同 user_id 注入），返回挂载副屏 CDN 库映射所需的 {name,cdn_url} 记录；失败抛错。
 * botId 形如 `default:146836`，接口要求纯 bot 名，故剥离 `:工号` 后缀。
 * 旧 ocb /api/bot-render-screens?bot_id= 路由在 teamclaw 网关不存在（404），已废弃。
 */
export async function listBotRenderScreens(botId: string): Promise<BotRenderScreen[]> {
  const separatorIndex = botId.indexOf(':');
  const pureBotId = separatorIndex >= 0 ? botId.slice(0, separatorIndex) : botId;
  const ownerId = separatorIndex >= 0 ? botId.slice(separatorIndex + 1) : undefined;
  const res = await botEditorController.listRenderScreens(pureBotId, ownerId);
  // RenderScreenDto 缺 bot_id，按需补齐为 BotRenderScreen（CDN 映射实际只用 name+cdn_url）
  return (res.data?.items ?? []).map((item) => ({
    id: item.id,
    bot_id: pureBotId,
    name: item.name,
    cdn_url: item.cdn_url,
  })) as BotRenderScreen[];
}
