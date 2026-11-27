import { Badge, Button, Empty, Spin } from '@/components/ui';
import type { TodoArtifact, TodoProgress, TodoProgressStageStatus, TodoStageProgress } from '@/domain/todos/models';
import { cn } from '@/utils/cn';
import { useState } from 'react';
import { AlertTriangle, CheckCircle2, Download, ListChecks, Loader2, RefreshCw } from 'lucide-react';
import { getPreviewKind } from '@/services/workspace/sessionFileUtils';
import { TodoFilePreviewModal } from './TodoFilePreviewModal';
import type { TodoFileArtifact } from './useTodoFilePreview';

const STATUS_LABEL: Record<TodoProgressStageStatus, string> = {
  not_started: '未开始',
  in_progress: '进行中',
  pending_action: '等待处理',
  interrupted: '已中断',
  completed: '已完成',
  cancelled: '已取消',
  unknown: '状态未知',
};

const STATUS_CLASS: Record<TodoProgressStageStatus, string> = {
  not_started: 'bg-muted text-muted-foreground',
  in_progress: 'bg-primary/10 text-primary',
  pending_action: 'bg-warning/10 text-warning',
  interrupted: 'bg-destructive/10 text-destructive',
  completed: 'bg-success/10 text-success',
  cancelled: 'bg-muted text-muted-foreground',
  unknown: 'bg-muted text-muted-foreground',
};

function findArtifact(output: TodoProgress['stages'][number]['outputs'][number], artifacts: TodoArtifact[]): TodoArtifact | undefined {
  if (output.artifactId) return artifacts.find((artifact) => artifact.artifactId === output.artifactId);
  return artifacts.find((artifact) => artifact.artifactName === output.name);
}

export function TodoProgressPanel({
  progress,
  artifacts,
  loading,
  error,
  onRetry,
  onExpandStage,
  onReadArtifact,
}: {
  progress: TodoProgress | null;
  artifacts: TodoArtifact[];
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  onExpandStage: (stageCode: string) => void;
  onReadArtifact: (artifactId: string, disposition: 'inline' | 'attachment') => Promise<Blob>;
}) {
  const [previewArtifactId, setPreviewArtifactId] = useState<string | null>(null);
  if (loading && !progress) {
    return <Spin tip="执行进度加载中…" className="py-8" />;
  }

  if (error && !progress) {
    return (
      <Empty
        title="执行进度加载失败"
        description={error}
        action={
          <Button variant="secondary" onClick={onRetry}>
            重试
          </Button>
        }
      />
    );
  }

  if (!progress) return <Empty title="暂无执行进度" description="请刷新后查看任务进度。" />;

  const previewArtifact =
    artifacts.find(
      (artifact): artifact is TodoFileArtifact => artifact.kind === 'file' && artifact.artifactId === previewArtifactId,
    ) ?? null;

  return (
    <section aria-label="执行阶段进度" className="space-y-4">
      {progress.runtimeUnavailable ? (
        <div className="flex items-start gap-2 rounded-lg border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-warning">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
          <div>
            <p className="m-0 font-medium">Product Runtime 暂不可用</p>
            <p className="mt-1 m-0">以下固定阶段仍然可信，但运行状态可能不是最新。</p>
          </div>
        </div>
      ) : null}

      <ol className="m-0 list-none space-y-3 p-0">
        {progress.stages.map((stage, index) => (
          <TodoProgressStageItem
            key={stage.stageInstanceId}
            stage={stage}
            isCurrent={progress.currentStageCode === stage.code}
            isLast={index === progress.stages.length - 1}
            artifacts={artifacts}
            onExpandStage={onExpandStage}
            onReadArtifact={onReadArtifact}
            onPreviewFile={(artifact) => setPreviewArtifactId(artifact.artifactId)}
          />
        ))}
      </ol>
      <TodoFilePreviewModal
        artifact={previewArtifact}
        onOpenChange={() => setPreviewArtifactId(null)}
        onDownload={(artifact) => {
          void onReadArtifact(artifact.artifactId, 'attachment');
        }}
        readArtifact={onReadArtifact}
      />
    </section>
  );
}

