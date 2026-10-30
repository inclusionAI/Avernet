/** @jest-environment jsdom */
// TaskPanelFetcher 分支覆盖率补齐：list-fallback 兜底链、refresh 模式、错误信封、
// abort guard、轮询/重试、终态停轮。仅 mock `global.fetch` 与 `useLoginRedirectStore` 复位。
// 不测 ACE 路径（已由 aceLogin.test.tsx 覆盖）；不修改源码。
import { TaskPanelFetcher } from '@/assets/TaskPanel/TaskPanelFetcher';
import type { TaskView } from '@/assets/TaskPanel/types';
import { useLoginRedirectStore } from '@/stores/loginRedirectStore';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { afterEach, beforeEach, describe, expect, it, jest } from '@jest/globals';
import { act, render, waitFor } from '@testing-library/react';

const originalFetch = global.fetch;

interface FetcherState {
  task: TaskView | null;
  loading: boolean;
  refreshing: boolean;
  error: string | null;
  retry: () => void;
}

let lastState: FetcherState;

/** 捕获最近一次 children 渲染时的内部状态，便于断言 task/error/refreshing 等。 */
const capture = () => (s: FetcherState) => {
  lastState = s;
  return null;
};

/** 模拟返回 200 + JSON body 的 Response。 */
const okResp = (body: unknown): Response =>
  ({
    ok: true,
    status: 200,
    headers: { get: () => 'application/json' },
    json: async () => body,
  }) as unknown as Response;

/** 模拟返回非 2xx 的 Response。 */
const badResp = (status: number): Response =>
  ({
    ok: false,
    status,
    headers: { get: () => 'application/json' },
    json: async () => ({}),
  }) as unknown as Response;

/** 后端定义但缺失时 finishedAt 为 null 的 dashboard（终态 DONE，不轮询）。 */
const buildDoneDashboard = (taskId = 't1', over: Partial<Record<string, unknown>> = {}) => {
  const data = {
    task_id: taskId,
    run_id: 1,
    status: 'DONE',
    needs_attention: false,
    task_type: 'dynamic',
    source_type: 'bot',
    owner_user_id: 'u1',
    owner_bot_id: 'b1',
    task_spec: { metadata: { title: '完成' }, goal: { objective: '', acceptances: [] } },
    create_time: '2026-01-01',
    finish_time: null,
    loop_round: 1,
    progress: {
      total: 1,
      pending: 0,
      planning: 0,
      running: 0,
      done: 1,
      failed: 0,
      hung: 0,
      skipped: 0,
      percent: 100,
    },
    tasks: [
      {
        node_id: 'n1',
        task_id: taskId,
        sequence: 1,
        status: 'DONE',
        task_spec: { metadata: { title: '节点' }, goal: { objective: '', acceptances: [] } },
        run_info: {},
      },
    ],
    relations: [],
    ...over,
  } as Record<string, unknown>;
  return { code: 200000, message: 'ok', request_id: 'r', data };
};

/** 非终态 RUNNING dashboard：mapper 把 status RUNNING → EXECUTING，节点 running → 轮询。 */
const buildRunningDashboard = (taskId = 't1') => {
  const data = {
    task_id: taskId,
    run_id: 1,
    status: 'RUNNING',
    needs_attention: false,
    task_type: 'dynamic',
    source_type: 'bot',
    owner_user_id: 'u1',
    owner_bot_id: 'b1',
    task_spec: { metadata: { title: '运行' }, goal: { objective: '', acceptances: [] } },
    create_time: '2026-01-01',
    finish_time: null,
    loop_round: 1,
    progress: {
      total: 1,
      pending: 0,
      planning: 0,
      running: 1,
      done: 0,
      failed: 0,
      hung: 0,
      skipped: 0,
      percent: 0,
    },
    tasks: [
      {
        node_id: 'n1',
        task_id: taskId,
        sequence: 1,
        status: 'RUNNING',
        task_spec: { metadata: { title: '节点' }, goal: { objective: '', acceptances: [] } },
        run_info: {},
      },
    ],
    relations: [],
  } as Record<string, unknown>;
  return { code: 200000, message: 'ok', request_id: 'r', data };
};

