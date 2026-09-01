import { Button, Modal, ModalContent, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import {
  SESSION_FILE_ALLOWED_EXT,
  SESSION_FILE_MAX_BATCH,
  formatFileSize,
} from '@/services/workspace/sessionFileUtils';
import type { UploadTask } from '@/stores/botSessionFileStore';
import { cn } from '@/utils/cn';
import { FileText, LoaderCircle, X } from 'lucide-react';
import { useCallback, useRef, useState } from 'react';

export interface BotUploadFilesModalProps {
  open: boolean;
  onClose: () => void;
  queue: UploadTask[];
  isUploading: boolean;
  stageFiles: (files: File[]) => string[];
  submit: () => Promise<void>;
  removeTask: (localId: string) => void;
}

export function BotUploadFilesModal({
  open,
  onClose,
  queue,
  isUploading,
  stageFiles,
  submit,
  removeTask,
}: BotUploadFilesModalProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [isDragOver, setIsDragOver] = useState(false);

  const pickFiles = useCallback(
    (files: FileList | File[] | null) => {
      if (!files) return;
      const arr = Array.from(files);
      if (arr.length) stageFiles(arr);
      if (inputRef.current) inputRef.current.value = '';
    },
    [stageFiles],
  );

  const handleClose = useCallback(() => {
    queue.forEach((t) => {
      if (t.phase === 'staged' || t.phase === 'failed') removeTask(t.localId);
    });
    onClose();
  }, [queue, removeTask, onClose]);

  const stagedCount = queue.filter((t) => t.phase === 'staged').length;

  return (
    <Modal open={open} onOpenChange={(o) => !o && void handleClose()}>
      <ModalContent size="lg" className="max-w-[560px]">
        <ModalHeader>
          <ModalTitle>上传文件</ModalTitle>
        </ModalHeader>

        <div
          onClick={() => inputRef.current?.click()}
          onDrop={(e) => {
            e.preventDefault();
            setIsDragOver(false);
            pickFiles(e.dataTransfer.files);
          }}
          onDragOver={(e) => {
            e.preventDefault();
            setIsDragOver(true);
          }}
          onDragLeave={() => setIsDragOver(false)}
          className={cn(
            'flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed px-4 py-8 text-center transition-colors',
            isDragOver ? 'border-primary bg-primary/10' : 'border-border bg-muted/50 hover:bg-muted',
          )}
        >
          <FileText className="mb-2 h-8 w-8 text-primary" />
          <p className="text-sm font-medium text-foreground">点击或拖拽上传文件</p>
          <p className="mt-1 text-xs text-muted-foreground">支持表格、文档、压缩包、HTML 与图片类文件</p>
          <p className="mt-0.5 text-[11px] text-muted-foreground">单次最多上传 {SESSION_FILE_MAX_BATCH} 个</p>
          <input ref={inputRef} type="file" multiple className="hidden" onChange={(e) => pickFiles(e.target.files)} />
        </div>

        <div className="mt-4">
          <p className="mb-1.5 text-[11px] text-muted-foreground">支持文件类型</p>
          <div className="flex flex-wrap gap-1.5">
            {SESSION_FILE_ALLOWED_EXT.map((ext) => (
              <span key={ext} className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                {ext.replace(/^\./, '')}
              </span>
            ))}
          </div>
        </div>

        {queue.length > 0 && (
          <div className="max-h-[220px] space-y-2 overflow-y-auto">
            {queue.map((task) => (
              <div
                key={task.localId}
                className="flex items-center gap-2 rounded-lg border border-border bg-background p-2"
              >
                <div className="flex h-8 w-8 flex-none items-center justify-center rounded-lg bg-muted">
                  <FileText className="h-4 w-4 text-muted-foreground" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <TooltipProvider>
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <span className="truncate text-sm text-foreground">{task.name}</span>
                        </TooltipTrigger>
                        <TooltipContent>{task.name}</TooltipContent>
                      </Tooltip>
                    </TooltipProvider>
                    <span className="flex-none text-[11px] text-muted-foreground">{formatFileSize(task.size)}</span>
                  </div>
                  {task.phase === 'staged' && <span className="text-[11px] text-muted-foreground">待上传</span>}
                  {task.phase === 'completing' && (
                    <span className="flex items-center gap-1 text-[11px] text-primary">
                      <LoaderCircle className="h-3 w-3 animate-spin" />
                      组装中
                    </span>
                  )}
                  {task.phase === 'uploading' && (
                    <div className="mt-1 flex items-center gap-2">
                      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
                        <div className="h-full bg-primary" style={{ width: `${task.progress}%` }} />
                      </div>
                      <span className="w-9 text-right text-[11px] tabular-nums text-muted-foreground">
                        {task.progress}%
                      </span>
                    </div>
                  )}
                  {task.phase === 'ready' && <span className="text-[11px] text-success">已完成</span>}
                  {task.phase === 'failed' && (
                    <span className="text-[11px] text-destructive">上传失败{task.error ? `：${task.error}` : ''}</span>
                  )}
                </div>
                {(task.phase === 'staged' || task.phase === 'uploading' || task.phase === 'failed') && (
                  <Button size="icon" variant="ghost" aria-label="移除" onClick={() => removeTask(task.localId)}>
                    <X className="h-3.5 w-3.5" />
                  </Button>
                )}
              </div>
            ))}
          </div>
        )}

        <ModalFooter>
          <Button variant="secondary" onClick={() => void handleClose()}>
            关闭
          </Button>
          <Button disabled={stagedCount === 0 || isUploading} onClick={() => void submit()}>
            {isUploading ? '上传中…' : '上传'}
          </Button>
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
}

export default BotUploadFilesModal;
