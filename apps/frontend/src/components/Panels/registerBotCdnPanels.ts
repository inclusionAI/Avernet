/**
 * CDN Panel 注册
 *
 * 支持从 Bot 配置或 BCS manifest 获取 CDN 配置，
 * 并为每个 CDN 组件注册到全局 Panel 路由表。
 *
 * 组件库模式：
 * - CDN 配置只包含库名（如 'asfui'）和 CDN URL
 * - 用户在 AixPanel.component 中写完整组件名（如 'asfui.MetaTitle'）
 * - 系统自动解析库名，找到对应 CDN，加载指定组件
 */
import {
  getManifest,
  type BcsManifestBundle,
} from '@/services/backend-api/BcnController';
import type { BotRenderScreen } from '@/services/backend-api/BotRenderController';
import { listBotRenderScreens } from '@/services/backend-api/BotRenderController';

/**
 * 合并后的 CDN 库配置存储（内存）
 * key: libraryName (如 'asfui')
 * value: cdnUrl
 *
 * 合并优先级：Bot 配置 > BCS manifest 配置。
 */
const libraryCdnMap = new Map<string, string>();

/**
 * BCS manifest CDN 配置存储。
 */
const manifestCdnMap = new Map<string, string>();

/**
 * Bot CDN 配置存储。
 * key: botId
 * value: 当前 bot 的 CDN 库配置
 */
const botCdnMaps = new Map<string, Map<string, string>>();

/**
 * 已注册的组件类型集合（防止重复注册）
 */
const registeredTypes = new Set<string>();

let manifestLoadPromise: Promise<number> | null = null;

interface LibraryCdnConfig {
  name?: string;
  cdn_url?: string;
  url?: string;
}

const UNKNOWN_BOT_CDN_SCOPE = '__unknown_bot__';
const BCN_PROXY_PREFIX = '/bcnproxy';

function isExternalUrl(url: string): boolean {
  return /^[a-z][a-z0-9+.-]*:/i.test(url) || url.startsWith('//');
}

function resolveManifestBundleUrl(url: string): string {
  const value = url.trim();
  if (
    !value ||
    isExternalUrl(value) ||
    value.startsWith(`${BCN_PROXY_PREFIX}/`)
  ) {
    return value;
  }
  if (value.startsWith('/')) {
    return `${BCN_PROXY_PREFIX}${value}`;
  }
  return `${BCN_PROXY_PREFIX}/${value}`;
}

/**
 * 将 CDN 配置同步到全局 window 对象
 * 供 SDK 的 AixPanelTrigger 使用
 */
function syncToGlobalWindow(): void {
  if (typeof window === 'undefined') return;

  // 创建/更新全局 Map
  if (!(window as any).aixLibraryCdnMap) {
    (window as any).aixLibraryCdnMap = new Map<string, string>();
  }

  (window as any).aixLibraryCdnMap.clear();

  // 同步当前配置到全局
  libraryCdnMap.forEach((cdnUrl, libraryName) => {
    (window as any).aixLibraryCdnMap.set(libraryName, cdnUrl);
  });

  // 提供全局查找函数
  (window as any).aixGetLibraryCdn = (
    libraryName: string,
  ): string | undefined => {
    return libraryCdnMap.get(libraryName);
  };

  console.log('[CDN Panel] CDN 配置已同步到 window.aixLibraryCdnMap');
}

function rebuildLibraryCdnMap(): void {
  libraryCdnMap.clear();

  manifestCdnMap.forEach((cdnUrl, libraryName) => {
    libraryCdnMap.set(libraryName, cdnUrl);
  });

  botCdnMaps.forEach((botCdnMap) => {
    botCdnMap.forEach((cdnUrl, libraryName) => {
      libraryCdnMap.set(libraryName, cdnUrl);
    });
  });

  syncToGlobalWindow();
}

function collectLibraryCdnConfigs(
  configs: LibraryCdnConfig[],
  sourceLabel: string,
): Map<string, string> {
  const cdnMap = new Map<string, string>();
  console.log(`[CDN Panel] 开始存储 ${configs.length} 个 ${sourceLabel} 配置`);
  for (const config of configs) {
    const cdnUrl = config.cdn_url || config.url;

    if (!cdnUrl || !config.name) {
      console.warn(`[CDN Panel] 跳过不完整的 ${sourceLabel} 配置:`, config);
      continue;
    }

    // 存储为库配置
    cdnMap.set(config.name, cdnUrl);
    console.log(`[CDN Panel] 存储库配置: ${config.name} -> ${cdnUrl}`);
  }
  return cdnMap;
}

