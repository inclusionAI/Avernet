// 会话行:只读(origin=others 的他人会话)与交互式(我发起 / 好友 Bot)共用一行结构。
// 只读形态不渲染任何写操作入口(收藏 / 更多 / 新建),仅打开只读历史;
// 交互式行通过 Hook 回调(选中 / 加载更多)委托既有会话操作,不在行内写业务规则。
//
// dmore index.html「会话目录二级*」帧 1:1 复原(2026-10-10 像素实测,B1 tasks 6.1 裁决):
// - 无树形 rail/前置消息图标:文字 13px/18px、x 与一级 bot 行平齐(稿 x=240);
// - 标题左缘对齐一级名称(固定 36px 头像槽之后);右缘内缩 8,收藏行右缘到边;
// - 选中胶囊与 bot 行同构:全宽(#EBEBEB → bg-selected) rx6(稿 实测 x208..510);
//   hover 胶囊与选中胶囊同几何(inset-x-2 全宽)——hover/选中背景长度一致,
//   行内按钮的热区宽度(左缘内缩/右缘为星标让位)只作用于点击,不再用于背景;
// - 收藏星标 #F39E1C(--star)常驻右缘 16px 槽位(稿 x267..283),未收藏 hover 才显现。
//   收藏按 2026-10-10 平齐化终审维持星标直操作;不并入菜单翻转。
// - 稿注记「hover到更多icon/click更多icon」:PR 454 恢复的操作(编辑标题/清除上下文/删除会话)
//   由 hover「…」菜单承接,置于星标左侧;稿二级行无消息条数/时间列,故 withMeta=false。
// - 右侧操作槽固定:星标(right 28..56px)/「…」(right 64..92px)绝对定位于行上、与文字 runway 重叠,
//   hover/聚焦/触屏/菜单开启不改变标题可用宽度,只切换时间与图标可见性;
//   无常驻星标行保留完整操作档(行右界 96px),常驻星标行再预留时间槽(行右界 112px);
//   时间在非 hover 态绝对显示于原右缘或星标左侧,再整体淡出,pl 恒定保证标题起点与宽度不随状态跳动。
// 行高 32 / 节奏 36(pitch)由列表容器承载,右余部无消息条数列(稿二级行无该元素)。
import { OverflowTooltip } from '@/components/OverflowTooltip';
import { Badge, Button, IconButton } from '@/components/ui';
import type { SessionInitiator } from '@/domain/conversation/sessionInitiator';
import type { BotChatSessionView } from '@/services/workspace/botSessionService';
import { formatRelativeTime } from '@/utils/format';
import { cn } from '@/utils/cn';
import { Star } from 'lucide-react';
import React from 'react';
import type { ConversationSessionRowActions } from '../hooks/useConversationSessionMenu';
import { ConversationSessionActions } from './ConversationSessionActions';
import { ConversationSessionStatus } from './ConversationSessionStatus';
import { ConversationInitiatorBadge } from './ConversationInitiatorBadge';

export interface ConversationSessionRowProps {
  session: BotChatSessionView;
  selected: boolean;
  showInitiator?: boolean;
  initiator?: SessionInitiator;
  /** 只读会话(他人发起 / 好友视角):仅打开历史,无任何写操作。 */
  readOnly?: boolean;
  onSelect(): void;
  onToggleFavorite?(): void;
  favoritePending?: boolean;
  /** Bot 正在回复中的会话 id(#2990 还原):命中当前行时标题左侧缩进区渲染 loading 旋转指示。 */
  replyingSessionId?: string | null;
  /** 恢复的单聊会话操作(编辑标题/清除上下文/删除会话);只读行不传。 */
  actions?: ConversationSessionRowActions;
  /** AgentCoding 属性行开启:标题真实溢出时延迟悬浮展示全量标题。 */
  showFullTitleTooltip?: boolean;
}

