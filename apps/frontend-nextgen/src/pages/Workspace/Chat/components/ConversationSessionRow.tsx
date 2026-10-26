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
// - 长标题让位:星标(right 28..56px)/「…」(right 64..92px)绝对定位于行上、与文字 runway 重叠,
//   文字右界随图标可见性收缩(静默 24px / 星标常驻 60px / hover·聚焦·触屏·菜单开启 96px),
//   让位只走 pr 不动 mr 热区,pl 恒定保证标题起点不随状态跳动。
// 行高 32 / 节奏 36(pitch)由列表容器承载,右余部无消息条数列(稿二级行无该元素)。
import { Badge, Button, IconButton } from '@/components/ui';
import type { BotChatSessionView } from '@/services/workspace/botSessionService';
import { formatRelativeTime } from '@/utils/format';
import { cn } from '@/utils/cn';
import { Loader2, Star } from 'lucide-react';
import React from 'react';
import type { ConversationSessionRowActions } from '../hooks/useConversationSessionMenu';
import { ConversationSessionActions } from './ConversationSessionActions';

export interface ConversationSessionRowProps {
  session: BotChatSessionView;
  selected: boolean;
  /** 只读会话(他人发起 / 好友视角):仅打开历史,无任何写操作。 */
  readOnly?: boolean;
  onSelect(): void;
  onToggleFavorite?(): void;
  favoritePending?: boolean;
  /** Bot 正在回复中的会话 id(#2990 还原):命中当前行时标题左侧缩进区渲染 loading 旋转指示。 */
  replyingSessionId?: string | null;
  /** 恢复的单聊会话操作(编辑标题/清除上下文/删除会话);只读行不传。 */
  actions?: ConversationSessionRowActions;
}

export const ConversationSessionRow = React.memo(function ConversationSessionRow({
  session,
  selected,
  readOnly = false,
  onSelect,
  onToggleFavorite,
  favoritePending = false,
  replyingSessionId,
  actions,
}: ConversationSessionRowProps) {
  const favorite = session.favorite === true;
  const timeText = formatRelativeTime(session.gmtModified);
  return (
    <div className="group/row relative flex min-h-8 items-center">
      {/* 行背景胶囊:hover 与选中同几何(inset-x-2 全宽 rx6),选中 = bg-selected,
          hover = muted/60 弱化底(键盘 focus 与触屏常驻,同星标显现规则)。 */}
      <span
        aria-hidden="true"
        className={cn(
          'absolute inset-y-0 left-2 right-2 rounded-md',
          // 选中胶囊升级为品牌蓝（2026-10-22 体验精修二轮）：原 dmore #EBEBEB 灰底与
          // hover muted/60 几乎不可辨，「当前会话」一眼认不出。改判与 shell 主导航
          // SpaceSwitcher 同语言（bg-primary/10 激活 pill），Bot 蓝名 → 会话蓝胶囊
          // 构成完整的蓝色路径；几何槽位契约（inset-x-2 全宽 rx6）原样保留。
          selected
            ? 'bg-primary/10'
            : 'bg-muted/60 opacity-0 transition-opacity group-hover/row:opacity-100 group-focus-within/row:opacity-100 [@media(hover:none)]:opacity-100',
        )}
      />
      {/* Bot 正在回复中(#2990 还原,2026-10-26 适配 52px 行几何):仅命中行渲染,
          标题左侧缩进区(40-54px,标题起点 60px)绝对定位旋转指示,不占 flex 流,
          标题起点不随 loading 出现跳动;只读行不渲染。 */}
      {!readOnly && replyingSessionId === session.sessionId && (
        <span
          role="status"
          aria-label="Bot 正在回复"
          className="absolute left-[40px] top-1/2 z-10 -translate-y-1/2"
        >
          <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" aria-hidden="true" />
        </span>
      )}
      <Button
        variant="ghost"
        aria-current={selected ? 'page' : undefined}
        onClick={onSelect}
        className={cn(
          'relative flex h-8 min-w-0 flex-1 items-center justify-start gap-2 rounded-md pl-2 pr-2 text-left hover:bg-transparent',
          // 标题左缘对齐一级名称：16px 外距 + 36px 头像槽 + 8px 间隔 = 52px margin + 8px padding。
          // 仅界定点击范围,hover 背景已移交全宽胶囊。
          favorite ? 'ml-[52px] mr-2' : 'ml-[52px] mr-4',
          // 长标题让位(星标/「…」与标题互叠,两者都看不清):文字右界 = mr + pr,须按图标可见性
          // 提前截断。让位只压缩文字(pr 不动热区 mr、不动胶囊几何),pl-2 恒定 → 标题起点不随状态跳动:
          // - 静默态(未收藏且未选中)图标全隐 → 基础 pr-2,右界 24px(同原 px-2);
          // - 星标常驻(收藏行 / 选中行) → 让至星标左界,右界 60px(收藏 mr-2→pr-52,非收藏 mr-4→pr-44);
          // - hover/键盘聚焦/触屏,以及「…」菜单打开期间(trigger 钉住 data-state=open,鼠标离行仍可见)
          //   → 让至「…」左界,右界 96px(收藏 mr-2→pr-88,非收藏 mr-4→pr-80)。
          // 只读行无任何图标,维持基础内边距。
          !readOnly && [
            favorite && 'pr-[52px]',
            !favorite && selected && 'pr-[44px]',
            'group-hover/row:pr-[80px] group-focus-within/row:pr-[80px] [@media(hover:none)]:pr-[80px] group-has-[[data-state=open]]/row:pr-[80px]',
            favorite &&
              'group-hover/row:pr-[88px] group-focus-within/row:pr-[88px] [@media(hover:none)]:pr-[88px] group-has-[[data-state=open]]/row:pr-[88px]',
          ],
        )}
      >
        <span
          className={cn(
            'min-w-0 flex-1 truncate text-[13px] font-normal leading-[18px] text-content-strong',
            // 选中行标题点亮品牌蓝（twMerge 后发覆盖 text-content-strong）：
            // 与蓝胶囊共同构成选中锚点，不加粗维持稿「选中不加黑」的克制。
            selected && 'text-primary',
          )}
        >
          {session.title}
        </span>
        {/* 相对时间(gmtModified 首次启用,2026-10-22 体验精修):静默态可见的右缘轻提示,
            让「最近聊过什么」一眼可辨;hover/键盘聚焦/触屏/「…」菜单打开期隐藏,把右缘
            让给操作图标(与星标显隐同一组触发条件,方向相反)。aria-hidden:时间是次级装饰性
            信息,且可访问名须保持纯标题(行内已由标题承载语义);仅读行右侧只有「只读」章,
            无图标区,时间常驻不参与让位。 */}
        {timeText && (
          <span
            aria-hidden="true"
            className={cn(
              'ml-1 shrink-0 text-[11px] font-normal leading-none text-content-hint',
              !readOnly &&
                'transition-opacity group-hover/row:opacity-0 group-focus-within/row:opacity-0 [@media(hover:none)]:opacity-0 group-has-[[data-state=open]]/row:opacity-0',
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
                  session.favorite === undefined || !session.favorite
                    ? 'text-muted-foreground'
                    : 'fill-star text-star',
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
