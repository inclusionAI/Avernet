// Bot 行 hover 操作区:新建会话 / 会话ID检索 / 归属筛选 / Bot 管理前置直达。
// 恢复自 #2990(对话菜单 UX 与 Bot 状态感知,被 10-22 品牌蓝精修临时摘除),适配 10-22 设计基座:
// - TC-G005 门禁拆分:自 ConversationBotItem 迁出,不改任何既有 UI/行为;
// - 检索开关(searchInputOpen)留在父级(显隐计算依赖);
// - 不可用 Bot(FAILED/PENDING)的「Bot 管理」常驻显示(2026-10-21 运维直达);
// - (2026-11-05 验收微调)「Bot 管理」自「更多操作」菜单前置直出——菜单仅此一项,收拢一层直达,
//   跳转带 Bot 名称过滤(装配层 ?keyword)定位到该 Bot;
// - 筛选菜单打开期间经触发器 data-state=open 在行容器上钉住操作区显隐与标题让位(group-has 变体);
// - 显隐下沉到 icon 级(2026-11-04 重构):操作组仅 hover/键盘/触屏显示,展开态不再整组钉住;
//   收起按钮与会话操作同组显隐(2026-11-05 回归修正:鼠标移开应与其它操作一起隐藏);
//   激活了特殊条件的单个 icon 常显(🔍 检索/实例标记、归属/范围筛选非默认项),其余 hover 才出现。
//   容器不再整体 opacity(父子相乘会把常显 icon 一并隐藏),群发条件逐 icon 重复保持等价语义。
import type { ConversationOrigin, ConversationSessionScope } from '@/domain/conversation/types';
import { IconButton } from '@/components/ui';
import type { ChatBotView } from '@/services/workspace/botSessionService';
import { cn } from '@/utils/cn';
import { Bot, ChevronDown, Plus } from 'lucide-react';
import { Points } from '@/tracking/points';
import { Tracert } from '@/utils/tracert';
import { ConversationOriginFilter } from './ConversationOriginFilter';
import { ConversationSessionSearchIconButton } from './ConversationSessionSearchIconButton';

export interface ConversationBotItemActionsProps {
  /** 行 Bot:筛选 botId / 埋点参数 / 「Bot 管理」菜单项回调透传。 */
  bot: ChatBotView;
  /** 当前 Bot 是否展开；展开时在操作组首位展示收起入口。 */
  expanded: boolean;
  /** 他人发起只读视角:新建与「更多操作」入口不渲染(检索/筛选保留)。 */
  isOthers: boolean;
  /** Bot 非可用态(FAILED/PENDING):会话类入口不渲染;「更多操作」入口常驻显示(2026-10-21 运维直达)。 */
  unavailable?: boolean;
  /** 归属/范围筛选激活(非默认项):筛选 icon 常显(2026-11-04 显隐重构,随操作组仅 hover 的例外)。 */
  filterActive: boolean;
  /** 检索入口可见(仅 all 范围)。 */
  searchVisible: boolean;
  /** 检索激活标记:品牌蓝圆点与图标点亮,Bot 收起时仍可见;激活时该 icon 常显(含实例指定标记)。 */
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
  /** 收起当前 Bot 的会话列表。 */
  onCollapse(): void;
  /** 新建会话(新建埋点随入口内聚)。 */
  onCreateSession(): void;
  /** 新建会话在途：按钮转 loading 并禁用，避免重复提交。 */
  createSessionPending?: boolean;
  /** 检索按钮开关:展开联动与输入框开关态由父级承载(handleSearchToggle)。 */
  onSearchToggle(): void;
  /** 「Bot 管理」前置入口渲染开关(父级计算):回调存在且 bot.spaceId 有值(缺空间信息不渲染),只读视角除外。 */
  showMenu: boolean;
  /** 「Bot 管理」入口回调:跳转该 Bot 所属空间的 Bot 管理页,带 Bot 名称过滤定位该 Bot。 */
  onOpenBotManagement?(bot: ChatBotView): void;
}

