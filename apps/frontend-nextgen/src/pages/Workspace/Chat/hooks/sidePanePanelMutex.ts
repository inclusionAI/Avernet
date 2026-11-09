/**
 * useSidePanePanelMutex —— chat 头部右缘域 × 副屏的开合互斥（chat-header-panels）。
 *
 * 根因几何：副屏是 AppShell 层的悬浮覆盖层（PersistentChatLayoutPanelViewport：
 * absolute right-0 z-10、宽=副屏真实宽），而头部右缘面板（历史消息/会话管理/资源管理）
 * 是对话区右缘的常驻 aside——副屏展开后右缘一带整条被覆盖（面板让宽了却不可见，
 * 表现即「侧边栏空白」）。两域互斥编排：
 * - 面板即将展开（dismissSidePaneForPanels）：副屏在开则 closePanel 收起（tabs 保留）并记让位；
 * - 面板全部收起：openPanel 自动回开副屏（closePanel 不清 tabs，openPanel 不会被 SDK
 *   autoCloseWhenEmpty 弹回）——任务 tab 等无 Monitor 重开入口的会话（消息无 <AixUI>
 *   标记时 Monitor 判空禁用），给位后关面板也能恢复副屏；
 * - 副屏展开（onSidePaneIsOpenChange(true)，含消息渲染器「挂载即 openTab」与任务导航
 *   focusTab 的自动展开）：经 closePanels 收右缘域面板。
 * 让位标记随副屏自行展开（用户 Monitor 重开/任务导航）、会话切换（clearSidePaneExclusion）作废。
 */
import type { PanelHandle } from '@tc-chat/core';
import { useCallback, useEffect, useRef } from 'react';

export interface UseSidePanePanelMutexOptions {
  /** 副屏 handle（与 ChatLayoutPanelSlot panelRef 同源）。 */
  panelRef: React.RefObject<PanelHandle | null>;
  /** 副屏真实开合态镜像（宿主持有；onSidePaneIsOpenChange 同步回写）。 */
  sidePaneOpen: boolean;
  /** 头部右缘域是否有面板在开（右缘面板/资源抽屉任一；全收后触发让位回开）。 */
  panelsOpen: boolean;
  /** 副屏展开时收右缘域面板（右缘面板置 null + 收资源抽屉）。 */
  closePanels: () => void;
  /** 副屏真实开合同步回宿主镜像（setSidePaneOpen）。 */
  onMirrorChange: (open: boolean) => void;
}

export interface UseSidePanePanelMutexResult {
  /** 右缘域面板即将展开：副屏让位收起并记录回开标记。 */
  dismissSidePaneForPanels: () => void;
  /** 组合后的副屏真实态同步入口（接线 ChatLayoutPanelSlot onOpenStateChange）：镜像 + 互斥收面板。 */
  onSidePaneIsOpenChange: (open: boolean) => void;
  /** 会话切换等边界作废让位回开标记（副屏已 closePanelForce 清 tabs，回开会空弹）。 */
  clearSidePaneExclusion: () => void;
}

export function useSidePanePanelMutex(options: UseSidePanePanelMutexOptions): UseSidePanePanelMutexResult {
  const { panelRef, sidePaneOpen, panelsOpen, closePanels, onMirrorChange } = options;
  const excludedRef = useRef(false);

  const dismissSidePaneForPanels = useCallback(() => {
    // 面板容器未挂载（只读态/舞台切换中）不记位，避免回开打到空 handle。
    if (!sidePaneOpen || !panelRef.current) return;
    excludedRef.current = true;
    panelRef.current.closePanel();
  }, [panelRef, sidePaneOpen]);

  const clearSidePaneExclusion = useCallback(() => {
    excludedRef.current = false;
  }, []);

  const onSidePaneIsOpenChange = useCallback(
    (open: boolean) => {
      onMirrorChange(open);
      if (open) {
        excludedRef.current = false; // 副屏真实展开后让位标记作废，面板全收不再触发回开。
        closePanels();
      }
    },
    [closePanels, onMirrorChange],
  );

  // 让位回开：面板全收后恢复让位前的副屏。
  useEffect(() => {
    if (!excludedRef.current) return;
    // 副屏真实展开且右缘域已全收（用户 Monitor 重开/任务导航）：让位出口，仅清标记。
    // 注意 panelsOpen 同收判断——dismiss 后到 SDK 关态回声（onIsOpenChange(false)）镜像翻转前，
    // sidePaneOpen 短暂仍为 true，不能在面板展开窗口内误判为「副屏已自行展开」。
    if (sidePaneOpen && !panelsOpen) {
      excludedRef.current = false;
      return;
    }
    if (panelsOpen) return;
    excludedRef.current = false;
    panelRef.current?.openPanel();
  }, [panelsOpen, panelRef, sidePaneOpen]);

  return { dismissSidePaneForPanels, onSidePaneIsOpenChange, clearSidePaneExclusion };
}
