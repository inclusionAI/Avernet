import { Button } from '@/components/ui/Button';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import type { BotEditorResource } from '@/domain/botEditor';
import { ChevronRight, Copy, Download, File, FilePlus, Folder, FolderOpen, FolderPlus, Loader2, Trash2 } from 'lucide-react';

/** 行首留白（px），dmore 052：chevron 距行左缘 8px */
const ROW_BASE_PADDING = 8;
/** 每层目录缩进步距（px） */
const INDENT_STEP = 20;
/** 文件行补的箭头槽：12px chevron + 8px 间距，使文件 icon 与同级目录 icon 对齐 */
const FILE_ICON_SLOT = 20;

/** 文件类型图标配色：目录 warning、json 紫、markdown 绿、其余中性灰。 */
const resourceIconClassName = (item: BotEditorResource) => {
  if (item.type === 'folder') return 'text-warning';
  if (/\.json$/i.test(item.name)) return 'text-violet-500';
  if (/\.(md|mdx)$/i.test(item.name)) return 'text-emerald-500';
  return 'text-zinc-400';
};

interface ResourcePanelRowProps {
  item: BotEditorResource;
  depth: number;
  /** 目录是否处于展开态（决定旋转箭头与展开图标）。 */
  isOpen: boolean;
  /** 该目录是否正在懒加载（决定箭头位是否转圈）。 */
  dirLoading: boolean;
  /** 行选中态：当前目录或当前预览命中该行。 */
  selected: boolean;
  editable: boolean;
  desktop: boolean;
  onToggleDirectory: (path: string) => void;
  /** 上传目标目录（复用面板隐藏 file input）。 */
  onAddFileTo: (path: string) => void;
  /** 打开「新建子目录」弹窗并预填父路径。 */
  onCreateSubdirectory: (path: string) => void;
  /** 打开预览抽屉（结果回写由面板持有）。 */
  onPreviewFile: (path: string) => void;
  onCopyPath: (path: string) => Promise<void>;
  onDownload: (path: string, type: BotEditorResource['type']) => Promise<void>;
  onDelete: (path: string) => Promise<void>;
}

