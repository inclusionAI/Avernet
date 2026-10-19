import { useEffect } from 'react';

/** 与会话管理一致：点击外部收起，入口 toggle 和 Portal 浮层继续自行处理交互。 */
export function useWorkspacePanelDismiss(
  enabled: boolean,
  contains: (target: Node) => boolean,
  onClose: (() => void) | undefined,
) {
  useEffect(() => {
    if (!enabled || !onClose) return;
    const handlePointerDown = (event: PointerEvent) => {
      if (!(event.target instanceof Node)) return;
      const target = event.target instanceof Element ? event.target : event.target.parentElement;
      if (
        contains(event.target) ||
        target?.closest(
          '[data-workspace-panel-trigger], [data-manage-panel-trigger], [role="dialog"], [role="alertdialog"], [role="menu"], [role="listbox"], [data-radix-popper-content-wrapper]',
        )
      )
        return;
      onClose();
    };
    document.addEventListener('pointerdown', handlePointerDown);
    return () => document.removeEventListener('pointerdown', handlePointerDown);
  }, [enabled, contains, onClose]);
}
