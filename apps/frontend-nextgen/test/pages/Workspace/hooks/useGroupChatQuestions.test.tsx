/** @jest-environment jsdom */
// useGroupChatQuestions 单测：Provider 订阅 / 草稿 / 提交态 / 连接态编排
// （specs/2026-10-13-bcs-ask-user-question Task4）。
// 覆盖：替换 Provider 即时隔离、重连 reset 暂存并恢复草稿、终态释放草稿、
// 卸载 / 旧 Provider 的迟到失败不产生新 toast、失败只 toast 一次。
import type {
  GroupQuestionInteraction,
  GroupQuestionResolution,
  QuestionDraft,
} from '@/domain/collaboration/groupQuestions';
import {
  useGroupChatQuestions,
  type GroupQuestionsProvider,
} from '@/pages/Workspace/hooks/useGroupChatQuestions';
import { createGroupChatProvider } from '@/services/workspace/groupChatProvider';
import type { GroupQuestionSnapshot } from '@/services/workspace/groupQuestionService';
import type { ConnectionStatusEvent } from '@tc-chat/adapters';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import { act, renderHook } from '@testing-library/react';
import { toast } from 'sonner';

// bare auto-mock（factory 不引用 jest，规避 @jest/globals 下 hoisting TDZ，对齐 useBotChat.test.ts）。
// toast.error / toast.success 均为 jest.fn，运行时断言调用。
jest.mock('sonner');

const toastError = toast.error as unknown as jest.Mock;

const keyAlpha = JSON.stringify(['run-1', 'i1']);
const keyBeta = JSON.stringify(['run-2', 'i2']);

const interactionAlpha: GroupQuestionInteraction = {
  key: keyAlpha,
  runId: 'run-1',
  interactionId: 'i1',
  botId: 'bot-alpha',
  questions: [
    {
      questionId: 'q1',
      question: '环境？',
      multiSelect: false,
      allowOther: true,
      options: [{ value: 'stage', label: '预发布' }],
    },
  ],
};

const interactionBeta: GroupQuestionInteraction = {
  key: keyBeta,
  runId: 'run-2',
  interactionId: 'i2',
  botId: 'bot-beta',
  questions: [
    {
      questionId: 'q2',
      question: '集群？',
      multiSelect: true,
      allowOther: false,
      options: [{ value: 'c1', label: '集群一' }],
    },
  ],
};

const draftAnswered: QuestionDraft = { values: ['stage'], other: '', skipped: false };
const submitResolution: GroupQuestionResolution = {
  action: 'submit',
  answers: { q1: { values: ['stage'] } },
};

type QuestionListener = (snapshot: GroupQuestionSnapshot) => void;
type StatusListener = (event: ConnectionStatusEvent) => void;

/** 可控 fake Provider：快照 / 状态订阅 + 可编排的 resolveQuestion。 */
function createFakeProvider(initialConnected: boolean) {
  const questionListeners = new Set<QuestionListener>();
  const statusListeners = new Set<StatusListener>();
  const resolveQuestion = jest.fn<any>();
  const subscribeToQuestions = jest.fn<any>((listener: QuestionListener) => {
    questionListeners.add(listener);
    return () => {
      questionListeners.delete(listener);
    };
  });
  const subscribeToConnectionStatus = jest.fn<any>((listener: StatusListener) => {
    statusListeners.add(listener);
    return () => {
      statusListeners.delete(listener);
    };
  });
  const base = { isConnected: initialConnected, resolveQuestion, subscribeToQuestions, subscribeToConnectionStatus };
  const provider: GroupQuestionsProvider = base;
  return {
    provider,
    resolveQuestion,
    subscribeToQuestions,
    emit(snapshot: GroupQuestionSnapshot) {
      for (const listener of Array.from(questionListeners)) listener(snapshot);
    },
    emitStatus(status: ConnectionStatusEvent['status']) {
      base.isConnected = status === 'connected';
      const event: ConnectionStatusEvent = { status, retryCount: 0 };
      for (const listener of Array.from(statusListeners)) listener(event);
    },
  };
}