export const ConversationSessionRow = React.memo(function ConversationSessionRow({
  session,
  selected,
  showInitiator,
  initiator,
  readOnly = false,
  onSelect,
  onToggleFavorite,
  favoritePending = false,
  replyingSessionId,
  actions,
  showFullTitleTooltip = false,
}: ConversationSessionRowProps) {
  const favorite = session.favorite === true;
  const timeText = formatRelativeTime(session.gmtModified);
  // Browse-only Coding Bot 会隐藏已有 Session 的 mutation 入口（actions 和收藏回调都不传）。
  // 此时 hover 没有右侧图标需要让位，标题热区和相对时间必须保持原位，避免时间先左移再消失。
  const hasHoverActions = Boolean(actions) || Boolean(onToggleFavorite);
  // 原契约：已收藏或当前选中的星标常驻；这类行需要给时间留出“星标左侧”的原位。
  const hasResidentStar = favorite || selected;
  return (
    <div className="group/row relative flex min-h-8 items-center">
      {/* 行背景胶囊:hover 与选中同几何(inset-x-2 全宽 rx6),选中 = bg-selected,
          hover = muted/60 弱化底(键盘 focus 与触屏常驻,同星标显现规则)。 */}
      <span
        aria-hidden="true"
        className={cn(
          'absolute inset-y-0 left-2 right-2 rounded-md',
          // 选中胶囊回归中性深（2026-11-02 稿：#09090B0F ≈ foreground 6%，即
          // bg-foreground/[6%]）：品牌蓝底与目录的灰阶层次落差过大，改回稿面灰阶
          // 选中语言，选中文字同步 #09090B（text-foreground）；几何槽位契约
          // （inset-x-2 全宽 rx6）原样保留。
          selected
            ? 'bg-foreground/[6%]'
            : 'bg-muted/60 opacity-0 transition-opacity group-hover/row:opacity-100 group-focus-within/row:opacity-100 [@media(hover:none)]:opacity-100',
        )}
      />
      {/* 会话左侧活动状态槽：标题始终使用固定缩进，因此状态出现/消失不会造成位移；
          仅在收到 AIXCoding 有效状态或普通 Bot 正在回复时渲染图标，空闲/未知状态留空。 */}
      <ConversationSessionStatus
        botId={session.botId}
        sessionId={session.sessionId}
        showReplying={!readOnly && replyingSessionId === session.sessionId}
      />
      <Button
        variant="ghost"
        aria-current={selected ? 'page' : undefined}
        onClick={onSelect}
        className={cn(
          'relative flex h-8 min-w-0 flex-1 items-center justify-start gap-2 rounded-md pl-2 pr-2 text-left hover:bg-transparent',
          showInitiator && 'h-auto min-h-12 py-1.5',
          // 标题左缘对齐一级名称：16px 外距 + 36px 头像槽 + 8px 间隔 = 52px margin + 8px padding。
          // 仅界定点击范围,hover 背景已移交全宽胶囊。
          favorite ? 'ml-[52px] mr-2' : 'ml-[52px] mr-4',
          // 固定右侧操作区：标题右边界在 hover 进出时保持不变，
          // 可见性只由时间/图标/收藏切换承担。
          // 常驻星标行额外预留时间槽（约 16px），保证“星标左侧的时间”不压标题；
          // 无常驻星标时只保留 hover 操作档，时间仍可回到原右缘。
          !readOnly &&
            hasHoverActions && [
              favorite
                ? 'pr-[104px]'
                : hasResidentStar
                  ? 'pr-[88px]'
                  : 'pr-[80px]',
            ],
        )}
      >
        {(() => {
          // AgentCoding-only display helper: ordinary rows keep the plain title node.
          const titleElement = showInitiator ? (
            <span className="w-full truncate">{session.title}</span>
          ) : (
            session.title
          );
          const codingTitleElement = (
            <span className={cn('block truncate', showInitiator && 'w-full')}>{session.title}</span>
          );
          const titleNode = showFullTitleTooltip ? (
            <OverflowTooltip delayDuration={350}>{codingTitleElement}</OverflowTooltip>
          ) : (
            titleElement
          );
          return (
            <span
              className={cn(
                'min-w-0 flex-1 truncate text-[13px] font-normal leading-[18px] text-content-strong',
                // 选中行标题点亮 #09090B（2026-11-02 稿 = text-foreground，twMerge 后发覆盖）：
                // 与 6% 灰阶选中胶囊共同构成选中锚点，不加粗维持稿「选中不加黑加粗」的克制。
                selected && 'text-foreground',
                showInitiator && 'flex flex-col items-start gap-1',
              )}
            >
              {titleNode}
              {showInitiator && <ConversationInitiatorBadge initiator={initiator} />}
            </span>
          );
        })()}
        {/* 相对时间(gmtModified 首次启用,2026-10-22 体验精修):静默态可见的右缘轻提示,
            让「最近聊过什么」一眼可辨;hover/键盘聚焦/触屏/「…」菜单打开期隐藏,把右缘
            让给操作图标(与星标显隐同一组触发条件,方向相反)。aria-hidden:时间是次级装饰性
            信息,且可访问名须保持纯标题(行内已由标题承载语义);仅读行右侧只有「只读」章,
            无图标区,时间常驻不参与让位。 */}
        {timeText && (
          <span
            aria-hidden="true"
            className={cn(
              // 交互行时间不进入标题布局流：标题只根据固定 pr 决定右界。
              // 无常驻星标时回到right-2 原右缘；已收藏/选中时锚到星标左侧 8px
              // （结合行 mr，非收藏/收藏统一落在行右侧 60px），避免与常驻星标互叠。
              // hover/聚焦/触屏/菜单开启期仅淡出，不挤压标题或造成左右跳动。
              !readOnly && hasHoverActions
                ? `pointer-events-none absolute ${
                    hasResidentStar ? (favorite ? 'right-[52px]' : 'right-[44px]') : 'right-2'
                  } top-1/2 -translate-y-1/2 whitespace-nowrap opacity-100 transition-opacity duration-150 ease-out group-hover/row:opacity-0 group-focus-within/row:opacity-0 [@media(hover:none)]:opacity-0 group-has-[[data-state=open]]/row:opacity-0`
                : 'ml-1 shrink-0',
              'text-[11px] font-normal leading-none text-content-hint',
            )}
          >
            {timeText}
          </span>
        )}
        {readOnly && (
          <Badge tone="neutral" className="shrink-0 rounded px-1.5 py-0 text-[10px] leading-4">
            只读
          </Badge>
        )}
      </Button>
      {!readOnly && actions && (
        <div className="absolute right-[64px] top-1/2 z-10 -translate-y-1/2">
          <ConversationSessionActions
            title={session.title}
            createdAt={session.gmtCreate}
            actions={actions}
            withMeta={false}
          />
        </div>
      )}
      {!readOnly && onToggleFavorite && (
        // 显隐挂外层 span(与 ConversationSessionActions 平齐形态同款):Button 基类的
        // disabled:opacity-50(加载更多在途时全列表星标 disabled)特异性高于行内 opacity-0,
        // 挂在按钮上会把它顶穿 → 未 hover 也全部显影;套嵌 opacity 相乘,隐藏态不再被破防。
        <span
          className={cn(
            'absolute right-7 top-1/2 -translate-y-1/2',
            !favorite &&
              !selected &&
              'opacity-0 transition-opacity group-hover/row:opacity-100 group-focus-within/row:opacity-100 [@media(hover:none)]:opacity-100',
          )}
        >
          <IconButton
            label={
              session.favorite === undefined
                ? '收藏状态暂不可用，请重新加载会话列表'
                : session.favorite
                  ? '取消收藏'
                  : '收藏会话'
            }
            ariaLabel={session.favorite ? '取消收藏' : '收藏会话'}
            size="sm"
            icon={
              <Star
                className={cn(
                  'h-3.5 w-3.5',
                  session.favorite === undefined || !session.favorite ? 'text-muted-foreground' : 'fill-star text-star',
                )}
              />
            }
            disabled={favoritePending || session.favorite === undefined || Boolean(actions?.pending)}
            aria-busy={favoritePending || Boolean(actions?.pending)}
            className="h-6 w-6 rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
            onClick={(event) => {
              event.stopPropagation();
              onToggleFavorite();
            }}
          />
        </span>
      )}
    </div>
  );
});
