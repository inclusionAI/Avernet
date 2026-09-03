// Admin 专用轻量 tab 组件（对齐 PRD ant-tabs 视觉，不引入 antd）。
// - UnderlineTabs：主 tab（空间管理/工单中心），下划线指示器，54px 高，ink-bar 3px 主色。
// - CardTabs：工单视图 tab（待我处理/我发起的/已处理），卡片式，40px 高，激活态白底主色字，独立于下方内容面板（间距由调用方给）。
import { Button } from '@/components/ui';
import { cn } from '@/utils/cn';
import React from 'react';

export interface TabOption<T extends string> {
  value: T;
  label: React.ReactNode;
}

export function UnderlineTabs<T extends string>({
  value,
  options,
  onChange,
  className,
}: {
  value: T;
  options: TabOption<T>[];
  onChange: (v: T) => void;
  className?: string;
}) {
  return (
    <div className={cn('flex items-stretch gap-8', className)} role="tablist">
      {options.map((opt) => {
        const active = opt.value === value;
        return (
          <Button
            key={opt.value}
            variant="ghost"
            role="tab"
            aria-selected={active}
            onClick={() => onChange(opt.value)}
            className={cn(
              'relative h-[54px] justify-center px-1 text-sm transition-colors hover:bg-transparent',
              active
                ? 'font-medium text-foreground hover:text-foreground'
                : 'font-normal text-muted-foreground hover:text-primary',
            )}
          >
            <span className="flex h-full items-center">{opt.label}</span>
            {/* 下划线始终渲染，inactive 时 scale-x-0 收起，切换时中心展开 */}
            <span
              className={cn(
                'absolute inset-x-0 bottom-0 h-[3px] rounded-t-full bg-primary',
                'transition-transform duration-200 ease-out',
                active ? 'scale-x-100' : 'scale-x-0',
              )}
              aria-hidden
            />
          </Button>
        );
      })}
    </div>
  );
}

export function CardTabs<T extends string>({
  value,
  options,
  onChange,
  className,
}: {
  value: T;
  options: TabOption<T>[];
  onChange: (v: T) => void;
  className?: string;
}) {
  return (
    <div className={cn('flex items-end gap-1', className)} role="tablist">
      {options.map((opt) => {
        const active = opt.value === value;
        return (
          <Button
            key={opt.value}
            variant="ghost"
            role="tab"
            aria-selected={active}
            onClick={() => onChange(opt.value)}
            className={cn(
              'h-10 justify-center px-4 text-sm rounded-lg border transition-colors',
              active
                ? 'border-border bg-card font-medium text-primary shadow-sm hover:bg-card'
                : 'border-border bg-muted/60 font-normal text-muted-foreground hover:bg-muted hover:text-foreground',
            )}
          >
            {opt.label}
          </Button>
        );
      })}
    </div>
  );
}

export default UnderlineTabs;

// 纤细分段控件（segmented：容器 rounded-md、项 h24/fz12/自然宽度、
// 选中白底深色 medium、未选灰字）。用于工单分类「全部/审批类/通知类」。
export function MiniSegmented<T extends string>({
  value,
  options,
  onChange,
  className,
}: {
  value: T;
  options: TabOption<T>[];
  onChange: (v: T) => void;
  className?: string;
}) {
  return (
    <div className={cn('inline-flex items-center rounded-md bg-muted p-0.5', className)} role="tablist">
      {options.map((opt) => {
        const active = opt.value === value;
        return (
          <Button
            key={opt.value}
            variant="ghost"
            role="tab"
            aria-selected={active}
            onClick={() => onChange(opt.value)}
            className={cn(
              'h-6 shrink-0 justify-center rounded px-2.5 text-xs leading-6 transition-colors',
              active
                ? 'bg-card font-medium text-foreground shadow-sm hover:bg-card'
                : 'font-normal text-muted-foreground hover:text-foreground',
            )}
          >
            {opt.label}
          </Button>
        );
      })}
    </div>
  );
}