describe('useGroupChatQuestions', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('订阅问答快照并暴露真实连接状态；setDraft 按交互 key + questionId 保存', () => {
    const p = createFakeProvider(true);
    const { result } = renderHook(() => useGroupChatQuestions(p.provider));
    act(() => p.emit({ reason: 'update', interactions: [interactionAlpha] }));
    expect(result.current.interactions).toEqual([interactionAlpha]);
    expect(result.current.connected).toBe(true);

    act(() => result.current.setDraft(keyAlpha, 'q1', draftAnswered));
    expect(result.current.drafts[keyAlpha]!.q1).toEqual(draftAnswered);
    // 同 qid 覆盖更新
    act(() => result.current.setDraft(keyAlpha, 'q1', { values: [], other: 'x', skipped: false }));
    expect(result.current.drafts[keyAlpha]!.q1).toEqual({ values: [], other: 'x', skipped: false });
  });

  it('questionId 为 __proto__ 时草稿按自有键保存（不做原型污染）', () => {
    const p = createFakeProvider(true);
    const { result } = renderHook(() => useGroupChatQuestions(p.provider));
    act(() => result.current.setDraft(keyAlpha, '__proto__', draftAnswered));
    const drafts = result.current.drafts[keyAlpha]!;
    expect(Object.prototype.hasOwnProperty.call(drafts, '__proto__')).toBe(true);
    expect(drafts['__proto__']).toEqual(draftAnswered);
  });

  it('切换 Provider 即时隔离：渲染期清空旧状态，旧 Provider 迟到快照不污染新模型', () => {
    const pA = createFakeProvider(true);
    const pB = createFakeProvider(true);
    const { result, rerender } = renderHook(
      ({ provider }: { provider: GroupQuestionsProvider | null }) => useGroupChatQuestions(provider),
      { initialProps: { provider: pA.provider as GroupQuestionsProvider | null } },
    );
    const staleListenerA = (pA.subscribeToQuestions as unknown as jest.Mock).mock.calls[0][0] as QuestionListener;
    act(() => staleListenerA({ reason: 'update', interactions: [interactionAlpha] }));
    act(() => result.current.setDraft(keyAlpha, 'q1', draftAnswered));
    expect(result.current.interactions).toEqual([interactionAlpha]);

    rerender({ provider: pB.provider });
    // 即时隔离：切到新 Provider 的同一次渲染即清空旧状态（不等待 effect 清理）
    expect(result.current.interactions).toEqual([]);
    expect(result.current.drafts).toEqual({});
    expect(result.current.connected).toBe(true);

    // 旧 Provider 被捕获的迟到快照不得更新新状态
    act(() => staleListenerA({ reason: 'update', interactions: [interactionAlpha] }));
    expect(result.current.interactions).toEqual([]);

    // 新 Provider 正常工作
    const listenerB = (pB.subscribeToQuestions as unknown as jest.Mock).mock.calls[0][0] as QuestionListener;
    act(() => listenerB({ reason: 'update', interactions: [interactionBeta] }));
    expect(result.current.interactions).toEqual([interactionBeta]);
  });

  it('Provider 变为 null（会话关闭）时释放草稿，resolve 静默失败不提示', async () => {
    const pA = createFakeProvider(true);
    const { result, rerender } = renderHook(
      ({ provider }: { provider: GroupQuestionsProvider | null }) => useGroupChatQuestions(provider),
      { initialProps: { provider: pA.provider as GroupQuestionsProvider | null } },
    );
    act(() => pA.emit({ reason: 'update', interactions: [interactionAlpha] }));
    act(() => result.current.setDraft(keyAlpha, 'q1', draftAnswered));

    rerender({ provider: null });
    expect(result.current.interactions).toEqual([]);
    expect(result.current.drafts).toEqual({});
    let outcome: boolean | null = null;
    await act(async () => {
      outcome = await result.current.resolve(keyBeta, { action: 'cancel' });
    });
    expect(outcome).toBe(false);
    expect(toastError).not.toHaveBeenCalled();
  });

  it('重连 reset 暂存草稿：无重放不可见、重复 reset 不清空，重放后恢复', () => {
    const p = createFakeProvider(true);
    const { result } = renderHook(() => useGroupChatQuestions(p.provider));
    act(() => p.emit({ reason: 'update', interactions: [interactionAlpha] }));
    act(() => result.current.setDraft(keyAlpha, 'q1', draftAnswered));

    // reset：无重放不展示旧交互，但草稿暂存
    act(() => p.emit({ reason: 'reset', interactions: [] }));
    expect(result.current.interactions).toEqual([]);
    expect(result.current.drafts[keyAlpha]!.q1).toEqual(draftAnswered);

    // 同一重连周期内可能发生两次 reset（替换连接 + 握手开始），不得清空暂存草稿
    act(() => p.emit({ reason: 'reset', interactions: [] }));
    expect(result.current.drafts[keyAlpha]!.q1).toEqual(draftAnswered);

    // 服务端重放同 key 交互 → 恢复可见，草稿沿用
    act(() => p.emit({ reason: 'update', interactions: [interactionAlpha] }));
    expect(result.current.interactions).toEqual([interactionAlpha]);
    expect(result.current.drafts[keyAlpha]!.q1).toEqual(draftAnswered);
  });

  it('终态移除交互时释放对应草稿，其余交互不受影响', () => {
    const p = createFakeProvider(true);
    const { result } = renderHook(() => useGroupChatQuestions(p.provider));
    act(() => p.emit({ reason: 'update', interactions: [interactionAlpha, interactionBeta] }));
    act(() => result.current.setDraft(keyAlpha, 'q1', draftAnswered));
    act(() => result.current.setDraft(keyBeta, 'q2', { values: ['c1'], other: '', skipped: false }));

    // alpha 终态移除，beta 仍在待处理列表
    act(() => p.emit({ reason: 'terminal', interactions: [interactionBeta] }));
    expect(result.current.interactions).toEqual([interactionBeta]);
    expect(result.current.drafts[keyAlpha]).toBeUndefined();
    expect(result.current.drafts[keyBeta]!.q2).toEqual({ values: ['c1'], other: '', skipped: false });
  });

  it('终态移除已重放交互：未重放交互的暂存草稿保留，重放后恢复（重放/终态交错）', () => {
    const p = createFakeProvider(true);
    const draftBeta: QuestionDraft = { values: ['c1'], other: '', skipped: false };
    const { result } = renderHook(() => useGroupChatQuestions(p.provider));
    // 两个交互均已填写草稿
    act(() => p.emit({ reason: 'update', interactions: [interactionAlpha, interactionBeta] }));
    act(() => result.current.setDraft(keyAlpha, 'q1', draftAnswered));
    act(() => result.current.setDraft(keyBeta, 'q2', draftBeta));
    // 重连 reset 暂存草稿
    act(() => p.emit({ reason: 'reset', interactions: [] }));
    expect(result.current.drafts[keyBeta]!.q2).toEqual(draftBeta);

    // 服务端只重放 alpha（beta 的重放迟到）：此前周期 remaining=[alpha]
    act(() => p.emit({ reason: 'update', interactions: [interactionAlpha] }));
    // alpha 终态移除（如他人抢先处理）：只释放 alpha 的草稿，
    // 不得清掉本周期内尚未重放的 beta 暂存草稿（协议允许重放交错，允许其后再重放）。
    act(() => p.emit({ reason: 'terminal', interactions: [] }));
    expect(result.current.drafts[keyAlpha]).toBeUndefined();
    expect(result.current.drafts[keyBeta]).toBeDefined();
    expect(result.current.drafts[keyBeta]!.q2).toEqual(draftBeta);

    // beta 随后重放 → 可见且草稿恢复沿用
    act(() => p.emit({ reason: 'update', interactions: [interactionBeta] }));
    expect(result.current.interactions).toEqual([interactionBeta]);
    expect(result.current.drafts[keyBeta]!.q2).toEqual(draftBeta);
  });

  it('resolve 跟踪提交中状态；成功返回 true 并清空提交态', async () => {
    const p = createFakeProvider(true);
    let settle!: () => void;
    p.resolveQuestion.mockImplementation(
      () =>
        new Promise<void>((resolveFn) => {
          settle = resolveFn;
        }),
    );
    const { result } = renderHook(() => useGroupChatQuestions(p.provider));
    act(() => p.emit({ reason: 'update', interactions: [interactionAlpha] }));

    let outcome: Promise<boolean> | null = null;
    act(() => {
      outcome = result.current.resolve(keyAlpha, submitResolution);
    });
    expect(result.current.submittingKeys).toEqual([keyAlpha]);
    expect(p.resolveQuestion).toHaveBeenCalledWith(keyAlpha, submitResolution);

    await act(async () => {
      settle();
    });
    expect(result.current.submittingKeys).toEqual([]);
    await expect(outcome).resolves.toBe(true);
  });

  it('resolve 失败只 toast 一次并返回 false；草稿保留、提交态复位', async () => {
    const p = createFakeProvider(true);
    p.resolveQuestion.mockRejectedValue(new Error('提交被拒绝'));
    const { result } = renderHook(() => useGroupChatQuestions(p.provider));
    act(() => p.emit({ reason: 'update', interactions: [interactionAlpha] }));
    act(() => result.current.setDraft(keyAlpha, 'q1', draftAnswered));

    let outcome: boolean | null = null;
    await act(async () => {
      outcome = await result.current.resolve(keyAlpha, submitResolution);
    });
    expect(outcome).toBe(false);
    expect(toastError).toHaveBeenCalledTimes(1);
    expect(toastError).toHaveBeenCalledWith('提交被拒绝');
    // 失败不吞输入：草稿仍在，可重试
    expect(result.current.drafts[keyAlpha]!.q1).toEqual(draftAnswered);
    expect(result.current.submittingKeys).toEqual([]);
  });

  it('卸载后迟到的提交失败不 toast', async () => {
    const p = createFakeProvider(true);
    let rejectFn!: (error: Error) => void;
    p.resolveQuestion.mockImplementation(
      () =>
        new Promise<void>((_, reject) => {
          rejectFn = reject;
        }),
    );
    const { result, unmount } = renderHook(() => useGroupChatQuestions(p.provider));
    let outcome: Promise<boolean> | null = null;
    act(() => {
      outcome = result.current.resolve(keyAlpha, submitResolution);
    });
    unmount();

    await act(async () => {
      rejectFn(new Error('迟到失败'));
    });
    expect(toastError).not.toHaveBeenCalled();
    await expect(outcome).resolves.toBe(false);
  });

  it('切换 Provider 后旧提交失败不在新页面 toast；新 Provider 失败正常提示一次', async () => {
    const pA = createFakeProvider(true);
    const pB = createFakeProvider(true);
    let rejectA!: (error: Error) => void;
    pA.resolveQuestion.mockImplementation(
      () =>
        new Promise<void>((_, reject) => {
          rejectA = reject;
        }),
    );
    pB.resolveQuestion.mockRejectedValueOnce(new Error('新会话失败'));
    const { result, rerender } = renderHook(
      ({ provider }: { provider: GroupQuestionsProvider | null }) => useGroupChatQuestions(provider),
      { initialProps: { provider: pA.provider as GroupQuestionsProvider | null } },
    );
    act(() => {
      void result.current.resolve(keyAlpha, submitResolution);
    });
    rerender({ provider: pB.provider });

    await act(async () => {
      rejectA(new Error('旧会话失败'));
    });
    expect(toastError).not.toHaveBeenCalled();

    let outcome: boolean | null = null;
    await act(async () => {
      outcome = await result.current.resolve(keyBeta, { action: 'cancel' });
    });
    expect(outcome).toBe(false);
    expect(toastError).toHaveBeenCalledTimes(1);
    expect(toastError).toHaveBeenCalledWith('新会话失败');
  });

  it('connected 跟随真实连接状态（含初始为断线的 Provider）', () => {
    const p = createFakeProvider(false);
    const { result } = renderHook(() => useGroupChatQuestions(p.provider));
    expect(result.current.connected).toBe(false);
    act(() => p.emitStatus('connected'));
    expect(result.current.connected).toBe(true);
    act(() => p.emitStatus('disconnected'));
    expect(result.current.connected).toBe(false);
    act(() => p.emitStatus('connecting'));
    expect(result.current.connected).toBe(false);
  });

  it('真实 GroupChatProvider 结构性满足 Provider 接口，可直接驱动本 Hook（Task5 装配契约）', () => {
    const provider = createGroupChatProvider({
      sessionId: 's1',
      groupId: 'g1',
      identityId: 'me',
      wsOrigin: 'wss://x.test',
    });
    const { result, unmount } = renderHook(() => useGroupChatQuestions(provider));
    // 订阅即回放：未连接的 Provider 无待处理交互，connected 取真实连接状态。
    expect(result.current.interactions).toEqual([]);
    expect(result.current.connected).toBe(false);
    unmount();
  });
});