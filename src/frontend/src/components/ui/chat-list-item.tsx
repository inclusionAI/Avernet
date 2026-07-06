/**
 * ChatListItem - 通用会话列表行组件
 *
 * 覆盖三种场景：
 * - Assistant 对话列表（ConversationTab）：无头像，有 hover 操作菜单
 * - 单聊列表（PrivateChat）：Bot 头像 + 状态点，未读 badge
 * - 群聊列表（GroupChat）：群组图标，成员头像行，未读 badge
 */

import { cn } from '@/utils/utils';
import { Loader2, MoreHorizontal } from 'lucide-react';
import React, { useEffect, useRef, useState } from 'react';

export interface ActionItem {
  icon?: React.ReactNode;
  label: string;
  onClick: (e: React.MouseEvent) => void;
  /** danger 显示红色 */
  variant?: 'default' | 'danger';
  /** loading 状态 */
  loading?: boolean;
}

export interface ChatListItemProps {
  /** 左侧头像区域，传 null 则不占位 */
  avatar?: React.ReactNode;
  /** 主标题 */
  title: string;
  /** 标题下方的副标题（如创建者信息） */
  subtitle?: string;
  /** 右上角时间文字 */
  time?: string;
  /** 第二行预览文字 */
  preview?: string;
  /** 右下角 badge（未读数 / 消息条数等），传 0 或 null 则不显示 */
  badge?: number | string | null;
  /** badge 样式变体：red = 红色未读，neutral = 灰色数量 */
  badgeVariant?: 'red' | 'neutral';
  /** 右下角状态（字符串或自定义 ReactNode，如 SessionStatusTag） */
  status?: React.ReactNode;
  /** 是否选中 */
  selected?: boolean;
  /** 点击回调 */
  onClick?: () => void;
  /** hover 时显示 MoreHorizontal 按钮，点击展开 dropdown */
  actions?: ActionItem[];
  /** 仅有一个 action 时，点击 "..." 直接触发而不展开 dropdown */
  singleAction?: boolean;
  /** 第三行额外内容（如成员头像行） */
  extra?: React.ReactNode;
  /** title 右侧的内联标签（如"3人"） */
  titleTag?: React.ReactNode;
  className?: string;
  /** 埋点属性 */
  spm?: string;
  'data-aspm-param'?: string;
}

