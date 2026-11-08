import type { BotRuntimeStage } from '@/domain/botWorkshop';
import { Button, Empty, IconButton, Skeleton } from '@/components/ui';
import { TooltipProvider } from '@/components/ui/Tooltip';
import { useBotSessionFilePreview } from '@/pages/Workspace/hooks/useBotSessionFilePreview';
import type { BotSessionFileView } from '@/services/workspace/botSessionFileService';
import { cn } from '@/utils/cn';
import { FileText, Upload, X } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { PreviewPane } from '../GroupChatPane/SessionFilesPreviewPane';
import { BotSessionFilesRow } from './BotSessionFilesRow';
import {
  getSessionFileCategory,
  SESSION_FILE_CATEGORY_LABELS,
  type SessionFileCategory,
} from '../SessionFileTypeIcon';

export interface BotSessionFilesPanelProps {
  readyFiles: BotSessionFileView[];
  isLoadingList: boolean;
  botId?: string;
  sessionId?: string;
  userId?: string;
  ownerId?: string;
  stage?: BotRuntimeStage;
  onClose: () => void;
  onUploadClick: () => void;
  onOpen: () => void;
  onDelete: (file: BotSessionFileView) => void;
  onDownload: (file: BotSessionFileView) => void;
  onReference: (file: BotSessionFileView) => void;
}

/**
 * 单聊「资源管理」右侧副屏（dmore artboard-008 对齐）：
 * 48px 标题行 + 32px 筛选条（下划线 Tabs + 上传图标钮）+ 61px 紧排文件行。
 * 容器由父级提供（ResizableWorkspaceSidebar 语义）；内部为下钻式两视图：
 * 列表视图为主，点击文件行本体或行内「预览」按钮切到预览视图（带「返回文件列表」）；
 * 预览视图头部支持「引用到输入框」与「下载」。
 * 行/预览内核自 BotSessionFilesModal 迁移，数据 Hook 与行为不变
 * （含单聊特有的「引用到输入框」行操作）。
 */
export function BotSessionFilesPanel({
  readyFiles,
  isLoadingList,
  botId,
  sessionId,
  userId,
  ownerId,
  stage,
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

  // 类型筛选 Tab（对齐设计稿「全部/文档/代码」形态）：不做设计稿外的计数装饰，
  // 按实际存在的大类动态生成；单聊无 mimeType，按文件名判定（与其预览 Hook 口径一致）。
  const categoryOptions = useMemo(() => {
    const order = ['media', 'document', 'code', 'sheet', 'other'] as const;
    const present = new Set<SessionFileCategory>();
    for (const file of readyFiles) present.add(getSessionFileCategory(file.displayName));
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
    stage,
  });

  const previewFile: { name: string; size: number } | null = selectedFile
    ? { name: selectedFile.displayName, size: selectedFile.sizeBytes ?? 0 }
    : null;

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
            file={previewFile}
            filesStateEmpty={false}
            preview={preview}
            onBack={() => setSelectedId(null)}
            onDownload={() => {
              if (selectedFile) onDownload(selectedFile);
            }}
            onReference={() => {
              if (selectedFile) onReference(selectedFile);
            }}
          />
        </div>
      ) : (
        <>
          {/* 筛选条（设计稿：32px 高、发丝线下边、左起下划线 Tabs、右端 24px 边框上传钮）。 */}
          <div className="flex h-[33px] shrink-0 items-center justify-between border-b-[0.5px] border-zinc-300/40 pl-5 pr-5">
            {readyFiles.length > 0 ? (
              /* 间距修正：原 w-6 恰好等于二字标签宽度，标签之间零间距；
                  改按设计稿 40px tab 节奏（8px 左右内距，紧排无额外 gap），
                  下划线随文字收敛为 inset-x-2。 */
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
              onClick={onUploadClick}
              className="h-6 w-6 shrink-0 rounded-lg border-zinc-200 bg-background p-0 text-muted-foreground hover:bg-background hover:text-foreground"
            >
              <Upload className="h-3.5 w-3.5" aria-hidden />
            </Button>
          </div>

          <div className="app-scrollbar flex min-h-0 flex-1 flex-col overflow-y-auto">
            {/* 文件列表（设计稿：61px 紧排行，图标 20px + 文件名 13px + 元数据 11px；首行顶距 8px）。 */}
            <div className="min-h-0 flex-1 pt-2">
              {isLoadingList && readyFiles.length === 0 ? (
                /* 加载骨架与真实文件行同构：61px 行高 + 20px 图标位 + 文件名/元数据
                    双条描线，宽度按行错落模拟不同文件名；避免整版灰色实心块，
                    且加载完成瞬间行位零跳动。 */
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
                  description="点击右上「上传文件」，或引用本会话已有文件。"
                />
              ) : (
                <TooltipProvider>
                  <ul className="m-0 list-none p-0">
                    {visibleFiles.map((file) => (
                      <BotSessionFilesRow
                        key={file.resourceId}
                        file={file}
                        onOpenPreview={() => setSelectedId(file.resourceId)}
                        onReference={onReference}
                        onDownload={onDownload}
                        onDelete={onDelete}
                      />
                    ))}
                  </ul>
                </TooltipProvider>
              )}
            </div>
          </div>
        </>
      )}
    </aside>
  );
}

export default BotSessionFilesPanel;