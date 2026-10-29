// 可调节侧栏的尺寸/持久化纯逻辑：从 ResizableWorkspaceSidebar 拆出（TC-G005 文件大小）。
// 宽度钳制、localStorage 读写兜底与 45% 比例上限计算均无 React 依赖，独立可测。
export const WORKSPACE_SIDEBAR_MIN_WIDTH = 280;
export const WORKSPACE_SIDEBAR_DEFAULT_WIDTH = 320;
export const WORKSPACE_SIDEBAR_MAX_WIDTH = 480;
export const WORKSPACE_SIDEBAR_MAX_RATIO = 0.45;
export const WORKSPACE_SIDEBAR_COLLAPSED_WIDTH = 56;
export const WORKSPACE_SIDEBAR_STORAGE_KEY = 'teamclaw:workspace-sidebar-width';
export const WORKSPACE_SIDEBAR_COLLAPSED_STORAGE_KEY = 'teamclaw:workspace-sidebar-collapsed';

export function clampWidth(width: number, minWidth: number, maxWidth: number): number {
  return Math.min(Math.max(Math.round(width), minWidth), maxWidth);
}

export function readStoredWidth(key: string, defaultWidth: number, minWidth: number, maxWidth: number): number {
  if (typeof window === 'undefined') return defaultWidth;
  try {
    const raw = window.localStorage.getItem(key);
    if (raw === null) return defaultWidth;
    const stored = Number(raw);
    if (!Number.isFinite(stored)) return defaultWidth;
    return Math.min(Math.max(Math.round(stored), minWidth), maxWidth);
  } catch {
    return defaultWidth;
  }
}

export function persistWidth(key: string, width: number): void {
  try {
    window.localStorage.setItem(key, String(Math.round(width)));
  } catch {
    // 浏览器禁用存储时仍保留当前会话内调宽能力。
  }
}

export function readStoredCollapsed(): boolean {
  if (typeof window === 'undefined') return false;
  try {
    return window.localStorage.getItem(WORKSPACE_SIDEBAR_COLLAPSED_STORAGE_KEY) === 'true';
  } catch {
    return false;
  }
}

export function persistCollapsed(collapsed: boolean): void {
  try {
    window.localStorage.setItem(WORKSPACE_SIDEBAR_COLLAPSED_STORAGE_KEY, String(collapsed));
  } catch {
    // 浏览器禁用存储时仍保留当前会话内收起能力。
  }
}

export function dynamicMaxWidth(containerWidth: number, minWidth: number, maxWidth: number): number {
  if (containerWidth <= 0) return maxWidth;
  return Math.max(minWidth, Math.min(maxWidth, Math.floor(containerWidth * WORKSPACE_SIDEBAR_MAX_RATIO)));
}
