/**
 * ChatHeaderActions —— 对话页头部五图标列（chat-header-panels，dmore 实测语义）。
 *
 * 收藏会话(star 动作开关) / 会话管理(→会话详情面板) / 历史消息(→历史+搜索面板) /
 * 资源管理(→会话文件副屏) / 副屏(taskPanel 开合)。28×28 ghost、4px 间距；
 * 面板互斥由 useChatHeaderPanels 编排，本组件只呈现状态与转发回调。
 */
import { IconButton } from '@/components/ui';
import { cn } from '@/utils/cn';
import { FolderOpen, History, Monitor, SlidersHorizontal, Star } from 'lucide-react';
import type { ReactNode } from 'react';
import type { ChatHeaderFavoritesModel, ChatHeaderPanelKind } from '../hooks/useChatHeaderPanels';

export interface ChatHeaderActionsProps {
  favorites: ChatHeaderFavoritesModel;
  openPanel: ChatHeaderPanelKind | null;
  onTogglePanel: (kind: ChatHeaderPanelKind) => void;
  fileDrawerOpen: boolean;
  onToggleFileDrawer: () => void;
  sidePaneOpen: boolean;
  onToggleSidePane: () => void;
  className?: string;
}

/** 28×28 ghost 图标钮；激活态以 Container 底色表达（dmore: icon 14px #71717A 默认）。 */
function HeaderIcon({
  label,
  icon,
  active,
  onClick,
  disabled,
}: {
  label: string;
  icon: ReactNode;
  active: boolean;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <IconButton
      label={label}
      icon={icon}
      size="sm"
      disabled={disabled}
      onClick={onClick}
      aria-pressed={active}
      className={cn('rounded-md text-muted-foreground', active && 'bg-selected text-foreground')}
    />
  );
}

export function ChatHeaderActions({
  favorites,
  openPanel,
  onTogglePanel,
  fileDrawerOpen,
  onToggleFileDrawer,
  sidePaneOpen,
  onToggleSidePane,
  className,
}: ChatHeaderActionsProps) {
  return (
    <div className={cn('flex items-center gap-1', className)}>
      <HeaderIcon
        label={favorites.favorite ? '取消收藏当前会话' : '收藏当前会话'}
        icon={<Star className={cn('size-4', favorites.favorite && 'fill-star text-star')} />}
        active={favorites.favorite}
        onClick={favorites.onToggle}
        disabled={favorites.disabled}
      />
      <HeaderIcon
        label="会话管理"
        icon={<SlidersHorizontal className="size-4" />}
        active={openPanel === 'detail'}
        onClick={() => onTogglePanel('detail')}
      />
      <HeaderIcon
        label="历史消息"
        icon={<History className="size-4" />}
        active={openPanel === 'history'}
        onClick={() => onTogglePanel('history')}
      />
      <HeaderIcon
        label="资源管理"
        icon={<FolderOpen className="size-4" />}
        active={fileDrawerOpen}
        onClick={onToggleFileDrawer}
      />
      <HeaderIcon label="副屏" icon={<Monitor className="size-4" />} active={sidePaneOpen} onClick={onToggleSidePane} />
    </div>
  );
}
