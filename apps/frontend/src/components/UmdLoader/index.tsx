import React from 'react';
import type { UmdLoaderProps } from './types';
import { useUmd } from './useUmd';

/**
 * 内层加载器：驱动 useUmd，loading 渲染 skeleton，error 抛出由外层 ErrorBoundary 捕获，
 * 成功时把 data/methods/components 透传给远程组件。
 */
function UmdLoaderInner(props: UmdLoaderProps) {
  const {
    cdn,
    entry,
    timeout,
    dependencies,
    retryTimes,
    onReady,
    onUmdLoad,
    skeleton,
    data,
    methods,
    components,
    children,
  } = props;

  const { Component, error, loading } = useUmd({
    cdn,
    entry,
    timeout,
    dependencies,
    retryTimes,
    onReady,
    onUmdLoad,
  });

  if (error) {
    throw error;
  }
  if (loading) {
    return <>{skeleton}</>;
  }
  if (Component) {
    return (
      <Component {...data} {...methods} components={components}>
        {children}
      </Component>
    );
  }
  return null;
}

interface ErrorBoundaryState {
  error: Error | null;
  prevCdn?: string;
  prevEntry?: string;
}

/**
 * UmdLoader（默认导出）：hooks 无法捕获子树错误，外层用 class 兜底。
 * - cdn/entry 变化时重置 error 并通过 key 强制 remount 内层加载器
 * - componentDidCatch 调 onError 并渲染 fallback
 */
class UmdLoader extends React.PureComponent<
  UmdLoaderProps,
  ErrorBoundaryState
> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromProps(
    nextProps: UmdLoaderProps,
    prevState: ErrorBoundaryState,
  ): ErrorBoundaryState | null {
    if (
      nextProps.cdn !== prevState.prevCdn ||
      nextProps.entry !== prevState.prevEntry
    ) {
      return {
        error: null,
        prevCdn: nextProps.cdn,
        prevEntry: nextProps.entry,
      };
    }
    return null;
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    this.props.onError?.(error, info);
    this.setState({ error });
  }

  render() {
    const { fallback, cdn, entry } = this.props;
    if (this.state.error) {
      return <>{fallback}</>;
    }
    return <UmdLoaderInner {...this.props} key={`${cdn}${entry ?? ''}`} />;
  }
}

export default UmdLoader;
export { clearModuleCache } from './cache';
export { loadUmd } from './loadUmd';
export type { LoadUmdOptions, UmdLoaderProps, UseUmdResult } from './types';
export { useUmd } from './useUmd';