/** CANCELLED 终态 dashboard：isCancelled → isTerminal=true → 不轮询。 */
const buildCancelledDashboard = (taskId = 't1') => {
  const data = {
    task_id: taskId,
    run_id: 1,
    status: 'CANCELLED',
    needs_attention: false,
    task_type: 'dynamic',
    source_type: 'bot',
    owner_user_id: 'u1',
    owner_bot_id: 'b1',
    task_spec: { metadata: { title: '取消' }, goal: { objective: '', acceptances: [] } },
    create_time: '2026-01-01',
    finish_time: null,
    loop_round: 1,
    progress: {
      total: 1,
      pending: 0,
      planning: 0,
      running: 0,
      done: 0,
      failed: 0,
      hung: 0,
      skipped: 0,
      percent: 0,
    },
    tasks: [
      {
        node_id: 'n1',
        task_id: taskId,
        sequence: 1,
        status: 'CANCELLED',
        task_spec: { metadata: { title: '节点' }, goal: { objective: '', acceptances: [] } },
        run_info: {},
      },
    ],
    relations: [],
  } as Record<string, unknown>;
  return { code: 200000, message: 'ok', request_id: 'r', data };
};

/** dashboard 各 label 字段全空：兜底链 / 空 label 分支用。 */
const buildEmptyLabelDashboard = (taskId = 't1') => {
  const data = {
    task_id: taskId,
    run_id: 1,
    status: 'DONE',
    needs_attention: false,
    task_type: '',
    source_type: '',
    owner_user_id: 'u1',
    owner_bot_id: '',
    task_spec: { metadata: { title: 'T' }, goal: { objective: '', acceptances: [] } },
    create_time: '',
    finish_time: null,
    loop_round: 1,
    progress: {
      total: 1,
      pending: 0,
      planning: 0,
      running: 0,
      done: 1,
      failed: 0,
      hung: 0,
      skipped: 0,
      percent: 100,
    },
    tasks: [
      {
        node_id: 'n1',
        task_id: taskId,
        sequence: 1,
        status: 'DONE',
        task_spec: { metadata: { title: 'N' }, goal: { objective: '', acceptances: [] } },
        run_info: {},
      },
    ],
    relations: [],
  } as Record<string, unknown>;
  return { code: 200000, message: 'ok', request_id: 'r', data };
};

/** fetch 工厂：按 URL 子串分发，dashboard 走闭包内的 dashboardEnvelope。 */
const makeFetch = (
  dashboardEnvelope: unknown | ((url: string) => unknown),
  listEnvelope: unknown = { code: 0, message: 'ok', request_id: 'r', data: [] },
) =>
  jest.fn((url: string) => {
    if (url.includes('/dashboard')) {
      const body = typeof dashboardEnvelope === 'function' ? (dashboardEnvelope as (u: string) => unknown)(url) : dashboardEnvelope;
      return Promise.resolve(okResp(body));
    }
    return Promise.resolve(okResp(listEnvelope));
  }) as unknown as jest.Mock;

const dashboardCallCount = (mock: jest.Mock) =>
  mock.mock.calls.filter((c: unknown[]) => (c[0] as string).includes('/dashboard')).length;

beforeEach(() => {
  useLoginRedirectStore.getState().reset();
  lastState = undefined as unknown as FetcherState;
});

afterEach(() => {
  global.fetch = originalFetch;
  useLoginRedirectStore.getState().reset();
  jest.useRealTimers();
});

