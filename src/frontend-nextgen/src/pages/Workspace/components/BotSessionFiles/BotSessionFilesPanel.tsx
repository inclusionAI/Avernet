import { Button, ConfirmDialog, Empty, IconButton, Skeleton } from '@/components/ui';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import { useBotSessionFilePreview } from '@/pages/Workspace/hooks/useBotSessionFilePreview';
import type { BotSessionFileView } from '@/services/workspace/botSessionFileService';
import { formatFileSize, getPreviewKind } from '@/services/workspace/sessionFileUtils';
import { Download, Eye, FileText, Reply, Trash2, Upload } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { PreviewPane } from '../GroupChatPane/SessionFilesPreviewPane';
import { ManagePanelHeader } from '../ManagePanel/ManagePanelHeader';
import { ManagePanelTabs, type ManagePanelTabOption } from '../ManagePanel/ManagePanelTabs';
import {
  getSessionFileCategory,
  SESSION_FILE_CATEGORY_LABELS,
  SessionFileTypeIcon,
  type SessionFileCategory,
} from '../SessionFileTypeIcon';

export interface BotSessionFilesPanelProps {
  sessionName: string;
  readyFiles: BotSessionFileView[];
  isLoadingList: boolean;
  botId?: string;
  sessionId?: string;
  userId?: string;
  ownerId?: string;
  onClose: () => void;
  onUploadClick: () => void;
  onOpen: () => void;
  onDelete: (file: BotSessionFileView) => void;
  onDownload: (file: BotSessionFileView) => void;
  onReference: (file: BotSessionFileView) => void;
}

/**
 * 单聊会话文件右侧内嵌面板（与协作群 SessionFilesPanel 同构的副屏）。
 * 容器由父级提供（ResizableWorkspaceSidebar 语义）；内部为下钻式两视图（验收微调三轮）：
 * 列表视图为主，点击文件行整屏切到预览视图（带「返回文件列表」）；列表行/预览内核自
 * BotSessionFilesModal 迁移，数据 Hook 与行为不变（含单聊特有的「引用到输入框」行操作）。
 */
