/** @jest-environment jsdom */
// 注意:@jest/globals 的 import 必须最先(被 mock 模块在此之前),否则 hoist 后 jest 绑定未初始化。
import { afterEach, beforeEach, describe, expect, it, jest } from '@jest/globals';

// TaskPanelFetcher(副屏自管轮询内核)行覆盖补全:补 aceLogin 专测之外的所有路径——
// 成功加载/轮询 refresh/终态停止、resp 非 ok、信封业务失败、data 空、retry 手动 + 自动重试、
// AbortError 静默、切 taskId 竞态丢弃旧响应、列表兜底补齐(ok/!ok/reject/数组形态)等。
import { TaskPanelFetcher } from '@/assets/TaskPanel/TaskPanelFetcher';
import { useLoginRedirectStore } from '@/stores/loginRedirectStore';
import { act, render } from '@testing-library/react';

const POLLING_INTERVAL = 5000;
const originalFetch = global.fetch;

// ── 造数工具 ───────────────────────────────────────────────────────────────

/** 最小 dashboard 图(成功信封 code 取 6 位成功段 200000,信封剥离后落在 data 载荷)。 */
function dashboardEnvelope(taskId: string, graphStatus: string, nodeStatus: string) {
  return {
    code: 200000,
    message: 'ok',
    request_id: 'r1',
    data: {
      task_id: taskId,
      run_id: 1,
      status: graphStatus,
      needs_attention: false,
      tasks: [
        {
          node_id: 'n1',
          task_id: taskId,
          sequence: 1,
          status: nodeStatus,
          task_spec: { metadata: { title: '节点' }, goal: { objective: '目标' } },
        },
      ],
      relations: [],
    },
  };
}

const jsonResponse = (json: unknown) =>
  ({ ok: true, status: 200, headers: { get: () => 'application/json' }, json: async () => json }) as unknown as Response;

const notOkResponse = (status: number) => ({ ok: false, status }) as unknown as Response;

const okList = (data: unknown) => jsonResponse({ code: 200000, data });

const aceBody = {
  actionType: 'LOGIN',
  buserviceErrorCode: 'USER_NOT_LOGIN',
  decisionBy: 'ACE',
  buserviceErrorMsg: 'https://login.example.com/pubLogin?goto=z',
};

// ── fetch mock:按 URL 派发到 dashboard / list 两个 handler 队列 ──────────────

const fetchMock = jest.fn<typeof fetch>();
/** dashboard 请求按序消费的响应(取尽后回落到 dashboardFallback)。 */
let dashboardQueue: Array<() => Response | Promise<Response>> = [];
let dashboardFallback: () => Response | Promise<Response> = () =>
  jsonResponse(dashboardEnvelope('t1', 'RUNNING', 'RUNNING'));
/** list 请求兜底响应(默认空列表,单测按需覆盖)。 */
let listResponse: () => Response | Promise<Response> = () => okList({ items: [] });

function dashboardCallCount(): number {
  return fetchMock.mock.calls.filter(([url]: unknown[]) => String(url).includes('/dashboard')).length;
}
function listCallCount(): number {
  return fetchMock.mock.calls.filter(([url]: unknown[]) => String(url).includes('/list')).length;
}

beforeEach(() => {
  jest.useFakeTimers();
  useLoginRedirectStore.getState().reset();
  dashboardQueue = [];
  dashboardFallback = () => jsonResponse(dashboardEnvelope('t1', 'RUNNING', 'RUNNING'));
  listResponse = () => okList({ items: [] });
  fetchMock.mockClear();
  fetchMock.mockImplementation(((url: unknown) => {
    const s = String(url);
    if (s.includes('/dashboard')) {
      const handler = dashboardQueue.shift() ?? dashboardFallback;
      return Promise.resolve(handler());
    }
    if (s.includes('/list')) {
      return Promise.resolve(listResponse());
    }
    return Promise.reject(new Error(`unexpected fetch url: ${s}`));
  }) as unknown as typeof fetch);
  global.fetch = fetchMock as unknown as typeof fetch;
});

afterEach(() => {
  global.fetch = originalFetch;
  useLoginRedirectStore.getState().reset();
  jest.clearAllTimers();
  jest.useRealTimers();
});

// ── 渲染工具:捕获 children 渲染回调收到的最新 state ─────────────────────────

type FetcherState = Parameters<Parameters<typeof TaskPanelFetcher>[0]['children']>[0];

