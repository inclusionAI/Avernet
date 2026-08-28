import type { OrganizationPath } from '@/domain/collaborationPrivacy/types';

export interface OrganizationNode {
  label: string;
  path: OrganizationPath;
  hasChildren: boolean;
}

export const organizationPathKey = (path: OrganizationPath) => path.join('\u0000');

export function buildOrganizationColumns(options: OrganizationPath[], activePath: OrganizationPath) {
  const maxDepth = Math.max(0, ...options.map((path) => path.length));
  const columns: OrganizationNode[][] = [];
  for (let depth = 0; depth < maxDepth; depth += 1) {
    if (depth > 0 && !activePath[depth - 1]) break;
    const prefix = activePath.slice(0, depth);
    const labels = [...new Set(options
      .filter((path) => prefix.every((segment, index) => path[index] === segment))
      .map((path) => path[depth])
      .filter(Boolean))];
    if (labels.length === 0) break;
    columns.push(labels.map((label) => {
      const path = [...prefix, label];
      return {
        label,
        path,
        hasChildren: options.some((option) => option.length > path.length && path.every((segment, index) => option[index] === segment)),
      };
    }));
  }
  return columns;
}

export function toggleOrganizationPath(selected: OrganizationPath[], path: OrganizationPath) {
  const key = organizationPathKey(path);
  return selected.some((item) => organizationPathKey(item) === key)
    ? selected.filter((item) => organizationPathKey(item) !== key)
    : [...selected, path];
}
