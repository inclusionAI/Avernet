// Admin 方角小标签（admin 视觉交互指南 §2.2/§7.1）。
// 不复用 ui Badge（其默认 rounded-full 胶囊形与方角 tag 不符），admin 模块内部复用。
// 颜色只走 shadcn 语义 token：蓝统一 primary（--primary=#165dff，见 showcase-patterns §0/§2.4），紫 brand / 绿 success / 橙 warning。禁裸 RGB 与 --color-* 遗产，暗色自适配。
import { cn } from '@/utils/cn';
import React from 'react';

export type TagTone = 'blue' | 'purple' | 'green' | 'orange';

const TONES: Record<TagTone, string> = {
  blue: 'bg-primary/10 text-primary',
  purple: 'bg-brand/10 text-brand',
  green: 'bg-success/10 text-success',
  orange: 'bg-warning/10 text-warning',
};

export interface TagProps extends React.HTMLAttributes<HTMLSpanElement> {
  tone?: TagTone;
}

export function Tag({ tone, className, ...props }: TagProps) {
  return (
    <span
      className={cn(
        'inline-flex h-5 items-center gap-1 rounded-sm px-2 text-[12px] font-medium leading-5',
        tone ? TONES[tone] : 'bg-muted text-muted-foreground',
        className,
      )}
      {...props}
    />
  );
}

export default Tag;