/**
 * 存储查询到的 CDN 配置
 *
 * 组件库模式：
 * - name: "asfui" (库名)
 * - cdn_url: "https://xxx/asfui.umd.js"
 *
 * 支持组件名格式：
 * - "asfui" -> 使用库默认导出
 * - "asfui.MetaTitle" -> 使用 asfui 库中的 MetaTitle 组件
 */
export function storeCdnConfigs(screens: BotRenderScreen[]): void {
  const screensByBotId = new Map<string, BotRenderScreen[]>();
  screens.forEach((screen) => {
    const botId = screen.bot_id || UNKNOWN_BOT_CDN_SCOPE;
    const botScreens = screensByBotId.get(botId) || [];
    botScreens.push(screen);
    screensByBotId.set(botId, botScreens);
  });

  screensByBotId.forEach((botScreens, botId) => {
    botCdnMaps.set(botId, collectLibraryCdnConfigs(botScreens, 'Bot CDN'));
  });

  rebuildLibraryCdnMap();
  console.log(
    `[CDN Panel] Bot CDN 存储完成，当前共有 ${libraryCdnMap.size} 个库配置`,
  );
}

/**
 * 存储 BCS manifest 返回的 CDN 配置
 *
 * manifest 格式：
 * - name: "bcsPanel"
 * - url: "https://xxx/index.js"
 */
export function storeManifestBundles(bundles: BcsManifestBundle[]): void {
  manifestCdnMap.clear();
  const resolvedBundles = bundles.map((bundle) => ({
    ...bundle,
    url: bundle.url ? resolveManifestBundleUrl(bundle.url) : bundle.url,
  }));

  collectLibraryCdnConfigs(resolvedBundles, 'BCS manifest').forEach(
    (cdnUrl, libraryName) => {
      manifestCdnMap.set(libraryName, cdnUrl);
    },
  );

  rebuildLibraryCdnMap();
  console.log(
    `[CDN Panel] BCS manifest 存储完成，当前共有 ${libraryCdnMap.size} 个库配置`,
  );
}

/**
 * 获取所有库名
 */
export function getLibraryNames(): string[] {
  const names = Array.from(libraryCdnMap.keys());
  console.log('[CDN Panel] 获取库名列表:', names);
  return names;
}

/**
 * 清空 CDN 配置（切换 Bot 时调用）
 */
export function clearCdnConfig(): void {
  libraryCdnMap.clear();
  manifestCdnMap.clear();
  botCdnMaps.clear();
  registeredTypes.clear();

  // 清理全局 window 上的配置
  if (typeof window !== 'undefined') {
    (window as any).aixLibraryCdnMap?.clear?.();
    syncToGlobalWindow();
    console.log('[CDN Panel] CDN 配置已清空');
  }
}

syncToGlobalWindow();

/**
 * 清空 BCS manifest CDN 配置。
 */
export function clearManifestCdnConfig(): void {
  manifestCdnMap.clear();
  rebuildLibraryCdnMap();
  console.log('[CDN Panel] BCS manifest CDN 配置已清空');
}

/**
 * 清空 Bot CDN 配置。
 * 传入 botId 时只清理该 Bot；不传则清理全部 Bot CDN 配置。
 */
export function clearBotCdnConfig(botId?: string): void {
  if (botId) {
    botCdnMaps.delete(botId);
    console.log(`[CDN Panel] Bot ${botId} CDN 配置已清空`);
  } else {
    botCdnMaps.clear();
    console.log('[CDN Panel] Bot CDN 配置已清空');
  }

  rebuildLibraryCdnMap();
}

/**
 * 解析组件名
 * 格式: "libraryName.componentName" 或 "libraryName"
 */
function parseComponentName(componentKey: string): {
  libraryName: string;
  exportName: string | null;
} {
  const parts = componentKey.split('.');
  if (parts.length >= 2) {
    // asfui.MetaTitle -> library: asfui, export: MetaTitle
    return {
      libraryName: parts[0],
      exportName: parts.slice(1).join('.'),
    };
  }
  // asfui -> library: asfui, export: null (使用默认)
  return {
    libraryName: componentKey,
    exportName: null,
  };
}

