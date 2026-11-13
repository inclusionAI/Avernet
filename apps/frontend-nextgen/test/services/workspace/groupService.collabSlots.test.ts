/** @jest-environment node */
import type { GroupView } from '@/domain/collaboration';
import { getExtensionSlots, registerExtensionSlots } from '@/extensions/slots';
import type { GroupTasksExtension } from '@/extensions/contract';
import * as groupController from '@/services/backendApi/collaboration/collaborationGroupController';
import * as sessionController from '@/services/backendApi/collaboration/sessionController';
import { createGroupViaExecute } from '@/services/workspace/groupExecuteService';
import { groupService } from '@/services/workspace/groupService';
import type { DomainResult } from '@/services/workspace/identityService';
import { afterEach, beforeEach, describe, expect, it, jest } from '@jest/globals';

// auto-mock（不带 factory），避免 hoisted factory 内引用 jest.fn() 触发 @jest/globals 的 TDZ。
// sessionController 同样被 mock 以隔离 listGroupSessions（loadGroupDetail 路径用），本批断言不直接断言其调用。
jest.mock('@/services/backendApi/collaboration/collaborationGroupController');
jest.mock('@/services/backendApi/collaboration/sessionController');

const gc = groupController as unknown as Record<string, jest.Mock<any>>;
const sc = sessionController as unknown as Record<string, jest.Mock<any>>;

beforeEach(() => {
  jest.resetAllMocks();
});

describe('createGroupViaExecute 经 groupTasks 扩展槽位委派（2026-11 collab-form Task 7A）', () => {
  // 全局 setup（test/setup/registerExtensionSlots.ts）注册的默认槽位对象：null 化用例后按原对象恢复，
  // 保证槽位改写不外泄（覆盖通道见 test/setup/registerExtensionSlots.ts 头注释）。
  const baseline = getExtensionSlots();
  const resetToCollabNull = () => {
    registerExtensionSlots({ groupTasks: null, groupServiceBotRouting: null, taskSessionNavigationObserver: null, taskPanelBootstrap: null, sessionTaskPanelTab: null, taskPanelHost: null });
  };
  const smInput = {
    name: '我的群',
    strategy: 'state_machine' as const,
    definitionYaml: 'participants:\n  - alpha\nroles:\n  - driver',
    driverBotUuid: 'b1',
    originator: 'actor-bot',
    participants: [{ actor_id: 'b1' }],
  };

  afterEach(() => {
    registerExtensionSlots(baseline);
  });

  it('槽位 null（collab 形态）返回 GROUP_EXECUTE_UNAVAILABLE：零任务引擎请求且不回落普通建群', async () => {
    const originalFetch = global.fetch;
    global.fetch = jest.fn<typeof fetch>();
    try {
      resetToCollabNull();

      const res = await groupService.createGroupViaExecute({ ...smInput }, 'u-1');

      expect(res).toEqual({
        ok: false,
        error: {
          code: 'GROUP_EXECUTE_UNAVAILABLE',
          friendlyMessage: '当前站点不支持以任务执行方式创建协作群。',
          canRetry: false,
        },
      });
      expect(gc.createGroup).not.toHaveBeenCalled();
      expect(global.fetch).not.toHaveBeenCalled();
    } finally {
      global.fetch = originalFetch;
    }
  });

  it('槽位在位时按 (input, ownerUserId, 回落普通建群) 委派执行器并透传其结果', async () => {
    const sentinel: DomainResult<GroupView> = { ok: true, data: { groupId: 'g-slot' } as never };
    const slotExecute = jest.fn<typeof createGroupViaExecute>().mockResolvedValue(sentinel);
    const stubTasks: GroupTasksExtension | null = baseline.groupTasks;
    if (!stubTasks) throw new Error('jest 全局 setup 应已注册非空 groupTasks 占位槽位');
    registerExtensionSlots({ ...baseline, groupTasks: { ...stubTasks, createGroupViaExecute: slotExecute } });

    const res = await groupService.createGroupViaExecute(smInput, 'u-1');

    expect(slotExecute).toHaveBeenCalledTimes(1);
    expect(slotExecute.mock.calls[0][0]).toMatchObject({ strategy: 'state_machine', name: '我的群' });
    expect(slotExecute.mock.calls[0][1]).toBe('u-1');
    expect(res).toBe(sentinel);
    // 回落函数接线普通建群链路：slot 收到的第三参直接调通 createGroupApi。
    gc.createGroup.mockResolvedValue({
      code: 20000, message: '', request_id: 'r', data: { group_id: 'g-plain', name: '我的群', participants: [] },
    });
    await slotExecute.mock.calls[0][2]({ ...smInput, strategy: 'chat' });
    expect(gc.createGroup).toHaveBeenCalledTimes(1);
  });

  it('Open 真实槽位（槽位内为既有 execute 建群器）：保留执行器守护与普通建群回落', async () => {
    const stubTasks: GroupTasksExtension | null = baseline.groupTasks;
    if (!stubTasks) throw new Error('jest 全局 setup 应已注册非空 groupTasks 占位槽位');
    registerExtensionSlots({
      ...baseline,
      groupTasks: { ...stubTasks, createGroupViaExecute: createGroupViaExecute },
    });

    // state_machine + 服务 Bot 路由 tags → 既有 executor 守护（GROUP_ROUTE_UNSUPPORTED，不回落）。
    const guarded = await groupService.createGroupViaExecute(
      { ...smInput, participants: [{ actor_id: 'b1', tags: ['verify'] }] },
      'u-1',
    );
    expect(guarded).toEqual({
      ok: false,
      error: expect.objectContaining({ code: 'GROUP_ROUTE_UNSUPPORTED' }),
    });
    expect(gc.createGroup).not.toHaveBeenCalled();

    // 非 state_machine → executor 内置回落 → 注入的 fallback 即普通 createGroup 链路。
    gc.createGroup.mockResolvedValue({
      code: 20000, message: '', request_id: 'r', data: { group_id: 'g-plain', name: '我的群', participants: [] },
    });
    gc.getGroup.mockResolvedValue({
      code: 20000, message: '', request_id: 'r',
      data: {
        group_id: 'g-plain', name: '我的群', status: 'active', visibility: 'private',
        originator_actor_id: 'actor-bot', participants: [], driver_bot_uuid: 'b1',
        collaboration: { strategy: 'chat', delivery_policy: { bot_final_delivery: 'send_to_driver' } },
        created_at: 1, updated_at: 1,
      },
    });
    sc.listGroupSessions.mockResolvedValue({ code: 20000, data: { items: [], total: 0, offset: 0, limit: 50 } });
    const fellBack = await groupService.createGroupViaExecute({ ...smInput, strategy: 'chat' }, 'u-1');
    expect(gc.createGroup).toHaveBeenCalledTimes(1);
    expect(fellBack).toMatchObject({ ok: true, data: { groupId: 'g-plain' } });
  });
});
