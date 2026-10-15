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
 */
import type { PanelHandle } from '@tc-chat/core';
import { ChatLayout } from '@tc-chat/ui/es/ChatLayout';
import type { ChatLayoutPanelProps } from '@tc-chat/ui/es/ChatLayout/types';
import { forwardRef, useCallback } from 'react';

/** Root cloneElement 注入的内部 props（公开类型未声明，运行时存在）。 */
type ChatLayoutPanelInjectedProps = {
  dynamicWidth?: string | null;
  containerWidth?: number | null;
  onIsOpenChange?: (isOpen: boolean) => void;
};

export type ChatLayoutPanelSlotProps = ChatLayoutPanelProps & {
  /** App 侧真实开合态观察口（含默认挂载 openTab 自动展开，非仅用户点按路径）。 */
  onOpenStateChange?: (isOpen: boolean) => void;
};

// 公开类型缺 onIsOpenChange 声明，运行时由 Root 注入并由本组件组合分发。
const ChatLayoutPanelWithInjectedProps = ChatLayout.Panel as unknown as React.ComponentType<
  ChatLayoutPanelProps & ChatLayoutPanelInjectedProps & React.RefAttributes<PanelHandle>
>;

export const ChatLayoutPanelSlot = forwardRef<PanelHandle, ChatLayoutPanelSlotProps>(
  function ChatLayoutPanelSlot(props, ref) {
    const { onOpenStateChange, ...rest } = props;
    const injected = rest as ChatLayoutPanelProps & ChatLayoutPanelInjectedProps;
    const rootIsOpenChange = injected.onIsOpenChange;
    const handleIsOpenChange = useCallback(
      (isOpen: boolean) => {
        // Root 注入的 setter 原样透传（ResizeHandle 开合镜像不受影响）。
        rootIsOpenChange?.(isOpen);
        onOpenStateChange?.(isOpen);
      },
      [rootIsOpenChange, onOpenStateChange],
    );
    return <ChatLayoutPanelWithInjectedProps {...injected} ref={ref} onIsOpenChange={handleIsOpenChange} />;
  },
);
// ChatLayoutRoot 以 displayName === 'ChatLayout.Panel' 识别副屏插槽（cloneElement 注入宽态与
// onIsOpenChange），故此处保留同名 displayName 才能被 Root 识别为插槽并完成注入。
ChatLayoutPanelSlot.displayName = 'ChatLayout.Panel';