import type { BotEditorResource } from '@/domain/botEditor';
import { botEditorService } from '@/services/botWorkshop/botEditorService';
import {
  excludeCheckState,
  toggleExclude,
  type ExcludeCheckState,
} from '@/services/botWorkshop/excludeTree';
import { buildVisibleResourceTree } from '@/services/botWorkshop/resourceTree';
import { useCallback, useEffect, useMemo, useState } from 'react';

export interface UseExcludeFileTreeResult {
  resources: BotEditorResource[];
  visibleTree: Array<{ item: BotEditorResource; depth: number }>;
  expanded: string[];
  excluded: Set<string>;
  /** 正在拉取子目录的目录 path 集合;UI 用来给 chevron 显示 spin。 */
  pendingDirs: Set<string>;
  loading: boolean;
  error?: string;
  /** 展开目录;若子目录尚未拉取,先 GET /openapi/v1/bots/{src}/resources?path=<dir> 再合并。 */
  expand: (path: string) => Promise<void>;
  /** 折叠目录(不卸载已加载条目,仅收起展示)。 */
  collapse: (path: string) => void;
  toggle: (path: string, type: 'file' | 'folder') => void;
  checkState: (path: string, type: 'file' | 'folder') => ExcludeCheckState;
}

/**
 * 复制 Bot 第一步「排除文件」的数据层:对源 Bot 复用 detail 页同一套
 * `botEditorController.listResources` 逐目录懒加载,产出 flat `BotEditorResource[]` +
 * `excluded` Set。excludes 语义与后端契约一致:含目录则整子树排除,空集 = 全量复制。
 *
 * 不复用 `useBotEditor` 是因为它的 fetch 曲线绑定当前 Bot 编辑上下文(ownerId/deployment);
 * 复制场景下源 Bot 是「我可见但归属他人」的只读视图,身份走 `sourceOwnerId`,
 * 且不应触发 `useBotEditor` 的写入器与协作者策略。
 */
export function useExcludeFileTree(
  sourceBotId: string | undefined,
  sourceOwnerId?: string,
): UseExcludeFileTreeResult {
  const [resources, setResources] = useState<BotEditorResource[]>([]);
  const [expanded, setExpanded] = useState<string[]>([]);
  const [excluded, setExcluded] = useState<Set<string>>(() => new Set());
  const [pendingDirs, setPendingDirs] = useState<Set<string>>(() => new Set());
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>();

  useEffect(() => {
    let active = true;
    setResources([]);
    setExpanded([]);
    setExcluded(new Set());
    setError(undefined);
    if (!sourceBotId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    void (async () => {
      try {
        const items = await botEditorService.listResources(sourceBotId, '', sourceOwnerId);
        if (!active) return;
        setResources(items);
      } catch (loadError) {
        if (!active) return;
        setError(loadError instanceof Error ? loadError.message : '源 Bot 资源加载失败');
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => {
      active = false;
    };
  }, [sourceBotId, sourceOwnerId]);

  const expand = useCallback(
    async (path: string) => {
      if (!sourceBotId) return;
      setExpanded((current) => (current.includes(path) ? current : [...current, path]));
      // 判断该目录是否已加载:存在任意以该 path 为 parentPath 的条目。root 用 '' 标识。
      const loaded = resources.some((item) => item.parentPath === path);
      if (loaded) return;
      setPendingDirs((current) => {
        const next = new Set(current);
        next.add(path);
        return next;
      });
      try {
        const children = await botEditorService.listResources(sourceBotId, path, sourceOwnerId);
        setResources((current) => {
          const seen = new Set(current.map((item) => item.path));
          const merged = [...current];
          children.forEach((child) => {
            if (!seen.has(child.path)) merged.push(child);
          });
          return merged;
        });
      } catch (loadError) {
        setError(loadError instanceof Error ? loadError.message : '子目录加载失败');
      } finally {
        setPendingDirs((current) => {
          const next = new Set(current);
          next.delete(path);
          return next;
        });
      }
    },
    [sourceBotId, sourceOwnerId, resources],
  );

  const collapse = useCallback((path: string) => {
    setExpanded((current) => current.filter((item) => item !== path));
  }, []);

  const toggle = useCallback(
    (path: string, type: 'file' | 'folder') => {
      setExcluded((current) => toggleExclude(resources, current, path, type));
    },
    [resources],
  );

  const checkState = useCallback(
    (path: string, type: 'file' | 'folder') => excludeCheckState(resources, excluded, path, type),
    [resources, excluded],
  );

  const visibleTree = useMemo(
    () => buildVisibleResourceTree(resources, expanded),
    [resources, expanded],
  );

  return {
    resources,
    visibleTree,
    expanded,
    excluded,
    pendingDirs,
    loading,
    error,
    expand,
    collapse,
    toggle,
    checkState,
  };
}
