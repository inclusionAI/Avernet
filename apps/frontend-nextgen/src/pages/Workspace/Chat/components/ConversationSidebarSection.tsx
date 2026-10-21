// 对话页侧栏目录分组卡:标题行(可收起分组的切换入口) + 分组体
// (loading / error / 空态 / 列表),收起时渲染「Bot 泊位」压缩预览。
// 与 ConversationSidebar 拆分自同文件(2026-10-10 收起需求 + TC-G005 文件体积门禁)。
import { Button, Empty, Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui';
import type { ConversationBotView } from '@/domain/conversation/types';
import { ChevronDown, ChevronRight, Info } from 'lucide-react';
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
  /** 空态标题;空分组隐藏的分组不传。 */
  emptyTitle?: string;
  /** 空态提示,可含跳转链接(由调用方组装 ReactNode)。 */
  emptyHint?: ReactNode;
  isSearching: boolean;
  /** 标题行来源提示(i 图标悬停说明;缺省不渲染)。 */
  infoHint?: string;
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
  const { emptyTitle, emptyHint, isSearching, renderItem, collapsible, infoHint } = props;
  const { collapsed, onToggleCollapsed, onQuickOpen, dockSelectedBotId } = props;
  const bodyHidden = collapsible === true && collapsed;
  return (
    <div className="pb-1" role="group" aria-label={title}>
      {/* 分组标题行（dmore DOM 实测：h=32、与列表统一 36px 节奏）：12px 常规字重 #868686；
          x 与列表内容左缘对齐；标题行不再渲染「+」添加入口（2026-10-14 体验修复）。
          可收起分组标题即切换按钮（2026-10-10 需求：我的/好友 Bot 列表支持展开收起，
          箭头语义与 Bot 行尾随箭头一致：ChevronDown=展开,ChevronRight=收起）。 */}
      <div className="flex min-h-8 items-center justify-between gap-1 px-4">
        <div className="flex min-w-0 items-center gap-0.5">
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
          {infoHint && (
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <span
                    role="img"
                    aria-label={infoHint}
                    className="inline-flex shrink-0 cursor-help items-center text-content-icon"
                  >
                    <Info className="h-3 w-3" aria-hidden="true" />
                  </span>
                </TooltipTrigger>
                <TooltipContent>{infoHint}</TooltipContent>
              </Tooltip>
            </TooltipProvider>
          )}
        </div>
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
          <Empty compact title={emptyTitle} description={emptyHint} />
        )
      ) : (
        // 36px 列节奏 = 32px 行 + 4px 间距（dmore DOM 实测，B1 精修 6.2）。
        <div className="flex flex-col gap-1">{items.map(renderItem)}</div>
      )}
    </div>
  );
}