describe('TaskPanelFetcher list-fallback 兜底链 (L74-122)', () => {
  it('数组 data + 命中记录 + 终态 status → fallback 字段全量回填到 merge', async () => {
    global.fetch = makeFetch(buildEmptyLabelDashboard('t1'), {
      code: 0,
      message: 'ok',
      request_id: 'r',
      data: [
        {
          task_id: 't1',
          source_type: 'bot',
          owner_bot_name: 'BotX',
          owner_bot_id: 'b1',
          execution_config: { task_type: 'yaml' },
          gmt_create: '2026-01-01',
          gmt_modified: '2026-01-02',
          status: 'DONE',
        },
      ],
    });

    const { unmount } = render(
      <TaskPanelFetcher apiBaseUrl="" taskId="t1" userId="u1">
        {capture()}
      </TaskPanelFetcher>,
    );
    await waitFor(() => expect(lastState.task).not.toBeNull());
    expect(lastState.task!.taskTypeLabel).toBe('YAML 编排');
    expect(lastState.task!.sourceLabel).toBe('Bot 对话');
    expect(lastState.task!.ownerBotName).toBe('BotX');
    expect(lastState.task!.createdAt).toBe('2026-01-01');
    expect(lastState.task!.finishedAt).toBe('2026-01-02');
    unmount();
  });

  it('data 为 {items:[...]} 形态 + 非终态记录 → finishedAt 取 null', async () => {
    global.fetch = makeFetch(buildDoneDashboard('t1'), {
      code: 0,
      message: 'ok',
      request_id: 'r',
      data: {
        items: [
          {
            task_id: 't1',
            source_type: 'bot',
            owner_bot_name: 'BotX',
            gmt_create: '2026-01-01',
            gmt_modified: '2026-02-02',
            status: 'RUNNING',
          },
        ],
      },
    });

    const { unmount } = render(
      <TaskPanelFetcher apiBaseUrl="" taskId="t1" userId="u1">
        {capture()}
      </TaskPanelFetcher>,
    );
    await waitFor(() => expect(lastState.task).not.toBeNull());
    // dashboard 给的 sourceLabel='Bot 对话' 非空，merge 后回退不触发；这里只断言 finishedAt=null (status RUNNING 不在终态集)
    expect(lastState.task!.finishedAt).toBeNull();
    unmount();
  });

  it('data 为空数组 → items=[] → 无记录 → return (L90, L104 !record)', async () => {
    global.fetch = makeFetch(buildDoneDashboard('t1'), {
      code: 0,
      message: 'ok',
      request_id: 'r',
      data: [],
    });

    const { unmount } = render(
      <TaskPanelFetcher apiBaseUrl="" taskId="t1" userId="u1">
        {capture()}
      </TaskPanelFetcher>,
    );
    await waitFor(() => expect(lastState.task).not.toBeNull());
    unmount();
  });

  it('data 为 null → 非数组非对象 → items=[] (L87-89 false-false)', async () => {
    global.fetch = makeFetch(buildDoneDashboard('t1'), {
      code: 0,
      message: 'ok',
      request_id: 'r',
      data: null,
    });

    const { unmount } = render(
      <TaskPanelFetcher apiBaseUrl="" taskId="t1" userId="u1">
        {capture()}
      </TaskPanelFetcher>,
    );
    await waitFor(() => expect(lastState.task).not.toBeNull());
    unmount();
  });

  it('list 响应不 ok → 直接 return (L82)', async () => {
    const fetchMock = jest.fn((url: string) => {
      if (url.includes('/list')) return Promise.resolve(badResp(500));
      if (url.includes('/dashboard')) return Promise.resolve(okResp(buildDoneDashboard('t1')));
      return Promise.resolve(okResp({}));
    }) as unknown as jest.Mock;
    global.fetch = fetchMock;

    const { unmount } = render(
      <TaskPanelFetcher apiBaseUrl="" taskId="t1" userId="u1">
        {capture()}
      </TaskPanelFetcher>,
    );
    await waitFor(() => expect(lastState.task).not.toBeNull());
    // list fetch 被调用但无 listFallback 写入
    const listCalls = fetchMock.mock.calls.some((c: unknown[]) => (c[0] as string).includes('/list'));
    expect(listCalls).toBe(true);
    unmount();
  });

  it('list fetch 抛错 → catch 静默 (L116-118)', async () => {
    global.fetch = jest.fn((url: string) => {
      if (url.includes('/list')) return Promise.reject(new Error('list network error'));
      if (url.includes('/dashboard')) return Promise.resolve(okResp(buildDoneDashboard('t1')));
      return Promise.resolve(okResp({}));
    }) as unknown as jest.Mock;

    const { unmount } = render(
      <TaskPanelFetcher apiBaseUrl="" taskId="t1" userId="u1">
        {capture()}
      </TaskPanelFetcher>,
    );
    await waitFor(() => expect(lastState.task).not.toBeNull());
    // catch 吞错，task 仍从 dashboard 设置成功
    unmount();
  });

  it('切换 taskId 使旧闭包 cancelled=true → return (L104 cancelled)', async () => {
    let listResolve!: (v: Response) => void;
    const listPromise = new Promise<Response>((res) => {
      listResolve = res;
    });
    global.fetch = jest.fn((url: string) => {
      if (url.includes('/list')) return listPromise;
      if (url.includes('/dashboard')) return Promise.resolve(okResp(buildDoneDashboard('t2')));
      return Promise.resolve(okResp({}));
    }) as unknown as jest.Mock;

    const r = render(
      <TaskPanelFetcher apiBaseUrl="" taskId="t1" userId="u1">
        {capture()}
      </TaskPanelFetcher>,
    );
    // 同步转为 t2，使 t1 的 Effect1 cleanup 设 cancelled=true；listPromise 尚未 resolve
    r.rerender(
      <TaskPanelFetcher apiBaseUrl="" taskId="t2" userId="u1">
        {capture()}
      </TaskPanelFetcher>,
    );

    // 现在 resolve list fetch：旧闭包 cancelled=true → 命中记录也 return；新闭包 cancelled=false → 进 setListFallback
    await act(async () => {
      listResolve(
        okResp({
          code: 0,
          message: 'ok',
          request_id: 'r',
          data: [
            {
              task_id: 't1',
              source_type: 'bot',
              gmt_create: '2026-01-01',
              gmt_modified: '2026-01-01',
              status: 'DONE',
            },
            {
              task_id: 't2',
              source_type: 'api',
              gmt_create: '2026-01-02',
              gmt_modified: '2026-01-02',
              status: 'DONE',
            },
          ],
        }),
      );
      await Promise.resolve();
    });

    await waitFor(() => expect(lastState.task).not.toBeNull());
    // 新闭包 listFallback 写入 createdAt='2026-01-02'，但 dashboard 给的 createdAt='2026-01-01' 优先；
    // 这里仅断言未抛错且 task 已设置（覆盖 cancelled 分支即已足够）。
    r.unmount();
  });

  it('无 userId → setListFallback({}) 直接 return (L70-73)', async () => {
    global.fetch = makeFetch(buildEmptyLabelDashboard('t1'));

    const { unmount } = render(
      <TaskPanelFetcher apiBaseUrl="" taskId="t1">
        {capture()}
      </TaskPanelFetcher>,
    );
    await waitFor(() => expect(lastState.task).not.toBeNull());
    // 无 list fetch
    const hasListFetch = (global.fetch as jest.Mock).mock.calls.some((c: unknown[]) =>
      (c[0] as string).includes('/list'),
    );
    expect(hasListFetch).toBe(false);
    // 标签全空(回退链 fallback 也为空)
    expect(lastState.task!.taskTypeLabel).toBe('');
    expect(lastState.task!.sourceLabel).toBe('');
    expect(lastState.task!.ownerBotName).toBe('');
    expect(lastState.task!.createdAt).toBe('');
    expect(lastState.task!.finishedAt).toBeNull();
    unmount();
  });
});

