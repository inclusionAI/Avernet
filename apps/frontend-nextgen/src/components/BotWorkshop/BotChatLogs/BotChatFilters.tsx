import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/Popover';
import type { BotChatFilters as Filters } from '@/domain/botChats';
import { RotateCcw, Search, SlidersHorizontal } from 'lucide-react';
import React from 'react';

interface Props {
  value: Filters;
  loading: boolean;
  onChange: (key: keyof Filters, value: string) => void;
  onQuery: () => void;
  onReset: () => void;
}

/**
 * 查询脊线（设计延展 §2)：首页 Toolbar 同语法——常驻小标题在控件左侧、
 * 控件内不重复标题。高频字段（时间/traceID/输入-输出）常驻一行；
 * 低频五字段收进「更多筛选」Popover，生效数以品牌蓝徽标实时反馈。
 */
const lowFreqFields: { key: keyof Filters; label: string; mono?: boolean }[] = [
  { key: 'bizScene', label: '业务场景' },
  { key: 'bizTaskId', label: '业务任务' },
  { key: 'groupId', label: '群ID', mono: true },
  { key: 'sessionId', label: 'sessionID', mono: true },
  { key: 'sessionKey', label: 'sessionKey', mono: true },
];

const labelClass = 'shrink-0 whitespace-nowrap text-xs font-medium text-muted-foreground';

export function BotChatFilters({ value, loading, onChange, onQuery, onReset }: Props) {
  const onKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter') onQuery();
  };
  const activeLowFreqCount = lowFreqFields.filter((field) => value[field.key]).length;
  const clearLowFreq = () => lowFreqFields.forEach((field) => onChange(field.key, ''));

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-3">
      {/* 时间范围：单一容器内并置起止两个 datetime-local。 */}
      <div className="flex w-full min-w-0 items-center gap-2 sm:w-auto">
        <span id="bot-chat-filter-time-label" className={labelClass}>
          时间
        </span>
        <div className="flex h-8 min-w-0 flex-1 items-center gap-1 rounded-lg border border-input bg-background px-2 focus-within:border-brand focus-within:ring-1 focus-within:ring-brand/20 sm:w-[336px]">
          <Input
            aria-label="开始时间"
            className="min-w-0 flex-1 border-0 bg-transparent px-1 focus-visible:border-transparent focus-visible:ring-0"
            type="datetime-local"
            value={value.fromDate}
            onChange={(event) => onChange('fromDate', event.target.value)}
            onKeyDown={onKeyDown}
          />
          <span className="text-xs text-muted-foreground">～</span>
          <Input
            aria-label="结束时间"
            className="min-w-0 flex-1 border-0 bg-transparent px-1 focus-visible:border-transparent focus-visible:ring-0"
            type="datetime-local"
            value={value.toDate}
            onChange={(event) => onChange('toDate', event.target.value)}
            onKeyDown={onKeyDown}
          />
        </div>
      </div>
      {/* traceID：等宽占位，标识符语境走仪器字体（签名元素之一）。 */}
      <label className="flex w-full min-w-0 items-center gap-2 sm:w-auto">
        <span id="bot-chat-filter-trace-label" className={labelClass}>
          traceID
        </span>
        <Input
          aria-labelledby="bot-chat-filter-trace-label"
          className="w-full min-w-0 flex-1 font-mono sm:w-[200px]"
          value={value.traceId}
          placeholder="9f3a…"
          onChange={(event) => onChange('traceId', event.target.value)}
          onKeyDown={onKeyDown}
        />
      </label>
      <label className="flex w-full min-w-0 items-center gap-2 sm:w-auto">
        <span id="bot-chat-filter-keyword-label" className={labelClass}>
          输入/输出
        </span>
        <div className="relative w-full min-w-0 flex-1 sm:w-[240px]">
          <Search
            className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
            aria-hidden
          />
          <Input
            aria-labelledby="bot-chat-filter-keyword-label"
            className="pl-9"
            value={value.keyword}
            placeholder="input/output"
            onChange={(event) => onChange('keyword', event.target.value)}
            onKeyDown={onKeyDown}
          />
        </div>
      </label>
      <Popover>
        <PopoverTrigger asChild>
          <Button variant="secondary" leftIcon={<SlidersHorizontal className="size-4" />}>
            更多筛选
            {activeLowFreqCount > 0 ? (
              <span
                aria-label={`已生效 ${activeLowFreqCount} 个筛选`}
                className="ml-0.5 inline-flex h-4.5 min-w-4.5 items-center justify-center rounded-full bg-primary px-1 text-[10px] font-medium leading-none text-primary-foreground"
              >
                {activeLowFreqCount}
              </span>
            ) : null}
          </Button>
        </PopoverTrigger>
        <PopoverContent align="end" className="w-[340px] space-y-3 p-4">
          <div className="space-y-3">
            {lowFreqFields.map((field) => (
              <label key={field.key} className="flex w-full items-center gap-2">
                <span className={`w-[68px] ${labelClass}`}>{field.label}</span>
                <Input
                  aria-label={field.label}
                  className={`min-w-0 flex-1 ${field.mono ? 'font-mono' : ''}`}
                  value={value[field.key]}
                  placeholder="请输入"
                  onChange={(event) => onChange(field.key, event.target.value)}
                  onKeyDown={onKeyDown}
                />
              </label>
            ))}
          </div>
          <div className="flex items-center justify-between border-t border-border pt-3">
            <Button
              variant="ghost"
              size="sm"
              onClick={clearLowFreq}
              disabled={activeLowFreqCount === 0}
              leftIcon={<RotateCcw className="size-3.5" />}
            >
              清空低频
            </Button>
            <Button size="sm" onClick={onQuery}>
              查询
            </Button>
          </div>
        </PopoverContent>
      </Popover>
      <div className="ml-auto flex items-center gap-2">
        <Button loading={loading} leftIcon={<Search className="size-4" />} onClick={onQuery}>
          查询
        </Button>
        <Button variant="ghost" leftIcon={<RotateCcw className="size-4" />} onClick={onReset}>
          重置
        </Button>
      </div>
    </div>
  );
}