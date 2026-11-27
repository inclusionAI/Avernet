import { Badge, Button } from '@/components/ui';
import type { TodoArtifact, TodoProgress, TodoProgressStageStatus, TodoStageProgress } from '@/domain/todos/models';
import { getPreviewKind } from '@/services/workspace/sessionFileUtils';
import { cn } from '@/utils/cn';
import { AlertTriangle, CheckCircle2, ChevronDown, ChevronRight, Circle, Download, Loader2, RefreshCw, UserRound } from 'lucide-react';
import { useState } from 'react';
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

function formatDateTime(value?: string): string | undefined {
  if (!value) return undefined;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false });
}

export function TodoProgressStageItem({
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
  const running = stage.status === 'in_progress';
  const completed = stage.status === 'completed';
  const attention = stage.status === 'pending_action' || stage.status === 'interrupted';
  const hasSteps = stage.detailsLoaded && stage.steps.length > 0;
  const [stepsExpanded, setStepsExpanded] = useState(running);

  return (
    <li className="grid grid-cols-[28px_minmax(0,1fr)] gap-x-3">
      <div className="relative flex justify-center">
        {!isLast ? <span aria-hidden className="absolute bottom-0 top-7 left-1/2 w-px -translate-x-1/2 bg-border" /> : null}
        <span
          aria-hidden
          className={cn(
            'relative z-10 flex size-7 shrink-0 items-center justify-center rounded-full',
            completed && 'bg-success/15 text-success',
            running && 'animate-pulse bg-primary/10 text-primary',
            attention && 'bg-warning/10 text-warning',
            !completed && !running && !attention && 'bg-muted/60 text-muted-foreground/50',
          )}
        >
          {completed ? (
            <CheckCircle2 className="size-4" aria-hidden />
          ) : running ? (
            <Loader2 className="size-4 animate-spin" aria-hidden />
          ) : attention ? (
            <AlertTriangle className="size-3.5" aria-hidden />
          ) : (
            <Circle className="size-3.5" aria-hidden />
          )}
        </span>
      </div>

      <div className={cn('min-w-0', isLast ? 'pb-0' : 'pb-5')}>
        <div className="flex min-h-7 flex-wrap items-center gap-x-2 gap-y-1">
          <span className={cn('text-sm font-medium', stage.status === 'not_started' ? 'text-muted-foreground' : 'text-foreground')}>
            {stage.name}
          </span>
          <Badge className={cn('border-0 px-1.5 py-0.5 text-[10px]', STATUS_CLASS[stage.status])}>
            {STATUS_LABEL[stage.status]}
          </Badge>
          {stage.executorSubjectName || stage.executorSubjectId ? (
            <span className="inline-flex items-center gap-1 rounded-full border border-border bg-muted/40 px-2 py-0.5 text-xs text-foreground/85">
              <UserRound className="size-3 text-muted-foreground" aria-hidden />
              {stage.executorSubjectName || stage.executorSubjectId}
            </span>
          ) : null}
          {stage.ownerUserName || stage.ownerUserId ? (
            <span className="text-xs text-muted-foreground">负责人 {stage.ownerUserName || stage.ownerUserId}</span>
          ) : null}
          {isCurrent ? <span className="rounded bg-primary/10 px-1.5 py-0.5 text-[10px] text-primary">当前阶段</span> : null}
          {stage.attemptCount > 1 ? <span className="text-xs text-muted-foreground">第 {stage.attemptCount} 次尝试</span> : null}
          {stage.updatedAt ? <span className="text-xs text-muted-foreground">{formatDateTime(stage.updatedAt)}</span> : null}
          {completed && hasSteps ? (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-auto gap-0.5 p-0 text-xs text-muted-foreground hover:bg-transparent hover:text-foreground"
              aria-expanded={stepsExpanded}
              onClick={() => setStepsExpanded((current) => !current)}
            >
              {stepsExpanded ? (
                <ChevronDown className="size-3.5" aria-hidden />
              ) : (
                <ChevronRight className="size-3.5" aria-hidden />
              )}
              <span>{stage.steps.length} 个子步骤</span>
            </Button>
          ) : null}
        </div>

        {stage.summary ? <p className="mt-1 m-0 text-xs leading-5 text-muted-foreground">{stage.summary}</p> : null}
        {running && stage.currentStep && !hasSteps ? (
          <p className="mt-1.5 m-0 rounded-lg bg-muted/30 px-2.5 py-1.5 text-xs text-muted-foreground">
            当前步骤：{stage.currentStep}
          </p>
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
          <div className="mt-2 space-y-1.5 rounded-lg border border-border bg-muted/20 px-3 py-2">
            {stage.outputs.map((output) => {
              const artifact = findArtifact(output, artifacts);
              return (
                <div
                  key={`${output.kind}-${output.name}`}
                  className="flex items-center justify-between gap-3 text-xs"
                >
                  <div className="min-w-0">
                    <p className="m-0 truncate font-medium text-foreground">{output.name}</p>
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

        {hasSteps && stepsExpanded ? (
          <div className="mt-2 space-y-1.5 rounded-lg border border-border bg-muted/20 px-3 py-2">
            {stage.steps.map((step) => (
              <div key={step.stepId} className="flex items-start gap-2 text-xs">
                <span
                  aria-hidden
                  className={cn(
                    'mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full',
                    STATUS_CLASS[step.status],
                  )}
                >
                  {step.status === 'completed' ? (
                    <CheckCircle2 className="size-3" />
                  ) : step.status === 'in_progress' ? (
                    <Loader2 className="size-3 animate-spin" />
                  ) : (
                    <Circle className="size-2" />
                  )}
                </span>
                <div className="min-w-0">
                  <span className={cn('font-medium', step.status === 'in_progress' ? 'text-primary' : 'text-foreground/80')}>
                    {step.name}
                  </span>
                  {step.status === 'in_progress' ? (
                    <span className="ml-2 rounded bg-primary/10 px-1 py-0.5 text-[10px] text-primary">进行中</span>
                  ) : null}
                  {step.detail ? <p className="m-0 mt-0.5 text-[11px] leading-4 text-muted-foreground">{step.detail}</p> : null}
                </div>
              </div>
            ))}
          </div>
        ) : null}

        {!stage.detailsLoaded ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="mt-2 h-7 px-0 text-xs text-muted-foreground hover:bg-transparent hover:text-foreground"
            leftIcon={<RefreshCw className="size-3.5" aria-hidden />}
            onClick={() => onExpandStage(stage.code)}
          >
            加载子步骤
          </Button>
        ) : null}

        {stage.detailsLoaded && (stage.messagesComplete === false || stage.ignoredMessageCount > 0) ? (
          <p className="mt-2 m-0 text-[11px] text-muted-foreground">
            {stage.messagesComplete === false ? '消息可能不完整。' : ''}
            {stage.ignoredMessageCount ? ` 已忽略 ${stage.ignoredMessageCount} 条不可识别消息。` : ''}
          </p>
        ) : null}
      </div>
    </li>
  );
}
