// @sdd: 方式② CDN UMD 副屏加载器（迁移自 ocb UmdPanel，适配新 `@tc-chat/ui` SDK）。
//
// 背景：新引擎 `@tc-chat/ui` 的 `resolveBusinessEntry` 在「全局 CDN map 命中」分支把
// `entry` 算成 `componentName`（如 `StateMachineRunView`），丢了 `libraryName.` 前缀，
// 导致引擎 `loadCDNComponent` 按 `window.StateMachineRunView`（顶层）找不到——而 UMD
// 实际暴露在 `window.bcsPanel.StateMachineRunView`（vite lib.name='bcsPanel' + named exports）。
// 引擎 SDK 不可改（node_modules）。
//
// 解法：在 teamclaw 侧 `registerPanelContent('umd', UmdPanel)` 接管 type='umd' tab，
// UmdPanel 读 `params._componentKey`（resolveBusinessEntry 注入的完整 `lib.Component`）
// 重建点路径 exportName，加载用自管 resolveUmdModule（智能探测 UMD 全局对象，兼容库名与 UMD 实际全局名不一致）。
// 对齐 ocb UmdPanel 的"包装 + 自控 entry"链路，不引入 @alipay/umd-loader。
//
// 数据平铺：镜像引擎 BusinessComponentMount/CDNMount——把 `params.data` 业务字段平铺到组件
// 顶层 props（剔除保留键），并透传 tab/params/payload/onAction/onInteraction/eventEmitter。
import { Skeleton } from '@/components/ui';
import { ensureReactGlobal } from '@/services/workspace/ensureReactGlobal';
import { registerPanelContent } from '@tc-chat/ui/es/SidePanelContent';
import type { PanelContentProps } from '@tc-chat/ui/es/SidePanelContent/registry';
import { Component, type ComponentType, useEffect, useState } from 'react';
import { resolveBcsStateMachineApiBaseUrl } from './bcsPanelBaseUrl';
import { RESERVED_DATA_KEYS, resolveExportName } from './resolveUmdExport';
import { resolveUmdComponent } from './resolveUmdModule';

/**
 * 远程 UMD 副屏渲染错误边界：捕获远程组件渲染期异常（如 CDN UMD 尚未兼容信封响应、
 * 解析 envelope 为业务对象后访问 undefined 字段导致的渲染崩溃），降级为提示而非让
 * 整个页面白屏。仅兜底渲染期错误；加载失败仍由 UmdPanel 的 error 态处理。
 * `resetKey` 变化（切到不同副屏 tab）时重置边界，便于重新尝试渲染。
 */
class PanelRenderErrorBoundary extends Component<
  { children: React.ReactNode; resetKey: string },
  { error: Error | null }
> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error): { error: Error | null } {
    return { error };
  }

  componentDidUpdate(prev: { resetKey: string }): void {
    if (prev.resetKey !== this.props.resetKey && this.state.error) {
      this.setState({ error: null });
    }
  }

  render(): React.ReactNode {
    if (this.state.error) {
      return (
        <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center">
          <h3 className="m-0 text-sm font-semibold text-red-500">副屏渲染异常</h3>
          <p className="m-0 max-w-md text-xs text-[var(--color-muted)]">
            {this.state.error.message || '远程副屏组件渲染时发生错误。'}
          </p>
        </div>
      );
    }
    return this.props.children;
  }
}

interface UmdState {
  Component: ComponentType<any> | null;
  error: string | null;
}

/**
 * UmdPanel：type='umd' 副屏 tab 的统一加载器。
 *
 * 接收 BusinessComponentMount 透传的 `{tab, params, payload, onAction, onInteraction, eventEmitter}`，
 * 其中 `params`（= resolveBusinessEntry 的 finalPayload）含 `cdn`/`entry`/`_componentKey`/`_libraryName`/`data`。
 * 加载完成后把 `data` 业务字段平铺给远程 UMD 组件（与引擎 CDNMount 行为一致）。
 */
