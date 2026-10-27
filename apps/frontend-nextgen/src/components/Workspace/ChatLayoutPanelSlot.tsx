/**
 * ChatLayoutPanelSlot —— 副屏插槽包装组件（真实开合态观察口）。
 *
 * 副屏 open/close 的唯一事件源是 ChatLayout.Panel 内部的 onIsOpenChange
 * （所有 isOpen 写路径——openTab 自动展开 / closePanel / closePanelForce /
 * autoCloseWhenEmpty 弹回——都经 setIsOpenWrapped 同步外发）。但 JSX 上直接给
 * <ChatLayout.Panel onIsOpenChange> 会被 ChatLayoutRoot 经 cloneElement 覆盖
 * （Root 占用它维护自身 ResizeHandle 开合镜像），应用层拿不到。
 *
 * 解法：以本包装组件充当面板插槽。Root 按 displayName 识别插槽并注入
 * dynamicWidth / containerWidth / onIsOpenChange，注入的 onIsOpenChange 落到
 * 本组件后组合分发——Root 的 setter 原样透传（ResizeHandle 语义不变），
 * App 侧 onOpenStateChange 串联其后，获得与 SDK 一致的真实开合态
 * （Monitor 激活态与 SidePanelCollapseButton 显隐同源消费）。
 *
 * 收起策略 dismissOnOutside（默认 true）为私有 prop：会话主界面传 false 时不订阅点外
 * 收起、仅显式信号关闭；转发 SDK 前剥离。
 */
