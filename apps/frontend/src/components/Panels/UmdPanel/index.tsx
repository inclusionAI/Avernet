import UmdLoader from '@/components/UmdLoader';
import type { PanelContentProps } from '@aix-chat/ui';
import React, { useEffect } from 'react';
import ReactDOM from 'react-dom';

/**
 * UmdPanel
 *
 * 通过 UMD Loader 从 CDN 动态加载远程组件的副屏面板。
 * 必须提供 payload.cdn 和 payload.entry 参数：
 *   - cdn: 组件的 CDN 地址，例如 'https://cdn.example.com/p/.../dist/index.umd.js'
 *   - entry: 组件在 UMD bundle 中的导出名称，例如 'RiskSummary'
 *   - data: 传递给组件的 props 数据（可选），例如 { riskId: '123', showDetail: true }
 *
 * 事件订阅机制（新增）：
 *   - 聊天区 → Panel：通过 payload.eventEmitter 订阅事件
 *     事件由 chatBridge.emitPanelEvent() 触发
 *   - Panel → 聊天区：通过 onAction / onInteraction 回调发送消息
 *
 * 调用示例：
 * chatBridge.openPanelTab({
 *   id: 'umd-risk-1',
 *   title: '风险概览',
 *   type: 'umd',
 *   payload: {
 *     cdn: 'https://cdn.example.com/p/your-package/dist/index.umd.js',
 *     entry: 'RiskSummary',
 *     data: { riskId: '12345', showDetail: true }
 *   }
 * });
 *
 * // 发送事件到面板（在组件内部通过 props.eventEmitter.on() 订阅）
 * chatBridge.emitPanelEvent('umd-risk-1', {
 *   type: 'focus',
 *   payload: { nodeId: '123' }
 * });
 */
// 确保 React 和 ReactDOM 在全局可用（供 UMD 组件使用）
// 某些 UMD 包在构建时 external 了 React，需要在加载前挂载到 window
function ensureReactGlobal() {
  if (typeof window !== 'undefined') {
    if (!(window as any).React) {
      (window as any).React = React;
    }
    if (!(window as any).ReactDOM) {
      (window as any).ReactDOM = ReactDOM;
    }
  }
}

export function UmdPanel({
  payload,
  onAction,
  onInteraction,
}: PanelContentProps) {
  const cdn = payload?.cdn;
  const entry = payload?.entry;
  // 兼容两种数据格式：
  // 1. CDN 模式：payload.data 包含业务数据
  // 2. Registry 模式：payload 直接就是业务数据
  const data = payload?.data ?? payload;

  // 从 payload 获取 eventEmitter（由 SDK 自动创建并注入）
  const { eventEmitter } = payload as {
    data: any;
    eventEmitter?: any;
  };

  // 在组件初始化时确保 React 全局可用
  useEffect(() => {
    ensureReactGlobal();
  }, []);

  if (!cdn || !entry) {
    // 获取原始 component key（从 payload._componentKey 或 entry 推断）
    const componentKey = (payload as any)?._componentKey || entry || 'unknown';
    const libraryName =
      (payload as any)?._libraryName || componentKey.split('.')[0];

    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-red-500 max-w-md p-4">
          <div className="font-semibold mb-2">
            组件库「{libraryName}」不存在
          </div>
          <div className="text-sm text-gray-600">
            组件「{componentKey}」被识别为组件库模式，但未找到其 CDN 配置。
            <br />
            请在 Bot 配置-副屏配置里填写正确的组件库 CDN 和名称。
          </div>
        </div>
      </div>
    );
  }

  // 生成唯一的 key，确保 payload 数据变化时重新加载组件
  const loaderKey = `${cdn}-${entry}-${JSON.stringify(data)}`;

  // 包装 onAction/onInteraction，添加日志
  const wrappedOnAction = (action: any) => {
    console.log('[UmdPanel] onAction:', action);
    onAction?.(action);
  };
  const wrappedOnInteraction = (record: any) => {
    console.log('[UmdPanel] onInteraction:', record);
    onInteraction?.(record);
  };

  // 准备传递给 UMD 组件的数据
  // UMD 组件期望直接在 props 上接收数据，而不是嵌套在 data 对象中
  const umdData = {
    ...data,
    payload,
    onAction: wrappedOnAction,
    onInteraction: wrappedOnInteraction,
    eventEmitter,
  };

  console.log('[UmdPanel] 传递给 UmdLoader 的 data:', umdData);

  return (
    <div
      className="w-full h-full p-3 bg-white"
      style={{
        padding: '12px 12px 0px',
        background: 'rgb(255, 255, 255)',
        borderRadius: '6px',
      }}
    >
      <UmdLoader
        key={loaderKey}
        cdn={cdn}
        entry={entry}
        data={umdData}
        dependencies={{
          React: React,
          ReactDOM: ReactDOM,
        }}
        onUmdLoad={() => {
          console.log('[UmdPanel] UMD 模块加载成功', { cdn, entry });
        }}
        onError={(error) => {
          console.error('[UmdPanel] 加载组件失败:', error);
        }}
        skeleton={
          <div className="flex items-center justify-center h-full">
            <div className="text-gray-500">加载中...</div>
          </div>
        }
        fallback={
          <div className="flex items-center justify-center h-full">
            <div className="text-red-500">组件加载失败</div>
          </div>
        }
      />
    </div>
  );
}