/**
 * 获取库的 CDN 配置
 */
export function getLibraryCdn(libraryName: string): string | undefined {
  return libraryCdnMap.get(libraryName);
}

/**
 * 检查库是否已配置 CDN
 */
export function hasLibraryCdn(libraryName: string): boolean {
  return libraryCdnMap.has(libraryName);
}

/**
 * 检查组件是否可以渲染
 */
export function canRenderComponent(componentKey: string): boolean {
  const { libraryName } = parseComponentName(componentKey);
  return libraryCdnMap.has(libraryName);
}

/**
 * 注册单个 CDN Panel（组件库模式）
 *
 * @param componentKey 组件完整名称（如 'asfui.MetaTitle'）
 * @returns 是否成功注册
 */
export async function registerCdnPanel(componentKey: string): Promise<boolean> {
  // 防止重复注册
  if (registeredTypes.has(componentKey)) {
    console.log(`[CDN Panel] ${componentKey} 已注册，跳过`);
    return false;
  }

  // 解析组件名
  const { libraryName, exportName } = parseComponentName(componentKey);

  // 查找库配置
  const cdnUrl = getLibraryCdn(libraryName);
  if (!cdnUrl) {
    console.warn(`[CDN Panel] 未找到库 ${libraryName} 的 CDN 配置`);
    return false;
  }

  try {
    // 动态导入以减少初始加载时间
    const [{ registerPanelContent }, { UmdPanel }] = await Promise.all([
      import('@aix-chat/ui'),
      import('./UmdPanel'),
    ]);

    // 创建包装组件，注入 CDN 配置
    const WrappedUmdPanel = (props: any) => {
      const currentCdnUrl = getLibraryCdn(libraryName);

      return UmdPanel({
        ...props,
        payload: {
          ...props.payload,
          cdn: currentCdnUrl,
          entry: exportName || libraryName, // 如果有具体组件名就用，否则用库名
        },
      });
    };

    WrappedUmdPanel.displayName = `UmdPanel(${componentKey})`;

    // 注册到 SDK 全局路由表
    registerPanelContent(componentKey, WrappedUmdPanel);
    registeredTypes.add(componentKey);

    console.log(
      `[CDN Panel] 已注册: ${componentKey} -> ${cdnUrl} (export: ${
        exportName || libraryName
      })`,
    );
    return true;
  } catch (error) {
    console.error(`[CDN Panel] 注册 ${componentKey} 失败:`, error);
    return false;
  }
}

/**
 * 预注册所有库的基础 Panel（库名作为 component key）
 *
 * @returns 成功注册的库数量
 */
export async function registerStoredCdnPanels(): Promise<number> {
  let count = 0;

  // 为每个库注册一个基础 Panel（使用库名作为 component key）
  for (const libraryName of libraryCdnMap.keys()) {
    const success = await registerCdnPanel(libraryName);
    if (success) count++;
  }

  if (count > 0) {
    console.log(`[CDN Panel] 批量注册完成: ${count} 个库`);
  }

  return count;
}

/**
 * 按需注册指定组件（用于 AixPanel 解析时）
 *
 * @param componentKey 组件完整名称（如 'asfui.MetaTitle'）
 * @returns 是否成功注册
 */
export async function registerComponentOnDemand(
  componentKey: string,
): Promise<boolean> {
  // 检查是否可以渲染
  if (!canRenderComponent(componentKey)) {
    console.warn(`[CDN Panel] 无法渲染 ${componentKey}，未找到对应的库配置`);
    return false;
  }

  // 如果已注册，直接返回成功
  if (registeredTypes.has(componentKey)) {
    return true;
  }

  // 按需注册
  return await registerCdnPanel(componentKey);
}

/**
 * 查询并注册指定 Bot 的 CDN Panel
 *
 * @param botId Bot ID
 * @returns 注册的库数量
 */
