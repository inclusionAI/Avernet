/**
 * 副屏 CDN 库映射注入器（方式②数据桥，迁移自 ocb registerBotCdnPanels.ts）。
 *
 * 职责：把 BCS manifest（协作群）与 Bot render screens（单 bot）拉到的 CDN 库映射，
 * 合并后写入引擎运行时扩展点 `window.aixLibraryCdnMap`（Map）与 `window.aixGetLibraryCdn`（fn），
 * 供引擎 `resolveBusinessEntry`/`getLibraryCdnFromGlobal` 把 <AixUI component="lib.X"> 的 lib 名
 * 解析成 CDN URL，交 `loadCDNComponent` 加载 UMD。
 *
 * 迁移说明（相对 ocb）：
 * - 数据桥职责：本注入器只负责"喂数据给引擎扩展点"（window.aixLibraryCdnMap / aixGetLibraryCdn），
 *   把 BCS manifest / Bot render screens 拉到的库名 → CDN URL 映射交给引擎 resolveBusinessEntry 解析。
 * - UmdPanel 包装（方式②组件加载层）：引擎 resolveBusinessEntry 的 CDN 分支把 entry 算成 componentName
 *   （丢了 libraryName 前缀），致 loadCDNComponent 找不到 window[libraryName][component]。
 *   teamclaw 在 services/bcs/UmdPanel.tsx 注册 registerPanelContent('umd', UmdPanel) 接管 type='umd' tab，
 *   按 params._componentKey 重建完整点路径 exportName 修复该缺陷（对齐 ocb UmdPanel"包装 + 自控 entry"链路）。
 * - bcsPanel 走方式②CDN 优先：manifest 返回 bcsPanel 的 CDN URL 即写入 aixLibraryCdnMap，
 *   引擎 resolveBusinessEntry CDN 分支命中 → 加载远程 UMD；远程 UMD 调 /bcnproxy/state-machine-runs/**
 *   （dev 由 config.local.ts 的 /bcnproxy 代理改写到 gateway；部署态需 CDN UMD 自身或调用方
 *   经 params.apiBaseUrl 注入 /api/v1/collaboration，属 CDN 侧事项）。
 * - bcsPanel 经 manifest 返回 CDN URL → 写入 aixLibraryCdnMap → 引擎 CDN 分支加载远程 UMD；
   远程 UMD 的取数基址由 UmdPanel 经 bcsPanelBaseUrl 注入 /api/v1/collaboration（同源反代信封解包）。
 *
 * 合并优先级（与 ocb 一致）：Bot 配置 > BCS manifest 配置（同库名后者不覆盖前者）。
 *
 * 数据源：BCS manifest 经 teamclaw gateway /api/v1/collaboration/manifest（信封，getManifest 解包），
 * Bot render screens 走 openapi /openapi/v1/bots/{bot_id}/render-screens 接口。
 */
import {
  getManifest,
  listBotRenderScreens,
  type BcsManifestBundle,
  type BotRenderScreen,
} from './bcsManifestController';

/** 库名 → CDN URL 的运行时映射（内存态，同步到 window）。 */
const libraryCdnMap = new Map<string, string>();

/** BCS manifest 来源的 CDN 配置（低优先级）。 */
const manifestCdnMap = new Map<string, string>();

/** Bot render screens 来源的 CDN 配置，按 botId 分桶（高优先级）。 */
const botCdnMaps = new Map<string, Map<string, string>>();

/** 库级 CDN 配置条目（统一 manifest bundle 与 bot render screen 两种来源的异名收口）。 */
export interface LibraryCdnConfig {
  /** 库名。 */
  name?: string;
  /** CDN URL（manifest 用 url，bot render screen 用 cdn_url）。 */
  cdn_url?: string;
  url?: string;
}

/** window 上引擎扩展点的最小表述（供单元测试以 typeof window 守卫）。 */
interface AixLibraryCdnGlobal {
  aixLibraryCdnMap?: Map<string, string>;
  aixGetLibraryCdn?: (libraryName: string) => string | undefined;
}

function resolveCdnUrl(config: LibraryCdnConfig): string | undefined {
  return config.cdn_url || config.url;
}

/** 把来源配置数组收口成「库名 → CDN URL」Map，跳过缺字段条目。 */
function collectLibraryCdnConfigs(configs: LibraryCdnConfig[]): Map<string, string> {
  const cdnMap = new Map<string, string>();
  for (const config of configs) {
    const cdnUrl = resolveCdnUrl(config);
    if (!cdnUrl || !config.name) continue;
    cdnMap.set(config.name, cdnUrl);
  }
  return cdnMap;
}

/** 同步合并后的库映射到 window.aixLibraryCdnMap + window.aixGetLibraryCdn。仅浏览器环境。 */
export function syncToGlobalWindow(): void {
  if (typeof window === 'undefined') return;
  const w = window as unknown as AixLibraryCdnGlobal & typeof globalThis;
  if (!w.aixLibraryCdnMap) w.aixLibraryCdnMap = new Map<string, string>();
  w.aixLibraryCdnMap.clear();
  libraryCdnMap.forEach((cdnUrl, libraryName) => w.aixLibraryCdnMap!.set(libraryName, cdnUrl));
  w.aixGetLibraryCdn = (libraryName: string): string | undefined => libraryCdnMap.get(libraryName);
}

