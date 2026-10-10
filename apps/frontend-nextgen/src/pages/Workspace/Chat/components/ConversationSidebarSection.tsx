// 对话页侧栏目录分组卡:标题行(可收起分组的切换入口) + 分组体
// (loading / error / 空态 / 列表),收起时渲染「Bot 泊位」压缩预览。
// 与 ConversationSidebar 拆分自同文件(2026-10-10 收起需求 + TC-G005 文件体积门禁)。
import { Button, Empty, IconButton } from '@/components/ui';
import type { ConversationBotView } from '@/domain/conversation/types';
import { ChevronDown, ChevronRight, Plus } from 'lucide-react';
import type { ReactNode } from 'react';
import { ListErrorState } from '../../components/ListErrorState';
import { ConversationBotDock } from './ConversationBotDock';
import { ConversationBotRowsSkeleton } from './ConversationSkeletons';

export interface ConversationSidebarSectionProps {
  title: string;
  loading: boolean;
  error: string | null;
  onRetry(): void;
  items: ConversationBotView[];
  emptyTitle: string;
  emptyHint: string;
  isSearching: boolean;
  /** 空态「前往公开 Bot」入口;团队分组等不引导跳转时缺省不渲染。 */
  onOpenPublicBots?(): void;
  renderItem(view: ConversationBotView): ReactNode;
  /** 可收起分组(我的/好友 Bot):缺省展开,标题行点击切换;搜索时强制展开以保证可检索。 */
  collapsible?: boolean;
  collapsed: boolean;
  onToggleCollapsed(): void;
  /** 收起分组「Bot 泊位」直达:传入即启用;头像点击展开分组并打开该 Bot 会话。 */
  onQuickOpen?(view: ConversationBotView): void;
  /** 泊位中当前选中 Bot 的 botId(调用方按 section 判定后传入);缺省不选中。 */
  dockSelectedBotId?: string | null;
}

export function ConversationSidebarSection(props: ConversationSidebarSectionProps) {
  const { title, loading, error, onRetry, items } = props;
  const { emptyTitle, emptyHint, isSearching, onOpenPublicBots, renderItem, collapsible } = props;
  const { collapsed, onToggleCollapsed, onQuickOpen, dockSelectedBotId } = props;
  const bodyHidden = collapsible === true && collapsed;
  return (
    <div className="pb-1" role="group" aria-label={title}>
      {/* 分组标题行（dmore DOM 实测：h=32、与列表统一 36px 节奏）：12px 常规字重 #868686；
          x 与列表内容左缘对齐；hover 保留「+」添加入口。
          可收起分组标题即切换按钮（2026-10-10 需求：我的/好友 Bot 列表支持展开收起，
          箭头语义与 Bot 行尾随箭头一致：ChevronDown=展开,ChevronRight=收起）。 */}
      <div className="group/title flex min-h-8 items-center justify-between gap-1 px-4">
        {collapsible ? (
          <Button
            variant="ghost"
            aria-expanded={!collapsed}
            className="flex h-auto min-w-0 items-center justify-start gap-1 rounded-none px-0 py-1 text-xs font-normal text-content-soft hover:bg-transparent"
            onClick={onToggleCollapsed}
          >
            <span className="truncate">{title}</span>
            {collapsed ? (
              <ChevronRight className="h-3.5 w-3.5 shrink-0 text-content-icon" aria-hidden="true" />
            ) : (
              <ChevronDown className="h-3.5 w-3.5 shrink-0 text-content-icon" aria-hidden="true" />
            )}
          </Button>
        ) : (
          <p className="text-xs font-normal text-content-soft">{title}</p>
        )}
        <IconButton
          label="添加 Bot"
          variant="ghost"
          icon={<Plus className="h-3.5 w-3.5" />}
          className="h-5 w-5 rounded-sm text-muted-foreground opacity-0 transition-opacity hover:bg-muted hover:text-foreground group-hover/title:opacity-100 group-focus-within/title:opacity-100 [@media(hover:none)]:opacity-100"
          onClick={onOpenPublicBots}
        />
      </div>
      {bodyHidden ? (
        // 收起分组不演「盲区」:渲染「Bot 泊位」压缩预览(有成员才出现),
        // 头像直达 + 选中态底保留,展开材料与列表行完全一致(16px AvatarTile)。
        onQuickOpen && items.length > 0 ? (
          <ConversationBotDock
            items={items}
            selectedBotId={dockSelectedBotId ?? null}
            onQuickOpen={onQuickOpen}
            onExpandGroup={onToggleCollapsed}
          />
        ) : null
      ) : loading ? (
        <ConversationBotRowsSkeleton rows={3} />
      ) : error ? (
        <ListErrorState message={error} onRetry={onRetry} />
      ) : items.length === 0 ? (
        isSearching ? null : (
          <Empty
            compact
            title={emptyTitle}
            description={emptyHint}
            action={
              <Button variant="secondary" size="sm" onClick={onOpenPublicBots}>
                前往公开 Bot
              </Button>
            }
          />
        )
      ) : (
        // 36px 列节奏 = 32px 行 + 4px 间距（dmore DOM 实测，B1 精修 6.2）。
        <div className="flex flex-col gap-1">{items.map(renderItem)}</div>
      )}
    </div>
  );
}
