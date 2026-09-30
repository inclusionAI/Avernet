/**
 * 环境配置
 * 运行时环境判断和服务器地址获取
 *
 * 配置源：
 * - config/servers.config.ts - 服务器地址类型 + 开源占位默认
 * - AppExt.servers / AppExt.envResolver - 运行期真实值（内部经 src/internal 注入）
 */

import { getExt } from '@/capabilities';
import { AppExt } from '@/shell/extension';
import type { EnvName, ServerConfig } from '../../config/servers.config';
import { getElectronEnv, isElectron } from './platform';

// 全局类型声明：LOCAL_DEV_ENV 由 config.local.ts 的 define 配置注入
declare const LOCAL_DEV_ENV: 'LOCAL' | 'DEV' | 'PRE' | 'PROD' | undefined;

/**
 * 导出服务器配置类型供外部使用。
 * 注:真实 SERVERS 值经 AppExt.servers 注入（开源占位 / 内部真实），此处不再导出值。
 */
export { type ServerConfig, type EnvName };

/**
 * 获取当前环境
 * 优先级：开发环境变量 > hostname 自动判断
 * 注意：Electron 桌面端不使用此函数，直接使用 ELECTRON_ENV
 */
function getCurrentEnv(): EnvName {
  // 1. 仅开发环境：从 config.local.ts 注入的环境变量读取
  //    确保 proxy 配置和运行时环境判断保持一致
  //    define 配置会将 LOCAL_DEV_ENV 作为全局常量注入
  if (typeof LOCAL_DEV_ENV !== 'undefined') {
    const devEnv = LOCAL_DEV_ENV;
    if (
      devEnv === 'LOCAL' ||
      devEnv === 'DEV' ||
      devEnv === 'PRE' ||
      devEnv === 'PROD'
    ) {
      return devEnv as EnvName;
    }
  }

  // 2. 根据 hostname 自动判断（内网域名特征判断经 envResolver 注入；
  //    开源默认 envResolver 恒返回 null → 走下方默认值）
  const env = getExt(AppExt).envResolver.resolveEnvFromHostname(
    window.location.hostname,
  );
  if (env) {
    return env;
  }

  // 3. 默认预发环境
  return 'PRE';
}

/**
 * 获取当前环境的服务器配置
 * Electron 桌面端：使用本地服务器地址（如 http://127.0.0.1:30001）
 * Web 端：根据环境使用 PRE/PROD/LOCAL 配置
 */
export function getServers(): ServerConfig {
  // Electron 桌面端：使用本地 Agent 地址
  if (isElectron()) {
    const electronEnv = getElectronEnv();
    const { apiHost, apiProtocol } = electronEnv || {};
    const localServer = `${apiProtocol || 'http'}://${
      apiHost || '127.0.0.1:20003'
    }`;

    return {
      MANAGEMENT: localServer,
      SESSION: localServer,
      ASFAGENT: localServer,
      // Electron 本地 Agent 统一承载所有服务（含 aixharness / mcpcenter 代理），
      // 与其它键一致指向 localServer，补齐 ServerConfig 必填项。
      AIXHARNESS: localServer,
      MCPCENTER: localServer,
    };
  }

  // Web 端：根据环境获取远程服务器配置（真实值经 AppExt.servers 注入）
  const env = getCurrentEnv();
  return getExt(AppExt).servers[env];
}

/**
 * 获取 Electron 桌面端服务器地址
 * @returns Electron 本地 Agent 地址，如 http://127.0.0.1:30001
 */
export function getElectronServerUrl(): string {
  if (isElectron()) {
    const electronEnv = getElectronEnv();
    const { apiHost, apiProtocol } = electronEnv || {};
    return `${apiProtocol || 'http'}://${apiHost || '127.0.0.1:20003'}`;
  }
  return 'http://127.0.0.1:20003'; // 默认值（理论上不应该在非 Electron 环境调用）
}

/**
 * 获取 /proxypass 的完整目标地址
 * @param path 路径，如 /proxypass/xxx/api/moltis/ws
 * @returns 完整的绝对地址
 *
 * Electron 桌面端：http://127.0.0.1:30001/xxx/api/moltis/ws（不需要 /proxypass 前缀）
 * Web 端：<SESSION 服务器>/proxypass/xxx/api/moltis/ws
 */
export function getProxypassAbsoluteUrl(path: string): string {
  const servers = getServers();

  // 移除开头的 /proxypass
  const cleanPath = path.replace(/^\/proxypass/, '');

  // Electron 桌面端：直接返回本地地址，不需要 /proxypass 前缀
  if (isElectron()) {
    return `${servers.SESSION}${cleanPath}`;
  }

  // Web 端：需要保留 /proxypass 前缀
  return `${servers.SESSION}/proxypass${cleanPath}`;
}

/**
 * 获取当前环境名称（用于调试）
 */
export function getEnvName(): string {
  return getCurrentEnv();
}

/**
 * 获取当前环境名称（用于调试）全小写
 */
export function getEnvNameLowerCase(): string {
  return getCurrentEnv().toLowerCase();
}
