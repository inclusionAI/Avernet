/**
 * PersistentChatLayoutPanelHost —— 跨 Workspace 子路由复用同一个 SDK 副屏实例。
 *
 * /workspace/chat 与 /workspace/collaboration 是两个独立路由。真实 ChatLayout.Panel 若挂在
 * 路由内，切换时必然随 Outlet 卸载；即便把它 portal 到新插槽，旧插槽与新插槽之间也会
 * 存在 DOM 暂时脱离 document 的过渡帧。
 *
 * Provider 因此把真实 Panel 固定挂在 AppShell 的稳定 viewport 中。路由内
 * ChatLayoutPanelSlot 只负责注册当前会话的 bridge/action，并给 ChatLayout 留出透明占位。
 * 任务节点导航期间不仅复用 Panel 实例与 DOM，还冻结它的渲染 props；会话 bridge/action
 * 通过稳定代理切换到新路由，避免路由提交触发 TaskPanel 内容树重新 render。
 */
import { useTaskConversationNavigationStore } from '@/stores/taskConversationNavigationStore';
import type { BridgeHandler, ChatBridge, PanelHandle } from '@tc-chat/core';
import { ChatLayout } from '@tc-chat/ui/es/ChatLayout';
import type { ChatLayoutPanelProps } from '@tc-chat/ui/es/ChatLayout/types';
import React, {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';

import {
  callBridgeMethod,
  emptyPanelContext,
  initialPanelWidth,
  shallowEqualPanelProps,
  type PanelPresentation,
  type PersistentChatLayoutPanelProps,
  type PersistentPanelHostState,
} from './persistentChatLayoutPanelHostUtils';

export type { PersistentChatLayoutPanelProps } from './persistentChatLayoutPanelHostUtils';

type PersistentChatLayoutPanelHostValue = {
  handle: PanelHandle;
  activeOwnerId: string | null;
  isOpen: boolean;
  panelWidth: number;
  panel: ReactNode;
  present: (ownerId: string, props: PersistentChatLayoutPanelProps, preserveExisting: boolean) => void;
  dismiss: (ownerId: string, preserve: boolean) => void;
  closePanel: () => void;
  reportPanelWidth: (width: number) => void;
};

const PersistentChatLayoutPanelHostContext = createContext<PersistentChatLayoutPanelHostValue | null>(null);

const PersistentPanel = ChatLayout.Panel as unknown as React.ComponentType<
  PersistentChatLayoutPanelProps & React.RefAttributes<PanelHandle>
>;

export function PersistentChatLayoutPanelProvider({ children }: { children: ReactNode }): JSX.Element {
  const [state, setState] = useState<PersistentPanelHostState>({
    presentation: null,
    instanceId: 0,
    isOpen: false,
    panelWidth: 0,
  });
  const activeOwnerRef = useRef<string | null>(null);
  const latestPropsRef = useRef<PersistentChatLayoutPanelProps | null>(null);
  const currentBridgeRef = useRef<ChatBridge | undefined>(undefined);
  const panelHandlersRef = useRef(new Map<string, BridgeHandler>());
  const rawHandleRef = useRef<PanelHandle | null>(null);

  const syncBridge = useCallback((nextBridge: ChatBridge | undefined) => {
    const previousBridge = currentBridgeRef.current;
    if (previousBridge === nextBridge) return;
    panelHandlersRef.current.forEach((_handler, name) => {
      callBridgeMethod(previousBridge, 'unregisterHandler', name);
    });
    currentBridgeRef.current = nextBridge;
    panelHandlersRef.current.forEach((handler, name) => {
      callBridgeMethod(nextBridge, 'registerHandler', name, handler);
    });
  }, []);

  // SDK Panel 只在挂载时向 bridge 注册命令。这里给它一个永久稳定的代理，并在目标会话
  // 改变时把已注册命令迁移到新 bridge；Panel 无需因 bridge identity 改变而重新 render。
  const stableBridge = useMemo(
    () =>
      new Proxy({} as ChatBridge, {
        get: (_target, property) => {
          if (property === 'registerHandler') {
            return (name: string, handler: BridgeHandler) => {
              panelHandlersRef.current.set(name, handler);
              callBridgeMethod(currentBridgeRef.current, 'registerHandler', name, handler);
            };
          }
          if (property === 'unregisterHandler') {
            return (name: string) => {
              panelHandlersRef.current.delete(name);
              callBridgeMethod(currentBridgeRef.current, 'unregisterHandler', name);
            };
          }
          const bridge = currentBridgeRef.current;
          if (!bridge || typeof bridge !== 'object') return undefined;
          const value = Reflect.get(bridge, property, bridge) as unknown;
          return typeof value === 'function' ? value.bind(bridge) : value;
        },
      }),
    [],
  );

  const captureHandle = useCallback((handle: PanelHandle | null) => {
    // SDK 每次 render 都可能生成新的 imperative handle；只写 ref，避免 callback ref → setState 渲染环。
    rawHandleRef.current = handle;
  }, []);

  const stableHandle = useMemo<PanelHandle>(
    () => ({
      openTab: (tab) => rawHandleRef.current?.openTab(tab),
      closeTab: (id) => rawHandleRef.current?.closeTab(id),
      updateTab: (id, update) => rawHandleRef.current?.updateTab(id, update),
      focusTab: (id) => rawHandleRef.current?.focusTab(id),
      openPanel: () => rawHandleRef.current?.openPanel(),
      closePanel: () => rawHandleRef.current?.closePanel(),
      closePanelForce: () => rawHandleRef.current?.closePanelForce(),
      flushContext: () => rawHandleRef.current?.flushContext() ?? emptyPanelContext(),
      flushInteractions: () => rawHandleRef.current?.flushInteractions() ?? [],
    }),
    [],
  );

  const handleIsOpenChange = useCallback((isOpen: boolean) => {
    setState((current) => (current.isOpen === isOpen ? current : { ...current, isOpen }));
    latestPropsRef.current?.onIsOpenChange?.(isOpen);
  }, []);
  const handleAction = useCallback<NonNullable<ChatLayoutPanelProps['onAction']>>((action) => {
    latestPropsRef.current?.onAction?.(action);
  }, []);
  const handleTabClose = useCallback<NonNullable<ChatLayoutPanelProps['onTabClose']>>((tabId) => {
    latestPropsRef.current?.onTabClose?.(tabId);
  }, []);

  const buildRenderProps = useCallback(
    (props: PersistentChatLayoutPanelProps): PersistentChatLayoutPanelProps => ({
      ...props,
      bridge: stableBridge,
      onAction: handleAction,
      onTabClose: handleTabClose,
      onIsOpenChange: handleIsOpenChange,
    }),
    [handleAction, handleIsOpenChange, handleTabClose, stableBridge],
  );

  const present = useCallback<PersistentChatLayoutPanelHostValue['present']>(
    (ownerId, props, preserveExisting) => {
      // 先切换命令代理，再提交 React 状态；layout effect 阶段完成，浏览器绘制前已指向目标会话。
      activeOwnerRef.current = ownerId;
      latestPropsRef.current = props;
      syncBridge(props.bridge);
      const candidateProps = buildRenderProps(props);

      setState((current) => {
        const previous = current.presentation;
        const sameOwner = previous?.ownerId === ownerId;
        const reuseRetainedInstance = Boolean(previous && preserveExisting);
        const reuseInstance = sameOwner || reuseRetainedInstance;

        let renderProps = candidateProps;
        if (reuseRetainedInstance && previous) {
          // 任务联动切链时冻结真实 Panel 的 props，保证 TaskPanel 内容树不发生一次路由驱动 render。
          // 目标路由后续真的发生拖宽时才同步 dynamicWidth/containerWidth；这是用户交互，不是切链闪动。
          const resizedAfterHandoff =
            sameOwner &&
            previous.retained &&
            candidateProps.dynamicWidth !== null &&
            candidateProps.dynamicWidth !== undefined &&
            candidateProps.dynamicWidth !== previous.props.dynamicWidth;
          renderProps = resizedAfterHandoff
            ? {
                ...previous.props,
                dynamicWidth: candidateProps.dynamicWidth,
                containerWidth: candidateProps.containerWidth,
              }
            : previous.props;
        }

        const nextPresentation: PanelPresentation = {
          ownerId,
          props: renderProps,
          retained: reuseRetainedInstance,
        };
        if (
          previous &&
          reuseInstance &&
          previous.retained === nextPresentation.retained &&
          shallowEqualPanelProps(previous.props, renderProps)
        ) {
          // ownerId 仍需从过渡态 null 迁到目标插槽，否则 ref/PanelRegistry 无法接管。
          return previous.ownerId === ownerId ? current : { ...current, presentation: nextPresentation };
        }

        return {
          presentation: nextPresentation,
          instanceId: previous && !reuseInstance ? current.instanceId + 1 : current.instanceId,
          isOpen: reuseInstance ? current.isOpen : props.defaultOpen ?? false,
          panelWidth: reuseInstance ? current.panelWidth : initialPanelWidth(props),
        };
      });
    },
    [buildRenderProps, syncBridge],
  );

  const dismiss = useCallback<PersistentChatLayoutPanelHostValue['dismiss']>(
    (ownerId, preserve) => {
      // 目标插槽可能已在同一次 commit 中接管。旧插槽迟到的 cleanup 不能撤销新 bridge/owner。
      if (activeOwnerRef.current !== ownerId) return;
      activeOwnerRef.current = null;
      if (!preserve) {
        latestPropsRef.current = null;
        syncBridge(undefined);
      }
      setState((current) => {
        const presentation = current.presentation;
        if (!presentation || presentation.ownerId !== ownerId) return current;
        if (preserve) {
          return {
            ...current,
            presentation: { ...presentation, ownerId: null, retained: true },
          };
        }
        return {
          presentation: null,
          instanceId: current.instanceId + 1,
          isOpen: false,
          panelWidth: 0,
        };
      });
    },
    [syncBridge],
  );

  const reportPanelWidth = useCallback((width: number) => {
    if (!Number.isFinite(width) || width <= 0) return;
    setState((current) =>
      Math.abs(current.panelWidth - width) < 0.5 ? current : { ...current, panelWidth: width },
    );
  }, []);

  const closePanel = useCallback(() => {
    const store = useTaskConversationNavigationStore.getState();
    if (store.navigation) store.completeNavigation(store.navigation.id);
    rawHandleRef.current?.closePanel();
  }, []);

  const panelProps = state.presentation?.props;
  const panel = useMemo(
    () =>
      panelProps ? (
        <PersistentPanel
          key={state.instanceId}
          {...panelProps}
          ref={captureHandle}
          onIsOpenChange={handleIsOpenChange}
        />
      ) : null,
    [captureHandle, handleIsOpenChange, panelProps, state.instanceId],
  );
  const hostValue = useMemo<PersistentChatLayoutPanelHostValue>(
    () => ({
      handle: stableHandle,
      activeOwnerId: state.presentation?.ownerId ?? null,
      isOpen: state.isOpen,
      panelWidth: state.panelWidth,
      panel,
      present,
      dismiss,
      closePanel,
      reportPanelWidth,
    }),
    [
      closePanel,
      dismiss,
      panel,
      present,
      reportPanelWidth,
      stableHandle,
      state.isOpen,
      state.panelWidth,
      state.presentation?.ownerId,
    ],
  );

  return (
    <PersistentChatLayoutPanelHostContext.Provider value={hostValue}>
      {children}
    </PersistentChatLayoutPanelHostContext.Provider>
  );
}

export function usePersistentChatLayoutPanelHost(): PersistentChatLayoutPanelHostValue | null {
  return useContext(PersistentChatLayoutPanelHostContext);
}
