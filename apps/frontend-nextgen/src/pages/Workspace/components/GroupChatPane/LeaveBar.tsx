import { MessageViewScopeBadge } from '@/components/MessageViewScope';
import { Badge, Button } from '@/components/ui';
import type { MessageViewScope } from '@/domain/collaboration/types';
import { Eye, EyeOff, Loader2, LogOut, Volume2 } from 'lucide-react';
import type { ReactNode } from 'react';

/** 「在会话中隐身」条（human 视角 present 态，显示在聊天输入框上方）。 */
export function LeaveBar({
  humanName,
  onLeave,
  leaving,
  viewScope,
  switchingViewScope,
  onViewScopeChange,
  activeRuns,
}: {
  humanName: string;
  onLeave: () => void;
  leaving: boolean;
  /** human 成员消息可见域回显；缺失（null/undefined）时不渲染视角切换按钮与视角 Badge。 */
  viewScope?: MessageViewScope | null;
  /** 视角切换请求进行中（禁用切换按钮）。 */
  switchingViewScope?: boolean;
  /** 切换消息可见域（参与者视角 ↔ 完整视角），参数为目标 scope。 */
  onViewScopeChange?: (scope: MessageViewScope) => void;
  activeRuns?: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-2 py-1.5 sm:flex-row sm:items-center sm:justify-between">
      <div className="flex min-w-0 flex-1 items-center gap-2.5">
        <Volume2 className="h-4 w-4 shrink-0 text-primary" />
        <div className="min-w-0 shrink-0">
          <p className="text-sm font-medium leading-tight text-foreground">
            {humanName}{' '}
            <Badge tone="success" className="ml-1 align-middle">
              用户发言模式
            </Badge>
            {viewScope ? (
              <span className="ml-1 align-middle">
                <MessageViewScopeBadge scope={viewScope} />
              </span>
            ) : null}
          </p>
          <p className="mt-0.5 text-xs leading-tight text-muted-foreground">以用户身份发言中，可随时隐身退出。</p>
        </div>
        {activeRuns}
      </div>
      <div className="flex w-full flex-wrap items-center justify-start gap-2 sm:w-auto sm:justify-end sm:shrink-0 sm:gap-3">
        <Button
          size="sm"
          variant="ghost"
          disabled={leaving}
          onClick={onLeave}
          className="shrink-0 border border-border text-muted-foreground hover:border-destructive/30 hover:text-destructive"
        >
          {leaving ? <Loader2 className="h-4 w-4 animate-spin" /> : <LogOut className="h-4 w-4" />}
          在会话中隐身
        </Button>
        {viewScope && onViewScopeChange ? (
          <Button
            size="sm"
            variant="ghost"
            disabled={switchingViewScope}
            onClick={() => onViewScopeChange(viewScope === 'full' ? 'participant' : 'full')}
            className="shrink-0 border border-border text-muted-foreground hover:border-primary/30 hover:text-primary"
          >
            {switchingViewScope ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : viewScope === 'full' ? (
              <EyeOff className="h-4 w-4" />
            ) : (
              <Eye className="h-4 w-4" />
            )}
            {viewScope === 'full' ? '切换到参与者视角' : '切换到完整视角'}
          </Button>
        ) : null}
      </div>
    </div>
  );
}
