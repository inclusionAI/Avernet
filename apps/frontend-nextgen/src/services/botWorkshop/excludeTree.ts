import type { BotEditorResource } from '@/domain/botEditor';

/** 规范化目录前缀:确保以 '/' 结尾,子路径严格以 prefix 开头才视为后代。 */
function withTrailingSlash(path: string): string {
  return path.endsWith('/') ? path : `${path}/`;
}

/** 收集某目录(含子目录与文件)的所有后代资源 path;folder 自身不计入。 */
export function collectDescendantPaths(resources: BotEditorResource[], folderPath: string): string[] {
  const prefix = withTrailingSlash(folderPath);
  return resources
    .filter((item) => item.path !== folderPath && item.path.startsWith(prefix))
    .map((item) => item.path);
}

export type ExcludeCheckState = 'checked' | 'unchecked' | 'indeterminate';

/**
 * 对单条资源计算其复选框状态。
 * 'checked' = 已排除;'unchecked' = 未排除;'indeterminate' = 文件夹子树部分排除。
 */
export function excludeCheckState(
  resources: BotEditorResource[],
  excluded: ReadonlySet<string>,
  path: string,
  type: 'file' | 'folder',
): ExcludeCheckState {
  if (excluded.has(path)) return 'checked';
  if (type === 'file') return 'unchecked';
  const descendants = collectDescendantPaths(resources, path);
  if (descendants.length === 0) return 'unchecked';
  const excludedCount = descendants.reduce((count, p) => (excluded.has(p) ? count + 1 : count), 0);
  if (excludedCount === 0) return 'unchecked';
  if (excludedCount === descendants.length) return 'checked';
  return 'indeterminate';
}

/**
 * 切换排除状态(单一事实源):
 * - 文件:在 excluded 中加入/移除自身。
 * - 文件夹:当前整子树已排除 → 恢复(移除自身并保留后代由用户逐条勾选);
 *   否则 → 整目录排除(加入自身,并清空所有后代 path,折叠为单一条目避免冗余)。
 */
export function toggleExclude(
  resources: BotEditorResource[],
  excluded: ReadonlySet<string>,
  path: string,
  type: 'file' | 'folder',
): Set<string> {
  const next = new Set(excluded);
  if (type === 'file') {
    if (next.has(path)) next.delete(path);
    else next.add(path);
    return next;
  }
  const descendants = collectDescendantPaths(resources, path);
  const state = excludeCheckState(resources, excluded, path, type);
  if (state === 'checked') {
    next.delete(path);
    descendants.forEach((p) => next.delete(p));
  } else {
    next.add(path);
    descendants.forEach((p) => next.delete(p));
  }
  return next;
}
