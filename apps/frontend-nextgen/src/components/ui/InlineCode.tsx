import { cn } from '@/utils/cn';
import React from 'react';

/** 行内代码标签：提示文案中 Bot 名称/用户名称等标识的统一行内强调形态
 * （弹窗、说明文字里内联展示，软色底 + 等宽字体；抽自 FriendRequestConfirmModal，
 * 移除好友确认弹窗为第二个调用方，收敛为共享组件）。 */
export function InlineCode({ className, ...props }: React.HTMLAttributes<HTMLElement>) {
  return (
    <code
      className={cn('rounded bg-muted px-1.5 py-0.5 font-mono text-[0.9em] break-all', className)}
      {...props}
    />
  );
}
