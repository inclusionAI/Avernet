import { Button } from '@/components/ui/Button';
import { Checkbox } from '@/components/ui/Checkbox';
import type { BotEditorResource } from '@/domain/botEditor';
import type { ExcludeCheckState } from '@/services/botWorkshop/excludeTree';
import { cn } from '@/utils/cn';
import { ChevronRight, File, Folder, FolderOpen, Loader2 } from 'lucide-react';
import type React from 'react';

interface ExcludeFileTreeProps {
  visibleTree: Array<{ item: BotEditorResource; depth: number }>;
  expanded: string[];
  pendingDirs: Set<string>;
  loading: boolean;
  error?: string;
  /** 返回该 path 的复选框状态(checked=已排除 / indeterminate / unchecked)。 */
  checkState: (path: string, type: 'file' | 'folder') => ExcludeCheckState;
  onExpand: (path: string) => void;
  onCollapse: (path: string) => void;
  onToggle: (path: string, type: 'file' | 'folder') => void;
}

const INDENT_STEP = 20;

export const ExcludeFileTree: React.FC<ExcludeFileTreeProps> = ({
  visibleTree,
  expanded,
  pendingDirs,
  loading,
  error,
  checkState,
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
        return (
          <li
            key={item.path}
            role="treeitem"
            aria-checked={checked ? 'true' : indeterminate ? 'mixed' : 'false'}
            aria-expanded={isFolder ? isOpen : undefined}
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
            <Checkbox
              checked={checked}
              indeterminate={indeterminate}
              aria-label={`${checked ? '取消排除' : '排除'} ${item.name}`}
              onCheckedChange={() => onToggle(item.path, item.type)}
            />
            {isFolder ? (
              isOpen ? (
                <FolderOpen className="size-4 shrink-0 text-primary/70" aria-hidden />
              ) : (
                <Folder className="size-4 shrink-0 text-primary/70" aria-hidden />
              )
            ) : (
              <File className="size-4 shrink-0 text-muted-foreground" aria-hidden />
            )}
            <span className="min-w-0 truncate text-foreground">{item.name}</span>
          </li>
        );
      })}
    </ul>
  );
};
