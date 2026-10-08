import type { BotEditorResource } from '@/domain/botEditor';

export function formatResourceBytes(size: number) {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

export function buildVisibleResourceTree(resources: BotEditorResource[], expanded: string[]) {
  const children = new Map<string, BotEditorResource[]>();
  resources.forEach((item) => children.set(item.parentPath, [...(children.get(item.parentPath) ?? []), item]));
  const result: Array<{ item: BotEditorResource; depth: number }> = [];
  const visited = new Set<string>();
  function append(parentPath: string, depth: number) {
    const ordered = [...(children.get(parentPath) ?? [])].sort((left, right) => {
      if (left.type === right.type) return 0;
      return left.type === 'folder' ? -1 : 1;
    });
    ordered.forEach((item) => {
      if (visited.has(item.path)) return;
      visited.add(item.path);
      result.push({ item, depth });
      if (item.type === 'folder' && expanded.includes(item.path)) append(item.path, depth + 1);
    });
  }
  append('', 0);
  return result;
}
