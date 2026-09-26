import React from 'react';
import ReactDOM from 'react-dom';
import {
  clearPendingFetch,
  getModule,
  getPendingFetch,
  setModule,
  setPendingFetch,
} from './cache';
import type { LoadUmdOptions } from './types';

type UmdPhase = 'fetch' | 'eval' | 'entry';

/**
 * 副屏 UMD 组件 CDN 同源代理开关（仅 dev 注入，见 config/config.local.ts 的 define）。
 * 为 true 时把跨域 CDN 的绝对地址改写成走 dev server 的同源路径
 * /__umd_cdn?target=<绝对CDN地址>，从浏览器看是同源请求，绕过 CDN 的 CORS 白名单；
 * 真实转发由 dev server proxy 完成（服务端到服务端，不受 CORS 约束）。
 * 生产构建未注入该常量，typeof 守卫使本段被 tree-shake，直连原始 CDN。
 */
declare const UMD_CDN_PROXY: boolean | undefined;

// ⚠️ 与 config/config.local.ts 的 UMD_CDN_PROXY_PATH 必须保持一致。
const UMD_CDN_PROXY_PATH = '/__umd_cdn';

/**
 * dev 下把绝对 http(s) CDN 地址改写成同源代理路径；其余情况（生产、相对路径）原样返回。
 * 仅改写「实际 fetch 的 URL」，缓存与 sourceURL 仍用原始 cdn 作为 key，便于复用与调试定位。
 */
function resolveFetchUrl(cdn: string): string {
  if (typeof UMD_CDN_PROXY === 'undefined' || !UMD_CDN_PROXY) return cdn;
  if (!/^https?:\/\//i.test(cdn)) return cdn;
  return `${UMD_CDN_PROXY_PATH}?target=${encodeURIComponent(cdn)}`;
}

function tagError(error: Error, phase: UmdPhase): Error {
  (error as any).umdPhase = phase;
  return error;
}

/**
 * 创建 eval sandbox。
 * - 函数作用域注入 React / ReactDOM / regeneratorRuntime（兼容旧 babel 产物）/ require
 * - require 从 dependencies + react/react-dom/regeneratorRuntime 解析（UMD commonjs2 分支）
 */
function createSandbox(
  dependencies: Record<string, any> = {},
): Record<string, any> {
  const globalScope: any = typeof window !== 'undefined' ? window : globalThis;
  const regeneratorRuntime = globalScope.regeneratorRuntime;
  const modules: Record<string, any> = {
    react: React,
    'react-dom': ReactDOM,
    regeneratorRuntime,
    ...dependencies,
  };
  return {
    React,
    ReactDOM,
    regeneratorRuntime,
    require: (name: string) => (name in modules ? modules[name] : undefined),
  };
}

/** 走 UMD 的 commonjs2 分支收集导出，sourceURL 便于 devtools 定位 */
function evalAsModule(
  cdn: string,
  code: string,
  context: Record<string, any>,
): Record<string, any> {
  const mod = { exports: {} as Record<string, any> };
  const ctx = { module: mod, exports: mod.exports, ...context };
  const keys = Object.keys(ctx);
  const values = keys.map((key) => ctx[key]);
  // eslint-disable-next-line no-new-func
  new Function(
    `return function(${keys.join(', ')}) {\n${code}\n//# sourceURL=${cdn}\n}`,
  )().apply(undefined, values);
  return mod.exports;
}

/** 无 entry 或 entry==='default' 时按 __esModule 取 default，否则取整个 module */
function findExported(
  moduleExports: Record<string, any> | undefined,
  name?: string,
): any {
  if (!name || name === 'default') {
    return moduleExports?.__esModule ? moduleExports.default : moduleExports;
  }
  return moduleExports?.[name];
}

function fetchCode(cdn: string, timeout: number): Promise<string> {
  const pending = getPendingFetch(cdn);
  if (pending) return pending;

  const promise = (async () => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    try {
      const res = await fetch(resolveFetchUrl(cdn), {
        signal: controller.signal,
      });
      if (!res.ok) {
        throw tagError(
          new Error(`UMD fetch failed: ${res.status} | ${cdn}`),
          'fetch',
        );
      }
      return await res.text();
    } catch (err: any) {
      if (controller.signal.aborted) {
        throw tagError(new Error(`UMD load timeout: ${cdn}`), 'fetch');
      }
      throw tagError(
        err instanceof Error ? err : new Error(String(err)),
        'fetch',
      );
    } finally {
      clearTimeout(timer);
      clearPendingFetch(cdn);
    }
  })();

  setPendingFetch(cdn, promise);
  return promise;
}

async function loadOnce(
  options: LoadUmdOptions,
): Promise<React.ComponentType<any>> {
  // 默认 10s：经 dev server 同源代理多一跳、且首次拉取大 bundle 的 CDN 可能较慢，
  // 3s 易误判超时。调用方可通过 options.timeout 覆盖。
  const { cdn, entry, dependencies, timeout = 10000 } = options;

  let mod = getModule(cdn);
  if (!mod || !mod.isEvaluated) {
    if (!mod) {
      const code = await fetchCode(cdn, timeout);
      // 并发下可能已被其它调用写入缓存，复查一次
      mod = getModule(cdn);
      if (!mod) {
        mod = { code, isEvaluated: false };
        setModule(cdn, mod);
      }
    }
    if (!mod.isEvaluated) {
      if (!mod.code) {
        throw tagError(new Error(`UMD empty: ${cdn}`), 'fetch');
      }
      try {
        mod.moduleExports = evalAsModule(
          cdn,
          mod.code,
          createSandbox(dependencies),
        );
        mod.isEvaluated = true;
      } catch (err: any) {
        throw tagError(
          err instanceof Error ? err : new Error(String(err)),
          'eval',
        );
      }
    }
  }

  const component = findExported(mod.moduleExports, entry);
  if (!component) {
    throw tagError(
      new Error(`Entry "${entry ?? 'default'}" not found in ${cdn}`),
      'entry',
    );
  }
  return component;
}

/**
 * 加载远程 UMD 模块并按 entry 解析返回组件。
 * 仅对 fetch/超时错误重试；eval 错误与 entry 缺失不重试。
 */
export async function loadUmd(
  options: LoadUmdOptions,
): Promise<React.ComponentType<any>> {
  const { retryTimes = 0 } = options;
  let attempt = 0;
  for (;;) {
    try {
      return await loadOnce(options);
    } catch (err: any) {
      const retriable = err?.umdPhase === 'fetch';
      if (retriable && attempt < retryTimes) {
        attempt += 1;
        continue;
      }
      throw err;
    }
  }
}
