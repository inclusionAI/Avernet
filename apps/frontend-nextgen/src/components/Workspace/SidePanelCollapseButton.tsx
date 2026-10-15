/**
 * SidePanelCollapseButton —— 副屏顶栏右上角「收起」悬浮控制。
 *
 * SDK ChatLayout.Panel 无 panel 级右侧槽（TopBar 布局为 [slotLeft | tabs | per-tab slotRight]，
 * 消息驱动的 tab 也无法从 AixUI 标记携带 ReactNode 槽位），故以绝对定位浮层落位：
 * 挂宿主舞台容器右缘，垂直对齐副屏 TopBar（min-height 44px）中线。
 *
 * 可见性与 Monitor 图标同源（真实开合态，经 ChatLayoutPanelSlot 承接 SDK onIsOpenChange
 * 同步到 header-panels hooks）：仅开合态为「开」时由宿主条件渲染（含消息渲染器挂载即
 * openTab 的默认展开）；点按经上层 toggleSidePane 关闭。
 */
import { IconButton } from '@/components/ui';
import { cn } from '@/utils/cn';
import { PanelRightClose } from 'lucide-react';

export function SidePanelCollapseButton({
  onClick,
  className,
}: {
  onClick: () => void;
  className?: string;
}): JSX.Element {
  return (
    <IconButton
      label="收起副屏"
      icon={<PanelRightClose className="size-4" aria-hidden />}
      size="sm"
      onClick={onClick}
      data-workspace-panel-trigger
      className={cn('absolute right-1.5 top-2 z-20 rounded-md text-muted-foreground', className)}
    />
  );
}
