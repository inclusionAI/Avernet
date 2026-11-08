import { getCapabilities } from '@/capabilities';

/**
 * 任务执行 Loop 运行时配置。
 *
 * TASK_API_BASE：
 * - execute/dashboard/list 均走相对路径，由当前环境的同源代理转发到真实网关。
 * - 不在业务代码中写死预发域名；本地开发通过 PRESET=pre/dev 选择对应代理目标。
 *
 * 副屏（src/assets/TaskPanel）不读本文件；apiBaseUrl（host）由 useTaskExecution 透传给
 * openTab.params，taskApiBase（路径前缀）由 empty.ts 副屏 wrapper / MyTaskDrawers 直渲染
 * 经 resolveTaskApiBase() 解析后注入，assets 保持纯净不自读 capability。
 */
export const TASK_API_BASE = '';

const DEFAULT_TASK_READ_API_BASE = '/openapi/v1/collaboration/tasks';

/**
 * 解析 task API 路径前缀（不含 host）：由 capability getTaskApiBase 注入。
 * - Open Core → /openapi/v1/collaboration/tasks；内部 overlay → /api/v1/collaboration/tasks。
 * - capability 缺省回退内面 /api/v1（向后兼容）。
 * - 请求期调用，确保 capability 已装填；assets 守卫禁止副屏反查本文件，故由 app-level
 *   （empty.ts 副屏 wrapper / MyTaskDrawers 直渲染页）解析后经 props 透传给 TaskPanelFetcher。
 */
export function resolveTaskApiBase(): string {
  return getCapabilities().getTaskApiBase().value ?? '/api/v1/collaboration/tasks';
}

/**
 * 任务读接口路径前缀（dashboard / list / bbs-list）。默认走 /openapi/v1 公开面；仅本地
 * 排障时可由 config.local.ts 注入 TEAMCLAW_TASK_API_BASE=/api/v1/collaboration/tasks，
 * 让读写请求统一直连 TASK_ENGINE_UPSTREAM。生产构建未注入时仍保持公开面默认值。
 *
 * 仅用于前端发起的「读」接口（dashboard/list/bbs-list）；execute/grant/revoke 等写口
 * 仍由 resolveTaskApiBase() 按 capability 决定（Open Core /openapi、内部 overlay /api/v1）。
 */
export const TASK_READ_API_BASE =
  typeof TEAMCLAW_TASK_API_BASE !== 'undefined' && TEAMCLAW_TASK_API_BASE
    ? TEAMCLAW_TASK_API_BASE
    : DEFAULT_TASK_READ_API_BASE;
