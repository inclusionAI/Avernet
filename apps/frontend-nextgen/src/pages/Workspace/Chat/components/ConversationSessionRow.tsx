// 会话行:只读(origin=others 的他人会话)与交互式(我发起 / 好友 Bot)共用一行结构。
// 只读形态不渲染任何写操作入口(收藏 / 更多 / 新建),仅打开只读历史;
// 交互式行通过 Hook 回调(选中 / 加载更多)委托既有会话操作,不在行内写业务规则。
//
// dmore index.html「会话目录二级*」帧 1:1 复原(2026-10-10 像素实测,B1 tasks 6.1 裁决):
// - 无树形 rail/前置消息图标:文字 13px/18px、x 与一级 bot 行平齐(稿 x=240);
// - hover 交互热区分级:左缘较 bot 胶囊(全宽 inset-x-2)再内缩 24、右缘再内缩 8,收藏行右缘到边;
// - 选中胶囊与 bot 行同构:全宽(#EBEBEB → bg-selected) rx6(稿 实测 x208..510);
// - 收藏星标 #F39E1C(--star)常驻右缘 16px 槽位(稿 x267..283),未收藏 hover 才显现。
//   收藏按 2026-10-10 平齐化终审维持星标直操作;不并入菜单翻转。
// - 稿注记「hover到更多icon/click更多icon」:PR 454 恢复的操作(编辑标题/清除上下文/删除会话)
//   由 hover「…」菜单承接,置于星标左侧;稿二级行无消息条数/时间列,故 withMeta=false。
// 行高 32 / 节奏 36(pitch)由列表容器承载,右余部无消息条数列(稿二级行无该元素)。
import { Badge, Button, IconButton } from '@/components/ui';
import type { BotChatSessionView } from '@/services/workspace/botSessionService';
import { cn } from '@/utils/cn';
import { Star } from 'lucide-react';
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
  actions,
}: ConversationSessionRowProps) {
  const favorite = session.favorite === true;
  return (
    <div className="group/row relative flex min-h-8 items-center">
      {/* 选中胶囊:全宽,与 bot 行同构(稿「会话目录二级_收藏+选中」实拍 #EBEBEB rx6 全宽)。 */}
      {selected && <span aria-hidden="true" className="absolute inset-y-0 left-2 right-2 rounded-md bg-selected" />}
      <Button
        variant="ghost"
        aria-current={selected ? 'page' : undefined}
        onClick={onSelect}
        className={cn(
          'relative flex h-8 min-w-0 flex-1 items-center justify-start gap-2 rounded-md px-2 text-left',
          // 交互/hover 热区分级:左缘 +24px、右缘 +8px(较 bot 胶囊);收藏行右缘到边(稿 271/279 宽)。
          favorite ? 'ml-8 mr-2' : 'ml-8 mr-4',
          selected ? 'hover:bg-transparent' : 'hover:bg-muted/60',
        )}
      >
        <span
          className={cn(
            'min-w-0 flex-1 truncate text-[13px] leading-[18px] tracking-[-0.08px]',
            selected ? 'font-medium text-foreground' : 'font-normal text-content-strong',
          )}
        >
          {session.title}
        </span>
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
                'h-4 w-4',
                session.favorite === undefined || !session.favorite ? 'text-muted-foreground' : 'fill-star text-star',
              )}
            />
          }
          disabled={favoritePending || session.favorite === undefined || Boolean(actions?.pending)}
          aria-busy={favoritePending || Boolean(actions?.pending)}
          className={cn(
            'absolute right-7 top-1/2 -translate-y-1/2 rounded-md',
            !favorite &&
              !selected &&
              'opacity-0 transition-opacity group-hover/row:opacity-100 group-focus-within/row:opacity-100 [@media(hover:none)]:opacity-100',
          )}
          onClick={(event) => {
            event.stopPropagation();
            onToggleFavorite();
          }}
        />
      )}
    </div>
  );
});