import {
  usePersistentChatLayoutPanelHost,
  type PersistentChatLayoutPanelProps,
} from '@/components/Workspace/PersistentChatLayoutPanelHost';
import { useWorkspacePanelDismiss } from '@/hooks/useWorkspacePanelDismiss';
import {
  isTaskNavigationHydratedAtCurrentRoute,
  shouldPreserveTaskPanelDuringNavigation,
} from '@/services/tasks/taskConversationNavigation';
import { persistPanelWidthPercent, TASK_PANEL_INITIAL_WIDTH } from '@/services/workspace/panelWidthMemory';
import { useTaskConversationNavigationStore } from '@/stores/taskConversationNavigationStore';
import type { PanelHandle } from '@tc-chat/core';
import { ChatLayout } from '@tc-chat/ui/es/ChatLayout';
import { PanelRegistryContext } from '@tc-chat/ui/es/ChatLayout/context';
import type { ChatLayoutPanelProps } from '@tc-chat/ui/es/ChatLayout/types';
import {
  forwardRef,
  useCallback,
  useContext,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react';

/** Root cloneElement 注入的内部 props（公开类型未声明，运行时存在）。 */
type ChatLayoutPanelInjectedProps = {
  dynamicWidth?: string | null;
  containerWidth?: number | null;
  onIsOpenChange?: (isOpen: boolean) => void;
};

export type ChatLayoutPanelSlotProps = ChatLayoutPanelProps & {
  /** App 侧真实开合态观察口（含默认挂载 openTab 自动展开，非仅用户点按路径）。 */
  onOpenStateChange?: (isOpen: boolean) => void;
  /** 点击面板外部是否收起；默认 true。false：仅经顶栏「副屏」钮/悬浮「收起」钮关闭。 */
  dismissOnOutside?: boolean;
};

// 公开类型缺 onIsOpenChange 声明，运行时由 Root 注入并由本组件组合分发。
const ChatLayoutPanelWithInjectedProps = ChatLayout.Panel as unknown as React.ComponentType<
  ChatLayoutPanelProps & ChatLayoutPanelInjectedProps & React.RefAttributes<PanelHandle>
>;

function taskNavigationAwareHandle(handle: PanelHandle): PanelHandle {
  return {
    openTab: (tab) => handle.openTab(tab),
    closeTab: (id) => {
      const store = useTaskConversationNavigationStore.getState();
      if (store.navigation?.tab?.id === id) store.completeNavigation(store.navigation.id);
      handle.closeTab(id);
    },
    updateTab: (id, update) => handle.updateTab(id, update),
    focusTab: (id) => handle.focusTab(id),
    openPanel: () => handle.openPanel(),
    closePanel: () => {
      const store = useTaskConversationNavigationStore.getState();
      if (store.navigation) store.completeNavigation(store.navigation.id);
      handle.closePanel();
    },
    closePanelForce: () => {
      if (shouldPreserveTaskPanelDuringNavigation()) return;
      // 普通会话切换会清空全部 tabs，同步丢弃旧任务宿主，后续重新打开时重新建立原点。
      useTaskConversationNavigationStore.getState().resetAll();
      handle.closePanelForce();
    },
    flushContext: () => handle.flushContext(),
    flushInteractions: () => handle.flushInteractions(),
  };
}

function assignPanelRef(ref: React.ForwardedRef<PanelHandle>, handle: PanelHandle | null): void {
  if (typeof ref === 'function') ref(handle);
  else if (ref) ref.current = handle;
}

export const ChatLayoutPanelSlot = forwardRef<PanelHandle, ChatLayoutPanelSlotProps>(function ChatLayoutPanelSlot(
  props,
  ref,
) {
  const { onOpenStateChange, dismissOnOutside = true, ...rest } = props;
  const persistentHost = usePersistentChatLayoutPanelHost();
  const hasPersistentHost = persistentHost !== null;
  const panelRegistry = useContext(PanelRegistryContext);
  const taskNavigation = useTaskConversationNavigationStore((state) => state.navigation);
  const reactId = useId().replace(/:/g, '');
  const ownerId = `workspace-panel-slot-${reactId}`;
  const panelClass = `workspace-sdk-panel-${reactId}`;
  const anchorRef = useRef<HTMLDivElement | null>(null);
  const rawHandleRef = useRef<PanelHandle | null>(null);
  const exposedHandleRef = useRef<PanelHandle | null>(null);
  const [open, setOpen] = useState(props.defaultOpen ?? false);
  const injected = rest as ChatLayoutPanelProps & ChatLayoutPanelInjectedProps;
  const rootIsOpenChange = injected.onIsOpenChange;
  const handleIsOpenChange = useCallback(
    (isOpen: boolean) => {
      setOpen(isOpen);
      // Root 注入的 setter 原样透传（ResizeHandle 开合镜像不受影响）。
      rootIsOpenChange?.(isOpen);
      onOpenStateChange?.(isOpen);
    },
    [rootIsOpenChange, onOpenStateChange],
  );
  // containerWidth 尚未由 SDK ResizeObserver 测得时，SDK 会忽略 active tab 的
  // defaultWidth，直接使用 panelWidth 回退。这里必须同步使用 TaskPanel 的 420px
  // 初始宽，否则面板会先以通用 380px 打开，直到主会话变化触发重新测量才变宽。
  const fallbackPanelWidth = injected.panelWidth ?? TASK_PANEL_INITIAL_WIDTH;
  const panelProps = useMemo<PersistentChatLayoutPanelProps>(
    () => ({
      ...injected,
      panelWidth: fallbackPanelWidth,
      // Persistent Panel 固定在 AppShell，不能携带随路由 owner 变化的 className；否则仅 class
      // 变化也会让 SDK Panel/TaskPanel 在切链时重新 render。无 host 的本地 Panel 仍保留唯一类名。
      className: [injected.className, 'workspace-sdk-panel', hasPersistentHost ? null : panelClass]
        .filter(Boolean)
        .join(' '),
      onIsOpenChange: handleIsOpenChange,
    }),
    [
      fallbackPanelWidth,
      handleIsOpenChange,
      injected.autoCloseWhenEmpty,
      injected.bridge,
      injected.className,
      injected.containerWidth,
      injected.defaultOpen,
      injected.dynamicWidth,
      injected.onAction,
      injected.onTabClose,
      injected.slotLeft,
      panelClass,
      hasPersistentHost,
    ],
  );

  const setLocalHandle = useCallback(
    (handle: PanelHandle | null) => {
      rawHandleRef.current = handle;
      const exposedHandle = handle ? taskNavigationAwareHandle(handle) : null;
      exposedHandleRef.current = exposedHandle;
      assignPanelRef(ref, exposedHandle);
    },
    [ref],
  );
  const persistentHandle = persistentHost?.handle;
  const persistentExposedHandle = useMemo(
    () => (persistentHandle ? taskNavigationAwareHandle(persistentHandle) : null),
    [persistentHandle],
  );

  const contains = useCallback(
    (target: Node) => {
      const panel = hasPersistentHost
        ? document.querySelector('[data-persistent-workspace-panel-viewport="true"] .workspace-sdk-panel')
        : document.querySelector(`.${panelClass}`);
      // Persistent Panel 位于 AppShell 稳定 viewport；ResizeHandle 仍由当前 ChatLayout 放在占位前。
      const resizeHandle = anchorRef.current?.previousElementSibling ?? panel?.previousElementSibling;
      return !!(panel?.contains(target) || resizeHandle?.contains(target));
    },
    [hasPersistentHost, panelClass],
  );
  const dismiss = useCallback(() => exposedHandleRef.current?.closePanel(), []);
  // 传 false 不订阅点外收起，仅保留 ref 显式关闭通道。
  useWorkspacePanelDismiss(open && dismissOnOutside, contains, dismiss);

  // 拖宽记忆:Root 注入的 dynamicWidth(ResizeHandle 拖拽输出百分比)+ containerWidth
  // 换算 px 落盘,任务 tab openTab 时读取恢复(见 panelWidthMemory 背景)。
  useEffect(() => {
    persistPanelWidthPercent(injected.dynamicWidth, injected.containerWidth);
  }, [injected.dynamicWidth, injected.containerWidth]);

  const presentPersistentPanel = persistentHost?.present;
  const dismissPersistentPanel = persistentHost?.dismiss;
  useLayoutEffect(() => {
    if (!presentPersistentPanel) return;
    presentPersistentPanel(ownerId, panelProps, shouldPreserveTaskPanelDuringNavigation());
  }, [ownerId, panelProps, presentPersistentPanel]);
  useLayoutEffect(
    () => () => {
      dismissPersistentPanel?.(ownerId, shouldPreserveTaskPanelDuringNavigation());
    },
    [dismissPersistentPanel, ownerId],
  );

  const ownsPersistentPanel = persistentHost?.activeOwnerId === ownerId;
  useLayoutEffect(() => {
    if (!persistentHandle || !persistentExposedHandle || !ownsPersistentPanel) return undefined;
    rawHandleRef.current = persistentHandle;
    exposedHandleRef.current = persistentExposedHandle;
    assignPanelRef(ref, persistentExposedHandle);
    panelRegistry?.register(persistentExposedHandle);
    return () => {
      if (rawHandleRef.current === persistentHandle) rawHandleRef.current = null;
      if (exposedHandleRef.current === persistentExposedHandle) exposedHandleRef.current = null;
      assignPanelRef(ref, null);
      panelRegistry?.register(null);
    };
  }, [ownsPersistentPanel, panelRegistry, persistentExposedHandle, persistentHandle, ref]);
  useLayoutEffect(() => {
    if (persistentHandle && ownsPersistentPanel) handleIsOpenChange(persistentHost.isOpen);
  }, [handleIsOpenChange, ownsPersistentPanel, persistentHandle, persistentHost?.isOpen]);

  useEffect(() => {
    // AppLayout 持有的 persistent panel 本身从未卸载，tab / TaskPanel 数据与滚动状态已经原地保留；
    // 仅无 Provider 的独立 ChatLayout（组件示例/测试）需要从快照重建 tab。
    if (persistentHost) return;
    const tab = taskNavigation?.tab;
    if (taskNavigation && tab && isTaskNavigationHydratedAtCurrentRoute(taskNavigation)) {
      rawHandleRef.current?.openTab(tab);
      // 保活信号不能在 openTab 后立即释放：目标会话的 provider/history effect 可能稍后才触发
      // closePanelForce。保留到显式收起或下一次普通路由切换，才能避免副屏“一闪而逝”。
    }
  }, [persistentHost, taskNavigation]);
  useEffect(
    () => () => {
      const store = useTaskConversationNavigationStore.getState();
      const navigation = store.navigation;
      // 离开任务导航目标（例如进入非会话页面）时没有新的 Panel 承接 closePanelForce，
      // 在旧插槽卸载阶段清理保活状态，避免以后返回同 URL 时意外重开 TaskPanel。
      if (navigation && !shouldPreserveTaskPanelDuringNavigation()) store.resetAll();
    },
    [],
  );

  if (persistentHost) {
    // 真实 Panel 固定渲染在 AppShell 的稳定 viewport；这里仅作为 ChatLayout 的透明右栏占位。
    // Root 自带的 ResizeHandle 仍紧邻此节点，容器宽度/拖拽百分比语义保持不变。
    const reservedWidth = persistentHost.isOpen ? persistentHost.panelWidth || fallbackPanelWidth : 0;
    const reservedWidthCss = typeof reservedWidth === 'number' ? `${reservedWidth}px` : reservedWidth;
    return (
      <div
        ref={anchorRef}
        aria-hidden="true"
        data-persistent-panel-slot={ownerId}
        style={{
          flex: `0 0 ${reservedWidthCss}`,
          width: reservedWidthCss,
          minWidth: 0,
          minHeight: 0,
          pointerEvents: 'none',
        }}
      />
    );
  }

  return <ChatLayoutPanelWithInjectedProps {...panelProps} ref={setLocalHandle} />;
});
// ChatLayoutRoot 以 displayName === 'ChatLayout.Panel' 识别副屏插槽（cloneElement 注入宽态与
// onIsOpenChange），故此处保留同名 displayName 才能被 Root 识别为插槽并完成注入。
ChatLayoutPanelSlot.displayName = 'ChatLayout.Panel';
