// 管理 Bot / 好友 Bot 通用行:展开区 + 行内动作(归属筛选 / 新建会话)。
// 展开区按 section/origin 分流:
// - managed+mine / friend:交互式 Session 列表(选中 / 新建 / 加载更多走 Hook 回调);
// - managed+others:好友用户分组只读视图(分组渲染在 ConversationFriendGroup)。
// 同一时刻仅一个 Bot 展开(跨我的/团队/好友分组,由 Store 单键替换统一互斥)。
import { Button, IconButton } from '@/components/ui';
import { supportsBotSessionFavorites } from '@/domain/botEngine';
import type {
  ConversationBotSection,
  ConversationOrigin,
  ConversationSessionListState,
  ConversationSessionScope,
} from '@/domain/conversation/types';
import { isManagedConversationSection } from '@/domain/conversation/types';
import { useConversationInstanceRow } from '../hooks/useConversationInstanceScope';
import type { ChatBotView } from '@/services/workspace/botSessionService';
import { cn } from '@/utils/cn';
import { ChevronDown, Plus } from 'lucide-react';
import React from 'react';
import { AvatarTile } from '../../components/AvatarTile';
import type { ConversationSessionListActions } from '../hooks/useConversationSessionActions';
import { ConversationBotUnavailableTooltip, resolveBotUnavailable } from './ConversationBotUnavailableTooltip';
import { ConversationBotRuntimeBadge } from './ConversationBotRuntimeBadge';
import { OthersArea, type ConversationBotItemOthers } from './ConversationBotOthersArea';
import { ConversationOriginFilter } from './ConversationOriginFilter';
import { ConversationSessionIdSearchInput } from './ConversationSessionIdSearchInput';
import { ConversationSessionList } from './ConversationSessionList';
import { ConversationSessionSearchIconButton } from './ConversationSessionSearchIconButton';
import { Points } from '@/tracking/points';
import { Tracert } from '@/utils/tracert';

export type { ConversationBotItemOthers } from './ConversationBotOthersArea';

export interface ConversationBotItemProps {
  bot: ChatBotView;
  section: ConversationBotSection;
  expanded: boolean;
  /** 当前 Bot 下选中的 Session(跨 origin 高亮)。 */
  selectedSessionId: string | null;
  /** 该 Bot 是否为当前舞台所属 Bot(跨 section 判定由装配层完成):名称点亮品牌蓝作行锚点,
   *  与其下会话胶囊形成「Bot → 会话」两级选中语义(2026-10-22 体验精修)。 */
  botSelected?: boolean;
  origin: ConversationOrigin;
  /** 生效读取范围(managed mine 用;friend 读 scopeByFriendBotId)。 */
  scope: ConversationSessionScope;
  /** 该 Bot 已生效的 sessionKey 条件(空串 = 未生效);仅 all 范围下允许提交新条件。 */
  sessionKey: string;
  /** mine / friend 交互会话列表;undefined 视为未加载(不判空)。 */
  sessions?: ConversationSessionListState;
  /** 仅 managed 传入:others 好友分组区数据。 */
  others?: ConversationBotItemOthers;
  onToggle(): void;
  onOriginChange(origin: ConversationOrigin): void;
  onScopeChange(scope: ConversationSessionScope): void;
  /** 提交 sessionKey 搜索(组件内已 trim;空串清除条件);装配层写 Store 并展开该 Bot。 */
  onSearchSession(sessionKey: string): void;
  onCreateSession(): void;
  onSelectSession(sessionId: string): void;
  onLoadMore(): void;
  onToggleFavorite(sessionId: string): void;
  isFavoritePending(sessionId: string): boolean;
  /** AgentCoding Bot 专用入口:与 Bot 工坊「去使用」一致,跳 /coding/coding-chat。 */
  onOpenAgentCodingBot(bot: ChatBotView): void;
  /** 恢复的单聊会话操作(编辑标题/清除上下文/删除会话);只读/好友分组区不消费。 */
  sessionActions?: ConversationSessionListActions;
}

