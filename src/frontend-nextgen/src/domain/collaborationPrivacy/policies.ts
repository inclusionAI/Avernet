import type { FriendApprovalConfig, OrganizationPath, OrganizationSearchEntry, PublicConfig } from './types';

function normalizePath(path: OrganizationPath): OrganizationPath {
  return path.map((segment) => segment.trim()).filter(Boolean);
}

function normalizeDepartmentNos(departmentNos?: string[]): string[] {
  return [...new Set((departmentNos ?? []).map((departmentNo) => departmentNo.trim()).filter(Boolean))].sort(
    (left, right) => left.localeCompare(right),
  );
}

export function normalizeOrganizationPaths(paths: OrganizationPath[]): OrganizationPath[] {
  const normalized = paths.map(normalizePath).filter((path) => path.length > 0);
  const unique = new Map(normalized.map((path) => [path.join('\u0000'), path]));
  return [...unique.entries()].sort(([left], [right]) => left.localeCompare(right, 'zh-CN')).map(([, path]) => path);
}

function normalizeOrganizationEntries(
  entries: OrganizationSearchEntry[] | undefined,
  paths: OrganizationPath[],
): OrganizationSearchEntry[] {
  const pathKeys = new Set(paths.map((path) => path.join('\u0000')));
  const normalized = (entries ?? [])
    .map((entry) => ({
      deptNo: entry.deptNo.trim(),
      path: normalizePath(entry.path),
    }))
    .filter((entry) => entry.deptNo && pathKeys.has(entry.path.join('\u0000')));
  return [...new Map(normalized.map((entry) => [entry.path.join('\u0000'), entry])).values()];
}

export function normalizePublicConfig(config: PublicConfig): PublicConfig {
  const organizationPaths = config.scope === 'restricted' ? normalizeOrganizationPaths(config.organizationPaths) : [];
  const organizationEntries = normalizeOrganizationEntries(config.organizationEntries, organizationPaths);
  return {
    scope: config.scope,
    organizationPaths,
    ...(organizationEntries.length > 0 ? { organizationEntries } : {}),
  };
}

export function publicConfigsEqual(left: PublicConfig, right: PublicConfig): boolean {
  const normalizedLeft = normalizePublicConfig(left);
  const normalizedRight = normalizePublicConfig(right);
  return (
    normalizedLeft.scope === normalizedRight.scope &&
    normalizedLeft.organizationPaths.map((path) => path.join('\u0000')).join('\u0001') ===
      normalizedRight.organizationPaths.map((path) => path.join('\u0000')).join('\u0001')
  );
}

export function validatePublicConfig(config: PublicConfig): PublicConfig {
  const normalized = normalizePublicConfig(config);
  if (normalized.scope === 'restricted' && normalized.organizationPaths.length === 0) {
    throw new Error('至少选择一个公开组织范围');
  }
  return normalized;
}

export function normalizeFriendApproval(config: FriendApprovalConfig): FriendApprovalConfig {
  const exemptOrganizationPaths =
    config.mode === 'partial_exempt' ? normalizeOrganizationPaths(config.exemptOrganizationPaths) : [];
  const exemptOrganizationEntries = normalizeOrganizationEntries(
    config.mode === 'partial_exempt' ? config.exemptOrganizationEntries : [],
    exemptOrganizationPaths,
  );
  return {
    mode: config.mode,
    exemptOrganizationPaths,
    exemptDepartmentNos: config.mode === 'partial_exempt' ? normalizeDepartmentNos(config.exemptDepartmentNos) : [],
    ...(exemptOrganizationEntries.length > 0 ? { exemptOrganizationEntries } : {}),
  };
}

export function validateFriendApproval(config: FriendApprovalConfig): FriendApprovalConfig {
  const normalized = normalizeFriendApproval(config);
  if (
    normalized.mode === 'partial_exempt' &&
    normalized.exemptOrganizationPaths.length === 0 &&
    (normalized.exemptDepartmentNos ?? []).length === 0
  ) {
    throw new Error('至少选择一个免审批组织范围');
  }
  return normalized;
}

export function friendApprovalConfigsEqual(left: FriendApprovalConfig, right: FriendApprovalConfig): boolean {
  const normalizedLeft = normalizeFriendApproval(left);
  const normalizedRight = normalizeFriendApproval(right);
  return (
    normalizedLeft.mode === normalizedRight.mode &&
    normalizedLeft.exemptOrganizationPaths.map((path) => path.join('\u0000')).join('\u0001') ===
      normalizedRight.exemptOrganizationPaths.map((path) => path.join('\u0000')).join('\u0001') &&
    (normalizedLeft.exemptDepartmentNos ?? []).join('\u0000') ===
      (normalizedRight.exemptDepartmentNos ?? []).join('\u0000')
  );
}
