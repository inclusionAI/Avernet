/**
 * app 层契约 barrel。
 * `import { AppExt, buildRoutes, buildMenus } from '@/shell'`
 */
export { AppExt } from './extension';
export { buildRoutes, buildMenus } from './registry';
export type {
  ModuleManifest,
  MenuManifest,
  MenuItem,
  RouteManifest,
  AuthAdapter,
  Resources,
} from './types';
