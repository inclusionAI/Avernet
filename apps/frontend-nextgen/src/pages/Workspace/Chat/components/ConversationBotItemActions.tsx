// Bot 行 hover 操作区:新建会话 / 会话ID检索 / 归属筛选 / 「更多操作」菜单(含「Bot 管理」入口)。
// 恢复自 #2990(对话菜单 UX 与 Bot 状态感知,被 10-22 品牌蓝精修临时摘除),适配 10-22 设计基座:
// - TC-G005 门禁拆分:自 ConversationBotItem 迁出,不改任何既有 UI/行为;
// - 「更多操作」菜单开关随本组件内聚,检索开关(searchInputOpen)留在父级(显隐计算依赖);
// - 不可用 Bot(FAILED/PENDING)的「更多操作」常驻显示(2026-10-21 运维直达),菜单仅「Bot 管理」;
// - 菜单打开期间经触发器 data-state=open 在行容器上钉住操作区显隐与标题让位(group-has 变体)。
import type { ConversationOrigin, ConversationSessionScope } from '@/domain/conversation/types';
import { Button, IconButton } from '@/components/ui';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/Popover';
import type { ChatBotView } from '@/services/workspace/botSessionService';
import { cn } from '@/utils/cn';
import { Bot, MoreHorizontal, Plus } from 'lucide-react';
import React from 'react';
import { Points } from '@/tracking/points';
import { Tracert } from '@/utils/tracert';
import { ConversationOriginFilter } from './ConversationOriginFilter';
import { ConversationSessionSearchIconButton } from './ConversationSessionSearchIconButton';

export interface ConversationBotItemActionsProps {
  /** 行 Bot:筛选 botId / 埋点参数 / 「Bot 管理」菜单项回调透传。 */
  bot: ChatBotView;
  /** 他人发起只读视角:新建与「更多操作」入口不渲染(检索/筛选保留)。 */
  isOthers: boolean;
  /** Bot 非可用态(FAILED/PENDING):会话类入口不渲染;「更多操作」入口常驻显示(2026-10-21 运维直达)。 */
  unavailable?: boolean;
  /** 展开或任一筛选/检索激活:操作区钉住可见(仅可用 Bot 生效)。 */
  actionsPinned: boolean;
  /** 检索入口可见(仅 all 范围)。 */
  searchVisible: boolean;
  /** 检索激活标记:品牌蓝圆点与图标点亮,Bot 收起时仍可见。 */
  searchMarked: boolean;
  /** 筛选入口可见(managed 或可收藏好友 Bot)。 */
  showFilter: boolean;
  /** 归属筛选支持(managed 才支持归属切换)。 */
  supportsOrigin: boolean;
  /** 收藏支持(好友 Bot 范围筛选)。 */
  supportsFavorites: boolean;
  /** 团队分组禁用「他人发起」归属项。 */
  othersDisabled: boolean;
  origin: ConversationOrigin;
  scope: ConversationSessionScope;
  onOriginChange(origin: ConversationOrigin): void;
  onScopeChange(scope: ConversationSessionScope): void;
  /** 新建会话(新建埋点随入口内聚)。 */
  onCreateSession(): void;
  /** 检索按钮开关:展开联动与输入框开关态由父级承载(handleSearchToggle)。 */
  onSearchToggle(): void;
  /** 「更多操作」入口渲染开关(父级计算):「有菜单项才显示入口」——回调存在且
   *  bot.spaceId 有值(菜单仅「Bot 管理」,缺空间信息不渲染),只读视角除外。 */
  showMenu: boolean;
  /** 「Bot 管理」菜单项回调:跳转该 Bot 所属空间的 Bot 管理页并定位该空间。 */
  onOpenBotManagement?(bot: ChatBotView): void;
}

