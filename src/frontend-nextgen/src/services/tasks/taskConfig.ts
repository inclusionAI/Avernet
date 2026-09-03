/**
 * 任务执行 Loop 运行时配置。
 *
 * TASK_API_BASE：
 * - execute/dashboard/list 均走相对路径，由当前环境的同源代理转发到真实网关。
 * - 不在业务代码中写死预发域名；本地开发通过 PRESET=pre/dev 选择对应代理目标。
 *
 * 副屏（src/assets/TaskPanel）不读本文件；apiBaseUrl 由 useTaskExecution 透传给 openTab.params。
 */
export const TASK_API_BASE = '';
