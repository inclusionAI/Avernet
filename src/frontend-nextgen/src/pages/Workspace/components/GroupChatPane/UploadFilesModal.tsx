import { Button, Modal, ModalContent, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import {
  SESSION_FILE_ALLOWED_EXT,
  SESSION_FILE_MAX_BATCH,
  formatFileSize,
} from '@/services/workspace/sessionFileUtils';
import { cn } from '@/utils/cn';
import { FileText, LoaderCircle, RotateCw, X } from 'lucide-react';
import { useCallback, useRef, useState } from 'react';
import type { UploadTask } from '../../hooks/useSessionFileUpload';

export interface UploadFilesModalProps {
  open: boolean;
  onClose: () => void;
  queue: UploadTask[];
  isUploading: boolean;
  stageFiles: (files: File[]) => void;
  submitStaged: () => Promise<void>;
  cancelTask: (localId: string) => Promise<void>;
  retryTask: (localId: string) => Promise<void>;
  discardAll: () => Promise<void>;
  clearCompleted: () => void;
  hasPending: () => boolean;
  onAddToSession: () => void;
}

export function UploadFilesModal(props: UploadFilesModalProps) {
  const {
    open,
    onClose,
    queue,
    isUploading,
    stageFiles,
    submitStaged,
    cancelTask,
    retryTask,
    discardAll,
    clearCompleted,
    hasPending,
    onAddToSession,
  } = props;
  const inputRef = useRef<HTMLInputElement>(null);
  const [isDragOver, setIsDragOver] = useState(false);

  const pickFiles = useCallback(
    (files: FileList | File[] | null) => {
      if (!files) return;
      const arr = Array.from(files);
      if (arr.length) {
        stageFiles(arr);
        void submitStaged();
      }
      if (inputRef.current) inputRef.current.value = '';
    },
    [stageFiles, submitStaged],
  );

  const handleClose = useCallback(async () => {
    if (hasPending()) {
      await discardAll();
    } else {
      clearCompleted();
    }
    onClose();
  }, [discardAll, clearCompleted, hasPending, onClose]);

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
          <p className="text-sm font-medium text-foreground">点击或拖拽选择文件</p>
          <p className="mt-1 text-xs text-muted-foreground">选中文件后将自动上传，完成后可添加至会话</p>
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
          <TooltipProvider>
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
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <span className="truncate text-sm text-foreground">{task.name}</span>
                        </TooltipTrigger>
                        <TooltipContent>{task.name}</TooltipContent>
                      </Tooltip>
                      <span className="flex-none text-[11px] text-muted-foreground">{formatFileSize(task.size)}</span>
                    </div>
                    {task.phase === 'staged' && <span className="text-[11px] text-muted-foreground">待上传</span>}
                    {(task.phase === 'preparing' || task.phase === 'completing') && (
                      <span className="flex items-center gap-1 text-[11px] text-primary">
                        <LoaderCircle className="h-3 w-3 animate-spin" />
                        {task.phase === 'preparing' ? '准备中' : '组装中'}
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
                      <span className="text-[11px] text-destructive">
                        上传失败{task.error ? `：${task.error}` : ''}
                      </span>
                    )}
                  </div>
                  <div className="flex flex-none items-center gap-1">
                    {task.phase === 'failed' && (
                      <Button
                        size="icon"
                        variant="ghost"
                        aria-label="重试"
                        onClick={() => void retryTask(task.localId)}
                      >
                        <RotateCw className="h-3.5 w-3.5" />
                      </Button>
                    )}
                    {task.phase === 'ready' ||
                    task.phase === 'staged' ||
                    task.phase === 'uploading' ||
                    task.phase === 'preparing' ? (
                      <Button
                        size="icon"
                        variant="ghost"
                        aria-label="移除"
                        onClick={() => void cancelTask(task.localId)}
                      >
                        <X className="h-3.5 w-3.5" />
                      </Button>
                    ) : null}
                  </div>
                </div>
              ))}
            </div>
          </TooltipProvider>
        )}

        <ModalFooter>
          <Button variant="secondary" onClick={() => void handleClose()}>
            关闭
          </Button>
          <Button
            disabled={queue.every((task) => task.phase !== 'ready' || !task.fileId) || isUploading}
            onClick={onAddToSession}
          >
            {isUploading ? '上传中…' : '添加至会话'}
          </Button>
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
}

export default UploadFilesModal;
