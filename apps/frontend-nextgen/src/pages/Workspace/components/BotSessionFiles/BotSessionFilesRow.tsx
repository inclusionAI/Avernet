import { Button, ConfirmDialog, IconButton } from '@/components/ui';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/Tooltip';
import type { BotSessionFileView } from '@/services/workspace/botSessionFileService';
import { formatFileSize, getPreviewKind } from '@/services/workspace/sessionFileUtils';
import { cn } from '@/utils/cn';
import { Download, Eye, Reply, Trash2 } from 'lucide-react';
import { SessionFileTypeIcon } from '../SessionFileTypeIcon';

interface BotSessionFilesRowProps {
  file: BotSessionFileView;
  /** 下钻动作（行本体与「预览」钮共用）：打开该文件的预览视图。 */
  onOpenPreview: (file: BotSessionFileView) => void;
  onReference: (file: BotSessionFileView) => void;
  onDownload: (file: BotSessionFileView) => void;
  onDelete: (file: BotSessionFileView) => void;
}

/**
 * 61px 紧排文件行（设计稿：图标 20px + 文件名 13px + 元数据 11px；悬停行底 zinc-100），
 * 行操作（预览/引用/下载/删除）悬停时出现在右端。
 * 下钻入口（2026-10-12 验收拍板，替代验收微调四轮的「行本体纯展示」）：
 * 可预览文件点击行本体直接下钻，行内「预览」图标按钮保留（可见入口 + 禁用语义承载）。
 * 不可预览类型（非图片/PDF/文本白名单）：行本体仍为纯展示，按钮禁用并提示原因。
 * 用 aria-disabled 而非原生 disabled——禁用态仍需 hover 出 Tooltip，
 * 原生 disabled button 不派发 pointer 事件会让提示失效。
 */
export function BotSessionFilesRow({ file, onOpenPreview, onReference, onDownload, onDelete }: BotSessionFilesRowProps) {
  const previewable = getPreviewKind(file.displayName) !== 'other';
  const rowContent = (
    <>
      <SessionFileTypeIcon name={file.displayName} className="h-5 w-5 shrink-0" />
      <div className="min-w-0 flex-1">
        <Tooltip>
          <TooltipTrigger asChild>
            <span className="block truncate text-[13px] leading-[21px] text-foreground">{file.displayName}</span>
          </TooltipTrigger>
          <TooltipContent>{file.displayName}</TooltipContent>
        </Tooltip>
        {/* 元数据模型统一（2026-09-20 用户拍板）：两行结构与群聊侧同构；
            单聊文件接口暂不返回上传者/时间，次行仅展示大小，
            后端补齐字段后本行自动扩充（设计稿为「风太 · 09/15 14:22 · 7.0KB」）。 */}
        <span className="mt-0.5 block truncate text-[11px] leading-[18px] text-muted-foreground">
          {file.sizeBytes ? formatFileSize(file.sizeBytes) : '--'}
        </span>
      </div>
    </>
  );
  return (
    <li className="group flex h-[61px] items-center rounded-lg px-5 transition-colors hover:bg-zinc-100">
      {previewable ? (
        <Button
          variant="ghost"
          onClick={() => onOpenPreview(file)}
          className="m-0 flex h-auto min-w-0 flex-1 shrink cursor-pointer items-center justify-start gap-1 rounded-none border-0 bg-transparent p-0 font-normal text-left hover:bg-transparent"
        >
          {rowContent}
        </Button>
      ) : (
        <div className="flex min-w-0 flex-1 shrink cursor-default items-center gap-1 text-left">{rowContent}</div>
      )}
      {/* 行操作（设计稿：悬停行底 zinc-100 时出现在右端）：常驻挂载
          opacity 切换可见（hover/focus-within），不做布局位移；Tooltip
          闪烁的历史对策仅限 pointer-events 切换，此处不受影响。 */}
      <div className="flex shrink-0 items-center opacity-0 transition-opacity group-focus-within:opacity-100 group-hover:opacity-100">
        <IconButton
          label={previewable ? '预览文件' : '该文件类型暂不支持预览'}
          // PR#413 评审跟进 P3（#257104459）：视觉提示精简，读屏名称保留
          // 文件名——ariaLabel 恢复含文件名的完整可访问名称，读屏用户可区分行。
          ariaLabel={
            previewable
              ? `预览文件 ${file.displayName}`
              : `预览文件 ${file.displayName}（该文件类型暂不支持预览）`
          }
          icon={<Eye className="h-3.5 w-3.5" aria-hidden />}
          size="sm"
          variant="ghost"
          aria-disabled={previewable ? undefined : true}
          onClick={previewable ? () => onOpenPreview(file) : undefined}
          className={cn(
            'h-5 w-5 text-muted-foreground hover:text-foreground',
            previewable ? undefined : 'cursor-not-allowed opacity-50',
          )}
        />
        <IconButton
          label="引用到输入框"
          icon={<Reply className="h-3.5 w-3.5" aria-hidden />}
          size="sm"
          variant="ghost"
          onClick={() => onReference(file)}
          className="h-5 w-5 text-muted-foreground hover:text-foreground"
        />
        <IconButton
          label="下载文件"
          icon={<Download className="h-3.5 w-3.5" aria-hidden />}
          size="sm"
          variant="ghost"
          onClick={() => onDownload(file)}
          className="h-5 w-5 text-muted-foreground hover:text-foreground"
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
            icon={<Trash2 className="h-3.5 w-3.5" aria-hidden />}
            size="sm"
            variant="ghost"
            className="h-5 w-5 text-muted-foreground hover:text-foreground"
          />
        </ConfirmDialog>
      </div>
    </li>
  );
}