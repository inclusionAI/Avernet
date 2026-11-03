// 管理 Bot / 好友 Bot 通用行:展开区 + 行内动作(归属筛选 / 新建会话)。
// 展开区按 section/origin 分流:
// - managed+mine / friend:交互式 Session 列表(选中 / 新建 / 加载更多走 Hook 回调);
// - managed+others:好友用户分组只读视图(分组渲染在 ConversationFriendGroup)。
// 同一时刻仅一个 Bot 展开(跨我的/团队/好友分组,由 Store 单键替换统一互斥)。
import { Button } from '@/components/ui';
import { supportsBotSessionFavorites } from '@/domain/botEngine';
import { formatCodingOtherSessionTitle } from '@/domain/conversation/codingSessionOwnership';
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
import { ChevronDown } from 'lucide-react';
import React from 'react';
import { AvatarTile } from '../../components/AvatarTile';
import type { ConversationSessionListActions } from '../hooks/useConversationSessionActions';
import { ConversationBotItemActions } from './ConversationBotItemActions';
import { ConversationBotUnavailableTooltip, resolveBotUnavailable } from './ConversationBotUnavailableTooltip';
import { ConversationBotRuntimeBadge } from './ConversationBotRuntimeBadge';
import { OthersArea, type ConversationBotItemOthers } from './ConversationBotOthersArea';
import { ConversationSessionIdSearchInput } from './ConversationSessionIdSearchInput';
import { ConversationSessionList } from './ConversationSessionList';

export type { ConversationBotItemOthers } from './ConversationBotOthersArea';

