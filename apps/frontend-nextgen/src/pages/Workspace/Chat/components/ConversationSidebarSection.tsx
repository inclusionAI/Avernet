// 对话页侧栏目录分组卡:标题行(含成员计数) + 分组体(loading / error / 空态 / 列表)。
// 收起分组只留标题行（2026-10-22 需求变更:「Bot 泊位」头像预览删除,收起即净,
// 成员数由标题右缘计数承载;原 2026-10-10「收起不留盲区」裁决作废）。
// 与 ConversationSidebar 拆分自同文件(2026-10-10 收起需求 + TC-G005 文件体积门禁)。
import { Button, Empty } from '@/components/ui';
import type { ConversationBotView } from '@/domain/conversation/types';
import { cn } from '@/utils/cn';
import { ChevronDown } from 'lucide-react';
import type { ReactNode } from 'react';
import { ListErrorState } from '../../components/ListErrorState';
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
  renderItem(view: ConversationBotView): ReactNode;
  /** 可收起分组(我的/好友 Bot):缺省展开,标题行点击切换;搜索时强制展开以保证可检索。 */
  collapsible?: boolean;
  collapsed: boolean;
  onToggleCollapsed(): void;
}

export function ConversationSidebarSection(props: ConversationSidebarSectionProps) {
  const { title, loading, error, onRetry, items } = props;
  const { emptyTitle, emptyHint, isSearching, renderItem, collapsible } = props;
  const { collapsed, onToggleCollapsed } = props;
  const bodyHidden = collapsible === true && collapsed;
  return (
    <div className="pb-1 pt-0.5" role="group" aria-label={title}>
      {/* 分组标题行（dmore DOM 实测：h=32、与列表统一 36px 节奏）：12px 常规字重 #868686；
          x 与列表内容左缘对齐；标题行不再渲染「+」添加入口（2026-10-14 体验修复）。
          可收起分组标题即切换按钮（2026-10-10 需求：我的/好友 Bot 列表支持展开收起，
          箭头语义与 Bot 行尾随箭头一致：ChevronDown=展开,ChevronRight=收起）。
          标题行右缘补成员计数（2026-10-22 体验精修）：aria-hidden 纯视觉数字（行数由列表
          本身可达，读屏不重复报），且置于切换按钮之外 → 按钮可访问名仍为纯标题。 */}
      <div className="flex min-h-8 items-center justify-between gap-1 px-4">
        {collapsible ? (
          <Button
            variant="ghost"
            aria-expanded={!collapsed}
            className={cn(
              // hover pill(2026-10-22 体验精修二轮):-mx-1 + px-1 补偿保持标题文字 x 不动,
              // 悬停给极轻 muted 底揭示「标题即开关」可点性;去掉了原 hover:bg-transparent 的零反馈。
              'flex h-auto min-w-0 items-center justify-start gap-1 rounded-md py-1 pr-1.5 pl-1 text-xs font-normal text-content-soft',
              '-mx-1 hover:bg-muted/50 transition-colors',
            )}
            onClick={onToggleCollapsed}
          >
            <span className="truncate">{title}</span>
            {/* chevron 旋转过渡:单 ChevronDown,收起 -rotate-90,与 Bot 行箭头同一动效语言。 */}
            <ChevronDown
              className={cn(
                'h-3.5 w-3.5 shrink-0 text-content-icon transition-transform duration-150',
                collapsed && '-rotate-90',
              )}
              aria-hidden="true"
            />
          </Button>
        ) : (
          <p className="text-xs font-normal text-content-soft">{title}</p>
        )}
        {items.length > 0 && (
          <span
            aria-hidden="true"
            className="shrink-0 text-[11px] font-normal tabular-nums text-content-icon"
          >
            {items.length}
          </span>
        )}
      </div>
      {bodyHidden ? (
        // 收起分组只留标题行(2026-10-22 需求变更):不再渲染任何 Bot 泊位/头像预览,
        // 成员量由标题右缘计数承载,展开仍由标题行一键恢复。
        null
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
