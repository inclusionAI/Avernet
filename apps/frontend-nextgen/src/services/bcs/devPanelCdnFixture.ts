/**
 * 方式②副屏 CDN 离线 fixture（dev / 自测 / 未接通后端时的安全网）。
 *
 * 直写 `window.aixLibraryCdnMap` + `window.aixGetLibraryCdn`，让引擎 `loadCDNComponent`
 * 在不依赖副屏配置页（另一同学负责）/ BCS 后端 / 真实 CDN 的情况下，也能按库名解析出 CDN URL，
 * 验证方式②"声明式 <AixUI component="lib.X"> → 引擎 umd 加载"完整链路。
 *
 * 用法：
 * - 独立自测页（/panel-self-test）已在组件挂载时调 ensureCdnSelfTestRegistered（走 registerPanelContent
 *   的 CDNRegistration 直注，与本 fixture 的 aixLibraryCdnMap 注入是两条独立验证支路，互不冲突）。
 * - 真实会话侧方式②联调未就绪时，业务可在 dev 入口调 injectDevPanelCdnFixture({'lib': url}) 注入已知
 *   业务库 UMD，使 <AixUI component="lib.X"> 在引擎侧可解析加载。
 *
 * 注意：本 fixture 提供的是"数据注入"（写 window.aixLibraryCdnMap），不注册组件；组件加载由引擎
 * loadCDNComponent 统一完成。故与 libraryCdnInjector 的 syncToGlobalWindow 共享同一 window 出口，
 * 注入后会被后续 rebuild 覆盖——仅供 dev/自测，不进生产装配。
 */

/** 库名 → CDN URL 的人工注入映射。 */
export type DevPanelCdnMap = Record<string, string>;

interface AixLibraryCdnGlobal {
  aixLibraryCdnMap?: Map<string, string>;
  aixGetLibraryCdn?: (libraryName: string) => string | undefined;
}

/**
 * 注入 dev/自测 CDN 库映射到 window.aixLibraryCdnMap + aixGetLibraryCdn。
 *
 * 与 libraryCdnInjector 的内存Map独立：本函数直接操作 window 上的 Map，不触碰
 * libraryCdnInjector 的 manifest/bot 分桶。dev 自测场景下 engine 经 getLibraryCdnFromGlobal
 * 读 window.aixLibraryCdnMap 命中注入值即可。
 *
 * @param map 库名 → CDN URL。重复注入会合并进既有 Map（不清空未列出条目，便于多库渐进注入）。
 * @returns 注入后的 aixLibraryCdnMap 快照（调试用）。
 */
export function injectDevPanelCdnFixture(map: DevPanelCdnMap): Map<string, string> {
  if (typeof window === 'undefined') return new Map();
  const w = window as unknown as AixLibraryCdnGlobal & typeof globalThis;
  if (!w.aixLibraryCdnMap) w.aixLibraryCdnMap = new Map<string, string>();
  for (const [libraryName, cdnUrl] of Object.entries(map)) {
    if (!libraryName || !cdnUrl) continue;
    w.aixLibraryCdnMap.set(libraryName, cdnUrl);
  }
  w.aixGetLibraryCdn = (libraryName: string): string | undefined =>
    w.aixLibraryCdnMap?.get(libraryName);
  return new Map(w.aixLibraryCdnMap);
}

/** 清空 dev/自测注入的 CDN 库映射（仅清 window 侧，不动 libraryCdnInjector 内存态）。 */
export function clearDevPanelCdnFixture(): void {
  if (typeof window === 'undefined') return;
  const w = window as unknown as AixLibraryCdnGlobal;
  w.aixLibraryCdnMap?.clear?.();
}