describe('TaskPanelFetcher fetchDashboard 错误/状态分支 (L136-178)', () => {
  it('!resp.ok → 抛 请求失败（500） (L149)', async () => {
    global.fetch = jest.fn((url: string) => {
      if (url.includes('/dashboard')) return Promise.resolve(badResp(500));
      if (url.includes('/list')) return Promise.resolve(okResp({ code: 0, message: 'ok', data: [] }));
      return Promise.resolve(okResp({}));
    }) as unknown as jest.Mock;

    const { unmount } = render(
      <TaskPanelFetcher apiBaseUrl="" taskId="t1" userId="u1">
        {capture()}
      </TaskPanelFetcher>,
    );
    await waitFor(() => expect(lastState.error).toBe('请求失败（500）'));
    expect(lastState.task).toBeNull();
    unmount();
  });

  it('业务信封失败(code=500000) → 抛 message (L158-160)', async () => {
    global.fetch = makeFetch({
      code: 500000,
      message: '业务错',
      request_id: 'r',
    });

    const { unmount } = render(
      <TaskPanelFetcher apiBaseUrl="" taskId="t1" userId="u1">
        {capture()}
      </TaskPanelFetcher>,
    );
    await waitFor(() => expect(lastState.error).toBe('业务错'));
    unmount();
  });

  it('unwrapHttpEnvelope 返回 null → 响应数据为空 (L162-164)', async () => {
    // code 200000 落 2xx 段，isEnvelopeFailure=false；unwrapHttpEnvelope 钻 result=null → null
    global.fetch = makeFetch({
      code: 200000,
      message: 'invalid',
      request_id: 'r',
      result: null,
    });

    const { unmount } = render(
      <TaskPanelFetcher apiBaseUrl="" taskId="t1" userId="u1">
        {capture()}
      </TaskPanelFetcher>,
    );
    await waitFor(() => expect(lastState.error).toBe('响应数据为空'));
    unmount();
  });

  it('AbortError → catch 静默 (L170 true)', async () => {
    jest.useFakeTimers();
    const abortErr: Error = new Error('aborted');
    abortErr.name = 'AbortError';
    global.fetch = jest.fn(() => Promise.reject(abortErr)) as unknown as jest.Mock;

    const { unmount } = render(
      <TaskPanelFetcher apiBaseUrl="" taskId="t1">
        {capture()}
      </TaskPanelFetcher>,
    );
    await act(async () => {
      await jest.advanceTimersByTimeAsync(10);
    });
    expect(lastState.task).toBeNull();
    expect(lastState.error).toBeNull(); // AbortError 不写 error
    expect(lastState.loading).toBe(false);
    unmount();
  });

  it('catch 中 err.message 为空 → 回退 "加载任务失败" (L172 || false)', async () => {
    // 抛一个无 message 的非 Error 对象
    global.fetch = jest.fn(() =>
      Promise.reject({ name: 'NotAbort', /* no message */ }),
    ) as unknown as jest.Mock;

    const { unmount } = render(
      <TaskPanelFetcher apiBaseUrl="" taskId="t1">
        {capture()}
      </TaskPanelFetcher>,
    );
    await waitFor(() => expect(lastState.error).toBe('加载任务失败'));
    unmount();
  });

  it('切换 taskId：旧 ctrl 的 catch 因 abortRef.current!==ctrl 提前 return (L171 true / L174 false)', async () => {
    // dashboard 总是返回 500；首次 fetchDashboard 会被 rerender 抢占 abortRef，旧 catch 走 abort guard 提前 return
    const fetchMock = jest.fn((url: string) => {
      if (url.includes('/list')) return Promise.resolve(okResp({ code: 0, message: 'ok', data: [] }));
      return Promise.resolve(badResp(500));
    }) as unknown as jest.Mock;
    global.fetch = fetchMock;

    const r = render(
      <TaskPanelFetcher apiBaseUrl="" taskId="t1" userId="u1">
        {capture()}
      </TaskPanelFetcher>,
    );
    // 同步 rerender 切换 taskId，使旧 Effect2 cleanup 抢占 abortRef，旧 ctrl 不再持有 abortRef
    r.rerender(
      <TaskPanelFetcher apiBaseUrl="" taskId="t2" userId="u1">
        {capture()}
      </TaskPanelFetcher>,
    );

    await waitFor(() => expect(lastState.error).toBe('请求失败（500）'));
    // 新 taskId 的 dashboard fetch 应有调用
    const t2DashCalls = fetchMock.mock.calls.some(
      (c: unknown[]) => (c[0] as string).includes('/dashboard') && (c[0] as string).includes('task_id=t2'),
    );
    expect(t2DashCalls).toBe(true);
    r.unmount();
  });
});