/** 资源目录单行：祖先导轨 + 行主体（悬停 Tooltip 补全路径）+ 悬停操作簇。 */
export function ResourcePanelRow({
  item,
  depth,
  isOpen,
  dirLoading,
  selected,
  editable,
  desktop,
  onToggleDirectory,
  onAddFileTo,
  onCreateSubdirectory,
  onPreviewFile,
  onCopyPath,
  onDownload,
  onDelete,
}: ResourcePanelRowProps) {
  const isFolder = item.type === 'folder';
  return (
    <div
      data-depth={depth}
      className={`group relative flex h-8 items-center rounded-md pr-1.5 transition-colors ${
        selected ? 'bg-zinc-200 dark:bg-zinc-800' : 'hover:bg-zinc-200/80 dark:hover:bg-zinc-800/70'
      }`}
      style={{
        // 文件行补一个箭头槽，使文件 icon 与同级目录 icon 纵向对齐
        paddingLeft: `${ROW_BASE_PADDING + depth * INDENT_STEP + (isFolder ? 0 : FILE_ICON_SLOT)}px`,
      }}
    >
      {/* 祖先层级导轨：行行相接拼成连续竖线，压在该级 chevron 下方（dmore 052） */}
      {Array.from({ length: depth }, (_, index) => (
        <span
          key={index}
          aria-hidden
          data-rail
          className="absolute inset-y-0 w-px bg-zinc-300 dark:bg-zinc-700"
          style={{ left: `${ROW_BASE_PADDING + index * INDENT_STEP + 4}px` }}
        />
      ))}
      {isFolder ? (
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                className="h-auto min-w-0 flex-1 justify-start rounded-md px-0 py-1 text-left font-normal hover:bg-transparent"
                aria-label={`${isOpen ? '收起' : '展开'}${item.name}`}
                aria-expanded={isOpen}
                onClick={() => onToggleDirectory(item.path)}
                leftIcon={
                  dirLoading ? (
                    <Loader2 className="size-3 shrink-0 animate-spin text-zinc-400" />
                  ) : (
                    <ChevronRight
                      aria-hidden
                      className={`size-3 shrink-0 text-zinc-400 transition-transform duration-150 ${
                        isOpen ? 'rotate-90' : ''
                      }`}
                    />
                  )
                }
              >
                {isOpen ? (
                  <FolderOpen aria-hidden className="size-4 shrink-0 text-warning" />
                ) : (
                  <Folder aria-hidden className="size-4 shrink-0 text-warning" />
                )}
                <span className="min-w-0 flex-1 truncate text-[13px] font-normal text-zinc-600 dark:text-zinc-400">
                  {item.name}
                </span>
                <span className="sr-only">{item.path}</span>
              </Button>
            </TooltipTrigger>
            <TooltipContent className="max-w-80 break-all">{item.path}</TooltipContent>
          </Tooltip>
        </TooltipProvider>
      ) : (
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                className="h-auto min-w-0 flex-1 justify-start rounded-md px-0 py-1 text-left font-normal hover:bg-transparent"
                aria-label={`预览${item.name}`}
                disabled={desktop && (item.size ?? 0) > 1048576}
                // 预览失败（如大文件 413000）已由全局错误通道提示，吞掉传播避免 dev 遮罩报 Unhandled Rejection
                onClick={() => onPreviewFile(item.path)}
              >
                <File aria-hidden className={`size-4 shrink-0 ${resourceIconClassName(item)}`} />
                <span className="min-w-0 flex-1 truncate text-[13px] font-normal text-zinc-600 dark:text-zinc-400">
                  {item.name}
                </span>
                <span className="sr-only">{item.path}</span>
              </Button>
            </TooltipTrigger>
            <TooltipContent className="max-w-80 break-all">{item.path}</TooltipContent>
          </Tooltip>
        </TooltipProvider>
      )}
      <div
        className={`relative z-10 flex shrink-0 items-center gap-0.5 transition-opacity ${
          selected ? 'opacity-100' : 'opacity-0 group-hover:opacity-100 group-focus-within:opacity-100'
        }`}
      >
        {item.type === 'folder' ? (
          <>
            <Button
              variant="ghost"
              size="icon"
              className="size-7 rounded-md text-zinc-500"
              disabled={!editable}
              aria-label={`向${item.name}添加文件`}
              leftIcon={<FilePlus className="size-4" />}
              onClick={() => onAddFileTo(item.path)}
            />
            <Button
              variant="ghost"
              size="icon"
              className="size-7 rounded-md text-zinc-500"
              disabled={!editable}
              aria-label={`在${item.name}中新建子目录`}
              leftIcon={<FolderPlus className="size-4" />}
              onClick={() => onCreateSubdirectory(item.path)}
            />
          </>
        ) : null}
        <Button
          variant="ghost"
          size="icon"
          className="size-7 rounded-md text-zinc-500"
          aria-label={`复制路径${item.name}`}
          leftIcon={<Copy className="size-4" />}
          onClick={() => void onCopyPath(item.path)}
        />
        <Button
          variant="ghost"
          size="icon"
          className="size-7 rounded-md text-zinc-500"
          disabled={desktop && item.type === 'folder'}
          aria-label={`下载${item.type === 'folder' ? '文件夹' : '文件'}${item.name}`}
          leftIcon={<Download className="size-4" />}
          onClick={() => void onDownload(item.path, item.type)}
        />
        <ConfirmDialog
          title="删除资源"
          description={`确认递归删除「${item.path}」？`}
          confirmVariant="destructive"
          onConfirm={() => onDelete(item.path)}
          disabled={!editable}
        >
          <Button
            variant="ghost"
            size="icon"
            className="size-7 rounded-md text-zinc-500 hover:text-destructive"
            aria-label={`删除${item.name}`}
            leftIcon={<Trash2 className="size-4" />}
          />
        </ConfirmDialog>
      </div>
    </div>
  );
}