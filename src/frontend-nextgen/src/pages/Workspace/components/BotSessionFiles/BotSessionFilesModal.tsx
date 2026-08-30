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
import { PreviewPane } from '@/pages/Workspace/components/GroupChatPane/SessionFilesPreviewPane';
import { useBotSessionFilePreview } from '@/pages/Workspace/hooks/useBotSessionFilePreview';
import type { BotSessionFileView } from '@/services/workspace/botSessionFileService';
import { formatFileSize } from '@/services/workspace/sessionFileUtils';
import { cn } from '@/utils/cn';
import { Download, FileText, FolderOpen, Reply, Trash2, Upload, X } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';

export interface BotSessionFilesModalProps {
  open: boolean;
  onClose: () => void;
  sessionName: string;
  readyFiles: BotSessionFileView[];
  isLoadingList: boolean;
  botId: string | null;
  sessionId: string | null;
  userId: string | null;
  ownerId?: string;
  onUploadClick: () => void;
  onOpen: () => void;
  onDelete: (file: BotSessionFileView) => void;
  onDownload: (file: BotSessionFileView) => void;
  onReference: (file: BotSessionFileView) => void;
}

export function BotSessionFilesModal({
  open,
  onClose,
  sessionName,
  readyFiles,
  isLoadingList,
  botId,
  sessionId,
  userId,
  ownerId,
  onUploadClick,
  onOpen,
  onDelete,
  onDownload,
  onReference,
}: BotSessionFilesModalProps) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const lastSyncedKey = useRef<string | null>(null);
  const wasOpen = useRef(false);

  useEffect(() => {
    if (open && !wasOpen.current) onOpen();
    wasOpen.current = open;
  }, [open, onOpen]);

  useEffect(() => {
    const key = `${botId ?? ''}_${sessionId ?? ''}`;
    if (lastSyncedKey.current !== key) {
      lastSyncedKey.current = key;
      setSelectedId(null);
      return;
    }
    if (readyFiles.length === 0) {
      setSelectedId(null);
      return;
    }
    if (selectedId && !readyFiles.some((f) => f.resourceId === selectedId)) setSelectedId(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [botId, sessionId, readyFiles, selectedId]);

  const selectedFile = useMemo(
    () => readyFiles.find((f) => f.resourceId === selectedId) ?? null,
    [readyFiles, selectedId],
  );

  const preview = useBotSessionFilePreview(selectedFile, { botId, sessionId, userId, ownerId });

  if (!open) return null;

  const previewFile: { name: string; size: number } | null = selectedFile
    ? { name: selectedFile.displayName, size: selectedFile.sizeBytes ?? 0 }
    : null;

  return (
    <Modal open onOpenChange={(o) => !o && onClose()}>
      <ModalContent
        showClose={false}
        closeLabel="关闭会话文件弹窗"
        className={cn(
          'flex h-[80vh] w-[calc(100vw-2rem)] max-w-[1080px] flex-col gap-0 overflow-hidden rounded-2xl p-0',
          'border border-[var(--color-border)] bg-[var(--color-card)] text-[var(--color-fg)] shadow-2xl',
        )}
      >
        <ModalTitle className="sr-only">资源管理 · {sessionName}</ModalTitle>
        <ModalDescription className="sr-only">查看、预览、下载本会话中已上传的文件。</ModalDescription>

        <header className="flex items-start justify-between gap-3 border-b border-[var(--color-border)] px-6 py-5">
          <div className="min-w-0 flex-1 pr-2">
            <div className="flex items-center gap-2">
              <FolderOpen className="h-5 w-5 shrink-0 text-[var(--color-primary)]" aria-hidden />
              <h2 className="m-0 truncate text-base font-semibold text-[var(--color-fg)]">资源管理 · {sessionName}</h2>
            </div>
            <p className="mt-1.5 text-sm leading-5 text-[var(--color-muted)]">
              已上传的文件仅对当前会话生效，可引用到输入框。
            </p>
          </div>
          <ModalClose asChild>
            <Button type="button" aria-label="关闭" variant="ghost" size="icon" className="size-7" onClick={onClose}>
              <X className="h-4 w-4" aria-hidden />
            </Button>
          </ModalClose>
        </header>

        <div className="flex items-center justify-between gap-4 border-b border-[var(--color-border)] px-6 pt-3">
          <div className="rounded-t-lg bg-[var(--color-primary-soft)] px-4 py-2.5 text-[13px] font-medium text-[var(--color-primary)]">
            文件
          </div>
          <span className="pb-2 text-xs text-[var(--color-muted)]">仅可访问当前会话文件</span>
        </div>

        <div className="flex min-h-0 flex-1">
          <div className="flex w-[320px] shrink-0 flex-col border-r border-[var(--color-border)]">
            <div className="flex items-center justify-between gap-2 border-b border-[var(--color-border)] px-4 py-3">
              <span className="text-xs font-medium text-[var(--color-muted)]">{`会话文件（${readyFiles.length}）`}</span>
              <Button
                size="sm"
                variant="ghost"
                leftIcon={<Upload className="h-3.5 w-3.5" aria-hidden />}
                onClick={onUploadClick}
                className="border border-[var(--color-primary-weak)] bg-[var(--color-primary-soft)] px-3 text-[var(--color-primary)] hover:bg-[var(--color-primary-soft)] hover:text-[var(--color-primary)]"
              >
                添加文件
              </Button>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto p-2">
              {isLoadingList && readyFiles.length === 0 ? (
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
                      const isActive = file.resourceId === selectedFile?.resourceId;
                      return (
                        <li
                          key={file.resourceId}
                          className="group flex items-center gap-1 rounded-lg px-1.5 py-1 transition-colors hover:bg-[var(--color-panel-muted)]"
                        >
                          <Button
                            type="button"
                            variant="ghost"
                            onClick={() => setSelectedId(file.resourceId)}
                            className={cn(
                              'h-9 min-w-0 flex-1 shrink justify-start gap-2 rounded-md px-2 text-left text-[13px] transition-colors',
                              isActive
                                ? 'bg-[var(--color-primary-soft)] text-[var(--color-primary)]'
                                : 'text-[var(--color-fg)] hover:text-[var(--color-primary)]',
                            )}
                          >
                            <FileText
                              className={cn(
                                'h-4 w-4 shrink-0',
                                isActive ? 'text-[var(--color-primary)]' : 'text-[var(--color-muted)]',
                              )}
                              aria-hidden
                            />
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <span className="min-w-0 flex-1 truncate">{file.displayName}</span>
                              </TooltipTrigger>
                              <TooltipContent>{file.displayName}</TooltipContent>
                            </Tooltip>
                            <span className="shrink-0 text-[11px] tabular-nums text-[var(--color-muted)]">
                              {file.sizeBytes ? formatFileSize(file.sizeBytes) : '--'}
                            </span>
                          </Button>
                          <div className="hidden shrink-0 items-center group-hover:flex">
                            <IconButton
                              label="引用到输入框"
                              icon={<Reply className="h-4 w-4" aria-hidden />}
                              size="sm"
                              variant="ghost"
                              onClick={() => onReference(file)}
                            />
                            <IconButton
                              label="下载文件"
                              icon={<Download className="h-4 w-4" aria-hidden />}
                              size="sm"
                              variant="ghost"
                              onClick={() => onDownload(file)}
                            />
                            <ConfirmDialog
                              title={`删除文件 ${file.displayName}`}
                              description="删除后本会话成员将无法再查看该文件。"
                              confirmText="删除"
                              confirmVariant="destructive"
                              onConfirm={() => onDelete(file)}
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
            file={previewFile}
            filesStateEmpty={readyFiles.length === 0 && !isLoadingList}
            preview={preview}
            onDownload={() => {
              if (selectedFile) onDownload(selectedFile);
            }}
          />
        </div>
      </ModalContent>
    </Modal>
  );
}

export default BotSessionFilesModal;