/** 重建合并库映射并同步到 window（bot 优先级 > manifest）。 */
function rebuildLibraryCdnMap(): void {
  libraryCdnMap.clear();
  manifestCdnMap.forEach((cdnUrl, libraryName) => libraryCdnMap.set(libraryName, cdnUrl));
  botCdnMaps.forEach((botCdnMap) => {
    botCdnMap.forEach((cdnUrl, libraryName) => libraryCdnMap.set(libraryName, cdnUrl));
  });
  syncToGlobalWindow();
}

/** 存储 BCS manifest 拉到的 CDN 配置（含 bcsPanel 等 CDN 库），触发重建同步。 */
export function storeManifestBundles(bundles: BcsManifestBundle[]): void {
  manifestCdnMap.clear();
  collectLibraryCdnConfigs(bundles).forEach((cdnUrl, libraryName) => {
    manifestCdnMap.set(libraryName, cdnUrl);
  });
  rebuildLibraryCdnMap();
}

/** 存储 Bot render screens 拉到的 CDN 配置（按 botId 分桶），触发重建同步。 */
export function storeCdnConfigs(screens: BotRenderScreen[]): void {
  const screensByBotId = new Map<string, BotRenderScreen[]>();
  for (const screen of screens) {
    const botId = screen.bot_id || '__unknown_bot__';
    const list = screensByBotId.get(botId) ?? [];
    list.push(screen);
    screensByBotId.set(botId, list);
  }
  screensByBotId.forEach((botScreens, botId) => {
    botCdnMaps.set(botId, collectLibraryCdnConfigs(botScreens));
  });
  rebuildLibraryCdnMap();
}

/** 存储指定 Bot 的 CDN 配置，供不同 API 数据源复用运行时注入能力。 */
export function storeBotCdnConfigs(botId: string, configs: LibraryCdnConfig[]): void {
  botCdnMaps.set(botId, collectLibraryCdnConfigs(configs));
  rebuildLibraryCdnMap();
}

/** 清空所有 CDN 配置（切换 Bot / 协作页卸载时调用）。 */
export function clearCdnConfig(): void {
  libraryCdnMap.clear();
  manifestCdnMap.clear();
  botCdnMaps.clear();
  if (typeof window !== 'undefined') {
    const w = window as unknown as AixLibraryCdnGlobal;
    w.aixLibraryCdnMap?.clear?.();
    syncToGlobalWindow();
  }
}

/** 清空 BCS manifest CDN 配置。 */
export function clearManifestCdnConfig(): void {
  manifestCdnMap.clear();
  rebuildLibraryCdnMap();
}

/** 清空 Bot CDN 配置：传 botId 只清该 Bot，不传全清。 */
export function clearBotCdnConfig(botId?: string): void {
  if (botId) botCdnMaps.delete(botId);
  else botCdnMaps.clear();
  rebuildLibraryCdnMap();
}

/** 取库名列表（调试/可渲染性判断）。 */
export function getLibraryNames(): string[] {
  return Array.from(libraryCdnMap.keys());
}

/** 取库的 CDN 配置。 */
export function getLibraryCdn(libraryName: string): string | undefined {
  return libraryCdnMap.get(libraryName);
}

/** 库是否已配置 CDN。 */
export function hasLibraryCdn(libraryName: string): boolean {
  return libraryCdnMap.has(libraryName);
}

/**
 * 查询并注册指定 Bot 的 CDN 库映射（单 bot 场景，对应 ocb queryAndRegisterBotCdnPanels）。
 *
 * 调用点：单聊按 botId 查询 Bot render screens。真实拉取经 openapi /openapi/v1/bots/{bot_id}/render-screens 接口。
 */
export async function queryAndRegisterBotLibraryCdn(botId: string): Promise<number> {
  if (!botId) return 0;
  try {
    const screens = await listBotRenderScreens(botId);
    if (screens.length === 0) {
      clearBotCdnConfig(botId);
      return 0;
    }
    storeCdnConfigs(screens);
    return libraryCdnMap.size;
  } catch {
    return 0;
  }
}

let manifestLoadPromise: Promise<number> | null = null;

/**
 * 查询并注册 BCS manifest 声明的 CDN 库映射（协作群场景，对应 ocb queryAndRegisterManifestCdnPanels）。
 *
 * manifest 来源于 BCS 后端，是协作页资源清单，不依赖单 bot；调用点：协作群/协作页打开副屏场景。
 * 请求去重：进行中的 manifest 拉取复用同一 Promise；失败也要清空旧 manifest CDN（不复用上次）。
 */
export function queryAndRegisterManifestLibraryCdn(): Promise<number> {
  if (manifestLoadPromise) return manifestLoadPromise;
  manifestLoadPromise = (async () => {
    try {
      clearManifestCdnConfig();
      const manifest = await getManifest();
      const bundles = manifest?.bundles ?? [];
      if (bundles.length === 0) return 0;
      storeManifestBundles(bundles);
      return libraryCdnMap.size;
    } catch {
      return 0;
    } finally {
      manifestLoadPromise = null;
    }
  })();
  return manifestLoadPromise;
}

/** 同步到 window（模块加载时执行一次，保证 window 扩展点就绪）。 */
syncToGlobalWindow();
