import { Button } from '@/components/ui/Button';
import { Drawer, DrawerContent, DrawerDescription, DrawerTitle } from '@/components/ui/Drawer';
import { Empty } from '@/components/ui/Empty';
import { Segmented } from '@/components/ui/Segmented';
import type { HelpTask } from '@/domain/communityHelp/types';
import { cn } from '@/utils/cn';
import { Bot, Check, ChevronRight, CircleAlert, Clock3, FileText, FlaskConical, Play } from 'lucide-react';
import { useState } from 'react';
import { HelpProgress, HelpStatusBadge } from './HelpTaskCard';

type PanelTab = 'progress' | 'activity' | 'artifacts';
const STAGE_LABELS = { pending: '待开始', running: '执行中', attention: '需确认', done: '已完成' };
const formatTime = (at: string) => new Date(at).toLocaleString('zh-CN', { hour12: false });

export function HelpTaskPanel({
  task,
  boardName,
  onClose,
  onAdvance,
}: {
  task: HelpTask;
  boardName: string;
  onClose: () => void;
  onAdvance: () => void;
}) {
  const [tab, setTab] = useState<PanelTab>('progress');
  const [selectedId, setSelectedId] = useState(
    () => (task.stages.find((stage) => stage.status !== 'done') ?? task.stages[task.stages.length - 1]).id,
  );
  const selected = task.stages.find((stage) => stage.id === selectedId) ?? task.stages[0];
  return (
    <Drawer
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DrawerContent size="lg" className="max-w-full" bodyClassName="flex flex-col p-0 pt-0">
        <header className="border-b border-border p-5 pr-14">
          <div className="mb-3 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <span className="max-w-full break-words">{boardName}</span>
            <ChevronRight className="size-3" aria-hidden />
            <span>任务副屏</span>
            <span className="rounded bg-muted px-1.5 py-0.5 text-xs">Mock</span>
          </div>
          <DrawerTitle className="break-words text-base leading-7">{task.title}</DrawerTitle>
          <DrawerDescription className="mt-2">查看协作进度与示例交付，不触发真实执行。</DrawerDescription>
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <HelpStatusBadge status={task.status} />
            <span className="min-w-0 max-w-full break-words text-xs text-muted-foreground">
              {task.source}
              {task.sourceRef ? ` · ${task.sourceRef}` : ''}
            </span>
          </div>
          <div className="mt-4">
            <HelpProgress task={task} />
          </div>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto p-5">
          <Segmented<PanelTab>
            value={tab}
            onChange={setTab}
            aria-label="任务详情视图"
            options={[
              { value: 'progress', label: '协作流程' },
              { value: 'activity', label: '执行动态' },
              { value: 'artifacts', label: `交付产物 (${task.artifacts.length})` },
            ]}
          />
          {tab === 'progress' && (
            <div className="mt-5 space-y-5">
              <section className="rounded-xl border border-border p-4" aria-label="任务信息">
                <h3 className="mb-2 text-xs font-semibold">任务目标</h3>
                <p className="whitespace-pre-wrap break-words text-xs leading-6 text-muted-foreground">
                  {task.description}
                </p>
                <p className="mt-3 text-xs text-muted-foreground">创建时间 · {formatTime(task.createdAt)}</p>
              </section>
              <section aria-label="协作阶段">
                <div className="mb-3 flex items-center justify-between">
                  <h3 className="text-sm font-semibold">协作流程</h3>
                  <span className="text-xs text-muted-foreground">点击节点查看详情</span>
                </div>
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                  {task.stages.map((stage, index) => (
                    <Button
                      key={stage.id}
                      variant="outline"
                      aria-label={`查看${stage.role}阶段`}
                      aria-pressed={stage.id === selected.id}
                      onClick={() => setSelectedId(stage.id)}
                      className={cn(
                        'h-auto min-w-0 flex-col items-start gap-2 whitespace-normal p-3 text-left',
                        stage.id === selected.id && 'border-primary bg-primary/5',
                      )}
                    >
                      <span className="flex w-full items-center justify-between gap-1 text-xs text-muted-foreground">
                        <span>STEP {String(index + 1).padStart(2, '0')}</span>
                        {stage.status === 'done' ? (
                          <Check className="size-3.5 text-success" aria-hidden />
                        ) : stage.status === 'attention' ? (
                          <CircleAlert className="size-3.5 text-destructive" aria-hidden />
                        ) : (
                          <Clock3 className="size-3.5" aria-hidden />
                        )}
                      </span>
                      <span className="text-xs font-semibold">{stage.role}</span>
                      <span
                        className={cn(
                          'text-xs',
                          stage.status === 'done'
                            ? 'text-success'
                            : stage.status === 'attention'
                            ? 'text-destructive'
                            : 'text-muted-foreground',
                        )}
                      >
                        {STAGE_LABELS[stage.status]}
                      </span>
                    </Button>
                  ))}
                </div>
              </section>
              <section className="rounded-xl border border-primary/20 bg-primary/5 p-4" aria-label="阶段详情">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h3 className="text-sm font-semibold">{selected.role}</h3>
                  <span className="text-xs text-muted-foreground">{STAGE_LABELS[selected.status]}</span>
                </div>
                <p className="mt-3 flex items-center gap-2 text-xs text-primary">
                  <Bot className="size-4 shrink-0" aria-hidden />
                  {selected.botNames.join('、')}
                </p>
                <p className="mt-3 text-xs leading-6 text-muted-foreground">{selected.instruction}</p>
                <div className="mt-3 rounded-lg bg-card p-3 text-xs leading-6">
                  <p className="mb-1 font-medium">阶段结果</p>
                  <p className="text-muted-foreground">
                    {selected.output ||
                      (selected.status === 'running' ? '正在模拟处理中，尚未产出结果。' : '等待前序阶段完成后开始。')}
                  </p>
                </div>
              </section>
            </div>
          )}
          {tab === 'activity' && (
            <ol className="mt-5 space-y-4" aria-label="任务执行动态">
              {task.activities.map((activity) => (
                <li key={activity.id} className="border-l-2 border-primary/20 pl-4">
                  <p className="text-xs text-muted-foreground">{formatTime(activity.at)}</p>
                  <p className="mt-1 break-words text-xs leading-6">{activity.message}</p>
                </li>
              ))}
            </ol>
          )}
          {tab === 'artifacts' &&
            (task.artifacts.length ? (
              <div className="mt-5 space-y-3" aria-label="任务交付产物">
                {task.artifacts.map((artifact) => (
                  <article key={artifact.id} className="rounded-xl border border-border bg-card p-4">
                    <h3 className="flex items-center gap-2 text-xs font-semibold">
                      <FileText className="size-4 text-primary" aria-hidden />
                      {artifact.name}
                    </h3>
                    <p className="mt-3 whitespace-pre-wrap break-words text-xs leading-6 text-muted-foreground">
                      {artifact.content}
                    </p>
                  </article>
                ))}
              </div>
            ) : (
              <Empty compact title="还没有交付产物" description="完成协作阶段后，将在这里展示对应的 Mock 产物。" />
            ))}
        </div>
        <footer className="flex flex-wrap items-center justify-between gap-3 border-t border-border bg-background px-5 py-4">
          <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <FlaskConical className="size-3.5" aria-hidden />
            仅演示，不执行真实任务
          </span>
          <Button
            disabled={task.status === 'done'}
            onClick={onAdvance}
            leftIcon={<Play className="size-3.5" aria-hidden />}
          >
            {task.status === 'done'
              ? '任务已完成'
              : task.status === 'attention'
              ? '模拟解除阻塞'
              : task.status === 'pending'
              ? '模拟开始协作'
              : '模拟推进下一阶段'}
          </Button>
        </footer>
      </DrawerContent>
    </Drawer>
  );
}
