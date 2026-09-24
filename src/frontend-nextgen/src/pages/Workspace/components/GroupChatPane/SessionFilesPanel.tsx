import { Button, ConfirmDialog, Empty, IconButton, Skeleton } from '@/components/ui';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import type { ParticipantView } from '@/domain/collaboration';
import type { AuthenticatedUserName } from '@/domain/userIdentity';
import { useSessionFilePreview } from '@/pages/Workspace/hooks/useSessionFilePreview';
import { useSessionFileUpload } from '@/pages/Workspace/hooks/useSessionFileUpload';
import { useSessionFiles } from '@/pages/Workspace/hooks/useSessionFiles';
import type { SessionFileView } from '@/services/workspace/sessionFileService';
import { formatFileSize, getPreviewKind } from '@/services/workspace/sessionFileUtils';
import { Download, Eye, FileText, Share2, Trash2, Upload } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { toast } from 'sonner';
import { ManagePanelHeader } from '../ManagePanel/ManagePanelHeader';
import { ManagePanelTabs, type ManagePanelTabOption } from '../ManagePanel/ManagePanelTabs';
import { formatMonthDayTime } from '../SessionCard';
import {
  getSessionFileCategory,
  SESSION_FILE_CATEGORY_LABELS,
  SessionFileTypeIcon,
  type SessionFileCategory,
} from '../SessionFileTypeIcon';
import { PreviewPane } from './SessionFilesPreviewPane';
import { UploadFilesModal } from './UploadFilesModal';

export interface SessionFilesPanelProps {
  sessionId: string;
  sessionName: string;
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
 * 会话文件右侧内嵌面板（验收方向：原全屏 Modal 改为与成员/管理面板同构的副屏）。
 * 容器：ResizableWorkspaceSidebar（由父级 GroupWorkspaceArea 提供，320-600px 可拖宽）；
 * 内部为下钻式两视图（验收微调三轮）：列表视图为主，点击文件行整屏切到该文件的预览视图
 * （预览是文件级操作，独享整个副屏宽度，带「返回文件列表」导航）；不占列表空间。
 * 列表行 / 预览 / 上传弹窗内核均自 SessionFilesModal 迁移，数据 Hook 与行为不变。
 */
export function SessionFilesPanel({
  sessionId,
  sessionName,
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

  // 类型过滤 Tab（验收微调）：按大类聚合（媒体/文档/代码/表格/其他）动态生成，
  // 仅显示实际存在的大类（带计数）；固定顺序不随数量变化，保证位置可预期。
  const categoryOptions = useMemo<ManagePanelTabOption<SessionFileCategory | 'all'>[]>(() => {
    const counts = new Map<SessionFileCategory, number>();
    for (const file of readyFiles) {
      const key = getSessionFileCategory(file.name, file.mimeType);
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    const present = (['media', 'document', 'code', 'sheet', 'other'] as const).filter((c) => counts.has(c));
    return [
      { value: 'all', label: `全部 ${readyFiles.length}` },
      ...present.map((c) => ({ value: c, label: `${SESSION_FILE_CATEGORY_LABELS[c]} ${counts.get(c)}` })),
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
      <ManagePanelHeader title="会话文件" subtitle={sessionName} statusLabel="可查看" onClose={onClose} />

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
        <div className="app-scrollbar flex min-h-0 flex-1 flex-col overflow-y-auto">
          {/* 文件列表视图（主视图）：行结构自 SessionFilesModal 迁移，行为不变。 */}
          <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-3">
            <span className="text-xs font-medium text-muted-foreground">{`会话文件（${filesState.total}）`}</span>
            <Button
              size="sm"
              variant="ghost"
              leftIcon={<Upload className="h-3.5 w-3.5" aria-hidden />}
              onClick={() => setUploadOpen(true)}
              className="border border-primary/30 bg-primary/10 px-3 text-primary hover:bg-primary/10 hover:text-primary"
            >
              上传文件
            </Button>
          </div>

          {/* 类型过滤 Tab（验收微调）：全量列表上方，按大类切换过滤。 */}
          {readyFiles.length > 0 ? (
            <ManagePanelTabs value={effectiveCategory} options={categoryOptions} onChange={setCategory} />
          ) : null}

          <div className="min-h-0 flex-1 p-2">
            {filesState.isLoading ? (
              <div className="space-y-1.5 p-1">
                {[1, 2, 3, 4].map((i) => (
                  <Skeleton.Block key={i} className="h-10 w-full rounded-lg" />
                ))}
              </div>
            ) : readyFiles.length === 0 ? (
              <Empty
                compact
                icon={<FileText className="h-5 w-5" aria-hidden />}
                title="暂无会话文件"
                description="点击上方「上传文件」上传，或选择本会话已有文件。"
              />
            ) : (
              <TooltipProvider>
                <ul className="m-0 list-none space-y-1 p-0">
                  {visibleFiles.map((file) => {
                    // 下钻模式（验收微调四轮）：预览收敛为行内操作按钮（首位），
                    // 行本体恢复纯展示（点击无动作）。
                    // 不可预览类型（非图片/PDF/文本白名单）：按钮禁用并提示原因。
                    // 用 aria-disabled 而非原生 disabled——禁用态仍需 hover 出 Tooltip，
                    // 原生 disabled button 不派发 pointer 事件会让提示失效。
                    const previewable = getPreviewKind(file.name, file.mimeType) !== 'other';
                    return (
                      <li
                        key={file.fileId}
                        className="flex items-center gap-1 rounded-lg px-2.5 py-2 transition-colors hover:bg-muted/50"
                      >
                        <div className="flex min-w-0 flex-1 shrink cursor-default items-start justify-start gap-2 rounded-md px-1.5 py-0.5 text-left text-xs text-foreground">
                          <SessionFileTypeIcon
                            name={file.name}
                            mimeType={file.mimeType}
                            className="mt-0.5 h-4 w-4 shrink-0"
                          />
                          <div className="min-w-0 flex-1">
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <span className="block truncate">{file.name}</span>
                              </TooltipTrigger>
                              <TooltipContent>{file.name}</TooltipContent>
                            </Tooltip>
                            <span className="mt-1 block truncate text-xs leading-4 text-muted-foreground">
                              {file.ownerName} · {formatMonthDayTime(file.createdAt * 1000)} ·{' '}
                              {formatFileSize(file.size)}
                            </span>
                          </div>
                        </div>
                        {/* 元数据模型统一（2026-09-20 用户拍板）：大小并入次行元数据
                            （上传者 · 时间 · 大小），与单聊侧同构；行内操作按钮常显
                            （不再 hover 悬浮切换，原 opacity/pointer-events 切换系
                            Tooltip 闪烁缺陷的历史对策，常显后不再需要）。 */}
                        <div className="flex shrink-0 items-center">
                          <IconButton
                            label={previewable ? '预览文件' : '该文件类型暂不支持预览'}
                            // PR#413 评审跟进 P3（#257104459）：视觉提示精简，读屏名称保留
                            // 文件名——ariaLabel 恢复含文件名的完整可访问名称，读屏用户可区分行。
                            ariaLabel={
                              previewable ? `预览文件 ${file.name}` : `预览文件 ${file.name}（该文件类型暂不支持预览）`
                            }
                            icon={<Eye className="h-4 w-4" aria-hidden />}
                            size="sm"
                            variant="ghost"
                            aria-disabled={previewable ? undefined : true}
                            onClick={previewable ? () => setSelectedId(file.fileId) : undefined}
                            className={previewable ? undefined : 'cursor-not-allowed opacity-50'}
                          />
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
