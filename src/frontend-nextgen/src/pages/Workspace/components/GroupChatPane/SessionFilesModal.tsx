import {
  Button,
  ConfirmDialog,
  Empty,
  IconButton,
  Modal,
  ModalClose,
  ModalContent,
  ModalDescription,
  ModalTitle,
  Skeleton,
} from '@/components/ui';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import { useSessionFilePreview } from '@/pages/Workspace/hooks/useSessionFilePreview';
import { useSessionFileUpload } from '@/pages/Workspace/hooks/useSessionFileUpload';
import { useSessionFiles } from '@/pages/Workspace/hooks/useSessionFiles';
import type { SessionFileView } from '@/services/workspace/sessionFileService';
import { formatFileSize } from '@/services/workspace/sessionFileUtils';
import { cn } from '@/utils/cn';
import { Download, FileText, FolderOpen, Share2, Trash2, Upload, X } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { toast } from 'sonner';
import { formatMonthDayTime } from '../SessionCard';
import { PreviewPane } from './SessionFilesPreviewPane';
import { UploadFilesModal } from './UploadFilesModal';

export interface SessionFilesModalProps {
  sessionId: string;
  sessionName: string;
  onClose: () => void;
}

async function copyText(text: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const input = document.createElement('textarea');
    input.value = text;
    document.body.appendChild(input);
    input.select();
    document.execCommand('copy');
    document.body.removeChild(input);
  }
}

