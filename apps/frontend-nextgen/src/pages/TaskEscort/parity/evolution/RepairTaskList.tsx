import { Button } from '@/components/ui/Button';
import { Modal, ModalContent, ModalDescription, ModalTitle } from '@/components/ui/Modal';
import { displayDate, statusLabels } from '@/domain/taskEscortParity/evolution';
import type { RepairCandidates } from '@/domain/taskEscortParity/repairTypes';
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { useState } from 'react';
import { NoData, ReadTabs } from './ReadPrimitives';
const active = (phase: string) => ['drafting', 'review', 'blocked', 'publishing', 'failed'].includes(phase);
export function RepairTaskList({ context, data }: { context: TaskEscortParityContext; data: RepairCandidates }) {
  const [open, setOpen] = useState(false),
    [tab, setTab] = useState<'active' | 'history'>('active');
  const tasks = data.tasks;
  const list = (rows: typeof tasks) => (
    <div className="grid gap-3 md:grid-cols-2">
      {!rows.length && <NoData text="暂无优化任务" />}
      {rows.map((t) => (
        <article key={t.taskId} className="space-y-2 rounded-lg border border-border bg-card p-4">
          <h4 className="break-all text-sm font-medium">{t.title || t.taskId}</h4>
          <p className="text-xs text-muted-foreground">
            {statusLabels[t.phase] ?? t.phase} · v{t.revision} · {t.itemCount} 项 · {displayDate(t.updatedAtMs)}
          </p>
          <Button
            variant="outline"
            size="sm"
            onClick={() =>
              context.navigate('issues', {
                repairTaskId: t.taskId,
                issueSignature: undefined,
                analysisId: undefined,
                runId: undefined,
              })
            }
          >
            查看任务 {t.taskId}
          </Button>
        </article>
      ))}
    </div>
  );
  return (
    <section className="space-y-3" aria-label="优化任务">
      <header className="flex items-center justify-between">
        <h3 className="text-base font-semibold">优化任务</h3>
        <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
          查看全部任务
        </Button>
      </header>
      <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
        {Object.entries(
          tasks.reduce<Record<string, number>>((r, t) => ({ ...r, [t.phase]: (r[t.phase] ?? 0) + 1 }), {}),
        ).map(([phase, n]) => (
          <span key={phase}>
            {statusLabels[phase] ?? phase} {n}
          </span>
        ))}
        <span>任务元数据 {tasks.length} 条</span>
      </div>
      {list(tasks.filter((t) => active(t.phase)))}
      <Modal open={open} onOpenChange={setOpen}>
        <ModalContent size="xl">
          <ModalTitle>全部优化任务</ModalTitle>
          <ModalDescription>按服务端任务状态分组，任务详情以冻结修复版本为准。</ModalDescription>
          <ReadTabs
            label="优化任务分组"
            value={tab}
            onChange={setTab}
            options={[
              { value: 'active', label: '需跟进' },
              { value: 'history', label: '历史' },
            ]}
          />
          {list(tasks.filter((t) => active(t.phase) === (tab === 'active')))}
        </ModalContent>
      </Modal>
    </section>
  );
}