/** Bot 行 hover 操作区:显隐由行容器的 group-hover/聚焦/钉住类驱动(相对行绝对定位)。 */
export function ConversationBotItemActions(props: ConversationBotItemActionsProps) {
  const {
    bot,
    expanded,
    isOthers,
    unavailable,
    filterActive,
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
    onCollapse,
    onCreateSession,
    createSessionPending = false,
    onSearchToggle,
    showMenu,
    onOpenBotManagement,
  } = props;
  // icon 级显隐群发类(2026-11-04 重构):与原容器级语义逐项等价——
  // 行 hover/行内键盘聚焦/触屏,以及筛选菜单打开期间(data-state=open,鼠标
  // 移出行不塌缩)整组显示;容器不再整体 opacity(父子相乘会连带隐藏常显 icon)。
  const iconVisibility =
    'opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100 [@media(hover:none)]:opacity-100 group-has-[[data-state=open]]:opacity-100';
  return (
    <div
      className={cn(
        'absolute top-1/2 right-2 z-10 flex -translate-y-1/2 items-center gap-0.5 pl-2.5',
        'mask-[linear-gradient(to_right,transparent,black_10px)]',
        // 遮盖底撤除:按钮 hover 自带 muted 底,操作区不再叠一层与行底不一致的底色;
        // right-2 贴行右缘(#2990 紧凑单行形态:行尾不再保留展开箭头位,折叠角标为
        // 名称行内元素;按钮间保持 gap-0.5 一致的 2px 间隙)。
        // 渐隐区 20px→10px(2026-11-03 紧凑化):4 按钮让位 122→112px、3 按钮 96→86px,
        // 让出的宽度归还名称(用户反馈 hover/展开后名称近零)。
        // 容器常渲染不整体隐藏(2026-11-04 显隐下沉 icon 级,激活 icon 常显需要父级透明)。
      )}
    >
      {/* 展开态的结构控制与其余会话操作并排，避免宽侧栏下角标与操作组视觉脱节。
          2026-11-05 回归修正:它不是常驻状态指示,而是普通行操作;鼠标移开时应与新
          建/检索/筛选/更多操作一起隐藏,hover、键盘聚焦、触屏和菜单打开时同组显示。 */}
      {expanded && !unavailable && (
        <IconButton
          label="收起会话列表"
          size="sm"
          icon={<ChevronDown className="h-3.5 w-3.5" />}
          className={cn('h-6 w-6 rounded-md text-muted-foreground hover:bg-muted hover:text-foreground', iconVisibility)}
          onClick={(event) => {
            event.stopPropagation();
            onCollapse();
          }}
        />
      )}
      {/* 会话类入口(新建/检索/筛选)仅可用 Bot 渲染:不可用 Bot 的会话操作无意义(2026-10-21)。 */}
      {!isOthers && !unavailable && (
        <IconButton
          label="新建会话"
          size="sm"
          loading={createSessionPending}
          icon={<Plus className="h-3.5 w-3.5" />}
          className={cn('h-6 w-6 rounded-md text-muted-foreground hover:bg-muted hover:text-foreground', iconVisibility)}
          onClick={(event) => {
            event.stopPropagation();
            Tracert.click(Points.workspaceChat.newSession, { bot_id: bot.botId });
            onCreateSession();
          }}
        />
      )}
      {/* 🔍 激活(检索生效/实例指定标记)时常显——用户始终知道该 Bot 有生效条件(2026-11-04 显隐重构)。 */}
      {!unavailable && searchVisible && (
        <ConversationSessionSearchIconButton
          marked={searchMarked}
          onToggle={onSearchToggle}
          triggerClassName={cn(iconVisibility, searchMarked && 'opacity-100')}
        />
      )}
      {/* 筛选激活(归属/范围非默认项)时常显——同 🔍 标记语义,始终提示生效条件(2026-11-04 显隐重构)。 */}
      {!unavailable && showFilter && (
        <ConversationOriginFilter
          botId={bot.botId}
          origin={origin}
          scope={scope}
          supportsOrigin={supportsOrigin}
          supportsFavorites={supportsFavorites}
          othersDisabled={othersDisabled}
          triggerClassName={cn(iconVisibility, filterActive && 'opacity-100')}
          onOriginChange={onOriginChange}
          onScopeChange={onScopeChange}
        />
      )}
      {/* 「Bot 管理」前置直达(2026-11-05 验收微调,原「更多操作」菜单仅此一项,收拢一层):
          hover 随操作区显隐,常居操作区末位(新建/检索/筛选之后);Tooltip/可访问名
          「跳转至 Bot 管理」;跳转带 Bot 名称过滤定位到该 Bot(装配层 ?keyword)。
          不可用 Bot 常驻显示,不随 hover 变化(2026-10-21 运维直达)。 */}
      {showMenu && onOpenBotManagement && (
        <IconButton
          label="跳转至 Bot 管理"
          size="sm"
          icon={<Bot className="h-3.5 w-3.5" />}
          className={cn(
            'h-6 w-6 rounded-md text-muted-foreground hover:bg-muted hover:text-foreground',
            iconVisibility,
            unavailable && 'opacity-100',
          )}
          onClick={(event) => {
            event.stopPropagation();
            onOpenBotManagement(bot);
          }}
        />
      )}
    </div>
  );
}