describe('TaskPanelFetcher 轮询/重试/终态停轮 (L184-220)', () => {
  it('非终态任务 5s 后触发 refresh 模式 (L136 setRefreshing=true)', async () => {
    jest.useFakeTimers();
    global.fetch = makeFetch(buildRunningDashboard('t1'));

    const { unmount } = render(
      <TaskPanelFetcher apiBaseUrl="" taskId="t1">
        {capture()}
      </TaskPanelFetcher>,
    );
    // flush 初始 fetch + state 更新
    await act(async () => {
      await jest.advanceTimersByTimeAsync(10);
    });
    await act(async () => {
      await jest.advanceTimersByTimeAsync(0);
    });
    expect(lastState.task).not.toBeNull();
    expect(lastState.task!.status).toBe('EXECUTING');

    const callsBefore = dashboardCallCount(global.fetch as jest.Mock);
    // 越过 POLLING_INTERVAL(5000) 触发 refresh 的 setTimeout
    await act(async () => {
      await jest.advanceTimersByTimeAsync(5000);
    });
    await act(async () => {
      await jest.advanceTimersByTimeAsync(10);
    });
    const callsAfter = dashboardCallCount(global.fetch as jest.Mock);
    expect(callsAfter).toBeGreaterThan(callsBefore);
    unmount();
  });

  it('终态 DONE 任务 → Effect3 不再排 setTimeout (L210-211 true)', async () => {
    jest.useFakeTimers();
    global.fetch = makeFetch(buildDoneDashboard('t1'));

    const { unmount } = render(
      <TaskPanelFetcher apiBaseUrl="" taskId="t1">
        {capture()}
      </TaskPanelFetcher>,
    );
    await act(async () => {
      await jest.advanceTimersByTimeAsync(10);
    });
    await act(async () => {
      await jest.advanceTimersByTimeAsync(0);
    });
    expect(lastState.task).not.toBeNull();
    expect(lastState.task!.status).toBe('DONE');

    const callsBefore = (global.fetch as jest.Mock).mock.calls.length;
    await act(async () => {
      await jest.advanceTimersByTimeAsync(10000);
    });
    const callsAfter = (global.fetch as jest.Mock).mock.calls.length;
    expect(callsAfter).toBe(callsBefore);
    unmount();
  });

  it('终态 CANCELLED 任务 → isCancelled=true → 不轮询 (L210 isCancelled)', async () => {
    jest.useFakeTimers();
    global.fetch = makeFetch(buildCancelledDashboard('t1'));

    const { unmount } = render(
      <TaskPanelFetcher apiBaseUrl="" taskId="t1">
        {capture()}
      </TaskPanelFetcher>,
    );
    await act(async () => {
      await jest.advanceTimersByTimeAsync(10);
    });
    await act(async () => {
      await jest.advanceTimersByTimeAsync(0);
    });
    expect(lastState.task!.status).toBe('CANCELLED');
    const callsBefore = (global.fetch as jest.Mock).mock.calls.length;
    await act(async () => {
      await jest.advanceTimersByTimeAsync(10000);
    });
    expect((global.fetch as jest.Mock).mock.calls.length).toBe(callsBefore);
    unmount();
  });

  it('任务为空且 retrySignal>0 → 排程初始重试 setTimeout (L201-204, retry L217-219)', async () => {
    jest.useFakeTimers();
    // dashboard 返回 200000+result=null → unwrapHttpEnvelope 返回 null → 抛 \"响应数据为空\"，task 保持 null
    global.fetch = makeFetch({
      code: 200000,
      message: 'invalid',
      request_id: 'r',
      result: null,
    });

    const { unmount } = render(
      <TaskPanelFetcher apiBaseUrl="" taskId="t1">
        {capture()}
      </TaskPanelFetcher>,
    );
    await act(async () => {
      await jest.advanceTimersByTimeAsync(10);
    });
    await act(async () => {
      await jest.advanceTimersByTimeAsync(0);
    });
    expect(lastState.task).toBeNull();
    const initialCalls = dashboardCallCount(global.fetch as jest.Mock);

    // 调用 retry：retryRef=0、retrySignal+1、立即 fetchDashboard('initial')
    await act(async () => {
      lastState.retry();
    });
    await act(async () => {
      await jest.advanceTimersByTimeAsync(0);
    });
    const afterRetryCalls = dashboardCallCount(global.fetch as jest.Mock);
    expect(afterRetryCalls).toBeGreaterThan(initialCalls);

    // Effect3 因 retrySignal 变更重跑，task=null + retrySignal>0 + retryRef<MAX → 排 setTimeout(initial) at 5000ms
    await act(async () => {
      await jest.advanceTimersByTimeAsync(5000);
    });
    await act(async () => {
      await jest.advanceTimersByTimeAsync(0);
    });
    const afterTimerCalls = dashboardCallCount(global.fetch as jest.Mock);
    expect(afterTimerCalls).toBeGreaterThan(afterRetryCalls);
    unmount();
  });
});

