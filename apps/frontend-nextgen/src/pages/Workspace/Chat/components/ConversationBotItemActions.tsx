// Bot 行 hover 操作区:新建会话 / 会话ID检索 / 归属筛选 / 「更多操作」菜单(含「Bot 管理」入口)。
// 2026-10-21 TC-G005 门禁拆分:自 ConversationBotItem 原样迁出,不改任何 UI/行为;
// moreOpen 菜单开关随本组件内聚,检索开关(searchInputOpen)留在父级(searchInputVisible 计算依赖)。
import type { ConversationOrigin, ConversationSessionScope } from '@/domain/conversation/types';
import { Button, IconButton } from '@/components/ui';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/Popover';
import type { ChatBotView } from '@/services/workspace/botSessionService';
import { cn } from '@/utils/cn';
import { Bot, MoreHorizontal, Plus, Search } from 'lucide-react';
import React from 'react';
import { ConversationOriginFilter } from './ConversationOriginFilter';

export interface ConversationBotItemActionsProps {
  /** 他人发起只读视角:新建与「更多操作」入口不渲染(检索/筛选保留)。 */
  isOthers: boolean;
  /** Bot 非可用态(FAILED/PENDING):会话类入口不渲染;「更多操作」入口常驻显示(2026-10-21 运维直达)。 */
  unavailable?: boolean;
  /** 展开或任一筛选/检索激活:操作区钉住可见(仅可用 Bot 生效)。 */
  actionsPinned: boolean;
  /** 检索入口可见(仅 all 范围)。 */
  searchVisible: boolean;
  /** 检索按钮激活圆点(检索词或实例过滤生效)。 */
  searchMarked: boolean;
  /** 筛选入口可见(managed 或可收藏好友 Bot)。 */
  showFilter: boolean;
  /** 归属筛选支持(managed 才支持归属切换)。 */
  supportsOrigin: boolean;
  /** 收藏能力(好友 Bot 范围筛选支持)。 */
  canFavorite: boolean;
  /** 团队组禁用「他人发起」归属项。 */
  othersDisabled: boolean;
  bot: ChatBotView;
  origin: ConversationOrigin;
  scope: ConversationSessionScope;
  onOriginChange(origin: ConversationOrigin): void;
  onScopeChange(scope: ConversationSessionScope): void;
  onCreateSession(): void;
  /** 检索按钮开关:展开联动与输入框开关态由父级承载(handleSearchToggle)。 */
  onSearchToggle(): void;
  /** 「Bot 管理」菜单项回调:好友 Bot 不传(菜单项不渲染);bot.spaceId 有值才显示菜单项。 */
  onOpenBotManagement?(bot: ChatBotView): void;
}

/** Bot 行 hover 操作区:显隐由行容器的 group-hover/聚焦/钉住类驱动(相对行绝对定位)。 */
export function ConversationBotItemActions(props: ConversationBotItemActionsProps) {
  const {
    isOthers,
    unavailable,
    actionsPinned,
    searchVisible,
    searchMarked,
    showFilter,
    supportsOrigin,
    canFavorite,
    othersDisabled,
    bot,
    origin,
    scope,
    onOriginChange,
    onScopeChange,
    onCreateSession,
    onSearchToggle,
    onOpenBotManagement,
  } = props;
  // 「更多操作」菜单开关(随本组件内聚;菜单项点击后关闭)。
  const [moreOpen, setMoreOpen] = React.useState(false);
  return (
    <div
      className={cn(
        'absolute top-1/2 right-2 z-10 flex -translate-y-1/2 items-center gap-0.5 pl-5',
        'mask-[linear-gradient(to_right,transparent,black_20px)]',
        // 遮盖底撤除:按钮 hover 自带 muted 底,操作区不再叠一层与行底不一致的底色;
        // right-2 贴行右缘(2026-10-20 设计稿还原:原行尾箭头位改由「更多操作」占位 icon 常居末位,
        // 按钮间保持 gap-0.5 一致的 2px 间隙,末位占位与行内边距同宽 8px)。
        'opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100 [@media(hover:none)]:opacity-100',
        // 展开或任一筛选(归属/范围/会话ID检索)激活时钉住可见;
        // 生效中的检索在 Bot 收起时仍可见(🔍 按钮激活态圆点)。
        actionsPinned && 'opacity-100',
        // 不可用 Bot:「更多操作」入口常驻显示,不随 hover 变化(2026-10-21 运维直达)。
        unavailable && 'opacity-100',
      )}
    >
      {/* 会话类入口(新建/检索/筛选)仅可用 Bot 渲染:不可用 Bot 的会话操作无意义(2026-10-21)。 */}
      {!unavailable && !isOthers && (
        <IconButton
          label="新建会话"
          size="sm"
          icon={<Plus className="h-3.5 w-3.5" />}
          className="h-6 w-6 rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
          onClick={(event) => {
            event.stopPropagation();
            onCreateSession();
          }}
        />
      )}
      {!unavailable && searchVisible && (
        <IconButton
          label="会话ID检索"
          size="sm"
          icon={
            <span className="relative flex items-center justify-center">
              <Search className="h-3.5 w-3.5" aria-hidden="true" />
              {searchMarked && (
                <span
                  className="absolute -right-0.5 -top-0.5 h-1.5 w-1.5 rounded-full bg-primary"
                  aria-hidden="true"
                />
              )}
            </span>
          }
          className={cn(
            'h-6 w-6 rounded-md hover:bg-muted',
            searchMarked ? 'text-primary hover:text-primary' : 'text-muted-foreground hover:text-foreground',
          )}
          onClick={(event) => {
            event.stopPropagation();
            onSearchToggle();
          }}
        />
      )}
      {!unavailable && showFilter && (
        <ConversationOriginFilter
          botId={bot.botId}
          origin={origin}
          scope={scope}
          supportsOrigin={supportsOrigin}
          supportsFavorites={canFavorite}
          othersDisabled={othersDisabled}
          onOriginChange={onOriginChange}
          onScopeChange={onScopeChange}
        />
      )}
      {/* 「更多操作」入口(2026-10-20 设计稿还原):hover 随操作区显隐,常居操作区末位;
          统一"有菜单项才显示入口"(2026-10-21 验收收紧,取消可用 Bot 空壳占位):
          有「Bot 管理」菜单项(回调+空间信息)才渲染,好友 Bot/空间信息缺失/只读视角不显示;
          不可用 Bot 入口常驻(见容器显隐)。 */}
      {!isOthers && onOpenBotManagement && bot.spaceId && (
        <Popover open={moreOpen} onOpenChange={setMoreOpen}>
          <PopoverTrigger asChild>
            <IconButton
              label="更多操作"
              size="sm"
              icon={<MoreHorizontal className="h-3.5 w-3.5" />}
              className="h-6 w-6 rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
            />
          </PopoverTrigger>
          <PopoverContent align="end" className="w-44 p-1">
            {onOpenBotManagement && bot.spaceId && (
              <Button
                variant="ghost"
                className="h-auto w-full justify-start gap-2 px-2 py-2 text-xs"
                onClick={() => {
                  setMoreOpen(false);
                  onOpenBotManagement(bot);
                }}
              >
                <Bot className="h-3.5 w-3.5" />
                Bot 管理
              </Button>
            )}
          </PopoverContent>
        </Popover>
      )}
    </div>
  );
}