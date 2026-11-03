import { IconButton } from '@/components/ui';
import { PanelRightClose } from 'lucide-react';
import { useLayoutEffect, useRef } from 'react';
import { usePersistentChatLayoutPanelHost } from './PersistentChatLayoutPanelHost';

/**
 * AppShell 内的稳定副屏画布。该节点不属于 Outlet，因此切换 Bot/群会话路由时不会卸载、
 * 移动或重新挂接真实 Panel DOM。
 */
export function PersistentChatLayoutPanelViewport(): JSX.Element | null {
  const host = usePersistentChatLayoutPanelHost();
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const { panel, reportPanelWidth } = host ?? {};

  useLayoutEffect(() => {
    const viewport = viewportRef.current;
    const panelElement = viewport?.firstElementChild;
    if (!(panelElement instanceof HTMLElement) || !reportPanelWidth) return undefined;

    const updateWidth = () => reportPanelWidth(panelElement.getBoundingClientRect().width);
    updateWidth();
    if (typeof ResizeObserver === 'undefined') return undefined;
    const observer = new ResizeObserver(updateWidth);
    observer.observe(panelElement);
    return () => observer.disconnect();
  }, [panel, reportPanelWidth]);

  if (!host || !host.panel) return null;
  const visibleWidth = host.isOpen ? host.panelWidth : 0;

  return (
    <div
      ref={viewportRef}
      data-persistent-workspace-panel-viewport="true"
      className="absolute inset-y-0 right-0 z-10 flex min-h-0 justify-end overflow-visible"
      style={{ width: visibleWidth }}
    >
      {host.panel}
      {host.isOpen ? (
        <IconButton
          label="收起副屏"
          icon={<PanelRightClose className="size-4" aria-hidden />}
          size="sm"
          onClick={host.closePanel}
          data-workspace-panel-trigger
          className="absolute right-1.5 top-2 z-20 rounded-md text-muted-foreground"
        />
      ) : null}
    </div>
  );
}
