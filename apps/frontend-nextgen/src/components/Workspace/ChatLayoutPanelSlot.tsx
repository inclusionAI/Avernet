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
 * 收起策略 dismissOnOutside（默认 true，单聊侧口径）为私有 prop：false 时不订阅点外
 * 收起、仅显式信号关闭；转发 SDK 前剥离。
 */
import { useWorkspacePanelDismiss } from '@/hooks/useWorkspacePanelDismiss';
import { persistPanelWidthPercent } from '@/services/workspace/panelWidthMemory';
import type { PanelHandle } from '@tc-chat/core';
import { ChatLayout } from '@tc-chat/ui/es/ChatLayout';
import type { ChatLayoutPanelProps } from '@tc-chat/ui/es/ChatLayout/types';
import { forwardRef, useCallback, useEffect, useId, useRef, useState } from 'react';
import { WORKSPACE_RIGHT_PANEL_LAYOUT } from './workspacePanelLayout';

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

export const ChatLayoutPanelSlot = forwardRef<PanelHandle, ChatLayoutPanelSlotProps>(function ChatLayoutPanelSlot(
  props,
  ref,
) {
  const { onOpenStateChange, dismissOnOutside = true, ...rest } = props;
  const handleRef = useRef<PanelHandle | null>(null);
  const panelClass = `workspace-sdk-panel-${useId().replace(/:/g, '')}`;
  const [open, setOpen] = useState(props.defaultOpen ?? false);
  const setHandle = useCallback(
    (handle: PanelHandle | null) => {
      handleRef.current = handle;
      if (typeof ref === 'function') ref(handle);
      else if (ref) ref.current = handle;
    },
    [ref],
  );
  const contains = useCallback(
    (target: Node) => {
      const panel = document.querySelector(`.${panelClass}`);
      // SDK Root 将拖宽条紧邻放在 Panel 前，不能增加外层 div 破坏宽度/插槽识别。
      return !!(panel?.contains(target) || panel?.previousElementSibling?.contains(target));
    },
    [panelClass],
  );
  const dismiss = useCallback(() => handleRef.current?.closePanel(), []);
  // 传 false 不订阅点外收起，仅保留 ref 显式关闭通道。
  useWorkspacePanelDismiss(open && dismissOnOutside, contains, dismiss);
  const injected = rest as ChatLayoutPanelProps & ChatLayoutPanelInjectedProps;
  // 拖宽记忆:Root 注入的 dynamicWidth(ResizeHandle 拖拽输出百分比)+ containerWidth
  // 换算 px 落盘,任务 tab openTab 时读取恢复(见 panelWidthMemory 背景)。
  useEffect(() => {
    persistPanelWidthPercent(injected.dynamicWidth, injected.containerWidth);
  }, [injected.dynamicWidth, injected.containerWidth]);
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
  return (
    <ChatLayoutPanelWithInjectedProps
      {...injected}
      panelWidth={injected.panelWidth ?? WORKSPACE_RIGHT_PANEL_LAYOUT.defaultWidth}
      ref={setHandle}
      className={[injected.className, 'workspace-sdk-panel', panelClass].filter(Boolean).join(' ')}
      onIsOpenChange={handleIsOpenChange}
    />
  );
});
// ChatLayoutRoot 以 displayName === 'ChatLayout.Panel' 识别副屏插槽（cloneElement 注入宽态与
// onIsOpenChange），故此处保留同名 displayName 才能被 Root 识别为插槽并完成注入。
ChatLayoutPanelSlot.displayName = 'ChatLayout.Panel';