function TodoProgressStageItem({
  stage,
  isCurrent,
  isLast,
  artifacts,
  onExpandStage,
  onReadArtifact,
  onPreviewFile,
}: {
  stage: TodoStageProgress;
  isCurrent: boolean;
  isLast: boolean;
  artifacts: TodoArtifact[];
  onExpandStage: (stageCode: string) => void;
  onReadArtifact: (artifactId: string, disposition: 'inline' | 'attachment') => Promise<Blob>;
  onPreviewFile: (artifact: TodoFileArtifact) => void;
}) {
  return (
    <li className="relative flex gap-4">
      <div className="flex flex-col items-center">
        <span
          aria-hidden
          className={cn(
            'flex size-9 shrink-0 items-center justify-center rounded-full',
            STATUS_CLASS[stage.status],
          )}
        >
          {stage.status === 'completed' ? (
            <CheckCircle2 className="size-4" aria-hidden />
          ) : stage.status === 'in_progress' ? (
            <Loader2 className="size-4 animate-spin" aria-hidden />
          ) : (
            <ListChecks className="size-4" aria-hidden />
          )}
        </span>
        {!isLast ? <span aria-hidden className="w-px flex-1 bg-border" /> : null}
      </div>

      <div className="min-w-0 flex-1 rounded-xl border border-border bg-card p-4">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-semibold text-foreground">{stage.name}</span>
          <Badge className={cn('border-0', STATUS_CLASS[stage.status])}>{STATUS_LABEL[stage.status]}</Badge>
          {isCurrent ? <Badge className="border-0 bg-secondary text-secondary-foreground">当前阶段</Badge> : null}
          <Badge className="border-0 bg-muted text-muted-foreground">第 {stage.attemptCount} 次尝试</Badge>
        </div>

        <p className="mt-2 m-0 text-xs leading-5 text-muted-foreground">{stage.summary}</p>
        {stage.currentStep ? (
          <p className="mt-1 m-0 text-xs font-medium text-foreground">当前步骤：{stage.currentStep}</p>
        ) : null}
        {stage.blocker ? (
          <div className="mt-2 rounded-lg border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-warning">
            {stage.blocker.reason || stage.blocker.errorCode ? (
              <p className="m-0">{stage.blocker.reason || stage.blocker.errorCode}</p>
            ) : null}
            {stage.blocker.requestedAction ? (
              <p className="mt-1 m-0 font-medium">下一步：{stage.blocker.requestedAction}</p>
            ) : null}
          </div>
        ) : null}

        {stage.outputs.length ? (
          <div className="mt-3 space-y-2">
            {stage.outputs.map((output) => {
              const artifact = findArtifact(output, artifacts);
              return (
                <div
                  key={`${output.kind}-${output.name}`}
                  className="flex items-center justify-between gap-3 rounded-lg border border-border bg-background px-3 py-2"
                >
                  <div className="min-w-0">
                    <p className="m-0 truncate text-xs font-medium text-foreground">{output.name}</p>
                    <p className="m-0 truncate text-[11px] text-muted-foreground">{output.summary || output.kind}</p>
                  </div>
                  {artifact ? (
                    <div className="flex shrink-0 gap-1">
                      {artifact.kind === 'file' &&
                      getPreviewKind(artifact.content.fileName, artifact.content.fileType) !== 'other' ? (
                        <Button
                          type="button"
                          variant="secondary"
                          size="sm"
                          onClick={() => onPreviewFile(artifact)}
                        >
                          预览
                        </Button>
                      ) : null}
                      <Button
                        type="button"
                        variant="secondary"
                        size="sm"
                        onClick={() => {
                          void onReadArtifact(artifact.artifactId, 'attachment');
                        }}
                      >
                        <Download className="size-3.5" aria-hidden />
                      </Button>
                    </div>
                  ) : null}
                </div>
              );
            })}
          </div>
        ) : null}

        {stage.detailsLoaded ? (
          stage.steps.length ? (
            <div className="mt-3 space-y-2">
              {stage.steps.map((step) => (
                <div key={step.stepId} className="rounded-lg bg-muted/40 px-3 py-2">
                  <div className="flex items-center gap-2">
                    <Badge className={cn('border-0', STATUS_CLASS[step.status])}>{STATUS_LABEL[step.status]}</Badge>
                    <span className="min-w-0 truncate text-xs font-medium text-foreground">{step.name}</span>
                  </div>
                  <p className="mt-1 m-0 text-[11px] leading-4 text-muted-foreground">{step.detail}</p>
                </div>
              ))}
            </div>
          ) : (
            <p className="mt-3 m-0 text-xs text-muted-foreground">暂无可展示的子步骤。</p>
          )
        ) : (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="mt-3"
            leftIcon={<RefreshCw className="size-3.5" aria-hidden />}
            onClick={() => onExpandStage(stage.code)}
          >
            加载子步骤
          </Button>
        )}

        {stage.detailsLoaded ? (
          <p className="mt-2 m-0 text-[11px] text-muted-foreground">
            {stage.messagesComplete === false ? '消息可能不完整。' : '消息完整性未声明。'}
            {stage.ignoredMessageCount ? ` 已忽略 ${stage.ignoredMessageCount} 条不可识别消息。` : ''}
          </p>
        ) : null}
      </div>
    </li>
  );
}
