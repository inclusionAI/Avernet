/**
 * 自定义协作群(task_dag / state_machine)的 execute 建群链路默认开关。
 *
 * 运行时由「发起协作」弹窗中「是否以任务执行」勾选框控制(仅选择自定义协作时出现):
 *   - 勾选 → 走 task execute 建群链路(groupService.createGroupViaExecute)。
 *   - 不勾 → 走 createGroup 默认链路(现状)。
 * 本常量是勾选框的初始默认值,以及 useCreateGroup.run 未显式传 viaExecute 时的回落值;
 * chat / manager_worker 不经此开关,始终走 createGroup 默认链路。
 */
export const GROUP_CREATE_VIA_EXECUTE = false;
