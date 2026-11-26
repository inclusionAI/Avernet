import { Button, Drawer, DrawerContent, DrawerDescription, DrawerTitle, Empty, IconButton } from '@/components/ui';
import { useWorkflowList } from '@/hooks/useWorkflowList';
import type { TaskComposerContext } from '@/services/tasks/taskMapper';
import { Maximize2, Minimize2, ShieldCheck, X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
// 仅复用抽屉尺寸/响应式样式；实时入口不加载演示数据或演示组件。
import '../TaskEscortDemo/styles.css';
import { WorkflowSelector } from './WorkflowSelector';

export interface TaskEscortPanelProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 与「+」任务菜单完全相同的上下文；这里不重算查看者或资产所有者。 */
  context: TaskComposerContext | null;
  botName?: string;
}

/** 实时护航目前仅接入可执行工作流列表，不推断定义/编辑权限或运行数据。 */
export function TaskEscortPanel({ open, onOpenChange, context, botName }: TaskEscortPanelProps) {
  const { workflows, workflowsLoading, workflowsError, loadWorkflows } = useWorkflowList(open ? context : null);
  const contextKey = context ? JSON.stringify([context.ownerUserId, context.ownerBotId]) : null;
  const [selection, setSelection] = useState<{ contextKey: string; workflowId: string } | null>(null);
  const [expanded, setExpanded] = useState(false);
  const closeRef = useRef<HTMLButtonElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    setSelection(null);
  }, [open, contextKey]);
  useEffect(() => {
    // 关闭时 hook 使用 null 清空缓存；每次重开都重新读取当前权限列表。
    if (open) void loadWorkflows();
  }, [open, loadWorkflows]);

  const available = open && context && !workflowsLoading && !workflowsError ? workflows : [];
  const workflow =
    (selection?.contextKey === contextKey
      ? available.find((item) => item.workflowId === selection.workflowId)
      : null) ?? available[0] ?? null;
  const selectWorkflow = (workflowId: string) => {
    // 只接受当前返回列表中的选择；旧菜单回调不能恢复跨身份/跨 Bot 的选中项。
    if (!contextKey || !available.some((item) => item.workflowId === workflowId)) return;
    setSelection({ contextKey, workflowId });
  };

  return (
    <Drawer open={open} onOpenChange={onOpenChange} modal={false}>
      <DrawerContent
        // 保留聊天共用入口的 aria-controls ID；独立 demo 宿主仍使用自己的原始抽屉。
        id="task-escort-demo-panel"
        ref={contentRef}
        side="right"
        showOverlay={false}
        showClose={false}
        className="task-escort-demo-panel workspace-overlay-panel max-w-none"
        data-expanded={expanded}
        bodyClassName="flex min-w-0 flex-col overflow-hidden p-0"
        onInteractOutside={(event) => event.preventDefault()}
        onEscapeKeyDown={(event) => {
          if (event.target instanceof Node && contentRef.current?.contains(event.target)) event.preventDefault();
        }}
        onKeyDownCapture={(event) => {
          // Select 的 Portal 先处理自身 Escape，不同时关闭抽屉。
          if (event.key !== 'Escape' || !event.currentTarget.contains(event.target as Node)) return;
          event.preventDefault();
          event.stopPropagation();
          onOpenChange(false);
        }}
        onOpenAutoFocus={(event) => {
          event.preventDefault();
          returnFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
          closeRef.current?.focus();
        }}
        onCloseAutoFocus={(event) => {
          event.preventDefault();
          const previous = returnFocusRef.current;
          const markedTrigger = document.querySelector<HTMLElement>('[data-task-escort-trigger]');
          const trigger =
            previous?.matches('[data-task-escort-trigger]') && previous.isConnected ? previous : markedTrigger;
          if (trigger) trigger.focus({ preventScroll: true });
          else if (previous?.isConnected) previous.focus({ preventScroll: true });
        }}
      >
        <header className="flex shrink-0 items-center justify-between gap-3 border-b border-border px-5 py-4">
          <div className="flex min-w-0 items-center gap-3">
            <span className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary">
              <ShieldCheck className="size-5" aria-hidden />
            </span>
            <DrawerTitle className="text-base font-semibold">任务护航</DrawerTitle>
          </div>
          <div className="flex shrink-0 items-center gap-1">
            <IconButton
              label={expanded ? '还原抽屉' : '展开抽屉'}
              aria-pressed={expanded}
              onClick={() => setExpanded(!expanded)}
              icon={expanded ? <Minimize2 className="size-4" /> : <Maximize2 className="size-4" />}
            />
            <IconButton
              ref={closeRef}
              label="关闭任务护航"
              onClick={() => onOpenChange(false)}
              icon={<X className="size-4" />}
            />
          </div>
        </header>
        <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-border bg-muted/30 px-5 py-3 text-xs">
          <WorkflowSelector
            key={contextKey}
            workflow={workflow}
            workflows={available}
            onSelect={selectWorkflow}
          />
          <span className="text-muted-foreground">当前用户可执行的工作流</span>
          {botName && <span className="ml-auto max-w-48 truncate text-muted-foreground">当前 Bot · {botName}</span>}
        </div>
        <main className="min-h-0 flex-1 overflow-y-auto overscroll-contain bg-muted/25 p-5" aria-busy={workflowsLoading}>
          {!context ? (
            <Empty title="缺少工作流上下文" description="请先选择可发起任务的 Bot 会话，再打开任务护航。" />
          ) : workflowsLoading ? (
            <div role="status"><Empty title="正在加载工作流" description="正在获取当前用户可执行的工作流列表。" /></div>
          ) : workflowsError ? (
            <div role="alert">
              <Empty
                title="工作流加载失败"
                description={workflowsError}
                action={<Button variant="outline" onClick={() => void loadWorkflows()}>重试加载工作流</Button>}
              />
            </div>
          ) : !workflow ? (
            <Empty title="暂无可执行的工作流" description="当前上下文未返回可执行的工作流，可关闭任务护航继续聊天。" />
          ) : (
            <Empty
              title="运行数据暂未接入"
              description="当前仅接入工作流列表，运行记录、指标与工作流定义尚未连接。可执行权限不代表查看或编辑权限。"
            />
          )}
        </main>
        <footer className="flex shrink-0 flex-wrap items-center justify-between gap-1 border-t border-border bg-background px-5 py-2 text-[10px] text-muted-foreground">
          <DrawerDescription asChild><span>仅接入工作流列表 · 不展示运行记录、指标或定义</span></DrawerDescription>
          <span>Esc 关闭 · 聊天区仍可交互</span>
        </footer>
      </DrawerContent>
    </Drawer>
  );
}

export default TaskEscortPanel;
