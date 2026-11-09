// 管理 Bot / 好友 Bot 通用行:展开区 + 行内动作(归属筛选 / 新建会话)。
// 展开区按 section/origin 分流:managed+mine/friend 交互式 Session 列表(选中/新建/加载更多);
// managed+others 好友用户分组只读视图(分组渲染在 ConversationFriendGroup)。
import { Button } from '@/components/ui';
import { supportsBotSessionFavorites } from '@/domain/botEngine';
import { formatCodingOtherSessionTitle } from '@/domain/conversation/codingSessionOwnership';
import { isManagedConversationSection } from '@/domain/conversation/types';
import { cn } from '@/utils/cn';
import React from 'react';
import { AvatarTile } from '../../components/AvatarTile';
import { useConversationInstanceRow } from '../hooks/useConversationInstanceScope';
import { ConversationBotItemActions } from './ConversationBotItemActions';
import { resolveBotRowLayout } from './ConversationBotItemLayout';
import { OthersArea } from './ConversationBotOthersArea';
import { ConversationBotRuntimeBadge } from './ConversationBotRuntimeBadge';
import { ConversationBotUnavailableTooltip, resolveBotUnavailable } from './ConversationBotUnavailableTooltip';
import { ConversationSessionIdSearchInput } from './ConversationSessionIdSearchInput';
import type { ConversationBotItemProps } from './ConversationBotItem.types';

import { ConversationSessionList } from './ConversationSessionList';

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
  createSessionPending = false,
  sessionActions,
}: ConversationBotItemProps) {
  const isManaged = isManagedConversationSection(section);
  const isOthers = isManaged && origin === 'others';
  const hasSessionControls = bot.chatable;
  const interactive = bot.chatable;
  // Bot 状态异常或好友服务 Bot 无 online 发布时，整行禁用并悬停提示原因。
  const { reason: unavailableReason, unavailable } = resolveBotUnavailable(bot, section);
  const canFavorite = supportsBotSessionFavorites(bot.engine);
  const [searchInputOpen, setSearchInputOpen] = React.useState(false);
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
  const {
    scope: instanceScope,
    row: instanceRow,
    inputDisabled: instanceInputDisabled,
    marked: searchMarked,
  } = useConversationInstanceRow(bot, sessionKey !== '');
  // 显隐与让位布局（2026-11-04 重构）拆至 ConversationBotItemLayout（TC-G005）：hover 三档让位+激活标记常显。
  const {
    filterActive,
    searchActive,
    searchVisible,
    showFilter,
    showMenu,
    stripVisible,
    hoverActionsPadding,
    pinnedMarked,
    pinnedMarkPadding,
  } = resolveBotRowLayout({
    isManaged,
    isOthers,
    canFavorite,
    origin,
    scope,
    sessionKey,
    expanded,
    unavailable,
    hasSessionControls,
    searchMarked,
    hasManagement: Boolean(onOpenBotManagement && bot.spaceId),
  });
  const searchInputVisible = expanded && searchVisible && (searchInputOpen || searchActive);
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
            // 名称/tag 以扣除操作区让位后的内容盒为响应基准；沿用发布分支的紧凑 gap-1。
            'flex min-w-0 flex-1 items-center gap-1 @container/conversation-bot-title',
            // 发布分支已把渐隐区由 20px 收紧到 10px。收起按钮并入操作组后，
            // 4/5 个按钮分别预留 112/138px；收起态 3/4 个按钮分别为 86/112px。
            stripVisible
              ? unavailable
                ? 'pr-9'
                : pinnedMarked
                  ? // 常显指示器静态让位 + hover 全组让位叠加(hover 类特异性高,非 hover
                    // 态落在常显宽度;漏叠会让展开态 hover 出现的整组按钮压名称——同日修正)。
                    [pinnedMarkPadding, hoverActionsPadding]
                  : hoverActionsPadding
              : false,
          )}
        >
          <span
            className={cn(
              // 名称是主信息，保留最小可视宽；tag 使用剩余空间并优先收缩。
              'min-w-12 truncate text-[13px] font-normal leading-[18px] tracking-[-0.08px] text-content-strong',
              // 发布分支选中态回归中性深色，与会话选中胶囊保持一致。
              botSelected && !unavailable && 'text-foreground',
            )}
          >
            {bot.displayName}
          </span>
          {/* 操作区出现时沿用发布分支的名称优先策略；静默态仍按自然宽度展示长 tag。 */}
          <ConversationBotRuntimeBadge
            bot={bot}
            className={cn(
              stripVisible &&
                'group-hover:hidden group-focus-within:hidden group-has-[[data-state=open]]:hidden [@media(hover:none)]:hidden',
              // 仅不可用「更多」常驻时 badge 恒让位。展开态收起按钮现在只在 hover/键盘/
              // 触屏/菜单打开时出现,由下方 group 变体按需让位,静默态不再为保留空间隐藏 badge。
              stripVisible && unavailable && 'hidden',
            )}
          />
        </div>
      </Button>
      {stripVisible && (
        <ConversationBotItemActions
          bot={bot}
          expanded={expanded}
          isOthers={isOthers}
          unavailable={unavailable}
          filterActive={filterActive}
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
          onCollapse={handleToggle}
          onCreateSession={onCreateSession}
          createSessionPending={createSessionPending}
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
