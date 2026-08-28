import type { FriendApprovalConfig, OrganizationPath, PublicConfig } from './types';

function normalizePath(path: OrganizationPath): OrganizationPath {
  return path.map((segment) => segment.trim()).filter(Boolean);
}

export function normalizeOrganizationPaths(paths: OrganizationPath[]): OrganizationPath[] {
  const normalized = paths.map(normalizePath).filter((path) => path.length > 0);
  const unique = new Map(normalized.map((path) => [path.join('\u0000'), path]));
  return [...unique.entries()].sort(([left], [right]) => left.localeCompare(right, 'zh-CN')).map(([, path]) => path);
}

export function normalizePublicConfig(config: PublicConfig): PublicConfig {
  return {
    scope: config.scope,
    organizationPaths: config.scope === 'restricted' ? normalizeOrganizationPaths(config.organizationPaths) : [],
  };
}

export function publicConfigsEqual(left: PublicConfig, right: PublicConfig): boolean {
  const normalizedLeft = normalizePublicConfig(left);
  const normalizedRight = normalizePublicConfig(right);
  return normalizedLeft.scope === normalizedRight.scope &&
    normalizedLeft.organizationPaths.map((path) => path.join('\u0000')).join('\u0001') ===
      normalizedRight.organizationPaths.map((path) => path.join('\u0000')).join('\u0001');
}

export function validatePublicConfig(config: PublicConfig): PublicConfig {
  const normalized = normalizePublicConfig(config);
  if (normalized.scope === 'restricted' && normalized.organizationPaths.length === 0) {
    throw new Error('至少选择一个公开组织范围');
  }
  return normalized;
}

export function normalizeFriendApproval(config: FriendApprovalConfig): FriendApprovalConfig {
  return {
    mode: config.mode,
    exemptOrganizationPaths:
      config.mode === 'partial_exempt' ? normalizeOrganizationPaths(config.exemptOrganizationPaths) : [],
  };
}

export function validateFriendApproval(config: FriendApprovalConfig): FriendApprovalConfig {
  const normalized = normalizeFriendApproval(config);
  if (normalized.mode === 'partial_exempt' && normalized.exemptOrganizationPaths.length === 0) {
    throw new Error('至少选择一个免审批组织范围');
  }
  return normalized;
}

export function friendApprovalConfigsEqual(left: FriendApprovalConfig, right: FriendApprovalConfig): boolean {
  const normalizedLeft = normalizeFriendApproval(left);
  const normalizedRight = normalizeFriendApproval(right);
  return normalizedLeft.mode === normalizedRight.mode &&
    normalizedLeft.exemptOrganizationPaths.map((path) => path.join('\u0000')).join('\u0001') ===
      normalizedRight.exemptOrganizationPaths.map((path) => path.join('\u0000')).join('\u0001');
}