/** Bot 行 hover 操作区:显隐由行容器的 group-hover/聚焦/钉住类驱动(相对行绝对定位)。 */
export function ConversationBotItemActions(props: ConversationBotItemActionsProps) {
  const {
    bot,
    isOthers,
    unavailable,
    actionsPinned,
    searchVisible,
    searchMarked,
    showFilter,
    supportsOrigin,
    supportsFavorites,
    othersDisabled,
    origin,
    scope,
    onOriginChange,
    onScopeChange,
    onCreateSession,
    onSearchToggle,
    showMenu,
    onOpenBotManagement,
  } = props;
  // 「更多操作」菜单开关(随本组件内聚;菜单项点击后关闭)。
  const [menuOpen, setMenuOpen] = React.useState(false);
  return (
    <div
      className={cn(
        'absolute top-1/2 right-2 z-10 flex -translate-y-1/2 items-center gap-0.5 pl-5',
        'mask-[linear-gradient(to_right,transparent,black_20px)]',
        // 遮盖底撤除:按钮 hover 自带 muted 底,操作区不再叠一层与行底不一致的底色;
        // right-2 贴行右缘(#2990 紧凑单行形态:行尾不再保留展开箭头位,折叠角标为
        // 名称行内元素;按钮间保持 gap-0.5 一致的 2px 间隙)。
        'opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100 [@media(hover:none)]:opacity-100',
        // 展开或任一筛选(归属/范围/会话ID检索)激活时钉住可见;
        // 生效中的检索在 Bot 收起时仍可见(🔍 按钮激活态圆点);
        // 「更多操作」菜单打开期间同样钉住(触发器 data-state=open,鼠标移出行不塌缩)。
        actionsPinned && 'opacity-100',
        'group-has-[[data-state=open]]:opacity-100',
        // 不可用 Bot:「更多操作」入口常驻显示,不随 hover 变化(2026-10-21 运维直达)。
        unavailable && 'opacity-100',
      )}
    >
      {/* 会话类入口(新建/检索/筛选)仅可用 Bot 渲染:不可用 Bot 的会话操作无意义(2026-10-21)。 */}
      {!isOthers && !unavailable && (
        <IconButton
          label="新建会话"
          size="sm"
          icon={<Plus className="h-3.5 w-3.5" />}
          className="h-6 w-6 rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
          onClick={(event) => {
            event.stopPropagation();
            Tracert.click(Points.workspaceChat.newSession, { bot_id: bot.botId });
            onCreateSession();
          }}
        />
      )}
      {!unavailable && searchVisible && (
        <ConversationSessionSearchIconButton marked={searchMarked} onToggle={onSearchToggle} />
      )}
      {!unavailable && showFilter && (
        <ConversationOriginFilter
          botId={bot.botId}
          origin={origin}
          scope={scope}
          supportsOrigin={supportsOrigin}
          supportsFavorites={supportsFavorites}
          othersDisabled={othersDisabled}
          onOriginChange={onOriginChange}
          onScopeChange={onScopeChange}
        />
      )}
      {/* 「更多操作」入口(#2990 还原):hover 随操作区显隐,常居操作区末位(新建/检索/筛选之后);
          「有菜单项才显示入口」由父级 showMenu 收紧,当前菜单仅「Bot 管理」一项。 */}
      {showMenu && onOpenBotManagement && (
        <Popover open={menuOpen} onOpenChange={setMenuOpen}>
          <PopoverTrigger asChild>
            <IconButton
              label="更多操作"
              size="sm"
              icon={<MoreHorizontal className="h-3.5 w-3.5" />}
              className="h-6 w-6 rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
            />
          </PopoverTrigger>
          <PopoverContent align="end" className="w-44 p-1">
            <Button
              variant="ghost"
              className="h-auto w-full justify-start gap-2 px-2 py-2 text-xs"
              onClick={() => {
                setMenuOpen(false);
                onOpenBotManagement(bot);
              }}
            >
              <Bot className="h-3.5 w-3.5" />
              Bot 管理
            </Button>
          </PopoverContent>
        </Popover>
      )}
    </div>
  );
}