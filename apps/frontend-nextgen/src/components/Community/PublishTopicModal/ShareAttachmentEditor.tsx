import { Button } from '@/components/ui/Button';
import { MAX_COMMUNITY_SHARE_ATTACHMENTS } from '@/services/community/communityService';
import { FileText, LoaderCircle, Paperclip, RefreshCw, Trash2, Upload } from 'lucide-react';
import { useRef } from 'react';
import { attachmentValidationError, type AttachmentDraft } from './model';

interface ShareAttachmentEditorProps {
  attachments: AttachmentDraft[];
  error?: string;
  onAddFiles: (files: File[]) => void;
  onRetry: (draft: AttachmentDraft) => void;
  onRemove: (key: string) => void;
}

function formatFileSize(sizeBytes: number): string {
  if (sizeBytes < 1024) return `${sizeBytes} B`;
  if (sizeBytes < 1024 * 1024) return `${(sizeBytes / 1024).toFixed(1)} KiB`;
  return `${(sizeBytes / 1024 / 1024).toFixed(1)} MiB`;
}

export function ShareAttachmentEditor({
  attachments,
  error,
  onAddFiles,
  onRetry,
  onRemove,
}: ShareAttachmentEditorProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);

  return (
    <section aria-label="附件设置" className="rounded-xl border border-border bg-card p-5">
      <header className="flex items-center gap-2">
        <Paperclip className="size-4 text-muted-foreground" aria-hidden />
        <h3 className="m-0 text-sm font-semibold text-foreground">附件</h3>
        <span className="ml-auto text-xs text-muted-foreground">
          {attachments.length}/{MAX_COMMUNITY_SHARE_ATTACHMENTS}
        </span>
      </header>

      {attachments.length ? (
        <ul className="mt-4 space-y-2" aria-label="分享附件列表">
          {attachments.map((draft) => (
            <li key={draft.key} className="flex items-center gap-3 rounded-lg border border-border bg-background px-3 py-2">
              {draft.status === 'uploading' ? (
                <LoaderCircle className="size-4 shrink-0 animate-spin text-primary" aria-hidden />
              ) : (
                <FileText className="size-4 shrink-0 text-muted-foreground" aria-hidden />
              )}
              <div className="min-w-0 flex-1">
                <p className="m-0 truncate text-sm font-medium text-foreground">{draft.file.name}</p>
                <p className="m-0 text-xs text-muted-foreground">
                  {formatFileSize(draft.file.size)} ·{' '}
                  {draft.status === 'uploading'
                    ? '上传中'
                    : draft.status === 'uploaded'
                      ? '上传成功'
                      : draft.error || '上传失败'}
                </p>
              </div>
              {draft.status === 'error' && !attachmentValidationError(draft.file) ? (
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label={`重试上传 ${draft.file.name}`}
                  onClick={() => onRetry(draft)}
                >
                  <RefreshCw className="size-4" aria-hidden />
                </Button>
              ) : null}
              <Button
                variant="ghost"
                size="icon"
                aria-label={`移除附件 ${draft.file.name}`}
                onClick={() => onRemove(draft.key)}
              >
                <Trash2 className="size-4" aria-hidden />
              </Button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-3 text-xs text-muted-foreground">上传 1–5 个真实文件，单个文件不超过 20 MiB。</p>
      )}

      <input
        ref={fileInputRef}
        className="sr-only"
        type="file"
        multiple
        aria-label="选择分享附件"
        onChange={(event) => {
          onAddFiles(Array.from(event.target.files ?? []));
          event.target.value = '';
        }}
      />
      <Button
        variant="ghost"
        size="sm"
        className="mt-3 justify-start px-0 text-xs text-primary"
        leftIcon={<Upload className="size-4" aria-hidden />}
        disabled={attachments.length >= MAX_COMMUNITY_SHARE_ATTACHMENTS}
        onClick={() => fileInputRef.current?.click()}
      >
        添加附件
      </Button>
      {error ? <p className="m-0 mt-2 text-xs text-destructive">{error}</p> : null}
    </section>
  );
}
