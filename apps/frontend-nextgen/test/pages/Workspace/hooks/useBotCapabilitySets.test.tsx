/** @jest-environment jsdom */
/** useBotCapabilitySets 单测（add-composer-capability-set-viewer）：
 * 装配资格（好友 Bot 不装配）、已激活过滤 + 默认集命名/置顶、in-flight 去重、
 * 明细 skills 拉取映射与失败容错、团队 Bot 显式 ownerId。 */
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import { useBotCapabilitySets, isCapabilitySetBot } from '@/pages/Workspace/hooks/useBotCapabilitySets';
import { botEditorController } from '@/services/backendApi/bots/botEditorController';
import type { ChatBotView } from '@/services/workspace/botSessionService';
import { act, renderHook } from '@testing-library/react';
import { toast } from 'sonner';

jest.mock('sonner', () => ({ toast: { error: jest.fn(), warning: jest.fn(), success: jest.fn() } }));

jest.mock('@/services/backendApi/bots/botEditorController', () => ({
  botEditorController: {
    listSkillSetResources: jest.fn(),
    listSkillSetSkills: jest.fn(),
  },
}));

function bot(overrides: Partial<ChatBotView> = {}): ChatBotView {
  return {
    botId: 'bot-1',
    realBotId: 'bot-1',
    displayName: 'Bot',
    online: true,
    chatable: true,
    ...overrides,
  };
}

const listMock = botEditorController.listSkillSetResources as jest.Mock;
const skillsMock = botEditorController.listSkillSetSkills as jest.Mock;

function makeSet(overrides: Record<string, unknown> = {}) {
  return {
    id: 'set-1',
    name: 'Default capability set',
    is_default: true,
    is_active: true,
    mcps: [{ server_code: 'mcp-a', name: 'MCP A' }],
    ...overrides,
  };
}

beforeEach(() => {
  jest.clearAllMocks();
});

describe('isCapabilitySetBot qualifications', () => {
  it('Friend Bot / not chatable no assembly', () => {
    expect(isCapabilitySetBot(bot({ isFriendBot: true }))).toBe(false);
    expect(isCapabilitySetBot(bot({ chatable: false }))).toBe(false);
    expect(isCapabilitySetBot(null)).toBe(false);
    expect(isCapabilitySetBot(bot())).toBe(true);
    expect(isCapabilitySetBot(bot({ isTeamBot: true }))).toBe(true);
  });
});

describe('useBotCapabilitySets', () => {
  it('Returns null when Friend Bot / userId missing', () => {
    const { result: friendResult } = renderHook(() => useBotCapabilitySets(bot({ isFriendBot: true }), 'u1'));
    expect(friendResult.current).toBeNull();
    const { result: noUser } = renderHook(() => useBotCapabilitySets(bot(), null));
    expect(noUser.current).toBeNull();
  });

  it('load only keeps activated sets, default set naming Default capability set and pinned to top, explicit ownerId passed through', async () => {
    listMock.mockResolvedValue({
      data: [
        makeSet({ id: 'set-b', name: 'Research', is_default: false }),
        makeSet(), // set-1：原始默认集，is_default=true
        makeSet({ id: 'set-c', name: 'Disabled set', is_default: false, is_active: false }),
      ],
    });
    const teamBot = bot({ isTeamBot: true, ownerId: 'owner-77' });
    const { result } = renderHook(() => useBotCapabilitySets(teamBot, 'u1'));
    expect(result.current).not.toBeNull();
    await act(async () => {
      result.current!.load();
    });
    // One call, request carries realBotId + entity ownerId (Design D2 team bot constraint).
    expect(listMock).toHaveBeenCalledWith('bot-1', 'owner-77');
    const sets = result.current!.sets;
    expect(sets).toHaveLength(2); // 已过滤停用集 set-c
    expect(
      sets.map((s) => ({ id: s.id, name: s.name, mcps: s.mcps.map((m) => m.serverCode) })),
    ).toEqual([
      { id: 'set-1', name: '默认能力集', mcps: ['mcp-a'] }, // 默认集归一名且置顶
      { id: 'set-b', name: 'Research', mcps: ['mcp-a'] },
    ]);
  });

  it('Consecutive load before completion goes through in-flight dedup (list requested only once)', async () => {
    let resolveList: (value: { data: unknown[] }) => void = () => {};
    listMock.mockReturnValue(
      new Promise((resolve) => {
        resolveList = resolve;
      }),
    );
    const { result } = renderHook(() => useBotCapabilitySets(bot(), 'u1'));
    act(() => {
      result.current!.load();
      result.current!.load();
    });
    expect(listMock).toHaveBeenCalledTimes(1);
    await act(async () => {
      resolveList({ data: [] });
    });
  });

  it('bot 变化时重置数据（旧 Bot 的集不跨会话泄漏）', async () => {
    listMock.mockResolvedValue({ data: [makeSet()] });
    const { result, rerender } = renderHook(({ b, u }: { b: ChatBotView; u: string | null }) => useBotCapabilitySets(b, u), {
      initialProps: { b: bot(), u: 'u1' },
    });
    await act(async () => {
      result.current!.load();
    });
    expect(result.current!.sets).toHaveLength(1);
    rerender({ b: bot({ realBotId: 'bot-2', botId: 'bot-2' }), u: 'u1' });
    expect(result.current!.sets).toHaveLength(0);
  });

  it('fetchSetSkills maps skills; on failure returns failed marker without throwing', async () => {
    skillsMock.mockImplementation((_botId: string, setId: string) => {
      if (setId === 'bad') return Promise.reject(new Error('boom'));
      return Promise.resolve({ data: [{ skill_id: 'sk-1', name: 'Skill 1', description: 'd' }] });
    });
    const { result } = renderHook(() => useBotCapabilitySets(bot({ ownerId: 'o1' }), 'u1'));
    await act(async () => {
      const ok = await result.current!.fetchSetSkills('set-1');
      expect(ok).toEqual({ skills: [{ skillId: 'sk-1', name: 'Skill 1', description: 'd' }], failed: false });
      const bad = await result.current!.fetchSetSkills('bad');
      expect(bad).toEqual({ skills: [], failed: true });
    });
    expect(skillsMock).toHaveBeenCalledWith('bot-1', 'set-1', 'o1');
  });

  it('列表请求失败时 toast 报错且 isLoading 复位', async () => {
    listMock.mockRejectedValue(new Error('list failed'));
    const { result } = renderHook(() => useBotCapabilitySets(bot(), 'u1'));
    await act(async () => {
      result.current!.load();
    });
    expect(toast.error).toHaveBeenCalled();
    expect(result.current!.isLoading).toBe(false);
  });
});