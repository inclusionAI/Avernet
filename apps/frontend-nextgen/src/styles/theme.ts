/**
 * 暗色模式切换工具
 *
 * 暗色模式默认关闭，仅在用户主动切换时启用。
 * 偏好持久化到 localStorage 的 `theme` 字段。
 *
 * 用法：
 *   import { toggleDark, isDark, setDark } from '@/styles/theme';
 *   toggleDark();       // 切换
 *   setDark(true);      // 显式设置为暗色
 *   isDark();           // 判断当前是否暗色
 */

const STORAGE_KEY = 'theme';

type ThemeMode = 'light' | 'dark';

/**
 * 判断当前是否为暗色模式
 */
export function isDark(): boolean {
  if (typeof document === 'undefined') return false;
  return document.documentElement.classList.contains('dark');
}

/**
 * 显式设置暗色或亮色模式
 */
export function setDark(dark: boolean): void {
  if (typeof document === 'undefined') return;
  const root = document.documentElement;
  if (dark) {
    root.classList.add('dark');
  } else {
    root.classList.remove('dark');
  }
  try {
    localStorage.setItem(STORAGE_KEY, dark ? 'dark' : 'light');
  } catch {
    // localStorage 不可用时静默失败
  }
}

/**
 * 在亮色和暗色之间切换
 */
export function toggleDark(): boolean {
  const next = !isDark();
  setDark(next);
  return next;
}

/**
 * 获取当前主题模式
 */
export function getTheme(): ThemeMode {
  return isDark() ? 'dark' : 'light';
}

/**
 * 初始化主题：从 localStorage 读取用户偏好
 * 默认为亮色（无记录或不支持 localStorage 时）
 *
 * 建议在应用入口（app.tsx 的 getInitialState）中调用
 */
export function initTheme(): void {
  if (typeof window === 'undefined') return;
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === 'dark') {
      document.documentElement.classList.add('dark');
    }
    // stored === 'light' 或无记录时不添加 .dark，保持默认亮色
  } catch {
    // localStorage 不可用时保持默认亮色
  }
}
