import type { GroupView } from '@/domain/collaboration';
import { resolveUserId } from '@/services/workspace/botSessionService';
import { GROUP_CREATE_VIA_EXECUTE } from '@/services/workspace/groupCreateConfig';
import { groupService } from '@/services/workspace/groupService';
import type { DomainResult } from '@/services/workspace/identityService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useCallback, useState } from 'react';

export interface CreateGroupInput {
  name: string;
  strategy: 'chat' | 'manager_worker' | 'state_machine';
  deliveryPolicy?: 'send_to_driver' | 'inject_observers';
  definitionYaml?: string;
  driverBotUuid: string;
  participants: Array<{ actor_id: string }>;
  context?: string;
  participantBindings?: Array<{ binding: string; actor_ids: string[] }>;
}

export interface UseCreateGroupResult {
  /** 上一次 create 调用的错误文案（仅 !ok 时）；用于 Modal 内 inline 展示。 */
  friendlyError?: string;
  /** 进行中标记。 */
  creating: boolean;
  /** 触发创建；成功时返回 GroupView，失败时返回 undefined 并设置 friendlyError。 */
  run: (input: CreateGroupInput, options?: { viaExecute?: boolean }) => Promise<DomainResult<GroupView>>;
  /** 清空 inline 错误（用户修改表单时调用）。 */
  clearError: () => void;
}

/**
 * useCreateGroup —— CreateGroupModal 的 action 层。
 *
 * Components 不允许直接 import Service（layering 约定）。本 Hook 拥有 groupService.createGroup
 * 调用，组件只消费 `run/friendlyError`；这与 useInviteAccept 的模式一致。
 * 因为 groupService 模块被测试 auto-mock，本 Hook 内对 groupService.createGroup 的调用同样
 * 会落入 mocked 实现，故 CreateGroupModal 的测试可以直接断言 `groupService.createGroup` 被调用。
 */
export function useCreateGroup(): UseCreateGroupResult {
  const activeIdentityId = useWorkspaceStore((s) => s.activeIdentityId);
  const [friendlyError, setFriendlyError] = useState<string | undefined>(undefined);
  const [creating, setCreating] = useState(false);

  const run = useCallback(
    async (input: CreateGroupInput, options?: { viaExecute?: boolean }): Promise<DomainResult<GroupView>> => {
      setCreating(true);
      setFriendlyError(undefined);
      // 自定义协作群(state_machine)按「是否以任务执行」走 task execute；chat / manager_worker 仍走原 createGroup 链路。
      // options.viaExecute 来自 发起协作弹窗 的勾选框(选择自定义协作时出现);缺省回落 GROUP_CREATE_VIA_EXECUTE 常量。
      // 执行使用方 owner_user_id 取 resolveUserId(activeIdentityId)，与任务 execute 链路同源。
      const viaExecute = options?.viaExecute ?? GROUP_CREATE_VIA_EXECUTE;
      const res =
        viaExecute && input.strategy === 'state_machine'
          ? await groupService.createGroupViaExecute(input, resolveUserId(activeIdentityId ?? ''))
          : await groupService.createGroup(input);
      setCreating(false);
      if (!res.ok) {
        setFriendlyError(res.error.friendlyMessage);
      }
      return res;
    },
    [activeIdentityId],
  );

  const clearError = useCallback(() => setFriendlyError(undefined), []);

  return { friendlyError, creating, run, clearError };
}
