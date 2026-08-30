/**
 * 自定义协作群的 execute 建群链路开关。state_machine 自定义协作群启用 execute 创建。
 *
 * 暂时关闭：execute 建群链路尚未测通，先回落 createGroup 默认链路，
 * 避免影响 state_machine 正常建群测试；execute 链路测通后再置 true 重新启用。
 * chat / manager_worker 仍走 createGroup 默认链路，不受此开关影响。
 */
export const GROUP_CREATE_VIA_EXECUTE = false;
