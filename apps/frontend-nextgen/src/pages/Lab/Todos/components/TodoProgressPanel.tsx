import { Button, Empty, Spin } from '@/components/ui';
import type { TodoArtifact, TodoProgress } from '@/domain/todos/models';
import { AlertTriangle } from 'lucide-react';
import { useState } from 'react';
import { TodoFilePreviewModal } from './TodoFilePreviewModal';
import { TodoProgressStageItem } from './TodoProgressStageItem';
import type { TodoFileArtifact } from './useTodoFilePreview';

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

      <ol className="m-0 list-none p-0">
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
