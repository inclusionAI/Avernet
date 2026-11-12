import { Button, Empty, Spin } from '@/components/ui';
import type { TodoArtifact } from '@/domain/todos/models';
import { Code2, Download, ExternalLink, Eye, File, FileText, Link2 } from 'lucide-react';
import { useState } from 'react';

interface TodoArtifactListProps {
  artifacts: TodoArtifact[];
  loading?: boolean;
  error?: string | null;
  onRetry?: () => void | Promise<void>;
  onReadFile: (artifactId: string, disposition: 'inline' | 'attachment') => Promise<Blob>;
}

function formatFileSize(size: number): string {
  if (!Number.isFinite(size) || size <= 0) return '未知大小';
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function ArtifactShell({
  icon,
  title,
  meta,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  meta: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <article className="rounded-2xl border border-border bg-background px-5 py-4 text-foreground">
      <div className="flex items-start gap-4">
        <span className="flex size-[52px] shrink-0 items-center justify-center rounded-2xl bg-muted text-foreground">
          {icon}
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="m-0 break-words text-base font-semibold">{title}</h3>
          <div className="mt-1 text-sm text-muted-foreground">{meta}</div>
          <div className="mt-4">{children}</div>
        </div>
      </div>
    </article>
  );
}

function triggerBlobDownload(blob: Blob, fileName: string) {
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = objectUrl;
  anchor.download = fileName;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(objectUrl);
}

function triggerBlobPreview(blob: Blob) {
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = objectUrl;
  anchor.target = '_blank';
  anchor.rel = 'noreferrer';
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000);
}

export function TodoArtifactList({ artifacts, loading, error, onRetry, onReadFile }: TodoArtifactListProps) {
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const handleFile = async (artifact: Extract<TodoArtifact, { kind: 'file' }>, disposition: 'inline' | 'attachment') => {
    const actionKey = `${artifact.artifactId}:${disposition}`;
    setBusyAction(actionKey);
    setActionError(null);
    try {
      const blob = await onReadFile(artifact.artifactId, disposition);
      if (disposition === 'inline') triggerBlobPreview(blob);
      else triggerBlobDownload(blob, artifact.content.fileName || artifact.artifactName);
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : '产物文件读取失败');
    } finally {
      setBusyAction(null);
    }
  };

  if (loading && artifacts.length === 0) {
    return <Spin tip="加载产物中…" className="py-12" />;
  }

  if (error && artifacts.length === 0) {
    return (
      <Empty
        compact
        title="产物加载失败"
        description={error}
        action={
          onRetry ? (
            <Button variant="secondary" size="sm" onClick={() => void onRetry()}>
              重试
            </Button>
          ) : undefined
        }
      />
    );
  }

  if (artifacts.length === 0) {
    return <Empty compact title="暂无产物" description="Bot 输出的文本、文件、链接和代码会汇总在这里。" />;
  }

  return (
    <section role="region" aria-label="待办产物" className="space-y-3">
      {error ? (
        <div className="rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
          {error}
        </div>
      ) : null}
      {actionError ? (
        <div className="rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
          {actionError}
        </div>
      ) : null}

      {artifacts.map((artifact) => {
        if (artifact.kind === 'text') {
          return (
            <ArtifactShell
              key={artifact.artifactId}
              icon={<FileText className="size-6" aria-hidden />}
              title={artifact.artifactName}
              meta="文本产物"
            >
              <p className="m-0 whitespace-pre-wrap break-words rounded-xl bg-muted/40 px-4 py-3 text-sm leading-6">
                {artifact.content.text}
              </p>
            </ArtifactShell>
          );
        }

        if (artifact.kind === 'file') {
          return (
            <ArtifactShell
              key={artifact.artifactId}
              icon={<File className="size-6" aria-hidden />}
              title={artifact.artifactName}
              meta={`${artifact.content.fileName} · ${formatFileSize(artifact.content.size)}`}
            >
              <div className="flex flex-wrap gap-2">
                <Button
                  variant="secondary"
                  size="sm"
                  loading={busyAction === `${artifact.artifactId}:inline`}
                  leftIcon={<Eye className="size-4" aria-hidden />}
                  onClick={() => void handleFile(artifact, 'inline')}
                >
                  预览
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  loading={busyAction === `${artifact.artifactId}:attachment`}
                  leftIcon={<Download className="size-4" aria-hidden />}
                  onClick={() => void handleFile(artifact, 'attachment')}
                >
                  下载
                </Button>
              </div>
            </ArtifactShell>
          );
        }

        if (artifact.kind === 'url_link') {
          return (
            <ArtifactShell
              key={artifact.artifactId}
              icon={<Link2 className="size-6" aria-hidden />}
              title={artifact.artifactName}
              meta={artifact.content.type || '链接产物'}
            >
              <a
                href={artifact.content.url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex max-w-full items-center gap-2 break-all text-sm font-medium text-primary hover:underline"
              >
                {artifact.content.url}
                <ExternalLink className="size-4 shrink-0" aria-hidden />
              </a>
            </ArtifactShell>
          );
        }

        return (
          <ArtifactShell
            key={artifact.artifactId}
            icon={<Code2 className="size-6" aria-hidden />}
            title={artifact.artifactName}
            meta="代码产物"
          >
            <div className="space-y-3 text-sm">
              <div>
                <div className="text-muted-foreground">代码库</div>
                <a
                  href={artifact.content.codeBaseAddress}
                  target="_blank"
                  rel="noreferrer"
                  className="mt-1 inline-flex max-w-full items-center gap-2 break-all font-medium text-primary hover:underline"
                >
                  {artifact.content.codeBaseName}
                  <ExternalLink className="size-4 shrink-0" aria-hidden />
                </a>
              </div>
              {artifact.content.prLink ? (
                <div>
                  <div className="text-muted-foreground">Pull Request</div>
                  <a
                    href={artifact.content.prLink}
                    target="_blank"
                    rel="noreferrer"
                    className="mt-1 inline-flex max-w-full items-center gap-2 break-all font-medium text-primary hover:underline"
                  >
                    {artifact.content.prLink}
                    <ExternalLink className="size-4 shrink-0" aria-hidden />
                  </a>
                </div>
              ) : null}
              {artifact.content.prDesc ? <p className="m-0 whitespace-pre-wrap">{artifact.content.prDesc}</p> : null}
            </div>
          </ArtifactShell>
        );
      })}
    </section>
  );
}
