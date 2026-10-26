import type { BridgeHandler, ChatBridge, PanelHandle } from '@tc-chat/core';
import type { ChatLayoutPanelProps } from '@tc-chat/ui/es/ChatLayout/types';

export type PersistentChatLayoutPanelProps = ChatLayoutPanelProps & {
  dynamicWidth?: string | null;
  containerWidth?: number | null;
  onIsOpenChange?: (isOpen: boolean) => void;
};

export type PanelPresentation = {
  ownerId: string | null;
  props: PersistentChatLayoutPanelProps;
  retained: boolean;
};

export type PersistentPanelHostState = {
  presentation: PanelPresentation | null;
  instanceId: number;
  isOpen: boolean;
  panelWidth: number;
};

export function shallowEqualPanelProps(
  left: PersistentChatLayoutPanelProps,
  right: PersistentChatLayoutPanelProps,
): boolean {
  const leftKeys = Object.keys(left) as Array<keyof PersistentChatLayoutPanelProps>;
  const rightKeys = Object.keys(right) as Array<keyof PersistentChatLayoutPanelProps>;
  return leftKeys.length === rightKeys.length && leftKeys.every((key) => left[key] === right[key]);
}

export function emptyPanelContext(): ReturnType<PanelHandle['flushContext']> {
  return { interactions: [], panelState: { tabs: [], activeTabId: null } };
}

export function initialPanelWidth(props: PersistentChatLayoutPanelProps): number {
  if (typeof props.panelWidth === 'number' && Number.isFinite(props.panelWidth) && props.panelWidth > 0) {
    return props.panelWidth;
  }
  if (typeof props.panelWidth === 'string') {
    const value = props.panelWidth.trim();
    const pxMatch = value.match(/^(\d+(?:\.\d+)?)px$/i);
    if (pxMatch) return Number(pxMatch[1]);
    const percentMatch = value.match(/^(\d+(?:\.\d+)?)%$/);
    if (percentMatch && props.containerWidth && props.containerWidth > 0) {
      return (Number(percentMatch[1]) / 100) * props.containerWidth;
    }
  }
  return 0;
}

export function callBridgeMethod(
  bridge: ChatBridge | undefined,
  method: 'registerHandler' | 'unregisterHandler',
  name: string,
  handler?: BridgeHandler,
): void {
  if (!bridge || typeof bridge !== 'object') return;
  if (method === 'registerHandler') {
    if (handler && typeof bridge.registerHandler === 'function') bridge.registerHandler(name, handler);
    return;
  }
  if (typeof bridge.unregisterHandler === 'function') bridge.unregisterHandler(name);
}