export function BotSessionFilesPanel({
  sessionName,
  readyFiles,
  isLoadingList,
  botId,
  sessionId,
  userId,
  ownerId,
  onClose,
  onUploadClick,
  onOpen,
  onDelete,
  onDownload,
  onReference,
}: BotSessionFilesPanelProps) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [category, setCategory] = useState<SessionFileCategory | 'all'>('all');
  // 面板挂载即触发一次列表刷新（对齐旧 Modal 的 open 转变 effect 语义；
  // 副屏面板由父级条件渲染，挂载 == 打开）。
  // sessionId 入依赖：副屏保持打开时切换会话（组件不卸载），列表已被 resetForSession
  // 清空，若刷新仅挂载时触发一次，新会话文件列表将永远不加载——会话切换需再触发一次。
  const onOpenRef = useRef(onOpen);
  onOpenRef.current = onOpen;
  useEffect(() => {
    onOpenRef.current();
  }, [sessionId]);

  // 类型过滤 Tab（验收微调）：按大类聚合动态生成（带计数），与群聊侧同构；
  // 单聊无 mimeType，按文件名判定（与其预览 Hook 口径一致）。
  const categoryOptions = useMemo<ManagePanelTabOption<SessionFileCategory | 'all'>[]>(() => {
    const counts = new Map<SessionFileCategory, number>();
    for (const file of readyFiles) {
      const key = getSessionFileCategory(file.displayName);
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
        : readyFiles.filter((f) => getSessionFileCategory(f.displayName) === effectiveCategory),
    [readyFiles, effectiveCategory],
  );

  const selectedFile = useMemo(
    () => readyFiles.find((f) => f.resourceId === selectedId) ?? null,
    [readyFiles, selectedId],
  );

  const preview = useBotSessionFilePreview(selectedFile, {
    botId: botId ?? null,
    sessionId: sessionId ?? null,
    userId: userId ?? null,
    ownerId,
  });

  const previewFile: { name: string; size: number } | null = selectedFile
    ? { name: selectedFile.displayName, size: selectedFile.sizeBytes ?? 0 }
    : null;

  return (
    <aside className="flex h-full flex-col bg-background">
      <ManagePanelHeader title="会话文件" subtitle={sessionName} statusLabel="可查看" onClose={onClose} />

      {selectedFile ? (
        /* 预览视图（下钻）：返回导航合并进 PreviewPane 头部，预览独享全部可用高度（无独立导航层）。 */
        <div data-testid="session-files-preview" className="flex min-h-0 flex-1 flex-col">
          <PreviewPane
            file={previewFile}
            filesStateEmpty={false}
            preview={preview}
            onBack={() => setSelectedId(null)}
            onDownload={() => {
              if (selectedFile) onDownload(selectedFile);
            }}
          />
        </div>
      ) : (
        <div className="app-scrollbar flex min-h-0 flex-1 flex-col overflow-y-auto">
          {/* 文件列表视图（主视图）：行结构自 BotSessionFilesModal 迁移，含单聊特有的引用操作。 */}
          <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-3">
            <span className="text-xs font-medium text-muted-foreground">{`会话文件（${readyFiles.length}）`}</span>
            <Button
              size="sm"
              variant="ghost"
              leftIcon={<Upload className="h-3.5 w-3.5" aria-hidden />}
              onClick={onUploadClick}
              className="border border-primary/30 bg-primary/10 px-3 text-primary hover:bg-primary/10 hover:text-primary"
            >
              上传文件
            </Button>
          </div>

          {/* 类型过滤 Tab（验收微调）：全量列表上方，按大类切换过滤（与群聊侧同构）。 */}
          {readyFiles.length > 0 ? (
            <ManagePanelTabs value={effectiveCategory} options={categoryOptions} onChange={setCategory} />
          ) : null}

          <div className="min-h-0 flex-1 p-2">
            {isLoadingList && readyFiles.length === 0 ? (
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
                    const previewable = getPreviewKind(file.displayName) !== 'other';
                    return (
                      <li
                        key={file.resourceId}
                        className="flex items-center gap-1 rounded-lg px-2.5 py-2 transition-colors hover:bg-muted/50"
                      >
                        <div className="flex min-w-0 flex-1 shrink cursor-default items-start justify-start gap-2 rounded-md px-1.5 py-0.5 text-left text-xs text-foreground">
                          <SessionFileTypeIcon name={file.displayName} className="mt-0.5 h-4 w-4 shrink-0" />
                          <div className="min-w-0 flex-1">
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <span className="block truncate">{file.displayName}</span>
                              </TooltipTrigger>
                              <TooltipContent>{file.displayName}</TooltipContent>
                            </Tooltip>
                            {/* 元数据模型统一（2026-09-20 用户拍板）：两行结构与群聊侧同构；
                                单聊文件接口暂不返回上传者/时间，次行仅展示大小，
                                后端补齐字段后本行自动扩充。 */}
                            <span className="mt-1 block truncate text-xs leading-4 text-muted-foreground">
                              {file.sizeBytes ? formatFileSize(file.sizeBytes) : '--'}
                            </span>
                          </div>
                        </div>
                        {/* 操作按钮常显：不再 hover 悬浮切换（原 opacity/pointer-events 切换系
                            Tooltip 闪烁缺陷的历史对策，与群聊侧同因同治后统一为常显）。 */}
                        <div className="flex shrink-0 items-center">
                          <IconButton
                            label={previewable ? '预览文件' : '该文件类型暂不支持预览'}
                            // PR#413 评审跟进 P3（#257104459）：视觉提示精简，读屏名称保留
                            // 文件名——ariaLabel 恢复含文件名的完整可访问名称，读屏用户可区分行。
                            ariaLabel={
                              previewable
                                ? `预览文件 ${file.displayName}`
                                : `预览文件 ${file.displayName}（该文件类型暂不支持预览）`
                            }
                            icon={<Eye className="h-4 w-4" aria-hidden />}
                            size="sm"
                            variant="ghost"
                            aria-disabled={previewable ? undefined : true}
                            onClick={previewable ? () => setSelectedId(file.resourceId) : undefined}
                            className={previewable ? undefined : 'cursor-not-allowed opacity-50'}
                          />
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
      )}
    </aside>
  );
}

export default BotSessionFilesPanel;