describe('TaskPanelFetcher children merge 用例 (L224-239)', () => {
  it('dashboard 各 label 非 null → merge 优先 task 值（第二个 || 短路未触发也已由其它用例覆盖）', async () => {
    global.fetch = makeFetch(buildDoneDashboard('t1', { finish_time: '2026-01-03' }));

    const { unmount } = render(
      <TaskPanelFetcher apiBaseUrl="" taskId="t1" userId="u1">
        {capture()}
      </TaskPanelFetcher>,
    );
    await waitFor(() => expect(lastState.task).not.toBeNull());
    expect(lastState.task!.taskTypeLabel).toBe('动态任务');
    expect(lastState.task!.sourceLabel).toBe('Bot 对话');
    expect(lastState.task!.createdAt).toBe('2026-01-01');
    expect(lastState.task!.finishedAt).toBe('2026-01-03');
    unmount();
  });
});

describe('TaskPanelFetcher joinUrl / early-return / includeActionLog 分支', () => {
  it('apiBaseUrl=null → fetchDashboard L128 提前 return + joinUrl L25 RHS 走默认空串', async () => {
    const fetchMock = jest.fn((url: string) => {
      if (url.includes('/list')) return Promise.resolve(okResp({ code: 0, message: 'ok', request_id: 'r', data: [] }));
      return Promise.resolve(okResp({}));
    }) as unknown as jest.Mock;
    global.fetch = fetchMock;

    render(
      <TaskPanelFetcher apiBaseUrl={null as unknown as string} taskId="t1" userId="u1">
        {capture()}
      </TaskPanelFetcher>,
    );
    // 等待 Effect1 (list fallback 在 apiBaseUrl=null 时仍执行 joinUrl(null, ...)) 与
    // Effect2 的 fetchDashboard 都触发；fetchDashboard 因 apiBaseUrl===null 提前 return，不发起 dashboard 拉取。
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await Promise.resolve(); });
    const dashCalls = fetchMock.mock.calls.some((c: unknown[]) => (c[0] as string).includes('/dashboard'));
    expect(dashCalls).toBe(false);
    const listCalls = fetchMock.mock.calls.some((c: unknown[]) => (c[0] as string).includes('/list'));
    expect(listCalls).toBe(true);
  });

  it('taskApiBase 不以 / 开头 → joinUrl L26 ternary RHS 补斜杠', async () => {
    global.fetch = makeFetch(buildDoneDashboard('t1'));

    const { unmount } = render(
      <TaskPanelFetcher apiBaseUrl="https://gw.example.com" taskApiBase="api/v1/tasks" taskId="t1" userId="u1">
        {capture()}
      </TaskPanelFetcher>,
    );
    await waitFor(() => expect(lastState.task).not.toBeNull());
    const urls = (global.fetch as jest.Mock).mock.calls.map((c: unknown[]) => c[0] as string);
    expect(urls.some((u: string) => u.includes('/api/v1/tasks/dashboard'))).toBe(true);
    unmount();
  });

  it('includeActionLog=true → URL 携带 true (L144 ternary true)', async () => {
    global.fetch = makeFetch(buildDoneDashboard('t1'));

    const { unmount } = render(
      <TaskPanelFetcher apiBaseUrl="" taskId="t1" includeActionLog>
        {capture()}
      </TaskPanelFetcher>,
    );
    await waitFor(() => expect(lastState.task).not.toBeNull());
    const dashUrl = (global.fetch as jest.Mock).mock.calls
      .map((c: unknown[]) => c[0] as string)
      .find((u: string) => u.includes('/dashboard'));
    expect(dashUrl).toBeDefined();
    expect(dashUrl).toContain('include_action_log=true');
    unmount();
  });

  it('业务失败信封 message 为空 → 错误文案回退 "业务错误码 ${code}" (L159 || RHS)', async () => {
    global.fetch = makeFetch({
      code: 500000,
      message: '',
      request_id: 'r',
    });

    const { unmount } = render(
      <TaskPanelFetcher apiBaseUrl="" taskId="t1" userId="u1">
        {capture()}
      </TaskPanelFetcher>,
    );
    await waitFor(() => expect(lastState.error).toBe('业务错误码 500000'));
    unmount();
  });
});