export interface ConversationBotItemProps {
  bot: ChatBotView;
  section: ConversationBotSection;
  expanded: boolean;
  /** 当前 Bot 下选中的 Session(跨 origin 高亮)。 */
  selectedSessionId: string | null;
  /** 该 Bot 是否为当前舞台所属 Bot(跨 section 判定由装配层完成):名称点亮 #09090B
   *  (text-foreground)作行锚点——收起时其下选中会话不可见,Bot 名顶位承载选中语义,
   *  与其下会话的灰阶选中胶囊形成「Bot → 会话」两级中性层次(2026-11-02 稿)。 */
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
  /** Coding Bot 的 others 直接使用按 sessionId 分流后的扁平会话列表。 */
  flatCodingOthers?: boolean;
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
  /** 「Bot 管理」菜单项回调(#2990 还原):跳转该 Bot 所属空间的 Bot 管理页;
   *  好友 Bot 不传(菜单项不渲染);bot.spaceId 有值才显示菜单入口。 */
  onOpenBotManagement?(bot: ChatBotView): void;
  /** Bot 正在回复中的会话 id(#2990 还原):仅当前会话;透传会话行渲染 loading 指示。 */
  replyingSessionId?: string | null;
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
  flatCodingOthers = false,
  onToggle,
  onOriginChange,
  onScopeChange,
  onSearchSession,
  onCreateSession,
  onSelectSession,
  onLoadMore,
  onToggleFavorite,
  isFavoritePending,
  onOpenBotManagement,
  replyingSessionId,
  sessionActions,
}: ConversationBotItemProps) {
  const isManaged = isManagedConversationSection(section);
  const isOthers = isManaged && origin === 'others';
  const hasSessionControls = bot.chatable;
  const interactive = bot.chatable;
  // Bot 状态异常或好友服务 Bot 无 online 发布时，整行禁用并悬停提示原因。
  const { reason: unavailableReason, unavailable } = resolveBotUnavailable(bot, section);
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
  // 「更多操作」菜单入口(#2990 还原):「有菜单项才显示入口」——回调存在且 bot.spaceId 有值
  // (菜单仅「Bot 管理」,缺空间信息不渲染),只读视角(others)不渲染。
  const showMenu = Boolean(onOpenBotManagement && bot.spaceId) && !isOthers;
  // 操作区渲染门控:可用行走既有会话入口(hasSessionControls);不可用 Bot 仅保留
  // 常驻「更多操作」(运维直达,2026-10-21),无菜单项则整区不渲染。
  const stripVisible = (hasSessionControls && !unavailable) || (unavailable && showMenu);
  // 🔍 开关式输入框:显隐为组件本地态;过滤词已生效时强制可见,激活的入口不因开关丢失。
  const [searchInputOpen, setSearchInputOpen] = React.useState(false);
  const searchInputVisible = expanded && searchVisible && (searchInputOpen || searchActive);
  const visibleSessions = React.useMemo(() => {
    if (!flatCodingOthers || !sessions) return sessions;
    return {
      ...sessions,
      items: sessions.items.map((session) => ({
        ...session,
        title: formatCodingOtherSessionTitle(session.sessionId, session.title),
      })),
    };
  }, [flatCodingOthers, sessions]);
  // 多实例服务 Bot 实例范围聚合视图(能力缺省 null → 零渲染零激活):实例行判定/检索输入禁用/圆点口径。
  const { scope: instanceScope, row: instanceRow, inputDisabled: instanceInputDisabled, marked: searchMarked } =
    useConversationInstanceRow(bot, searchActive);
  const handleSearchToggle = () => {
    if (!expanded) onToggle();
    setSearchInputOpen((current) => !current);
  };
  const handleToggle = () => {
    if (unavailable) return;
    if (!bot.chatable) return;
    onToggle();
  };

/* 一级行与协作群统一为 40px：固定头像槽位，引擎徽章与名称同行跟随(2026-10-20 设计稿还原)。 */
  const row = (
    <div
      aria-disabled={unavailable || undefined}
      className={cn(
        'group relative mx-2 flex min-h-10 items-center gap-2 rounded-md px-2 transition-colors',
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
'flex h-auto min-h-10 min-w-0 flex-1 items-center justify-start gap-2 rounded-none px-0 py-0.5 text-left hover:bg-transparent',
            (!interactive || unavailable) && 'cursor-not-allowed',
            !interactive && 'opacity-50',
          )}
        >
          {/* 头像 24px(2026-10-26 回退 #2990 紧凑单行形态):槽位保持 36px 不动 →
              会话行 ml-[52px]+pl-2 的 60px 标题起点几何契约不受影响。 */}
          <span className="relative flex h-9 w-9 shrink-0 items-center justify-center">
            <AvatarTile
              src={bot.avatarUrl}
              label={bot.displayName}
              className="h-6 w-6 rounded-full"
              fallbackContent={
                <span className="text-[10px] font-semibold leading-none tracking-[0.06em]">
                  {bot.displayName.slice(0, 1)}
                </span>
              }
            />
            {/* 在线信号点已移除(随上游 #2990 收起竖轨同款裁决):metadata status 对
                已删除 Bot 仍残留 ACTIVE,绿点失真误导;待后端好友关系/元数据同步修复后再恢复。 */}
          </span>
          <div
            className={cn(
              // 名称与引擎徽章同行跟随(2026-10-20 设计稿还原,#2990 紧凑单行);
              // 间距紧凑化(2026-11-03 用户反馈):名称/徽章/角标统一 gap-1。
              'flex min-w-0 flex-1 items-center gap-1',
              // 文字让位与操作区实际覆盖等宽(2026-11-03 紧凑化重算):渐隐区 10px(pl-2.5)+按钮行右距 right-2,
              // 无「更多操作」入口时 3 按钮 86px(pr-[86px]);有入口时 4 按钮 112px(pr-[112px])
              // (菜单打开期间 data-state=open 同样让位、鼠标离行不塌缩)。
              // 不可用 Bot 仅剩常驻「…」(10 渐隐区+单按钮+右内边距)让位恒定 36px;
              // 非钉住时仅悬停/键盘聚焦临时让位。
              stripVisible
                ? unavailable
                  ? 'pr-9'
                  : actionsPinned
                    ? showMenu
                      ? 'pr-[112px]'
                      : 'pr-[86px]'
                    : showMenu
                      ? 'group-hover:pr-[112px] group-focus-within:pr-[112px] group-has-[[data-state=open]]:pr-[112px]'
                      : 'group-hover:pr-[86px] group-focus-within:pr-[86px]'
                : false,
            )}
          >
            <span
              className={cn(
                // 名称 13px 常规字重:与会话标题同规格(13px/18px),去 Bot 名的
                // 加粗强调——层级交由头像/缩进/选中态承载,列表更静;
                // 选中态颜色依旧经由 botSelected 后发覆盖。
                'min-w-0 truncate text-[13px] font-normal leading-[18px] tracking-[-0.08px] text-content-strong',
                // 舞台所属 Bot 名称点亮 #09090B(2026-11-02 稿:品牌蓝与目录灰阶
                // 落差过大,回归中性深 text-foreground,与会话选中同语言;
                // 收起时下挂选中会话不可见,由 Bot 名顶位承载);
                // 不可用行维持灰名,不与禁用态打架。
                botSelected && !unavailable && 'text-foreground',
              )}
            >
              {bot.displayName}
            </span>
            {/* 引擎徽章让位优先于名称(2026-11-03 紧凑化):让位态操作区钉着 + 角标随行,
                此时若徽章仍 shrink-0 占位,名称会被压缩到近零(用户反馈「名称都快被隐藏了」)。
                改为操作区可见(悬停/键盘聚焦/菜单开/触屏)或钉住/不可用常驻时整徽章隐藏,
                把右缘空间优先留给名称;无操作区(chatable=false 无菜单)时不注入任何显隐类。 */}
            <ConversationBotRuntimeBadge
              bot={bot}
              className={cn(
                stripVisible &&
                  'group-hover:hidden group-focus-within:hidden group-has-[[data-state=open]]:hidden [@media(hover:none)]:hidden',
                stripVisible && (actionsPinned || unavailable) && 'hidden',
              )}
            />
            {/* 展开折叠角标(2026-10-20 设计稿还原):仅展开的 Bot 显示收起角标,默认紧跟
                引擎徽章右侧;收起态不渲染(展开由主按钮承载)。角标为纯鼠标入口:span 不进
                Tab 序、对读屏隐藏,点击先截断冒泡再转发同一 toggle(避免与主按钮双触发)。
                2026-11-03 紧凑化:角标槽位 24px→20px(h-5 w-5),不给名称再让 30px。 */}
            {hasSessionControls && !unavailable && expanded && (
              <span
                role="presentation"
                aria-hidden="true"
                onClick={(event) => {
                  event.stopPropagation();
                  handleToggle();
                }}
                className="inline-flex h-5 w-5 shrink-0 cursor-pointer items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
              >
                <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" />
              </span>
            )}
          </div>
        </Button>
        {stripVisible && (
          <ConversationBotItemActions
            bot={bot}
            isOthers={isOthers}
            unavailable={unavailable}
            actionsPinned={actionsPinned}
            searchVisible={searchVisible}
            searchMarked={searchMarked}
            showFilter={showFilter}
            supportsOrigin={isManaged}
            supportsFavorites={canFavorite}
            othersDisabled={section === 'team' && !bot.isAgentCodingBot}
            origin={origin}
            scope={scope}
            onOriginChange={onOriginChange}
            onScopeChange={onScopeChange}
            onCreateSession={onCreateSession}
            onSearchToggle={handleSearchToggle}
            showMenu={showMenu}
            onOpenBotManagement={onOpenBotManagement}
          />
        )}
      </div>
  );
  return (
    <div>
      {/* 不可用 Bot 整行悬停提示原因(可用时包装器原样透传,不挂 Tooltip)。 */}
      <ConversationBotUnavailableTooltip reason={unavailableReason}>{row}</ConversationBotUnavailableTooltip>
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
          {isOthers && !flatCodingOthers ? (
            <OthersArea others={others} />
          ) : (
            <ConversationSessionList
              actions={isOthers ? undefined : sessionActions}
              sessions={visibleSessions}
              selectedSessionId={selectedSessionId}
              onSelectSession={onSelectSession}
              onLoadMore={onLoadMore}
              onToggleFavorite={!isOthers && canFavorite ? onToggleFavorite : undefined}
              isFavoritePending={isFavoritePending}
              replyingSessionId={replyingSessionId ?? null}
              emptyTitle={isOthers ? '暂无他人发起的会话' : undefined}
              showCreateHint={!isOthers}
            />
          )}
        </div>
      )}
    </div>
  );
});
