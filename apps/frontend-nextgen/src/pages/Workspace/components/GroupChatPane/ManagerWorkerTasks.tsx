import { Button } from '@/components/ui';
// 窗格级任务导轨:portal 进会话容器内 absolute 覆盖,不能用视口级 Modal/Drawer。
import {
  PaneDialog,
  PaneDialogClose,
  PaneDialogContent,
  PaneDialogDescription,
  PaneDialogOverlay,
  PaneDialogPortal,
  PaneDialogTitle,
  PaneDialogTrigger,
} from '@/components/ui/PaneDialog';
import type { ManagerWorkerTasksProps } from '@/extensions/contracts/groupTasks';
import { isTerminalTask, taskIdentity } from '@/domain/groupTasks/projection';
import { useManagerWorkerTasks } from '@/pages/Workspace/hooks/useManagerWorkerTasks';
import { Check, ChevronRight, ChevronUp, CircleHelp, Clock3, LoaderCircle, X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { GroupTaskRow } from './GroupTaskRow';

// props 契约已上移至 src/extensions/contracts/groupTasks.ts（collab-form Task 17 源切片类型完备）。
export function ManagerWorkerTasks({ session, messages, connected = true }: ManagerWorkerTasksProps) {
  const model = useManagerWorkerTasks(session.sessionId, messages, session.participants, connected);
  const [open, setOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [container, setContainer] = useState<HTMLElement>();
  const trigger = useRef<HTMLButtonElement>(null);
  const running = model.tasks.filter((t) => t.status === 'running');
  const queued = model.tasks.filter((t) => t.status === 'queued');
  const unknown = model.tasks.filter((t) => t.status === 'unknown' && t.source !== 'receipt');
  const receipts = model.tasks.filter((t) => t.source === 'receipt');
  const latestRequestIndex = messages.reduce((last, message, index) => (message.role === 'user' ? index : last), -1);
  const currentReceipts = receipts.filter(
    (task) =>
      latestRequestIndex >= 0 &&
      messages.findIndex((message) => message.id === task.sourceMessageId) > latestRequestIndex,
  );
  const historicalReceipts = receipts.filter((task) => !currentReceipts.includes(task));
  const recent = model.tasks
    .filter((t) => isTerminalTask(t.status))
    .sort((a, b) => (b.finishedAt ?? b.lastActivityAt) - (a.finishedAt ?? a.lastActivityAt));
  const completed = recent.filter((t) => t.status === 'completed').length;
  const summary =
    running.length || queued.length
      ? `${running.length} 个任务执行中 · ${queued.length} 个排队中`
      : unknown.length
      ? `${unknown.length} 个任务状态待确认`
      : recent.length
      ? `${completed} 个任务已完成${recent.length > completed ? ` · ${recent.length - completed} 个异常结束` : ''}`
      : currentReceipts.length
      ? `任务状态尚未同步 · 当前 ${currentReceipts.length} 条派发回执`
      : historicalReceipts.length
      ? `暂无可确认任务 · ${historicalReceipts.length} 条历史回执`
      : '暂无任务';
  // Opening acknowledges the terminal records actually rendered, including arrivals while open.
  const unreadKey = model.unread.map((t) => `${taskIdentity(t)}:${t.status}:${t.finishedAt ?? t.version}`).join('|');
  useEffect(() => {
    if (open && unreadKey) model.markRead();
  }, [open, unreadKey]);
  const sections = [
    { title: '进行中', tasks: running },
    { title: '排队中', tasks: queued },
    { title: '状态待确认', tasks: unknown },
    { title: '最近任务', tasks: recent },
    { title: '本次派发回执', tasks: currentReceipts },
  ];
  const viewSource = (messageId: string) => {
    const conversation = trigger.current?.closest('[data-workspace-conversation]') ?? document;
    const target = [...conversation.querySelectorAll<HTMLElement>('[data-message-id]')].find((element) => element.dataset.messageId === messageId);
    setOpen(false);
    requestAnimationFrame(() => target?.scrollIntoView({ block: 'center', behavior: 'smooth' }));
  };
  return (
    <PaneDialog
      open={open}
      onOpenChange={(value) => {
        setContainer(trigger.current?.closest<HTMLElement>('[data-workspace-conversation]') ?? undefined);
        setOpen(value);
      }}
    >
      <PaneDialogTrigger asChild>
        <Button
          variant="ghost"
          ref={trigger}
          type="button"
          aria-label={`${summary}，查看任务`}
          className="flex h-auto min-h-8 w-full min-w-0 flex-wrap items-center justify-between gap-2 rounded-md px-0 py-0 text-left text-xs text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
        >
          <span className="flex min-w-0 items-center gap-2" role="status">
            {running.length ? (
              <LoaderCircle className="size-3.5 text-primary" aria-hidden />
            ) : unknown.length || receipts.length ? (
              <CircleHelp className="size-3.5" aria-hidden />
            ) : recent.length ? (
              <Check className="size-3.5 text-success" aria-hidden />
            ) : (
              <Clock3 className="size-3.5" aria-hidden />
            )}
            {summary}
          </span>
          <span className="flex shrink-0 items-center gap-2 px-2">
            {model.unread.length > 0 ? (
              <span className="text-foreground">{model.unread.length} 个任务有新结果</span>
            ) : null}
            查看任务 <ChevronUp className="size-3" aria-hidden />
          </span>
        </Button>
      </PaneDialogTrigger>
      <PaneDialogPortal container={container}>
        <PaneDialogOverlay className="absolute inset-0 z-40 bg-foreground/10" />
        {/* 480px task rail on desktop; one bottom sheet on narrow screens. */}
        <PaneDialogContent
          className="absolute inset-x-0 bottom-0 z-50 flex max-h-[85%] flex-col rounded-t-xl border border-border bg-background text-foreground outline-none sm:inset-x-auto sm:inset-y-0 sm:right-0 sm:max-h-full sm:w-[480px] sm:max-w-full sm:rounded-none"
          aria-describedby="group-task-description"
        >
          <div className="flex items-start justify-between gap-3 border-b border-border px-5 py-4">
            <div>
              <PaneDialogTitle className="text-base font-semibold">任务</PaneDialogTitle>
              <PaneDialogDescription id="group-task-description" className="mt-1 text-xs text-muted-foreground">
                {running.length || queued.length || unknown.length || recent.length
                  ? summary
                  : currentReceipts.length
                  ? `${currentReceipts.length} 条本次派发待确认`
                  : '当前没有执行中的任务'}
              </PaneDialogDescription>
            </div>
            <PaneDialogClose asChild>
              <Button variant="ghost" size="icon" aria-label="关闭任务面板">
                <X className="size-4" />
              </Button>
            </PaneDialogClose>
          </div>
          <div className="min-h-0 overflow-y-auto px-4 py-4">
            {model.error ? (
              <p role="status" className="mb-4 text-sm text-muted-foreground">
                {model.error}
              </p>
            ) : null}
            {model.tasks.length === 0 ? (
              <p className="py-6 text-sm text-muted-foreground">Manager 派发任务后，运行情况会显示在这里。</p>
            ) : null}
            {receipts.length > 0 ? (
              <p className="mb-4 flex items-start gap-2 rounded-md bg-muted/40 px-3 py-2 text-xs leading-5 text-muted-foreground">
                <CircleHelp className="mt-0.5 size-3.5 shrink-0" aria-hidden />
                派发已记录，执行状态尚未同步。
              </p>
            ) : null}
            {sections
              .filter((section) => section.tasks.length)
              .map((section) => (
                <section key={section.title} aria-label={section.title} className="mb-5 last:mb-0">
                  <h3 className="mb-1 flex justify-between px-2 text-xs font-medium text-muted-foreground">
                    {section.title}
                    <span>{section.tasks.length}</span>
                  </h3>
                  <div className="divide-y divide-border/60">
                    {section.tasks.map((task) => (
                      <GroupTaskRow
                        key={taskIdentity(task)}
                        task={task}
                        now={model.now}
                        unread={model.unread.some((item) => taskIdentity(item) === taskIdentity(task))}
                        pending={model.pending.has(taskIdentity(task))}
                        onCancel={() => void model.cancelTask(task)}
                        onViewSource={() => viewSource(task.sourceMessageId)}
                      />
                    ))}
                  </div>
                </section>
              ))}
            {historicalReceipts.length > 0 ? (
              <section aria-label="历史派发回执" className="mt-4 border-t border-border pt-2">
                <Button
                  variant="ghost"
                  type="button"
                  aria-expanded={historyOpen}
                  onClick={() => setHistoryOpen((value) => !value)}
                  className="flex h-auto min-h-11 w-full items-center gap-2 rounded-md px-2 text-left text-xs text-muted-foreground outline-none hover:bg-muted/40 focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <ChevronRight className={`size-3.5 ${historyOpen ? 'rotate-90' : ''}`} aria-hidden />
                  <span className="flex-1">历史派发</span>
                  <span>{historicalReceipts.length}</span>
                </Button>
                {historyOpen ? (
                  <div className="divide-y divide-border/60">
                    {historicalReceipts.map((task) => (
                      <GroupTaskRow
                        key={taskIdentity(task)}
                        task={task}
                        now={model.now}
                        unread={false}
                        pending={false}
                        onCancel={() => undefined}
                        onViewSource={() => viewSource(task.sourceMessageId)}
                      />
                    ))}
                  </div>
                ) : null}
              </section>
            ) : null}
          </div>
        </PaneDialogContent>
      </PaneDialogPortal>
    </PaneDialog>
  );
}