describe('TaskPanelFetcher list-fallback 字段映射分支 (L107-113)', () => {
  it('execution_config.task_type 非已知 label → 用原始字符串 (L107 ?? RHS)', async () => {
    global.fetch = makeFetch(buildEmptyLabelDashboard('t1'), {
      code: 0,
      message: 'ok',
      request_id: 'r',
      data: [
        {
          task_id: 't1',
          source_type: 'bot',
          gmt_create: '2026-01-01',
          gmt_modified: '2026-01-02',
          status: 'DONE',
          execution_config: { task_type: 'custom_type' },
        },
      ],
    });

    const { unmount } = render(
      <TaskPanelFetcher apiBaseUrl="" taskId="t1" userId="u1">
        {capture()}
      </TaskPanelFetcher>,
    );
    await waitFor(() => expect(lastState.task).not.toBeNull());
    expect(lastState.task!.taskTypeLabel).toBe('custom_type');
    unmount();
  });

  it('source_type 非已知 label → SOURCE_LABELS ?? RHS (L109 ?? RHS)', async () => {
    global.fetch = makeFetch(buildEmptyLabelDashboard('t1'), {
      code: 0,
      message: 'ok',
      request_id: 'r',
      data: [
        {
          task_id: 't1',
          source_type: 'unknown_source',
          gmt_create: '2026-01-01',
          gmt_modified: '2026-01-02',
          status: 'DONE',
          execution_config: { task_type: 'yaml' },
        },
      ],
    });

    const { unmount } = render(
      <TaskPanelFetcher apiBaseUrl="" taskId="t1" userId="u1">
        {capture()}
      </TaskPanelFetcher>,
    );
    await waitFor(() => expect(lastState.task).not.toBeNull());
    expect(lastState.task!.sourceLabel).toBe('unknown_source');
    unmount();
  });

  it('record 无 source_type → sourceLabel 取 undefined (L109 ternary false)', async () => {
    global.fetch = makeFetch(buildEmptyLabelDashboard('t1'), {
      code: 0,
      message: 'ok',
      request_id: 'r',
      data: [
        {
          task_id: 't1',
          gmt_create: '2026-01-01',
          gmt_modified: '2026-01-02',
          status: 'DONE',
          // 无 source_type
        },
      ],
    });

    const { unmount } = render(
      <TaskPanelFetcher apiBaseUrl="" taskId="t1" userId="u1">
        {capture()}
      </TaskPanelFetcher>,
    );
    await waitFor(() => expect(lastState.task).not.toBeNull());
    expect(lastState.task!.sourceLabel).toBe('');
    unmount();
  });

  it('record 无 status → status ?? "" 取 RHS (L112 ?? RHS)', async () => {
    global.fetch = makeFetch(buildEmptyLabelDashboard('t1'), {
      code: 0,
      message: 'ok',
      request_id: 'r',
      data: [
        {
          task_id: 't1',
          source_type: 'bot',
          gmt_create: '2026-01-01',
          gmt_modified: '2026-01-02',
          // 无 status
          execution_config: { task_type: 'yaml' },
        },
      ],
    });

    const { unmount } = render(
      <TaskPanelFetcher apiBaseUrl="" taskId="t1" userId="u1">
        {capture()}
      </TaskPanelFetcher>,
    );
    await waitFor(() => expect(lastState.task).not.toBeNull());
    // status 缺失 → 不在终态 set → finishedAt=null（dashboard finish_time 同为 null）
    expect(lastState.task!.finishedAt).toBeNull();
    unmount();
  });

  it('终态 status 且 gmt_modified 缺失 → gmt_modified ?? null 取 RHS (L113 ?? RHS)', async () => {
    global.fetch = makeFetch(buildEmptyLabelDashboard('t1'), {
      code: 0,
      message: 'ok',
      request_id: 'r',
      data: [
        {
          task_id: 't1',
          source_type: 'bot',
          gmt_create: '2026-01-01',
          // 无 gmt_modified
          status: 'DONE',
          execution_config: { task_type: 'yaml' },
        },
      ],
    });

    const { unmount } = render(
      <TaskPanelFetcher apiBaseUrl="" taskId="t1" userId="u1">
        {capture()}
      </TaskPanelFetcher>,
    );
    await waitFor(() => expect(lastState.task).not.toBeNull());
    // gmt_modified 缺失且 status=DONE(终态) → record.gmt_modified ?? null = null → fallback finishedAt=null
    expect(lastState.task!.finishedAt).toBeNull();
    unmount();
  });
});
