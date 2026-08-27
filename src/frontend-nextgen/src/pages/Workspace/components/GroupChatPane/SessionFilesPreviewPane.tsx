import { Badge, Button } from '@/components/ui';
import type { SessionFilePreviewState } from '@/pages/Workspace/hooks/useSessionFilePreview';
import { formatFileSize } from '@/services/workspace/sessionFileUtils';
import { Download, FileText, FolderOpen, Share2 } from 'lucide-react';
/** 预览面板所需的最小文件字段（群聊 / 单聊共用）。 */
export interface PreviewFile {
  name: string;
  size: number;
}

const KIND_META: Record<
  Exclude<SessionFilePreviewState['kind'], 'other'>,
  { label: string; tone: 'primary' | 'warning' }
> = {
  text: { label: '文本', tone: 'primary' },
  pdf: { label: 'PDF', tone: 'warning' },
  image: { label: '图片', tone: 'primary' },
};

interface UnsupportedPreviewProps {
  onDownload: () => void;
}

function UnsupportedPreview({ onDownload }: UnsupportedPreviewProps) {
  return (
    <div className="flex h-full min-h-[280px] flex-col items-center justify-center text-center text-[var(--color-muted)]">
      <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-xl bg-[var(--color-panel-strong)] text-[var(--color-muted)]">
        <FileText className="h-6 w-6" aria-hidden />
      </div>
      <p className="m-0 text-sm font-semibold text-[var(--color-fg)]">该文件类型暂不支持预览</p>
      <p className="mt-2 text-sm leading-6">请下载后使用对应软件查看</p>
      <Button
        size="md"
        variant="secondary"
        className="mt-5 bg-[var(--color-panel-muted)] text-[var(--color-fg)] hover:bg-[var(--color-panel-strong)]"
        leftIcon={<Download className="h-3.5 w-3.5" aria-hidden />}
        onClick={onDownload}
      >
        下载文件
      </Button>
    </div>
  );
}

interface PreviewBodyProps {
  file: PreviewFile;
  preview: SessionFilePreviewState;
  onDownload: () => void;
}

function PreviewBody({ file, preview, onDownload }: PreviewBodyProps) {
  if (preview.kind === 'other' || preview.status === 'unsupported') {
    return <UnsupportedPreview onDownload={onDownload} />;
  }
  if (preview.status === 'loading') {
    return (
      <div className="flex h-full min-h-[280px] flex-col items-center justify-center gap-3 text-[var(--color-muted)]">
        <div className="h-6 w-6 animate-spin rounded-full border-2 border-[var(--color-primary)] border-t-transparent" />
        <span className="text-sm">正在加载预览…</span>
      </div>
    );
  }
  if (preview.status === 'error') {
    return (
      <div className="flex h-full min-h-[280px] flex-col items-center justify-center gap-2 text-[var(--color-muted)]">
        <p className="m-0 text-sm font-semibold text-[var(--color-error)]">预览加载失败</p>
        <p className="m-0 text-xs">{preview.errorMessage ?? '请稍后重试或下载文件查看。'}</p>
      </div>
    );
  }

  if (preview.kind === 'image' && preview.contentUrl) {
    return (
      <div className="flex h-full items-center justify-center">
        <img
          src={preview.contentUrl}
          alt={file.name}
          className="max-h-full max-w-full rounded-xl border border-[var(--color-border)] object-contain"
        />
      </div>
    );
  }

  if ((preview.kind === 'pdf' || preview.kind === 'text') && preview.contentUrl) {
    return (
      <div className="h-full">
        <iframe
          title={file.name}
          src={preview.contentUrl}
          className="h-full w-full rounded-xl border border-[var(--color-border)] bg-white"
        />
      </div>
    );
  }

  return <UnsupportedPreview onDownload={onDownload} />;
}

interface PreviewPaneProps {
  file: PreviewFile | null;
  filesStateEmpty: boolean;
  preview: SessionFilePreviewState;
  onDownload: () => void;
  onShare?: () => void;
}

export function PreviewPane({ file, filesStateEmpty, preview, onDownload, onShare }: PreviewPaneProps) {
  return (
    <div className="flex min-w-0 flex-1 flex-col bg-[var(--color-card)]">
      {file ? (
        <>
          <header className="flex items-center justify-between gap-3 border-b border-[var(--color-border)] px-5 py-3.5">
            <div className="flex min-w-0 items-center gap-2">
              <h3 className="m-0 truncate text-sm font-semibold text-[var(--color-fg)]">{file.name}</h3>
              {preview.kind !== 'other' && (
                <Badge tone={KIND_META[preview.kind].tone}>{KIND_META[preview.kind].label}</Badge>
              )}
              <span className="shrink-0 text-[11px] tabular-nums text-[var(--color-muted)]">
                {formatFileSize(file.size)}
              </span>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              {onShare ? (
                <Button
                  size="sm"
                  variant="secondary"
                  leftIcon={<Share2 className="h-3.5 w-3.5" aria-hidden />}
                  onClick={() => void onShare()}
                >
                  分享
                </Button>
              ) : null}
              <Button
                size="sm"
                variant="secondary"
                leftIcon={<Download className="h-3.5 w-3.5" aria-hidden />}
                onClick={onDownload}
              >
                下载
              </Button>
            </div>
          </header>

          <div className="min-h-0 flex-1 overflow-auto p-4">
            <PreviewBody file={file} preview={preview} onDownload={onDownload} />
          </div>
        </>
      ) : (
        <div className="flex min-h-0 flex-1 items-center justify-center px-8 text-center">
          <div className="flex max-w-sm flex-col items-center text-[var(--color-muted)]">
            <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-xl bg-[var(--color-primary-soft)] text-[var(--color-primary)]">
              <FolderOpen className="h-6 w-6" aria-hidden />
            </div>
            <p className="m-0 text-sm font-semibold text-[var(--color-fg)]">
              {filesStateEmpty ? '会话内暂时没有文件' : '选择一个文件开始预览'}
            </p>
            <p className="mt-2 text-sm leading-6">
              {filesStateEmpty
                ? '点击左侧「添加文件」即可上传资源，所有成员均可查看与管理。'
                : '文件预览会显示在这里。'}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

export default PreviewPane;