export function UmdPanel(props: PanelContentProps): React.ReactElement {
  const { tab, onAction, onInteraction, eventEmitter } = props;
  const tabData = (tab?.params ?? tab?.payload ?? {}) as Record<string, unknown>;

  const cdn = tabData.cdn as string | undefined;
  const exportName = resolveExportName(tabData);
  const data = (tabData.data as Record<string, unknown> | undefined) ?? {};

  const [state, setState] = useState<UmdState>({ Component: null, error: null });

  // external-React UMD 依赖 window.React；ensureReactGlobal 兜底（幂等）。
  useEffect(() => {
    ensureReactGlobal();
  }, []);

  useEffect(() => {
    if (!cdn) {
      setState({ Component: null, error: '缺少 CDN 地址' });
      return;
    }
    const libraryName = (tabData._libraryName as string | undefined) ?? undefined;
    let cancelled = false;
    setState({ Component: null, error: null });
    resolveUmdComponent(cdn, exportName, libraryName)
      .then((Component) => {
        if (!cancelled) setState({ Component, error: null });
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setState({ Component: null, error: err instanceof Error ? err.message : String(err) });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [cdn, exportName]);

  if (state.error) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center">
        <h3 className="m-0 text-sm font-semibold text-red-500">副屏组件加载失败</h3>
        <p className="m-0 max-w-md text-xs text-[var(--color-muted)]">{state.error}</p>
        <p className="m-0 text-xs text-[var(--color-muted)]">
          {cdn} · 导出 {exportName}
        </p>
      </div>
    );
  }

  if (!state.Component) {
    return <Skeleton.Card />;
  }

  const Component = state.Component;

  // 平铺 data 业务字段（剔除保留键），让 UMD 组件可直接 props.xxx 访问（对齐 CDNMount）。
  const flattened: Record<string, unknown> = {};
  for (const key of Object.keys(data)) {
    if (!RESERVED_DATA_KEYS.includes(key)) flattened[key] = data[key];
  }

  // BCS 状态机副屏取数基址注入：远程 UMD（@alipay/bcn-panel-asset）默认 base 为 `/bcnproxy`，
  // 部署态无该反代会被 CORB 拦截；注入 `/api/v1/collaboration` 走 Tern 同源反代。已显式提供
  // apiBaseUrl/baseUrl 时不覆盖，非 BCS 状态机 panel 不干预。详见 bcsPanelBaseUrl.ts。
  const libraryName = (tabData._libraryName as string | undefined) ?? undefined;
  const injectedApiBaseUrl = resolveBcsStateMachineApiBaseUrl(
    libraryName,
    exportName,
    flattened.apiBaseUrl,
    flattened.baseUrl,
  );
  if (injectedApiBaseUrl) flattened.apiBaseUrl = injectedApiBaseUrl;

  const propsData = { ...tabData, eventEmitter };
  return (
    <PanelRenderErrorBoundary resetKey={`${cdn}#${exportName}`}>
      <Component
        tab={tab}
        params={propsData}
        payload={propsData}
        onAction={onAction}
        onInteraction={onInteraction}
        eventEmitter={eventEmitter}
        {...flattened}
      />
    </PanelRenderErrorBoundary>
  );
}

let registered = false;

/**
 * 注册方式② CDN UMD 副屏加载器：`registerPanelContent('umd', UmdPanel)`。
 *
 * 接管所有 `type='umd'` 的副屏 tab（声明式 `<AixUI component="lib.Comp">` 经
 * resolveBusinessEntry CDN 分支产出的 finalType='umd'，以及命令式 openTab({type:'umd'})）。
 * 幂等：仅注册一次。由 `src/extensions/empty.ts` 的 `registerSidePanelWiring()` 在 app 初始化调用。
 *
 * 方式② CDN 对开源版/内源版均可用，故属 Open Core 装配（非 src/internal/**）。
 */
export function registerUmdPanelHandler(): void {
  if (registered) return;
  registerPanelContent('umd', UmdPanel as ComponentType<PanelContentProps>);
  registered = true;
}
