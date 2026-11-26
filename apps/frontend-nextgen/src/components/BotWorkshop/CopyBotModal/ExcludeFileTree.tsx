import { Button } from '@/components/ui/Button';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/Tooltip';
import type { BotEditorResource } from '@/domain/botEditor';
import type { ExcludeCheckState } from '@/services/botWorkshop/excludeTree';
import { cn } from '@/utils/cn';
import { ChevronRight, File, Folder, FolderOpen, Loader2, Minus, X } from 'lucide-react';
import type React from 'react';

interface ExcludeFileTreeProps {
  visibleTree: Array<{ item: BotEditorResource; depth: number }>;
  expanded: string[];
  pendingDirs: Set<string>;
  loading: boolean;
  error?: string;
  /** 返回该 path 的复选框状态(checked=已排除 / indeterminate / unchecked)。 */
  checkState: (path: string, type: 'file' | 'folder') => ExcludeCheckState;
  /** 受保护(系统级、不可排除)路径判定;命中即整子树置灰不可勾选。 */
  isProtected?: (path: string) => boolean;
  onExpand: (path: string) => void;
  onCollapse: (path: string) => void;
  onToggle: (path: string, type: 'file' | 'folder') => void;
}

const INDENT_STEP = 20;

/** 受保护(系统必需)路径的 hover 提示文案。 */
const PROTECTED_HINT = '系统必需目录 / 文件,不支持配置为排除';

/** 受保护条目 hover 提示包裹:用 Tooltip 取代 title 属性(TC-G006)。 */
const ProtectedHint: React.FC<{ children: React.ReactElement }> = ({ children }) => (
  <TooltipProvider delayDuration={200}>
    <Tooltip>
      <TooltipTrigger asChild>{children}</TooltipTrigger>
      <TooltipContent side="top" className="whitespace-normal break-words text-left leading-5">
        {PROTECTED_HINT}
      </TooltipContent>
    </Tooltip>
  </TooltipProvider>
);

export const ExcludeFileTree: React.FC<ExcludeFileTreeProps> = ({
  visibleTree,
  expanded,
  pendingDirs,
  loading,
  error,
  checkState,
  isProtected,
  onExpand,
  onCollapse,
  onToggle,
}) => {
  if (loading && visibleTree.length === 0) {
    return <p className="px-1 py-3 text-xs text-muted-foreground">正在加载源 Bot 资源…</p>;
  }
  if (error) {
    return (
      <p role="alert" className="px-1 py-3 text-xs text-destructive">
        {error}
      </p>
    );
  }
  if (visibleTree.length === 0) {
    return (
      <p className="px-1 py-3 text-xs text-muted-foreground">源 Bot 暂无可复制的资源文件。</p>
    );
  }
  return (
    <ul role="tree" aria-label="源 Bot 资源排除树" className="max-h-[320px] overflow-y-auto py-1 text-xs">
      {visibleTree.map(({ item, depth }) => {
        const isFolder = item.type === 'folder';
        const isOpen = expanded.includes(item.path);
        const pending = pendingDirs.has(item.path);
        const state = checkState(item.path, item.type);
        const checked = state === 'checked';
        const indeterminate = state === 'indeterminate';
        const locked = isProtected?.(item.path) ?? false;
        return (
          <li
            key={item.path}
            role="treeitem"
            aria-checked={checked ? 'true' : indeterminate ? 'mixed' : 'false'}
            aria-expanded={isFolder ? isOpen : undefined}
            aria-disabled={locked || undefined}
            style={{ paddingLeft: depth * INDENT_STEP }}
            className="flex items-center gap-1.5 py-1 pr-2"
          >
            {isFolder ? (
              <Button
                type="button"
                variant="ghost"
                size="icon"
                aria-label={isOpen ? `折叠 ${item.name}` : `展开 ${item.name}`}
                onClick={() => (isOpen ? onCollapse(item.path) : onExpand(item.path))}
                className="size-4 shrink-0 text-muted-foreground hover:text-foreground"
              >
                {pending ? (
                  <Loader2 className="size-3 animate-spin" aria-hidden />
                ) : (
                  <ChevronRight className={cn('size-3.5 transition-transform', isOpen && 'rotate-90')} aria-hidden />
                )}
              </Button>
            ) : (
              <span className="size-4 shrink-0" aria-hidden />
            )}
            <Button
              type="button"
              variant="ghost"
              size="icon"
              role="checkbox"
              aria-checked={checked ? 'true' : indeterminate ? 'mixed' : 'false'}
              aria-disabled={locked || undefined}
              aria-label={locked ? `系统必需,不可排除 ${item.name}` : `${checked ? '取消排除' : '排除'} ${item.name}`}
              onClick={() => {
                if (locked) return;
                onToggle(item.path, item.type);
              }}
              disabled={locked}
              tabIndex={locked ? -1 : 0}
              className={cn(
                'group/exclude !size-4 !rounded-[5px] p-0',
                locked
                  ? '!border-input !bg-background text-muted-foreground/40 cursor-not-allowed'
                  : checked
                    ? '!border-destructive !bg-destructive/10 text-destructive hover:!bg-destructive/10 hover:!text-destructive'
                    : '!border-input !bg-background text-muted-foreground hover:!border-destructive hover:!bg-background hover:!text-destructive',
              )}
            >
              {checked ? (
                <X className="size-3" aria-hidden />
              ) : indeterminate ? (
                <Minus className="size-2.5" aria-hidden />
              ) : (
                <X
                  className="size-3 opacity-0 transition-opacity group-hover/exclude:opacity-50"
                  aria-hidden
                />
              )}
            </Button>
            {isFolder ? (
              isOpen ? (
                <FolderOpen className="size-4 shrink-0 text-primary/70" aria-hidden />
              ) : (
                <Folder className="size-4 shrink-0 text-primary/70" aria-hidden />
              )
            ) : (
              <File className="size-4 shrink-0 text-muted-foreground" aria-hidden />
            )}
            {locked ? (
              <ProtectedHint>
                <span className="min-w-0 truncate text-muted-foreground">{item.name}</span>
              </ProtectedHint>
            ) : (
              <span className="min-w-0 truncate text-foreground">{item.name}</span>
            )}
            {locked ? (
              <ProtectedHint>
                <span className="ml-auto shrink-0 whitespace-nowrap rounded-full border border-border bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
                  {isFolder ? '系统目录' : '系统文件'}
                </span>
              </ProtectedHint>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
};
