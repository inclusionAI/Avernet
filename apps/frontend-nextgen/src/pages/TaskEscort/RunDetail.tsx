import { useDetailFocusRestore } from '@/hooks/taskEscortWorkspace/useDetailFocus';
import { useRunDetail } from '@/hooks/taskEscortWorkspace/useRunDetail';
import { ArrowRight, FileText, ShieldCheck } from 'lucide-react';
import { RunStepGraph, RunStepTimeline } from './RunDetail.steps';
import { formatDuration, formatStartedAt } from './formatting';
import type { ViewProps } from './types';
import { Badge, Button, Modal, ModalContent, ModalDescription, ModalHeader, ModalTitle, StatusBadge } from './ui';

export function RunDetail({ demo }: ViewProps) {
  const focusRestore = useDetailFocusRestore();
  const { detail, close, setActiveStep } = useRunDetail(demo);
  if (!detail) return null;
  const { run, steps, selected, relatedIssues } = detail;

  return (
    <Modal
      open={Boolean(run)}
      onOpenChange={(open) => {
        if (!open) close();
      }}
    >
      <ModalContent
        {...focusRestore}
        data-detail-focus-fallback
        className="max-h-[90vh] w-[calc(100%-2rem)] max-w-4xl overflow-y-auto p-0"
      >
        <ModalHeader className="space-y-3 border-b border-border px-6 pb-5 pt-6 text-left">
          <div className="flex flex-wrap items-center gap-2 pr-6">
            <span className="mono text-xs text-muted-foreground">{run.id}</span>
            <StatusBadge status={run.status} />
            <Badge tone="outline" className="font-normal">
              运行快照
            </Badge>
          </div>
          <ModalTitle className="text-xl">{run.name}</ModalTitle>
          <ModalDescription>
            查看本次运行的节点路径、状态时间线与关联证据。节点拓扑为演示示意，不代表已连接真实执行引擎。
          </ModalDescription>
        </ModalHeader>
        <div className="space-y-6 px-6 py-5">
          <dl className="grid grid-cols-2 gap-4 rounded-lg border border-border bg-muted/20 p-4 sm:grid-cols-4">
            {[
              { label: '触发方式', value: run.trigger },
              { label: '执行 Bot', value: run.bot },
              {
                label: '执行耗时',
                value: run.status === 'waiting' ? '等待执行' : formatDuration(run.duration),
              },
              {
                label: '开始时间',
                value: formatStartedAt(run.startedAt),
              },
            ].map((item) => (
              <div key={item.label}>
                <dt className="text-[11px] text-muted-foreground">{item.label}</dt>
                <dd className="mt-2 break-words text-xs font-medium leading-5">{item.value}</dd>
              </div>
            ))}
          </dl>
          <RunStepGraph steps={steps} selected={selected} onSelect={(id) => setActiveStep({ runId: run.id, id })} />
          <div className="grid gap-6 md:grid-cols-[0.9fr_1.1fr]">
            <RunStepTimeline steps={steps} />
            <section aria-labelledby="run-evidence-heading">
              <h3 id="run-evidence-heading" className="mb-4 flex items-center gap-2 text-sm font-semibold">
                <FileText className="size-4 text-primary" />
                快照与关联证据
              </h3>
              <div className="rounded-lg border border-border bg-muted/20 p-4">
                <p className="mono break-all text-[11px] leading-6">
                  run_id: {run.id}
                  <br />
                  workflow_id: {run.workflowId}
                  <br />
                  current_node: {run.node}
                  <br />
                  status: {run.status}
                  <br />
                  duration_seconds: {run.duration}
                </p>
              </div>
              <p className="mb-2 mt-4 text-xs font-medium">
                同工作流异常 <span className="font-normal text-muted-foreground">({relatedIssues.length})</span>
              </p>
              <p className="mb-3 text-[11px] leading-5 text-muted-foreground">
                仅按工作流关联，不断言异常由本次运行产生。
              </p>
              {relatedIssues.length ? (
                <div className="space-y-2">
                  {relatedIssues.map((issue) => (
                    <div key={issue.id} className="rounded-lg border border-border p-3">
                      <p className="text-xs font-medium leading-5">{issue.title}</p>
                      <p className="mt-1 line-clamp-2 break-all text-[11px] leading-5 text-muted-foreground">
                        {issue.evidence[0] || issue.description}
                      </p>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="mt-2 h-6 px-0 text-[11px] text-primary hover:bg-transparent"
                        aria-label={`从运行查看异常：${issue.title}`}
                        onClick={() => {
                          close();
                          demo.navigate('issues');
                          demo.setWorkflowId(issue.workflowId);
                          demo.setSelectedIssue(issue.id);
                        }}
                      >
                        审阅异常建议
                        <ArrowRight className="ml-1 size-3" />
                      </Button>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="rounded-lg border border-dashed border-border p-4 text-xs leading-6 text-muted-foreground">
                  当前演示数据中，该工作流未关联异常记录。
                </p>
              )}
            </section>
          </div>
          <p className="flex items-start gap-2 rounded-lg bg-muted/40 p-3 text-[11px] leading-6 text-muted-foreground">
            <ShieldCheck className="mt-1 size-3.5 shrink-0 text-primary" />
            只读演示：不重新执行、不取消任务，也不请求后端。此处未提供虚构的逐步耗时或真实日志链接。
          </p>
        </div>
        <div className="flex justify-end border-t border-border px-6 py-4">
          <Button variant="outline" size="sm" onClick={close}>
            关闭详情
          </Button>
        </div>
      </ModalContent>
    </Modal>
  );
}
