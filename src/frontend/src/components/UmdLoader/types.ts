import type { ComponentType, ErrorInfo, ReactNode } from 'react';

export interface UmdLoaderProps {
  /** UMD bundle 的 CDN 地址 */
  cdn: string;
  /** UMD bundle 中要渲染的导出名（如 'RiskSummary'），缺省取 default 导出 */
  entry?: string;
  /** 传递给远程组件的 props */
  data?: Record<string, any>;
  /** 传递给远程组件的方法 */
  methods?: Record<string, (...args: any[]) => any>;
  /** 传递给远程组件的子组件 */
  components?: Record<string, ComponentType<any>>;
  /** 注入到 eval sandbox 的依赖（如 React、ReactDOM） */
  dependencies?: Record<string, any>;
  /** 加载超时（毫秒），默认 10000 */
  timeout?: number;
  /** 加载失败重试次数（仅网络/超时错误重试），默认 0 */
  retryTimes?: number;
  /** 加载成功回调 */
  onUmdLoad?: () => void;
  /** 兼容旧 API，等同 onUmdLoad */
  onReady?: () => void;
  /** 加载失败（含重试耗尽后）回调 */
  onError?: (error: Error, errorInfo?: ErrorInfo) => void;
  /** 加载中占位 */
  skeleton?: ReactNode;
  /** 加载失败占位 */
  fallback?: ReactNode;
  /** 子元素 */
  children?: ReactNode;
}

export interface UseUmdOptions {
  cdn: string;
  entry?: string;
  dependencies?: Record<string, any>;
  timeout?: number;
  retryTimes?: number;
  onUmdLoad?: () => void;
  onReady?: () => void;
}

export interface UseUmdResult {
  /** 加载成功的组件，loading 期间为 null */
  Component: ComponentType<any> | null;
  /** 加载错误 */
  error: Error | null;
  /** 是否正在加载 */
  loading: boolean;
}

export interface LoadUmdOptions {
  cdn: string;
  entry?: string;
  dependencies?: Record<string, any>;
  timeout?: number;
  retryTimes?: number;
}

export interface UmdModule {
  /** UMD 源码 */
  code: string;
  /** 是否已 eval */
  isEvaluated: boolean;
  /** eval 后的导出 */
  moduleExports?: Record<string, any>;
}
