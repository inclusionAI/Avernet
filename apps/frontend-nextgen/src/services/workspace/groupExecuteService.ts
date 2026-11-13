/**
 * 自定义协作群 execute 建群器 —— 从 groupService 物理拆出（2026-11 collab-form Task 7A 起，
 * BCS 群详情/会话只读链路再拆至 groupBcsReadService，任务引擎依赖收敛于本文件）。
 * - 本文件：state_machine 自定义协作群经 task execute 建群（createGroupViaExecute），
 *   建群成功后经 groupBcsReadService.loadBcsGroupDetail 回读完整 GroupView。
 * - groupService：协作群通用 CRUD（loadGroups / loadGroupDetail / createGroup / update / dissolve）；
 *   其 createGroupViaExecute 方法经扩展槽位 groupTasks.createGroupViaExecute 消费本实现
 *   （装配见 src/extensions/empty.ts），不再静态 import 本文件——collab 形态（槽位 null）下
 *   任务引擎代码不进群服务与建群消费方的 import graph。
 *
 * 依赖注入规避循环依赖：回落 createGroup 通过参数注入，本文件不 import groupService。
 *
 * 说明：GROUP_CREATE_VIA_EXECUTE 开关由 useCreateGroup 控制，仅 state_machine 自定义协作群
 * 进入 execute 链路；chat / manager_worker 仍走 groupService.createGroup。
 */
import type { GroupView } from '@/domain/collaboration';
import { executeTask } from '@/services/backendApi/tasks/taskController';
import { isEnvelopeFailure } from '@/services/backendApi/types';
import type { Envelope, ExecuteTaskResponse } from '@/services/tasks/taskModel';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { loadBcsGroupDetail } from './groupBcsReadService';
import type { CreateGroupInput } from './groupCreateRequest';
import { buildExecuteRequestFromGroup } from './groupTaskAdapter';
import type { DomainError, DomainResult } from './identityService';

function toDomainError(code: string, friendlyMessage: string): DomainError {
  return { code, friendlyMessage, canRetry: false };
}

/**
 * 走 task execute 创建自定义协作群(state_machine)。
 * - 非自定义协作群(非 state_machine) → 调用方注入的 fallbackCreateGroup 回落原链路。
 * - execute 以 Envelope{code,message,data,request_id} 统一封装：code 落在 2xx 成功段(200000-299999)为协议成功，
 *   其余 code（含 YAML 校验等业务错误）直接取 message 作 friendlyMessage 内联展示；
 *   HTTP 4xx/5xx/网络失败经 catch 映射。
 * - 建群成功依据 data.success && data.extend_props?.group_id（group_id 由后端 execute 同步回带），
 *   成功后标记 execute 群并经 loadBcsGroupDetail 拉回完整 GroupView。
 * - 开关见 useCreateGroup 的 GROUP_CREATE_VIA_EXECUTE；其有问题时关掉开关即回落原链路。
 */
export async function createGroupViaExecute(
  input: CreateGroupInput,
  ownerUserId: string,
  fallbackCreateGroup: (input: CreateGroupInput) => Promise<DomainResult<GroupView>>,
): Promise<DomainResult<GroupView>> {
  // task execute 尚无 participant tags 契约，不能静默丢失用户指定的路由。
  if (input.strategy === 'state_machine' && input.participants.some((participant) => participant.tags?.length)) {
    return {
      ok: false,
      error: toDomainError('GROUP_ROUTE_UNSUPPORTED', '指定服务 Bot 路由时，请取消“是否以任务执行”后创建。'),
    };
  }
  const req = buildExecuteRequestFromGroup(input, ownerUserId);
  if (!req) return fallbackCreateGroup(input);

  let resp: Envelope<ExecuteTaskResponse>;
  try {
    resp = await executeTask(req);
  } catch (err) {
    const status = (err as { status?: number })?.status;
    if (status === 400) {
      return { ok: false, error: toDomainError('GROUP_CREATE_INVALID', '创建参数校验不通过。') };
    }
    if (status === 403) {
      return { ok: false, error: toDomainError('GROUP_FORBIDDEN', '无权创建') };
    }
    return { ok: false, error: toDomainError('GROUP_CREATE_FAILED', '创建协作群失败，请稍后重试。') };
  }

  if (isEnvelopeFailure(resp)) {
    return {
      ok: false,
      error: toDomainError('GROUP_CREATE_INVALID', resp.message || '建群任务执行失败，请稍后重试。'),
    };
  }
  const env = resp.data;
  if (!env || !env.success) {
    return {
      ok: false,
      error: toDomainError('GROUP_CREATE_FAILED', env?.message ?? '建群任务执行失败，请稍后重试。'),
    };
  }
  const groupId = env.extend_props?.group_id;
  if (!groupId) {
    return {
      ok: false,
      error: toDomainError('GROUP_CREATE_FAILED', '建群任务已提交，execute 尚未回带 group_id（后端待实现）。'),
    };
  }
  // 标记本地 BCS 群：此后该群的详情/会话查询（含选中后 useSelectedGroupDetail/useSessionMap 的自动补拉）自动走 BCS。
  useWorkspaceStore.getState().markBcsGroup(groupId);
  return loadBcsGroupDetail(groupId);
}