export const ChatListItem: React.FC<ChatListItemProps> = ({
  avatar,
  title,
  subtitle,
  time,
  preview,
  badge,
  badgeVariant = 'red',
  status,
  selected = false,
  onClick,
  actions,
  singleAction,
  extra,
  titleTag,
  className,
  spm: dataAspmClick,
  'data-aspm-param': dataAspmParam,
}) => {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  const hasBadge =
    badge !== null && badge !== undefined && badge !== 0 && badge !== '0';
  const hasActions = actions && actions.length > 0;

  const selectedBg = 'bg-slate-100 hover:bg-slate-100 border-lavender-400';
  const selectedTitle = 'text-slate-800 font-medium';

  // 检查是否有 action 正在 loading
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const hasLoadingAction = actions?.some((a) => a.loading) ?? false;

  // 点击外部关闭菜单
  useEffect(() => {
    if (!menuOpen) return;
    const handleClickOutside = (e: MouseEvent) => {
      if (
        menuRef.current &&
        !menuRef.current.contains(e.target as Node) &&
        triggerRef.current &&
        !triggerRef.current.contains(e.target as Node)
      ) {
        setMenuOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [menuOpen]);

  const handleTriggerClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (singleAction && actions && actions.length === 1) {
      actions[0].onClick(e);
      return;
    }
    setMenuOpen((v) => !v);
  };

  const handleActionClick = (action: ActionItem, e: React.MouseEvent) => {
    e.stopPropagation();
    setMenuOpen(false);
    action.onClick(e);
  };

  return (
    <div
      onClick={onClick}
      data-aspm-click={dataAspmClick}
      data-aspm-expo
      data-aspm-param={dataAspmParam}
      className={cn(
        'relative group flex items-start gap-2 pl-[14px] pr-4 py-3 cursor-pointer transition-all border-l-2',
        selected ? selectedBg : 'border-transparent hover:bg-slate-50',
        className,
      )}
    >
      {/* 头像区 */}
      {avatar !== null && avatar !== undefined && (
        <div className="flex-shrink-0">{avatar}</div>
      )}

      {/* 内容区 */}
      <div className="flex-1 min-w-0 overflow-hidden">
        {/* 第一行：标题 + 时间 */}
        <div className="flex items-center justify-between gap-2 mb-0.5">
          <div className="flex items-center gap-1.5 min-w-0 overflow-hidden">
            <span
              className={cn(
                'text-sm truncate',
                selected ? selectedTitle : 'text-slate-800',
              )}
            >
              {title}
            </span>
            {titleTag}
          </div>
          {time && (
            <span
              className={cn(
                'text-[10px] text-slate-400 flex-shrink-0 ml-1 transition-opacity',
                // 有 actions 时，hover 时时间淡出让位给 MoreHorizontal
                hasActions && 'group-hover:opacity-0',
              )}
            >
              {time}
            </span>
          )}
        </div>

        {/* 副标题（如创建者信息） */}
        {subtitle && (
          <div>
            <span className="text-[10px] text-slate-400">{subtitle}</span>
          </div>
        )}

        {/* 第二行：预览 + badge/status */}
        {((preview !== null && preview !== undefined) ||
          hasBadge ||
          status) && (
          <div className="flex items-center justify-between gap-2">
            <p className="text-xs mb-0 text-slate-400 truncate leading-relaxed">
              {preview || ''}
            </p>
            {status ? (
              // status 支持任意 ReactNode（字符串会按原灰色小字渲染；自定义节点直接放出）
              typeof status === 'string' ? (
                <span className="flex-shrink-0 ml-1 text-[10px] text-slate-400">
                  {status}
                </span>
              ) : (
                <div className="flex-shrink-0 ml-1">{status}</div>
              )
            ) : (
              hasBadge &&
              !menuOpen &&
              (badgeVariant === 'red' ? (
                <span className="flex-shrink-0 ml-1 min-w-[18px] h-[18px] px-1 flex items-center justify-center text-[10px] text-white bg-red-500 rounded-full">
                  {typeof badge === 'number' && badge > 99 ? '99+' : badge}
                </span>
              ) : (
                <span className="flex-shrink-0 ml-1 text-[10px] text-slate-400">
                  {badge}
                </span>
              ))
            )}
          </div>
        )}

        {/* 第三行：extra（如成员头像） */}
        {extra && <div className="mt-1.5">{extra}</div>}
      </div>

      {/* MoreHorizontal 触发器：hover 时出现，替换时间位置 */}
      {hasActions && (
        <div className="absolute right-2 top-2.5">
          <button
            type="button"
            ref={triggerRef}
            onClick={handleTriggerClick}
            data-aspm-click="ca114854.da193929"
            data-aspm-desc="ChatListItem-更多操作按钮"
            data-aspm-param={``}
            data-aspm-expo
            className={cn(
              'p-1 rounded-md text-slate-400 hover:text-slate-600 hover:bg-slate-100 transition-all',
              menuOpen ? 'opacity-100' : 'opacity-0 group-hover:opacity-100',
            )}
          >
            <MoreHorizontal className="w-4 h-4" />
          </button>

          {/* Dropdown 菜单 */}
          {menuOpen && (
            <div
              ref={menuRef}
              className="absolute right-0 top-full mt-1 bg-white rounded-lg shadow-lg border border-slate-200 py-1 z-50 min-w-[120px]"
            >
              {actions!.map((action, idx) => (
                <button
                  type="button"
                  key={idx}
                  onClick={(e) => handleActionClick(action, e)}
                  disabled={action.loading}
                  data-aspm-click="ca114854.da193930"
                  data-aspm-desc="ChatListItem-操作项"
                  data-aspm-param={``}
                  data-aspm-expo
                  className={cn(
                    'w-full flex items-center gap-2 px-3 py-2 text-xs transition-colors text-left',
                    action.variant === 'danger'
                      ? 'text-red-500 hover:bg-red-50'
                      : 'text-slate-600 hover:bg-slate-50',
                    action.loading && 'opacity-50 cursor-not-allowed',
                  )}
                >
                  {action.loading ? (
                    <Loader2 className="w-3.5 h-3.5 flex-shrink-0 animate-spin" />
                  ) : action.icon ? (
                    <span className="w-3.5 h-3.5 flex-shrink-0">
                      {action.icon}
                    </span>
                  ) : null}
                  {action.label}
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default ChatListItem;