export async function queryAndRegisterBotCdnPanels(
  botId: string,
): Promise<number> {
  try {
    console.log(`[CDN Panel] 查询 Bot ${botId} 的 CDN 配置...`);
    const screens = await listBotRenderScreens({ bot_id: botId });

    if (!screens || screens.length === 0) {
      clearBotCdnConfig(botId);
      console.log(`[CDN Panel] Bot ${botId} 没有 CDN 配置`);
      return 0;
    }

    // 存储配置
    storeCdnConfigs(screens);

    // 注册基础 Panel（库名）
    const count = await registerStoredCdnPanels();

    console.log(`[CDN Panel] Bot ${botId} 注册完成: ${count} 个库`);
    return count;
  } catch (error) {
    console.error(`[CDN Panel] 查询或注册 Bot ${botId} CDN 配置失败:`, error);
    return 0;
  }
}

/**
 * 查询并注册 BCS manifest 中声明的 CDN Panel
 *
 * manifest 来源于 BCS 后端 GET /manifest，适用于群聊/协作页的虚拟 sender
 * 打开副屏场景，不依赖某个 Bot 的副屏配置。
 *
 * @returns 注册的库数量
 */
export async function queryAndRegisterManifestCdnPanels(): Promise<number> {
  if (manifestLoadPromise) {
    return manifestLoadPromise;
  }

  manifestLoadPromise = (async () => {
    try {
      console.log('[CDN Panel] 查询 BCS manifest CDN 配置...');

      // manifest 是协作页资源清单，请求失败时也不能复用上一次的 manifest CDN。
      clearManifestCdnConfig();

      const manifest = await getManifest();
      const bundles = Array.isArray(manifest?.bundles) ? manifest.bundles : [];

      if (bundles.length === 0) {
        console.log('[CDN Panel] BCS manifest 没有 CDN bundle 配置');
        return 0;
      }

      storeManifestBundles(bundles);

      const count = await registerStoredCdnPanels();

      console.log(
        `[CDN Panel] BCS manifest 注册完成: ${count} 个库，env=${manifest.env}`,
      );
      return count;
    } catch (error) {
      console.error('[CDN Panel] 查询或注册 BCS manifest CDN 配置失败:', error);
      return 0;
    } finally {
      manifestLoadPromise = null;
    }
  })();

  return manifestLoadPromise;
}

/**
 * 批量查询并注册多个 Bot 的 CDN Panel
 *
 * @param botIds Bot ID 列表
 * @returns 总注册数量
 */
export async function queryAndRegisterMultipleBotCdnPanels(
  botIds: string[],
): Promise<number> {
  console.log(`[CDN Panel] 批量查询 ${botIds.length} 个 Bot 的 CDN 配置...`);

  // 去重
  const uniqueBotIds = [...new Set(botIds)];

  // 并行查询所有 Bot 的 CDN 配置
  const results = await Promise.allSettled(
    uniqueBotIds.map((botId) => queryAndRegisterBotCdnPanels(botId)),
  );

  let totalCount = 0;
  results.forEach((result, index) => {
    if (result.status === 'fulfilled') {
      totalCount += result.value;
    } else {
      console.error(
        `[CDN Panel] 查询 Bot ${uniqueBotIds[index]} CDN 配置失败:`,
        result.reason,
      );
    }
  });

  console.log(`[CDN Panel] 批量注册完成: 共 ${totalCount} 个库`);
  return totalCount;
}

/**
 * 刷新 CDN 配置（不刷新页面）
 * 供 Bot 配置页面保存后调用，或手动在控制台调用
 *
 * @param botId Bot ID
 * @returns 刷新后的库数量
 */
export async function refreshCdnConfig(botId: string): Promise<number> {
  console.log(`[CDN Panel] 刷新 CDN 配置... Bot: ${botId}`);

  try {
    // 1. 清空该 Bot 的当前配置，保留 BCS manifest 和其他 Bot 配置
    clearBotCdnConfig(botId);
    console.log('[CDN Panel] 已清空该 Bot 的旧配置');

    // 2. 重新查询并注册
    const count = await queryAndRegisterBotCdnPanels(botId);

    console.log(
      `[CDN Panel] CDN 配置已刷新，共 ${count} 个库:`,
      getLibraryNames(),
    );

    // 3. 同步到全局 window
    syncToGlobalWindow();

    return count;
  } catch (error) {
    console.error('[CDN Panel] 刷新 CDN 配置失败:', error);
    return 0;
  }
}
