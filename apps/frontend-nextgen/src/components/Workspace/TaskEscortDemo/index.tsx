import { Drawer, DrawerContent, DrawerDescription, DrawerTitle, Empty, IconButton, Segmented } from '@/components/ui';
import { Maximize2, Minimize2, ShieldCheck, X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { DemoWorkflow } from './model';
import { Overview } from './Overview';
import './styles.css';
import { useTaskEscortDemo } from './useTaskEscortDemo';
import { WorkflowEditor } from './WorkflowEditor';
import { WorkflowSelector } from './WorkflowSelector';

export interface TaskEscortDemoPanelProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  botName?: string;
  workflows?: readonly DemoWorkflow[];
}

/** Presentation-only drawer. All edits and saves are scoped to this mounted instance. */
export function TaskEscortDemoPanel({ open, onOpenChange, botName, workflows }: TaskEscortDemoPanelProps) {
  const demo = useTaskEscortDemo(workflows);
  const { workflow } = demo;
  const [expanded, setExpanded] = useState(false);
  const closeRef = useRef<HTMLButtonElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const focusNodeRequested = useRef<string | null>(null);
  const locateNode = (id: Parameters<typeof demo.locateNode>[0]) => {
    if (!workflow) return;
    focusNodeRequested.current = workflow.id;
    demo.locateNode(id);
  };
  const selectWorkflow = (id: string) => {
    focusNodeRequested.current = null;
    demo.selectWorkflow(id);
  };
  useEffect(() => {
    if (!focusNodeRequested.current || !open) return;
    if (focusNodeRequested.current === workflow?.id) {
      document
        .querySelector<HTMLElement>('#task-escort-demo-panel [data-demo-selected-node="true"]')
        ?.focus({ preventScroll: true });
    }
    focusNodeRequested.current = null;
  }, [demo.tab, demo.selectedId, workflow?.id, open]);
  return (
    <Drawer open={open} onOpenChange={onOpenChange} modal={false}>
      <DrawerContent
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
          // Own in-drawer Escape below, even when a passive IconButton tooltip is topmost.
          // Outside/chat Escape retains Radix's normal dismissal behavior.
          if (event.target instanceof Node && contentRef.current?.contains(event.target)) event.preventDefault();
        }}
        onKeyDownCapture={(event) => {
          // Portaled Select content is outside the drawer DOM: let it close itself first.
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
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <DrawerTitle className="text-base font-semibold">任务护航</DrawerTitle>
              </div>
            </div>
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
          <WorkflowSelector workflow={workflow} workflows={demo.workflows} onSelect={selectWorkflow} />
          {botName && <span className="ml-auto max-w-48 truncate text-muted-foreground">当前 Bot · {botName}</span>}
        </div>
        {workflow && (
          <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-border px-5 py-3">
            <Segmented
              aria-label="任务护航视图"
              value={demo.tab}
              onChange={demo.setTab}
              options={[
                { value: 'overview', label: '运行概览' },
                { value: 'editor', label: '编辑器' },
              ]}
              className="w-52"
            />
          </div>
        )}
        <main
          key={`${workflow?.id ?? 'empty'}:${demo.tab}`}
          className="min-h-0 flex-1 overflow-y-auto overscroll-contain bg-muted/25 p-5"
        >
          {!workflow ? (
            <Empty title="无可访问的工作流" description="模拟权限下暂无可查看项。可关闭任务护航继续聊天。" />
          ) : demo.tab === 'overview' ? (
            <Overview workflow={workflow} onLocate={locateNode} />
          ) : (
            <WorkflowEditor demo={demo} />
          )}
        </main>
        <footer className="flex shrink-0 flex-wrap items-center justify-between gap-1 border-t border-border bg-background px-5 py-2 text-[10px] text-muted-foreground">
          <DrawerDescription asChild>
            <span>演示沙盒 · 所有运行记录、指标与节点状态均为虚构</span>
          </DrawerDescription>
          <span>Esc 关闭 · 聊天区仍可交互</span>
        </footer>
      </DrawerContent>
    </Drawer>
  );
}
export default TaskEscortDemoPanel;
