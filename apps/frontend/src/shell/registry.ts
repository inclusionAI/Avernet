/**
 * 模块注册表 → 路由 / 菜单。纯函数，由 ModuleManifest 列表生成。
 * 关闭（enabled === false）或未注册的模块，路由不出、菜单不显示。
 */
import type { MenuItem, ModuleManifest, RouteManifest } from './types';

const isOn = (m: ModuleManifest): boolean => m.enabled !== false;

/** 收集所有启用且声明了 route 的模块的路由配置。 */
export function buildRoutes(modules: ModuleManifest[]): RouteManifest[] {
  return modules.filter((m) => isOn(m) && m.route).map((m) => m.route!);
}

/** 收集所有启用且声明了 menu 的模块菜单项，按 order 升序。 */
export function buildMenus(modules: ModuleManifest[]): MenuItem[] {
  return modules
    .filter((m) => isOn(m) && m.menu)
    .map((m) => ({ id: m.id, ...m.menu! }))
    .sort((a, b) => a.order - b.order);
}