export const ConversationBotItem = React.memo(function ConversationBotItem({
  bot,
  section,
  expanded,
  selectedSessionId,
  botSelected = false,
  origin,
  scope,
  sessionKey,
  sessions,
  others,
  onToggle,
  onOriginChange,
  onScopeChange,
  onSearchSession,
  onCreateSession,
  onSelectSession,
  onLoadMore,
  onToggleFavorite,
  isFavoritePending,
  onOpenAgentCodingBot,
  sessionActions,
}: ConversationBotItemProps) {
  const isManaged = isManagedConversationSection(section);
  const isOthers = isManaged && origin === 'others';
  const isAgentCodingBot = Boolean(bot.isAgentCodingBot);
  const hasSessionControls = !isAgentCodingBot && bot.chatable;
  const interactive = isAgentCodingBot || bot.chatable;
  // Bot 可用状态(bots 接口 status):FAILED/PENDING 整行禁用并悬停提示原因(判定与文案见包装器)。
  const { reason: statusUnavailableReason, unavailable } = resolveBotUnavailable(bot.status);
  const canFavorite = supportsBotSessionFavorites(bot.engine);
  // 筛选/搜索的激活与可见:归属 others 或 favorite 范围点亮筛选;归属+范围筛选对管理 Bot 开放,
  // 好友 Bot 仅在有收藏能力时暴露「会话」范围(TEClaw 无收藏接口,不发收藏请求)。
  const filterActive = (isManaged && origin === 'others') || (canFavorite && scope === 'favorite');
  const searchActive = sessionKey !== '';
  // 不可用 Bot 操作区不渲染,展开/筛选/检索激活态的让位与钉住一并抑制。
  const actionsPinned = !unavailable && (expanded || filterActive || searchActive);
  // 搜索入口仅在有效范围 all 下展示:favorite 清空并隐藏(切回 all 不恢复),others 强制 all 保留。
  const searchVisible = scope === 'all';
  const showFilter = isManaged || canFavorite;
  // 🔍 开关式输入框:显隐为组件本地态;过滤词已生效时强制可见,激活的入口不因开关丢失。
  const [searchInputOpen, setSearchInputOpen] = React.useState(false);
  const searchInputVisible = expanded && searchVisible && (searchInputOpen || searchActive);
  // 多实例服务 Bot 实例范围聚合视图(能力缺省 null → 零渲染零激活):实例行判定/检索输入禁用/圆点口径。
  const { scope: instanceScope, row: instanceRow, inputDisabled: instanceInputDisabled, marked: searchMarked } =
    useConversationInstanceRow(bot, searchActive);
  const handleSearchToggle = () => {
    if (!expanded) onToggle();
    setSearchInputOpen((current) => !current);
  };
  const handleToggle = () => {
    if (unavailable) return;
    // AgentCoding Bot 是可点击入口,但不建普通 session:直接进专用 coding 对话页(与工坊「去使用」一致)。
    if (bot.isAgentCodingBot) {
      onOpenAgentCodingBot(bot);
      return;
    }
    if (!bot.chatable) return;
    onToggle();
  };

/* 一级行与协作群统一为 52px：固定头像槽位，头像和操作相对两行文字整体居中。 */
  const row = (
    <div
      aria-disabled={unavailable || undefined}
      className={cn(
        'group relative mx-2 flex min-h-13 items-center gap-2 rounded-md px-2 transition-colors',
        'hover:bg-muted/60',
        unavailable && 'opacity-60',
      )}
    >
        <Button
          variant="ghost"
          aria-label={bot.displayName}
          aria-expanded={interactive && !unavailable ? expanded : false}
          onClick={handleToggle}
          className={cn(
'flex h-auto min-h-13 min-w-0 flex-1 items-center justify-start gap-2 rounded-none px-0 py-1 text-left hover:bg-transparent',
            (!interactive || unavailable) && 'cursor-not-allowed',
            !interactive && 'opacity-50',
          )}
        >
          {/* 头像 32px 实感(2026-10-22 体验精修):槽位保持 36px 不动 → 会话行 52px
              起排几何契约不受影响;Bot 目录身份感由头像承载,不再依赖铅线分组。 */}
          <span className="relative flex h-9 w-9 shrink-0 items-center justify-center">
            <AvatarTile
              src={bot.avatarUrl}
              label={bot.displayName}
              className="h-8 w-8 rounded-full"
              fallbackContent={
                <span className="text-xs font-semibold leading-none tracking-[0.06em]">
                  {bot.displayName.slice(0, 1)}
                </span>
              }
            />
            {/* 在线信号点已移除(随上游 #2990 收起竖轨同款裁决):metadata status 对
                已删除 Bot 仍残留 ACTIVE,绿点失真误导;待后端好友关系/元数据同步修复后再恢复。 */}
          </span>
          <div
            className={cn(
              'flex min-w-0 flex-1 flex-col items-start gap-0.5',
              // 文字让位与操作区实际覆盖等宽:悬停/键盘聚焦弹出操作区,或任一筛选(归属/范围/检索)钉住可见。
              // 新建/检索/筛选全在时让位 96px(pr-24)且悬停/聚焦不可回缩(2026-10-14 对齐设计稿顺序:新建、检索、筛选);
              // 非钉住时仅悬停/键盘聚焦临时让位。
              hasSessionControls && (actionsPinned ? 'pr-24' : 'group-hover:pr-24 group-focus-within:pr-24'),
            )}
          >
            <span
              className={cn(
                // 名称 13px 常规字重(2026-10-22 需求):与会话标题同规格,去 Bot 名的
                // 加粗强调——层级交由头像/缩进/选中态承载,列表更静;
                // 选中态 text-primary 依旧经由 botSelected 后发覆盖。
                'w-full min-w-0 truncate text-[13px] font-normal leading-5 text-content-strong',
                // 舞台所属 Bot 名称点亮品牌蓝(与 shell 主导航激活态同语言,2026-10-22);
                // 不可用行维持灰名,不与禁用态打架。
                botSelected && !unavailable && 'text-primary',
              )}
            >
              {bot.displayName}
            </span>
            <ConversationBotRuntimeBadge bot={bot} />
          </div>
        </Button>
        {hasSessionControls && !unavailable && (
          <div
            className={cn(
              'absolute top-1/2 right-[34px] z-10 flex -translate-y-1/2 items-center gap-0.5 pl-5',
              'mask-[linear-gradient(to_right,transparent,black_20px)]',
              // 遮盖底撤除:按钮 hover 自带 muted 底,操作区不再叠一层与行底不一致的底色;
              // right-[34px] 使行尾箭头与操作区最右按钮(设计稿顺序 新建/检索/筛选,最右为筛选)之间
              // 保持与按钮间(gap-0.5)一致的 2px 等宽间隙。
              'opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100 [@media(hover:none)]:opacity-100',
              // 展开或任一筛选(归属/范围/会话ID检索)激活时钉住可见;
              // 生效中的检索在 Bot 收起时仍可见(🔍 按钮激活态圆点)。
              actionsPinned && 'opacity-100',
            )}
          >
            {!isOthers && (
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
            {searchVisible && (
              <ConversationSessionSearchIconButton marked={searchMarked} onToggle={handleSearchToggle} />
            )}
            {showFilter && (
              <ConversationOriginFilter
                botId={bot.botId}
                origin={origin}
                scope={scope}
                supportsOrigin={isManaged}
                supportsFavorites={canFavorite}
                othersDisabled={section === 'team'}
                onOriginChange={onOriginChange}
                onScopeChange={onScopeChange}
              />
            )}
          </div>
        )}
        {/* 展开箭头:鼠标可点的折叠入口(点击转发同一 toggle)。语义与键盘仍由
            主按钮承载,故不参与 Tab 序、对读屏隐藏,避免一行两个等价控件。 */}
        {hasSessionControls && !unavailable && (
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-hidden="true"
            tabIndex={-1}
            onClick={handleToggle}
            className="h-6 w-6 shrink-0 rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            {/* 展开箭头旋转过渡(2026-10-22 体验精修二轮):单 ChevronDown 图标,
                收起态 -rotate-90,替代 Right↔Down 双图标硬切换;aria 语义不变。 */}
            <ChevronDown
              className={cn('h-3.5 w-3.5 transition-transform duration-150', !expanded && '-rotate-90')}
              aria-hidden="true"
            />
          </Button>
        )}
      </div>
  );
  return (
    <div>
      {/* 不可用 Bot 整行悬停提示原因(可用时包装器原样透传,不挂 Tooltip)。 */}
      <ConversationBotUnavailableTooltip reason={statusUnavailableReason}>{row}</ConversationBotUnavailableTooltip>
      {expanded && hasSessionControls && !unavailable && (
        // 展开区不再整体缩进:session 行(dmore 平齐化 1:1)自带全宽行槽,交互热区在行内部分级。
        // 首帧 2px 下落淡入(2026-10-22 体验精修二轮):展开是「自 Bot 行生长出来」,
        // 而非瞬现的独立列表;reduced-motion 由 global.css 静止。
        <div aria-label={`会话列表：${bot.displayName}`} className="conversation-area-in">
          {searchInputVisible && (
            <>
              {instanceRow && <instanceRow.capability.InstancePicker botId={bot.botId} />}
              <ConversationSessionIdSearchInput
                value={sessionKey}
                onSearch={onSearchSession}
                disabled={instanceInputDisabled}
                preserveDraftOnExternalClear={Boolean(instanceScope?.state.multiInstance)}
              />
            </>
          )}
          {isOthers ? (
            <OthersArea others={others} />
          ) : (
            <ConversationSessionList
              actions={sessionActions}
              sessions={sessions}
              selectedSessionId={selectedSessionId}
              onSelectSession={onSelectSession}
              onLoadMore={onLoadMore}
              onToggleFavorite={canFavorite ? onToggleFavorite : undefined}
              isFavoritePending={isFavoritePending}
            />
          )}
        </div>
      )}
    </div>
  );
});
