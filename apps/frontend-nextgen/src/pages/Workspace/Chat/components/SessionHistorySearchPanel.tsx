/**
 * SessionHistorySearchPanel —— 历史消息面板（入口=头部「历史消息」，chat-header-panels）。
 *
 * dmore artboard-006/007（同一面板空态/搜索高亮两形态）：搜索输入 + 筛选（全部/钉住/发送方/时间段）
 * +「共找到 N 条结果」+ 结果列表点击定位高亮。本地降级态：隐藏「钉住」「平台」（均为后端索引维度，
 * 本地消息模型无 pinned 字段），并明示检索范围为已载入消息（spec 修订注）。
 */
import { Button, Input } from '@/components/ui';
import { cn } from '@/utils/cn';
import type { ChatMessage } from '@tc-chat/core';
import { Search } from 'lucide-react';
import type { UseSessionHistorySearchModel } from '../hooks/useChatHeaderPanels';

export interface SessionHistorySearchPanelProps {
  search: UseSessionHistorySearchModel;
  /** 点击结果：上层定位滚动 + 高亮（locateMessage）。 */
  onLocate: (messageId: string) => void;
  onScrollToHistory?: () => void;
}

const SENDER_OPTIONS: { value: 'user' | 'assistant' | 'system'; label: string }[] = [
  { value: 'user', label: '用户' },
  { value: 'assistant', label: 'Bot' },
  { value: 'system', label: '系统' },
];

const SENDER_LABEL: Record<ChatMessage['role'], string> = {
  user: '用户',
  assistant: 'Bot',
  system: '系统',
};

function FilterChip({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <Button
      variant={active ? 'secondary' : 'ghost'}
      size="sm"
      onClick={onClick}
      aria-pressed={active}
      className={cn('h-7 rounded-full px-2.5 text-xs', active && 'text-foreground')}
    >
      {label}
    </Button>
  );
}

/** 历史消息 + 搜索合并视图。 */
export function SessionHistorySearchPanel({ search, onLocate }: SessionHistorySearchPanelProps) {
  const { query, setKeyword, setSender, setTodayOnly, source, results } = search;
  const count = results.length;
  return (
    <section aria-label="历史消息" className="flex min-h-0 flex-1 flex-col gap-3">
      <div className="relative">
        <Search
          aria-hidden="true"
          className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground"
        />
        <Input
          value={query.keyword}
          onChange={(e) => setKeyword(e.target.value)}
          placeholder="搜索消息"
          aria-label="搜索消息"
          className="h-[30px] rounded-full pl-8 text-[13px]"
        />
      </div>
      <div className="flex flex-wrap items-center gap-1">
        <FilterChip
          label="全部"
          active={query.sender === null && !query.todayOnly}
          onClick={() => {
            setSender(null);
            setTodayOnly(false);
          }}
        />
        {/* 发送方筛选：单选切换，再点一次回全部。 */}
        {SENDER_OPTIONS.map((option) => (
          <FilterChip
            key={option.value}
            label={option.label}
            active={query.sender === option.value}
            onClick={() => setSender(query.sender === option.value ? null : option.value)}
          />
        ))}
        <FilterChip label="今天" active={query.todayOnly} onClick={() => setTodayOnly(!query.todayOnly)} />
      </div>
      {/* dmore：共找到 N 条结果 计数行。 */}
      <p className="text-xs text-muted-foreground">共找到 {count} 条结果</p>
      {source === 'local' && (
        <p className="text-xs text-content-soft">检索范围：当前会话已载入的消息（完整历史检索接口待接入）</p>
      )}
      <div className="flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto">
        {count === 0 ? (
          <p className="px-1 py-3 text-xs text-muted-foreground">无匹配消息</p>
        ) : (
          results.map((result) => (
            <Button
              key={result.messageId}
              variant="ghost"
              onClick={() => onLocate(result.messageId)}
              className="h-auto w-full flex-col items-start gap-1 rounded-lg px-2.5 py-2 text-left"
            >
              <span className="flex w-full items-center gap-2">
                <span className="shrink-0 text-xs text-muted-foreground">{SENDER_LABEL[result.role]}</span>
                <span className="min-w-0 flex-1 truncate text-[13px] text-foreground">{result.snippet}</span>
              </span>
            </Button>
          ))
        )}
      </div>
    </section>
  );
}