function mountFetcher(props: { taskId?: string; userId?: string; includeActionLog?: boolean } = {}) {
  const box = { latest: undefined as FetcherState | undefined };
  const utils = render(
    <TaskPanelFetcher
      apiBaseUrl=""
      taskId={props.taskId ?? 't1'}
      userId={props.userId}
      includeActionLog={props.includeActionLog}
    >
      {(s) => {
        box.latest = s;
        return null;
      }}
    </TaskPanelFetcher>,
  );
  return { box, ...utils };
}

/** 推进少量时间,清空 fetch 微任务队列。 */
async function flush() {
  await act(async () => {
    await jest.advanceTimersByTimeAsync(10);
  });
}

/** 推进一个轮询周期(5s)。 */
async function tickPoll() {
  await act(async () => {
    await jest.advanceTimersByTimeAsync(POLLING_INTERVAL);
  });
}

// ── 用例 ───────────────────────────────────────────────────────────────────

describe('TaskPanelFetcher 覆盖补全', () => {
  it('taskId 为空时不发任何请求', async () => {
    const { box } = mountFetcher({ taskId: '' });
    await flush();

    expect(fetchMock).not.toHaveBeenCalled();
    expect(box.latest?.loading).toBe(false);
    expect(box.latest?.task).toBeNull();
  });

  it('初始加载成功:默认 include_action_log=false,轮询 refresh 模式,DONE 终态停止', async () => {
    let resolvePending!: (resp: Response) => void;
    dashboardQueue = [
      () => jsonResponse(dashboardEnvelope('t1', 'RUNNING', 'RUNNING')),
      () =>
        new Promise<Response>((r) => {
          resolvePending = r;
        }),
    ];
    const { box } = mountFetcher();
    await flush();

    // 请求 URL:相对 base + 默认 taskApiBase,include_action_log 缺省 false
    const firstUrl = String(fetchMock.mock.calls[0][0]);
    expect(firstUrl).toContain('/openapi/v1/collaboration/tasks/dashboard?task_id=t1&include_action_log=false');

    // 图级 RUNNING → EXECUTING(非终态)
    expect(box.latest?.task?.status).toBe('EXECUTING');
    expect(box.latest?.loading).toBe(false);

    // 5s 后轮询重排:refresh 模式置 refreshing,不置 loading
    await tickPoll();
    expect(dashboardCallCount()).toBe(2);
    expect(box.latest?.refreshing).toBe(true);
    expect(box.latest?.loading).toBe(false);
    expect(box.latest?.error).toBeNull();

    // 未完成的 refresh 响应到达后任务更新为 DONE,refreshing 复位
    await act(async () => {
      resolvePending(jsonResponse(dashboardEnvelope('t1', 'DONE', 'DONE')));
      await jest.advanceTimersByTimeAsync(0);
    });
    expect(box.latest?.task?.status).toBe('DONE');
    expect(box.latest?.refreshing).toBe(false);

    // DONE 且无活跃节点 → 终态,不再轮询
    await tickPoll();
    expect(dashboardCallCount()).toBe(2);
  });

  it('includeActionLog=true 时 URL 携带 include_action_log=true', async () => {
    mountFetcher({ includeActionLog: true });
    await flush();

    expect(String(fetchMock.mock.calls[0][0])).toContain('include_action_log=true');
  });

  it('HTTP 非 ok → 错误文案;retry 后恢复', async () => {
    dashboardFallback = () => notOkResponse(502);
    const { box } = mountFetcher();
    await flush();

    expect(box.latest?.error).toBe('请求失败（502）');
    expect(box.latest?.task).toBeNull();

    // 手动 retry:mock 改为成功 → 任务恢复且 error 清空
    dashboardQueue = [() => jsonResponse(dashboardEnvelope('t1', 'DONE', 'DONE'))];
    await act(async () => {
      box.latest?.retry();
    });
    await flush();
    expect(box.latest?.task?.status).toBe('DONE');
    expect(box.latest?.error).toBeNull();
  });

  it('信封业务失败:透出 message', async () => {
    dashboardFallback = () => jsonResponse({ code: 50001, message: '任务被冻结' });
    const { box } = mountFetcher();
    await flush();

    expect(box.latest?.error).toBe('任务被冻结');
  });

  it('信封业务失败无 message:回落业务错误码文案', async () => {
    dashboardFallback = () => jsonResponse({ code: 50001 });
    const { box } = mountFetcher();
    await flush();

    expect(box.latest?.error).toBe('业务错误码 50001');
  });

  it('信封剥离后载荷为空数组 → 响应数据为空', async () => {
    dashboardFallback = () => jsonResponse({ code: 200000, message: 'ok', request_id: 'r', data: [] });
    const { box } = mountFetcher();
    await flush();

    expect(box.latest?.error).toBe('响应数据为空');
    expect(box.latest?.task).toBeNull();
  });

  it('ACE 登录拦截体 → 登记单飞跳转,不进错误分支', async () => {
    dashboardFallback = () => jsonResponse(aceBody);
    const { box } = mountFetcher();
    await flush();

    expect(useLoginRedirectStore.getState().pendingLoginUrl).toBe(aceBody.buserviceErrorMsg);
    expect(box.latest?.error).toBeNull();
    expect(box.latest?.task).toBeNull();
  });

  it('失败 + retry 未恢复 → 5s 后按 retrySignal 自动重试一次', async () => {
    dashboardFallback = () => notOkResponse(500);
    const { box } = mountFetcher();
    await flush();
    expect(dashboardCallCount()).toBe(1);

    await act(async () => {
      box.latest?.retry();
    });
    await flush();
    expect(dashboardCallCount()).toBe(2);

    // retrySignal>0 且任务为空 → 轮询 effect 重排一次 initial 拉取
    await tickPoll();
    expect(dashboardCallCount()).toBe(3);
  });

  it('CANCELLED 图级终态:即使节点仍 running 也不继续轮询', async () => {
    dashboardQueue = [() => jsonResponse(dashboardEnvelope('t1', 'CANCELLED', 'RUNNING'))];
    const { box } = mountFetcher();
    await flush();

    expect(box.latest?.task?.status).toBe('CANCELLED');
    await tickPoll();
    expect(dashboardCallCount()).toBe(1);
  });

  it('FAILED 但仍有 pending 活跃节点 → 非终态继续轮询', async () => {
    dashboardQueue = [
      () => jsonResponse(dashboardEnvelope('t1', 'FAILED', 'PENDING')),
      () => jsonResponse(dashboardEnvelope('t1', 'RUNNING', 'RUNNING')),
    ];
    const { box } = mountFetcher();
    await flush();

    expect(box.latest?.task?.status).toBe('FAILED');
    await tickPoll();
    expect(dashboardCallCount()).toBe(2);
  });

  it('切 taskId 后旧请求的迟到成功响应被丢弃(abortRef 竞态守卫)', async () => {
    let resolveStale!: (resp: Response) => void;
    dashboardQueue = [
      () =>
        new Promise<Response>((r) => {
          resolveStale = r;
        }),
      () => jsonResponse(dashboardEnvelope('t2', 'DONE', 'DONE')),
    ];
    const box = { latest: undefined as FetcherState | undefined };
    const { rerender } = render(
      <TaskPanelFetcher apiBaseUrl="" taskId="t1">
        {(s) => {
          box.latest = s;
          return null;
        }}
      </TaskPanelFetcher>,
    );

    // 切到 t2:旧 t1 请求被 abort,新请求成功返回
    await act(async () => {
      rerender(
        <TaskPanelFetcher apiBaseUrl="" taskId="t2">
          {(s) => {
            box.latest = s;
            return null;
          }}
        </TaskPanelFetcher>,
      );
      await jest.advanceTimersByTimeAsync(10);
    });
    expect(box.latest?.task?.id).toBe('t2');

    // 旧 t1 成功响应迟到 → abortRef 不匹配,静默丢弃,不覆盖 t2 的任务
    await act(async () => {
      resolveStale(jsonResponse(dashboardEnvelope('t1', 'RUNNING', 'RUNNING')));
      await jest.advanceTimersByTimeAsync(0);
    });
    expect(box.latest?.task?.id).toBe('t2');
    expect(box.latest?.error).toBeNull();
  });

  it('切 taskId 后旧请求失败 reject → abortRef 竞态守卫静默丢弃,不落错误态', async () => {
    let rejectStale!: (err: Error) => void;
    dashboardQueue = [
      () =>
        new Promise<Response>((_r, rej) => {
          rejectStale = rej;
        }),
      () => jsonResponse(dashboardEnvelope('t2', 'DONE', 'DONE')),
    ];
    const box = { latest: undefined as FetcherState | undefined };
    const { rerender } = render(
      <TaskPanelFetcher apiBaseUrl="" taskId="t1">
        {(s) => {
          box.latest = s;
          return null;
        }}
      </TaskPanelFetcher>,
    );

    // 切到 t2 → 新请求成功
    await act(async () => {
      rerender(
        <TaskPanelFetcher apiBaseUrl="" taskId="t2">
          {(s) => {
            box.latest = s;
            return null;
          }}
        </TaskPanelFetcher>,
      );
      await jest.advanceTimersByTimeAsync(10);
    });
    expect(box.latest?.task?.id).toBe('t2');

    // 旧 t1 请求以普通错误 reject(非 AbortError)→ abortRef 不匹配 → 静默丢弃
    await act(async () => {
      rejectStale(new Error('stale boom'));
      await jest.advanceTimersByTimeAsync(0);
    });
    expect(box.latest?.task?.id).toBe('t2');
    expect(box.latest?.error).toBeNull();
  });

  it('fetch 以 AbortError 命名错误 reject → 静默返回,不落错误态', async () => {
    dashboardFallback = () => {
      throw Object.assign(new Error('The operation was aborted'), { name: 'AbortError' });
    };
    const { box } = mountFetcher();
    await flush();

    expect(box.latest?.error).toBeNull();
    expect(box.latest?.loading).toBe(false);
    expect(box.latest?.refreshing).toBe(false);
    expect(dashboardCallCount()).toBe(1);
  });

  describe('任务列表兜底补齐(listFallback)', () => {
    const listRecord = {
      task_id: 't1',
      source_type: 'coop_group',
      owner_bot_id: 'b1',
      owner_bot_name: '列表Bot',
      execution_config: { task_type: 'yaml' },
      gmt_create: '2026-10-01 08:00',
      gmt_modified: '2026-10-02 09:30',
      status: 'DONE',
    };

    it('userId 存在时并行拉取任务列表,补齐 owner/创建/结束时间', async () => {
      // dashboard 图不带 owner/时间元信息,由 list 数据补齐
      dashboardFallback = () => jsonResponse(dashboardEnvelope('t1', 'DONE', 'DONE'));
      listResponse = () => okList({ items: [listRecord] });
      const { box } = mountFetcher({ userId: 'u1' });
      await flush();

      expect(listCallCount()).toBe(1);
      const listUrl = String(fetchMock.mock.calls.find(([u]: unknown[]) => String(u).includes('/list'))?.[0]);
      expect(listUrl).toContain('/list?user_id=u1&page=1&page_size=100');

      const task = box.latest?.task;
      expect(task?.ownerBotName).toBe('列表Bot');
      expect(task?.createdAt).toBe('2026-10-01 08:00');
      // 图级 REMOVING.DONE 但 finish_time 缺失,由列表 gmt_modified(DONE 态)补齐
      expect(task?.finishedAt).toBe('2026-10-02 09:30');
      expect(box.latest?.error).toBeNull();
    });

    it('list 数据为直接数组形态也能解析补齐', async () => {
      dashboardFallback = () => jsonResponse(dashboardEnvelope('t1', 'DONE', 'DONE'));
      listResponse = () => okList([{ ...listRecord, task_id: 'other' }, listRecord]);
      const { box } = mountFetcher({ userId: 'u1' });
      await flush();

      expect(box.latest?.task?.ownerBotName).toBe('列表Bot');
    });

    it('列表中无匹配记录 → 未命中守卫,补齐字段回落空值', async () => {
      dashboardFallback = () => jsonResponse(dashboardEnvelope('t1', 'DONE', 'DONE'));
      listResponse = () => okList({ items: [{ ...listRecord, task_id: 'different' }] });
      const { box } = mountFetcher({ userId: 'u1' });
      await flush();

      const task = box.latest?.task;
      expect(task?.status).toBe('DONE');
      expect(task?.createdAt).toBe('');
      expect(task?.finishedAt).toBeNull();
    });

    it('列表接口 HTTP 非 ok → 静默降级,不影响主数据', async () => {
      dashboardFallback = () => jsonResponse(dashboardEnvelope('t1', 'DONE', 'DONE'));
      listResponse = () => notOkResponse(500);
      const { box } = mountFetcher({ userId: 'u1' });
      await flush();

      expect(box.latest?.task?.status).toBe('DONE');
      // 列表信息补齐失败:字段回落 dashboard 自身取值
      expect(box.latest?.task?.createdAt).toBe('');
      expect(box.latest?.error).toBeNull();
    });

    it('列表请求网络异常 → 静默吞掉,主请求照常', async () => {
      dashboardFallback = () => jsonResponse(dashboardEnvelope('t1', 'DONE', 'DONE'));
      listResponse = () => {
        throw new Error('network down');
      };
      const { box } = mountFetcher({ userId: 'u1' });
      await flush();

      expect(box.latest?.task?.status).toBe('DONE');
      expect(box.latest?.error).toBeNull();
    });
  });
});
