import { Button, Empty, IconButton, Skeleton } from '@/components/ui';
import { TooltipProvider } from '@/components/ui/Tooltip';
import type { ParticipantView } from '@/domain/collaboration';
import type { AuthenticatedUserName } from '@/domain/userIdentity';
import { useSessionFilePreview } from '@/pages/Workspace/hooks/useSessionFilePreview';
import { useSessionFileUpload } from '@/pages/Workspace/hooks/useSessionFileUpload';
import { useSessionFiles } from '@/pages/Workspace/hooks/useSessionFiles';
import type { SessionFileView } from '@/services/workspace/sessionFileService';
import { cn } from '@/utils/cn';
import { FileText, Upload, X } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { toast } from 'sonner';
import {
  getSessionFileCategory,
  SESSION_FILE_CATEGORY_LABELS,
  type SessionFileCategory,
} from '../SessionFileTypeIcon';
import { PreviewPane } from './SessionFilesPreviewPane';
import { SessionFilesRow } from './SessionFilesRow';
import { UploadFilesModal } from './UploadFilesModal';

export interface SessionFilesPanelProps {
  sessionId: string;
  onClose: () => void;
  participants?: ParticipantView[];
  authenticatedUser?: AuthenticatedUserName | null;
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

/**
 * 群聊「资源管理」右侧副屏：与单聊 BotSessionFilesPanel 同构（dmore artboard-008 对齐）——
 * 48px 标题行 + 32px 筛选条（下划线 Tabs + 上传图标钮）+ 61px 紧排文件行。
 * 容器由父级提供（ResizableWorkspaceSidebar 语义）；内部为下钻式两视图：
 * 列表视图为主，点击文件行本体或行内「预览」按钮切到预览视图（带「返回文件列表」）；
 * 预览视图头部支持「分享」与「下载」。
 * 数据 Hook 与行为不变（含群聊特有的「分享」行操作），行内核自单聊副屏对齐迁移。
 */
export function SessionFilesPanel({
  sessionId,
  onClose,
  participants,
  authenticatedUser,
}: SessionFilesPanelProps) {
  const filesState = useSessionFiles(sessionId, participants, authenticatedUser);
  const upload = useSessionFileUpload(sessionId, filesState.prependFile);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [category, setCategory] = useState<SessionFileCategory | 'all'>('all');
  const lastSyncedSessionId = useRef<string | null>(null);

  const readyFiles = useMemo(() => filesState.files.filter((f) => f.status === 'ready'), [filesState.files]);

  // 类型筛选 Tab（对齐单聊侧「全部/文档/代码」形态）：不做计数装饰，
  // 按实际存在的大类动态生成；群聊按文件名 + mimeType 判定（与其分类口径一致）。
  const categoryOptions = useMemo(() => {
    const order = ['media', 'document', 'code', 'sheet', 'other'] as const;
    const present = new Set<SessionFileCategory>();
    for (const file of readyFiles) present.add(getSessionFileCategory(file.name, file.mimeType));
    return [
      { value: 'all' as const, label: '全部' },
      ...order.filter((c) => present.has(c)).map((c) => ({ value: c, label: SESSION_FILE_CATEGORY_LABELS[c] })),
    ];
  }, [readyFiles]);

  // 过滤失效兜底：选中大类下最后一个文件被删后渲染回「全部」（同帧派生，不闪空态）。
  const effectiveCategory = categoryOptions.some((o) => o.value === category) ? category : 'all';
  const visibleFiles = useMemo(
    () =>
      effectiveCategory === 'all'
        ? readyFiles
        : readyFiles.filter((f) => getSessionFileCategory(f.name, f.mimeType) === effectiveCategory),
    [readyFiles, effectiveCategory],
  );

  useEffect(() => {
    if (lastSyncedSessionId.current !== sessionId) {
      lastSyncedSessionId.current = sessionId;
      setSelectedId(null);
      return;
    }
    // 下钻模式无自动选中；仅当选中的文件被删除/不可用时退出预览视图回列表。
    if (selectedId && !readyFiles.some((f) => f.fileId === selectedId)) setSelectedId(null);
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
    <aside className="flex h-full flex-col bg-background">
      {/* 标题行（设计稿：48px，标题 14px 左起 20px，16px 关闭 X 右起 20px）。 */}
      <div className="flex h-12 shrink-0 items-center justify-between pl-5 pr-5">
        <h3 className="m-0 text-sm font-normal leading-5 tracking-[-0.15px] text-foreground">资源管理</h3>
        <IconButton
          label="关闭资源管理面板"
          icon={<X className="h-4 w-4" aria-hidden />}
          size="sm"
          variant="ghost"
          onClick={onClose}
          className="h-6 w-6 text-zinc-400 hover:bg-transparent hover:text-foreground"
        />
      </div>

      {selectedFile ? (
        /* 预览视图（下钻）：返回导航合并进 PreviewPane 头部，预览独享全部可用高度（无独立导航层）。 */
        <div data-testid="session-files-preview" className="flex min-h-0 flex-1 flex-col">
          <PreviewPane
            file={selectedFile}
            filesStateEmpty={false}
            preview={preview}
            onBack={() => setSelectedId(null)}
            onDownload={() => {
              if (selectedFile) void filesState.downloadFile(selectedFile);
            }}
            onShare={async () => {
              if (selectedFile) await handleShare(selectedFile);
            }}
          />
        </div>
      ) : (
        <>
          {/* 筛选条（设计稿：32px 高、发丝线下边、左起下划线 Tabs、右端 24px 边框上传钮）。 */}
          <div className="flex h-[33px] shrink-0 items-center justify-between border-b-[0.5px] border-zinc-300/40 pl-5 pr-5">
            {readyFiles.length > 0 ? (
              <div role="tablist" aria-label="资源类型筛选" className="flex h-8 self-end">
                {categoryOptions.map((option) => {
                  const active = option.value === effectiveCategory;
                  return (
                    <Button
                      key={option.value}
                      type="button"
                      variant="ghost"
                      role="tab"
                      aria-selected={active}
                      onClick={() => setCategory(option.value)}
                      className={cn(
                        'relative h-8 shrink-0 justify-center rounded-none border-0 px-2 text-xs leading-[18px]',
                        active ? 'font-medium text-zinc-800' : 'font-normal text-muted-foreground hover:bg-transparent',
                      )}
                    >
                      {option.label}
                      {active && (
                        <span aria-hidden className="absolute inset-x-2 bottom-0 h-0.5 rounded-full bg-zinc-800" />
                      )}
                    </Button>
                  );
                })}
              </div>
            ) : (
              <span />
            )}
            <Button
              variant="outline"
              aria-label="上传文件"
              onClick={() => setUploadOpen(true)}
              className="h-6 w-6 shrink-0 rounded-lg border-zinc-200 bg-background p-0 text-muted-foreground hover:bg-background hover:text-foreground"
            >
              <Upload className="h-3.5 w-3.5" aria-hidden />
            </Button>
          </div>

          <div className="app-scrollbar flex min-h-0 flex-1 flex-col overflow-y-auto">
            {/* 文件列表（设计稿：61px 紧排行，图标 20px + 文件名 13px + 元数据 11px；首行顶距 8px）。 */}
            <div className="min-h-0 flex-1 pt-2">
              {filesState.isLoading && readyFiles.length === 0 ? (
                /* 加载骨架与真实文件行同构（对齐单聊侧）：61px 行高 + 20px 图标位 + 文件名/元数据
                    双条描线，避免整版灰色实心块，加载完成瞬间行位零跳动。 */
                <ul className="m-0 list-none p-0">
                  {['w-2/5', 'w-3/5', 'w-1/2', 'w-2/3', 'w-3/5'].map((nameWidth, index) => (
                    <li key={index} aria-hidden className="flex h-[61px] items-center px-5">
                      <Skeleton.Block className="h-5 w-5 shrink-0 rounded-md" />
                      <div className="ml-1 flex min-w-0 flex-1 flex-col">
                        <Skeleton.Block className={cn('h-[13px] rounded-full', nameWidth)} />
                        <Skeleton.Block className="mt-1 h-[9px] w-10 rounded-full" />
                      </div>
                    </li>
                  ))}
                </ul>
              ) : readyFiles.length === 0 ? (
                <Empty
                  compact
                  icon={<FileText className="h-5 w-5" aria-hidden />}
                  title="暂无资源"
                  description="点击右上「上传文件」，或选择本会话已有文件。"
                />
              ) : (
                <TooltipProvider>
                  <ul className="m-0 list-none p-0">
                    {visibleFiles.map((file) => (
                      <SessionFilesRow
                        key={file.fileId}
                        file={file}
                        onOpenPreview={() => setSelectedId(file.fileId)}
                        onDownload={(f) => void filesState.downloadFile(f)}
                        onShare={(f) => void handleShare(f)}
                        onDelete={(f) => void filesState.removeFile(f.fileId)}
                      />
                    ))}
                  </ul>
                </TooltipProvider>
              )}
            </div>
          </div>
        </>
      )}

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
    </aside>
  );
}

export default SessionFilesPanel;