export function SessionFilesModal({ sessionId, sessionName, onClose }: SessionFilesModalProps) {
  const filesState = useSessionFiles(sessionId);
  const upload = useSessionFileUpload(sessionId, filesState.prependFile);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const lastSyncedSessionId = useRef<string | null>(null);

  const readyFiles = useMemo(() => filesState.files.filter((f) => f.status === 'ready'), [filesState.files]);

  useEffect(() => {
    if (lastSyncedSessionId.current !== sessionId) {
      lastSyncedSessionId.current = sessionId;
      setSelectedId(null);
    }
    if (readyFiles.length === 0) {
      setSelectedId(null);
      return;
    }
    const stillExists = readyFiles.some((f) => f.fileId === selectedId);
    if (!stillExists) setSelectedId(readyFiles[0].fileId);
  }, [sessionId, readyFiles, selectedId]);

  const selectedFile = useMemo(() => readyFiles.find((f) => f.fileId === selectedId) ?? null, [readyFiles, selectedId]);

  const preview = useSessionFilePreview(selectedFile);

  const handleShare = async (file: SessionFileView) => {
    const url = await filesState.shareFile(file.fileId);
    if (url) {
      await copyText(url);
      toast.success('分享链接已复制');
    }
  };

  return (
    <Modal open onOpenChange={(open) => !open && onClose()}>
      <ModalContent
        showClose={false}
        closeLabel="关闭会话文件弹窗"
        className={cn(
          'flex h-[80vh] w-[calc(100vw-2rem)] max-w-[1080px] flex-col gap-0 overflow-hidden rounded-lg p-0',
          'border border-border bg-card text-foreground shadow-2xl',
        )}
      >
        <ModalTitle className="sr-only">资源管理 · {sessionName}</ModalTitle>
        <ModalDescription className="sr-only">查看、预览、下载本会话中已上传的文件。</ModalDescription>

        <header className="flex items-start justify-between gap-3 border-b border-border px-3 py-4 sm:px-6 sm:py-5">
          <div className="min-w-0 flex-1 pr-2">
            <div className="flex items-center gap-2">
              <FolderOpen className="h-5 w-5 shrink-0 text-primary" aria-hidden />
              <h2 className="m-0 truncate text-base font-semibold text-foreground">资源管理 · {sessionName}</h2>
            </div>
            <p className="mt-1.5 text-sm leading-5 text-muted-foreground">
              已添加的资源仅对当前会话生效，协作群内所有成员均可查看与管理。
            </p>
          </div>
          <ModalClose asChild>
            <Button type="button" aria-label="关闭" variant="ghost" size="icon" className="size-7" onClick={onClose}>
              <X className="h-4 w-4" aria-hidden />
            </Button>
          </ModalClose>
        </header>

        <div className="flex items-center justify-between gap-4 border-b border-border px-3 pt-3 sm:px-6">
          <div className="rounded-t-lg bg-primary/10 px-4 py-2.5 text-[13px] font-medium text-primary">文件</div>
          <span className="pb-2 text-xs text-muted-foreground">仅可访问当前协作群会话文件</span>
        </div>

        <div className="flex min-h-0 flex-1">
          <div className="flex w-[min(320px,42%)] shrink-0 flex-col border-r border-border sm:w-[320px]">
            <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-3">
              <span className="text-xs font-medium text-muted-foreground">{`会话文件（${filesState.total}）`}</span>
              <Button
                size="sm"
                variant="ghost"
                leftIcon={<Upload className="h-3.5 w-3.5" aria-hidden />}
                onClick={() => setUploadOpen(true)}
                className="border border-primary/30 bg-primary/10 px-3 text-primary hover:bg-primary/10 hover:text-primary"
              >
                添加文件
              </Button>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto p-2">
              {filesState.isLoading ? (
                <div className="space-y-1.5 p-1">
                  {[1, 2, 3, 4].map((i) => (
                    <Skeleton.Block key={i} className="h-10 w-full rounded-lg" />
                  ))}
                </div>
              ) : readyFiles.length === 0 ? (
                <Empty
                  compact
                  icon={<FolderOpen className="h-5 w-5" aria-hidden />}
                  title="暂无会话文件"
                  description="点击上方「添加文件」上传，或选择本会话已有文件。"
                />
              ) : (
                <TooltipProvider>
                  <ul className="m-0 list-none p-0">
                    {readyFiles.map((file) => {
                      const isActive = file.fileId === selectedFile?.fileId;
                      return (
                        <li
                          key={file.fileId}
                          className="group flex items-center gap-1 rounded-lg px-1.5 py-1 transition-colors hover:bg-muted/50"
                        >
                          <Button
                            type="button"
                            variant="ghost"
                            onClick={() => setSelectedId(file.fileId)}
                            className={cn(
                              'min-w-0 flex-1 shrink justify-start gap-2 rounded-md px-2 py-1.5 text-left text-[13px] transition-colors',
                              isActive ? 'bg-primary/10 text-primary' : 'text-foreground hover:text-primary',
                            )}
                          >
                            <FileText
                              className={cn('h-4 w-4 shrink-0', isActive ? 'text-primary' : 'text-muted-foreground')}
                              aria-hidden
                            />
                            <div className="min-w-0 flex-1">
                              <Tooltip>
                                <TooltipTrigger asChild>
                                  <span className="block truncate">{file.name}</span>
                                </TooltipTrigger>
                                <TooltipContent>{file.name}</TooltipContent>
                              </Tooltip>
                              <span className="block truncate text-[11px] text-muted-foreground">
                                {file.ownerName} · {formatMonthDayTime(file.createdAt * 1000)}
                              </span>
                            </div>
                            <span className="shrink-0 self-center text-[11px] tabular-nums text-muted-foreground">
                              {formatFileSize(file.size)}
                            </span>
                          </Button>
                          <div className="hidden shrink-0 items-center group-hover:flex">
                            <IconButton
                              label="下载文件"
                              icon={<Download className="h-4 w-4" aria-hidden />}
                              size="sm"
                              variant="ghost"
                              onClick={() => void filesState.downloadFile(file)}
                            />
                            <IconButton
                              label="分享文件"
                              icon={<Share2 className="h-4 w-4" aria-hidden />}
                              size="sm"
                              variant="ghost"
                              onClick={() => void handleShare(file)}
                            />
                            <ConfirmDialog
                              title={`删除文件 ${file.name}`}
                              description="删除后本会话成员将无法再查看该文件。"
                              confirmText="删除"
                              confirmVariant="destructive"
                              onConfirm={() => void filesState.removeFile(file.fileId)}
                            >
                              <IconButton
                                label="删除文件"
                                icon={<Trash2 className="h-4 w-4" aria-hidden />}
                                size="sm"
                                variant="ghost"
                              />
                            </ConfirmDialog>
                          </div>
                        </li>
                      );
                    })}
                  </ul>
                </TooltipProvider>
              )}
            </div>
          </div>

          <PreviewPane
            file={selectedFile}
            filesStateEmpty={readyFiles.length === 0 && !filesState.isLoading}
            preview={preview}
            onDownload={() => {
              if (selectedFile) void filesState.downloadFile(selectedFile);
            }}
            onShare={async () => {
              if (selectedFile) await handleShare(selectedFile);
            }}
          />
        </div>

        <UploadFilesModal
          open={uploadOpen}
          onClose={() => setUploadOpen(false)}
          queue={upload.queue}
          isUploading={upload.isUploading}
          stageFiles={upload.stageFiles}
          submitStaged={async () => {
            await upload.submitStaged();
          }}
          onAddToSession={() => {
            upload.clearCompleted();
            setUploadOpen(false);
            toast.success('文件已添加至会话');
          }}
          cancelTask={upload.cancelTask}
          retryTask={upload.retryTask}
          discardAll={upload.discardAll}
          clearCompleted={upload.clearCompleted}
          hasPending={upload.hasPending}
        />
      </ModalContent>
    </Modal>
  );
}

export default SessionFilesModal;
