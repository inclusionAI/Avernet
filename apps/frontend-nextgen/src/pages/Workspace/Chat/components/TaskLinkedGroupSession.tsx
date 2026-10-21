import { GroupSessionView } from '@/assets/TaskPanel/GroupDrillDown';
import { Button } from '@/components/ui/Button';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import type { TaskLinkedGroupSession as LinkedTarget } from '@/stores/taskSidePanelStore';
import type { TaskNodeView } from '@/assets/TaskPanel/types';
import { X } from 'lucide-react';

/**
 * 对话页主区的跨域联动群会话视图(PRD 左右联动的"左边对话框"跨域分支):
 * 任务副屏点群节点时,原位覆盖主区展示该群会话内容(群成员 + 群消息明细,只读),
 * 不离开当前页面、不破坏已挂载的交互舞台与任务副屏 tab。
 * 内容承载复用资产 GroupSessionView(与 L2 下钻同源);"返回当前会话"收起覆盖层。
 */
export function TaskLinkedGroupSession({
  target,
  userId,
  onClose,
}: {
  target: LinkedTarget;
  userId?: string;
  onClose: () => void;
}) {
  // GroupSessionView 以 TaskNodeView 形状取数:构造联动伪节点(仅会话定位字段有意义)
  const node = {
    id: `linked-${target.sessionId}`,
    name: '群会话',
    sequence: 0,
    status: 'running',
    runMode: 'coop_group',
    groupId: target.groupId,
    sessionId: target.sessionId,
    artifacts: [],
    stepTraces: [],
    hasSubTask: false,
  } as unknown as TaskNodeView;

  return (
    <div
      aria-label="任务联动群会话"
      className="absolute inset-0 z-30 flex h-full min-h-0 w-full flex-col bg-background"
    >
      <header className="flex h-12 shrink-0 items-center justify-between gap-3 border-b border-border px-4">
        <div className="flex min-w-0 items-center gap-2">
          <span className="truncate text-sm font-semibold text-foreground">任务联动 · 群会话</span>
          <span className="truncate font-mono text-xs text-muted-foreground">{target.sessionId}</span>
        </div>
        <TooltipProvider delayDuration={300}>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              aria-label="返回当前会话"
              onClick={onClose}
              className="size-8 shrink-0 p-0 text-muted-foreground hover:text-foreground"
            >
              <X className="size-4" aria-hidden />
            </Button>
          </TooltipTrigger>
          <TooltipContent>返回当前会话</TooltipContent>
        </Tooltip>
        </TooltipProvider>
      </header>
      <div className="app-scrollbar min-h-0 flex-1 overflow-y-auto">
        <GroupSessionView
          node={node}
          bcsBaseUrl=""
          apiBaseUrl=""
          userId={userId}
          onBack={onClose}
        />
      </div>
    </div>
  );
}

export default TaskLinkedGroupSession;