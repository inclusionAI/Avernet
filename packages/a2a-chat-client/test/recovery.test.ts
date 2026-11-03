/**
 * B11 — RecoveryRunner 预算矩阵测试（spec stage2 §4.4 首版默认值逐条钉）。
 * ============================================================================
 *
 * 可观察性口径：预算/退避全走注入 clock（now/sleep 同步推进，sleeps 记账）；
 * backoff 抖动注入 clock.random（() => 1 钉 cap 上界、() => 0 钉 cap/2 下界）；
 * per-call 超时 race 用真实 setTimeout——按 recovery.ts 头注，不用 jest fake
 * timers（会连累注入 clock 语义），用小 perCallTimeoutMs 实测。
 *
 * 常量钉板与测试对位：
 *   · 5 attempts   ← DEFAULT_MAX_ATTEMPTS      ← (b)/(d)
 *   · 60s 总预算    ← DEFAULT_TOTAL_BUDGET_MS   ← (c)
 *   · [1,2,4,8]s caps ← DEFAULT_BACKOFF_CAPS_MS ← (b)
 *   · 10s per-call ← DEFAULT_PER_CALL_TIMEOUT_MS ← (e)
 */
import {
  RecoveryRunner,
  DEFAULT_MAX_ATTEMPTS,
  DEFAULT_TOTAL_BUDGET_MS,
  DEFAULT_BACKOFF_CAPS_MS,
  DEFAULT_PER_CALL_TIMEOUT_MS,
  type RecoveryEngine,
} from '../src/index';
import type { WireEvent } from '../src/index';
import type { WireFailure } from '../src/index';

// ─── 注入 clock 与 wire 构造件 ───────────────────────────────────────────

function makeClock(random: () => number = () => 1) {
  let t = 0;
  const sleeps: number[] = [];
  const clock = {
    now: () => t,
    sleep: async (ms: number) => { t += ms; sleeps.push(ms); },
    random,
  };
  return { clock, sleeps, time: () => t };
}

const artifactEvent = (seq: number, text: string): WireEvent => ({
  seq, type: 'artifactUpdate',
  member: { taskId: 't1', contextId: 'c1', artifact: { parts: [{ text }] }, append: true, lastChunk: false },
  metadata: { seq },
});

const finalEvent = (seq: number, state: string): WireEvent => ({
  seq, type: 'statusUpdate',
  member: { taskId: 't1', contextId: 'c1', status: { state } },
  metadata: { seq, final: 'true' },
});

async function* genOf(events: WireEvent[]): AsyncGenerator<WireEvent> {
  for (const e of events) {
    yield e;
  }
}

/** 首次 next() 即挂死的生成器（per-call 超时靶子） */
// eslint-disable-next-line require-yield -- 蓄意零 yield：首次 next() 永挂（per-call 超时靶子）
async function* hangGen(): AsyncGenerator<WireEvent> {
  await new Promise<never>(() => {});
}

/** 首次 next() 即抛 typed 失败的生成器（replay 拒绝口径） */
async function* rejectWith(err: Error): AsyncGenerator<WireEvent> {
  throw err;
  yield {} as WireEvent;   // unreachable（生成器形状要求）
}

function typed(failure: WireFailure): Error & { failure: WireFailure } {
  return Object.assign(new Error(failure.rpcError?.message ?? 'a2a failure'), { failure });
}

const refusalError = (reason: string) =>
  typed({ kind: 'rpc', rpcError: { code: -32000, message: reason, data: { reason } } });
const transportError = () => typed({ kind: 'transport' });

/** 剧本引擎：script 各面缺省 = 空/不抛；本层记账进 calls */
function scriptEngine(script: {
  replay?: () => AsyncGenerator<WireEvent>;
  getTask?: () => Promise<{ found: boolean; state?: string; latestText?: string }>;
  subscribeFuture?: () => AsyncGenerator<WireEvent>;
} = {}) {
  const calls = {
    replay: [] as Array<[string, number]>,
    getTask: [] as string[],
    subscribe: [] as string[],
  };
  const engine: RecoveryEngine = {
    async *replay(taskId, sinceSeq) {
      calls.replay.push([taskId, sinceSeq]);
      yield* (script.replay ?? (() => genOf([])))();
    },
    async getTask(taskId) {
      calls.getTask.push(taskId);
      return (script.getTask ?? (async () => ({ found: false })))();
    },
    async *subscribeFuture(taskId) {
      calls.subscribe.push(taskId);
      yield* (script.subscribeFuture ?? (() => genOf([])))();
    },
  };
  return { engine, calls };
}

const noopEvents = () => undefined;

// ─── 常量钉板（spec §4.4 首版默认值） ────────────────────────────────────

describe('具名常量（spec 首版默认）', () => {
  it('5 attempts / 60s 总预算 / [1,2,4,8]s 退避 cap / 10s per-call', () => {
    expect(DEFAULT_MAX_ATTEMPTS).toBe(5);
    expect(DEFAULT_TOTAL_BUDGET_MS).toBe(60_000);
    expect(DEFAULT_BACKOFF_CAPS_MS).toEqual([1000, 2000, 4000, 8000]);
    expect(DEFAULT_PER_CALL_TIMEOUT_MS).toBe(10_000);
  });
});

// ─── (a) 立即首 attempt + 终态成功 ───────────────────────────────────────

describe('首 attempt 立即成功（无退避）', () => {
  it('事件原样按序经 onEvent，final marker → terminal（带 state）；零 sleep', async () => {
    const { engine, calls } = scriptEngine({
      replay: () => genOf([
        artifactEvent(1, '你'),
        artifactEvent(2, '好'),
        finalEvent(3, 'TASK_STATE_COMPLETED'),
      ]),
    });
    const { clock, sleeps } = makeClock();
    const seen: WireEvent[] = [];
    const result = await new RecoveryRunner(engine, {}, clock)
      .run('t1', 7, (ev) => seen.push(ev));
    expect(result).toEqual({ outcome: 'terminal', state: 'TASK_STATE_COMPLETED' });
    // 事件透传保序保真（不合成不伪造）
    expect(seen.map((e) => [e.seq, e.type])).toEqual([
      [1, 'artifactUpdate'], [2, 'artifactUpdate'], [3, 'statusUpdate'],
    ]);
    expect(seen[2].member).toEqual({ taskId: 't1', contextId: 'c1', status: { state: 'TASK_STATE_COMPLETED' } });
    expect(calls.replay).toEqual([['t1', 7]]);   // 游标精确参数
    expect(sleeps).toEqual([]);                 // 立即，无 backoff
  });

  it('游标口径：appliedSeq=null → sinceSeq=0（永不为 undefined）', async () => {
    const { engine, calls } = scriptEngine({
      replay: () => genOf([finalEvent(0, 'TASK_STATE_CANCELED')]),
    });
    const { clock } = makeClock();
    const result = await new RecoveryRunner(engine, {}, clock)
      .run('t1', null, noopEvents);
    expect(result).toEqual({ outcome: 'terminal', state: 'TASK_STATE_CANCELED' });
    expect(calls.replay).toEqual([['t1', 0]]);
  });

  it('live 续流语义：backlog 首事件立即到 → 活跃；其后 20ms 静默期不受 per-call 截断', async () => {
    async function* liveGen(): AsyncGenerator<WireEvent> {
      yield artifactEvent(1, 't1');      // backlog 首事件即时到达 → 流活性
      for (const seq of [2, 3]) {
        await new Promise((r) => { setTimeout(r, 20); });
        yield artifactEvent(seq, `t${seq}`);
      }
      await new Promise((r) => { setTimeout(r, 20); });
      yield finalEvent(4, 'TASK_STATE_COMPLETED');
    }
    const { engine } = scriptEngine({ replay: () => liveGen() });
    const { clock } = makeClock();
    const seen: WireEvent[] = [];
    const result = await new RecoveryRunner(engine, { perCallTimeoutMs: 10 }, clock)
      .run('t1', 0, (ev) => { seen.push(ev); });
    expect(result).toEqual({ outcome: 'terminal', state: 'TASK_STATE_COMPLETED' });
    expect(seen.map((e) => e.seq)).toEqual([1, 2, 3, 4]);
  });

  it('清洁收尾且无 final（有交付）→ resumed（progress 无终态确认）', async () => {
    const { engine } = scriptEngine({ replay: () => genOf([artifactEvent(1, 'x')]) });
    const { clock, sleeps } = makeClock();
    const seen: WireEvent[] = [];
    const result = await new RecoveryRunner(engine, {}, clock)
      .run('t1', 0, (ev) => seen.push(ev));
    expect(result).toEqual({ outcome: 'resumed' });
    expect(seen).toHaveLength(1);
    expect(sleeps).toEqual([]);
  });
});

// ─── (b)(d) 退避 cap + attempt 上限 ─────────────────────────────────────

describe('退避与 attempt 上限', () => {
  it('(b) 退避序列钉 caps；抖动 [cap/2, cap]：random=1 → cap 上界', async () => {
    const { engine, calls } = scriptEngine({ replay: () => rejectWith(transportError()) });
    const { clock, sleeps } = makeClock(() => 1);
    const result = await new RecoveryRunner(engine, {}, clock)
      .run('t1', 0, noopEvents);
    expect(result).toEqual({ outcome: 'budget-exhausted' });
    // (d) DEFAULT_MAX_ATTEMPTS=5 次封顶；4 段退避按 caps 序列
    expect(calls.replay).toHaveLength(5);
    expect(sleeps).toEqual([1000, 2000, 4000, 8000]);
  });

  it('(b) random=0 → cap/2 下界', async () => {
    const { engine, calls } = scriptEngine({ replay: () => rejectWith(transportError()) });
    const { clock, sleeps } = makeClock(() => 0);
    const result = await new RecoveryRunner(engine, { maxAttempts: 3 }, clock)
      .run('t1', 0, noopEvents);
    expect(result).toEqual({ outcome: 'budget-exhausted' });
    expect(calls.replay).toHaveLength(3);
    expect(sleeps).toEqual([500, 1000]);
  });

  it('(d) caps 越界取末位（attempt>caps.length 后恒 8000）', async () => {
    const { engine, calls } = scriptEngine({ replay: () => rejectWith(transportError()) });
    const { clock, sleeps } = makeClock(() => 1);
    const result = await new RecoveryRunner(engine, { maxAttempts: 7, totalBudgetMs: 60_000 }, clock)
      .run('t1', 0, noopEvents);
    expect(result).toEqual({ outcome: 'budget-exhausted' });
    expect(calls.replay).toHaveLength(7);
    expect(sleeps).toEqual([1000, 2000, 4000, 8000, 8000, 8000]);
  });
});

// ─── (c) 60s 总预算耗尽 ──────────────────────────────────────────────────

describe('总预算耗尽（fake clock 推进）', () => {
  it('(c) 时间面先于 attempt 面耗尽：最后一段退避被剩余预算截断，此后不再发请求', async () => {
    const { engine, calls } = scriptEngine({ replay: () => rejectWith(transportError()) });
    const { clock, sleeps, time } = makeClock(() => 1);
    const result = await new RecoveryRunner(engine, { maxAttempts: 999 }, clock)
      .run('t1', 0, noopEvents);
    expect(result).toEqual({ outcome: 'budget-exhausted' });
    // 期望账本：a1 立即；caps 序列 1+2+4+8 后恒 8000；末段退避截到剩余 5000 后
    // 账平 60000 → 下一轮进门即 exhausted（"stop auto requests"，末段后零新请求）
    expect(sleeps).toEqual([1000, 2000, 4000, 8000, 8000, 8000, 8000, 8000, 8000, 5000]);
    expect(time()).toBe(DEFAULT_TOTAL_BUDGET_MS);   // 预算账不越界
    expect(calls.replay).toHaveLength(10);           // 第 11 次请求被预算门拦下
  });
});

// ─── (e) per-call 10s 超时（真实 setTimeout，小预算实测） ─────────────────

describe('per-call 超时（不越过剩余预算）', () => {
  it('(e) 挂死的 replay：超时计 attempt 失败可重试；DEFAULT_PER_CALL_TIMEOUT_MS 钉值', async () => {
    expect(DEFAULT_PER_CALL_TIMEOUT_MS).toBe(10_000);
    const { engine, calls } = scriptEngine({ replay: () => hangGen() });
    const { clock, sleeps } = makeClock(() => 1);
    const result = await new RecoveryRunner(
      engine,
      { perCallTimeoutMs: 10, backoffCapsMs: [1], maxAttempts: 2, totalBudgetMs: 10_000 },
      clock,
    ).run('t1', 0, noopEvents);
    expect(result).toEqual({ outcome: 'budget-exhausted' });   // 超时可重试 → 两次后封顶
    expect(calls.replay).toHaveLength(2);
    expect(sleeps).toEqual([1]);
  });

  it('(e) 挂死的 getTask（分诊链内）：同 1 attempt 计，超时后整链重来', async () => {
    const { engine, calls } = scriptEngine({
      replay: () => rejectWith(refusalError('cache_limit')),
      getTask: () => new Promise(() => { /* 挂死 */ }),      // 挂死
    });
    const { clock, sleeps } = makeClock(() => 1);
    const result = await new RecoveryRunner(
      engine,
      { perCallTimeoutMs: 10, backoffCapsMs: [1], maxAttempts: 2, totalBudgetMs: 10_000 },
      clock,
    ).run('t1', 0, noopEvents);
    expect(result).toEqual({ outcome: 'budget-exhausted' });
    expect(calls.replay).toHaveLength(2);
    expect(calls.getTask).toEqual(['t1', 't1']);          // GetTask→Subscribe 链整链计 1 attempt
    expect(calls.subscribe).toEqual([]);                  // 挂死在 GetTask，未到订阅
    expect(sleeps).toEqual([1]);
  });

  it('剩余预算 < per-call：调用等待被 min(剩余) 截短（budget 先死）', async () => {
    const { engine, calls } = scriptEngine({ replay: () => hangGen() });
    const { clock } = makeClock(() => 1);
    const result = await new RecoveryRunner(
      engine,
      { perCallTimeoutMs: 10, backoffCapsMs: [0], maxAttempts: 999, totalBudgetMs: 0 },
      clock,
    ).run('t1', 0, noopEvents);
    // totalBudgetMs=0：进门即 budget-exhausted，零引擎调用（"stop auto requests"）
    expect(result).toEqual({ outcome: 'budget-exhausted' });
    expect(calls.replay).toEqual([]);
  });
});

// ─── (f)(g) 拒绝分诊：GetTask 三态 ───────────────────────────────────────

describe('拒绝分诊（cache_limit / not_found_or_expired → GetTask）', () => {
  it('(f) cache_limit + GetTask 终态 → resumed-incomplete（state/latestText 随行）', async () => {
    const { engine, calls } = scriptEngine({
      replay: () => rejectWith(refusalError('cache_limit')),
      getTask: async () => ({ found: true, state: 'TASK_STATE_COMPLETED', latestText: '你好' }),
    });
    const { clock, sleeps } = makeClock();
    const result = await new RecoveryRunner(engine, {}, clock)
      .run('t1', 3, noopEvents);
    expect(result).toEqual({
      outcome: 'resumed-incomplete',
      state: 'TASK_STATE_COMPLETED',
      latestText: '你好',
    });
    expect(calls.replay).toEqual([['t1', 3]]);
    expect(calls.getTask).toEqual(['t1']);
    expect(calls.subscribe).toEqual([]);
    expect(sleeps).toEqual([]);   // 分诊成功不退避
  });

  it('(g) not_found_or_expired + GetTask found:false → unknown', async () => {
    const { engine, calls } = scriptEngine({
      replay: () => rejectWith(refusalError('not_found_or_expired')),
      getTask: async () => ({ found: false }),
    });
    const { clock, sleeps } = makeClock();
    const result = await new RecoveryRunner(engine, {}, clock)
      .run('t1', 0, noopEvents);
    expect(result).toEqual({ outcome: 'unknown' });
    expect(calls.getTask).toEqual(['t1']);
    expect(sleeps).toEqual([]);
  });

  it('非拒绝 rpc typed 失败 → 不进分诊，直接可重试（transport 同路）', async () => {
    const { engine, calls } = scriptEngine({
      replay: () => rejectWith(typed({ kind: 'rpc', rpcError: { code: -32603, message: 'x' } })),
    });
    const { clock, sleeps } = makeClock(() => 1);
    const cfg = { backoffCapsMs: [1], maxAttempts: 2, totalBudgetMs: 10_000 };
    const result = await new RecoveryRunner(engine, cfg, clock).run('t1', 0, noopEvents);
    expect(result).toEqual({ outcome: 'budget-exhausted' });
    expect(calls.getTask).toEqual([]);      // 未进分诊链
    expect(calls.replay).toHaveLength(2);
    expect(sleeps).toEqual([1]);           // 正常退避重试
  });

  it('F4：auth typed 失败 → 非可重试终局 fatal——首 attempt 即早退，预算不烧、'
    + '原始 failure 随行（不进分诊链、零 sleep）', async () => {
    const authFailure = {
      kind: 'auth' as const,
      rpcError: { code: -32000, message: 'Missing x-iam-token',
        data: { reason: 'auth_required' } },
    };
    const { engine, calls } = scriptEngine({
      replay: () => rejectWith(typed(authFailure)),
    });
    const { clock, sleeps } = makeClock(() => 1);
    const cfg = { backoffCapsMs: [1], maxAttempts: 5, totalBudgetMs: 10_000 };
    const result = await new RecoveryRunner(engine, cfg, clock).run('t1', 0, noopEvents);
    expect(result).toEqual({ outcome: 'fatal', failure: authFailure });
    expect(calls.getTask).toEqual([]);      // auth 不进分诊链
    expect(calls.replay).toHaveLength(1);   // 一次拒绝即早退（不烧 5 attempt 预算）
    expect(sleeps).toEqual([]);             // 零退避
  });

  it('F4：protocol typed 失败同 auth 路径——fatal 早退（LOCK_CANDIDATE_KINDS 同集）', async () => {
    const protocolFailure = { kind: 'protocol' as const };
    const { engine, calls } = scriptEngine({
      replay: () => rejectWith(typed(protocolFailure)),
    });
    const { clock, sleeps } = makeClock(() => 1);
    const result = await new RecoveryRunner(engine, { maxAttempts: 5 }, clock)
      .run('t1', 0, noopEvents);
    expect(result).toEqual({ outcome: 'fatal', failure: protocolFailure });
    expect(calls.replay).toHaveLength(1);
    expect(sleeps).toEqual([]);
  });
});

// ─── (h) GetTask 活跃 → subscribeFuture 竞态分支 ──────────────────────────

describe('GetTask 活跃 → subscribe 竞态', () => {
  it('(h) 订阅静默收尾 = attempt 失败 → 退避重来 → 预算耗尽（同一预算，无分支重计）', async () => {
    const { engine, calls } = scriptEngine({
      replay: () => rejectWith(refusalError('cache_limit')),
      getTask: async () => ({ found: true, state: 'TASK_STATE_WORKING' }),
      subscribeFuture: () => genOf([]),        // 静默收尾（任务恰在间隙终结的竞态）
    });
    const { clock, sleeps } = makeClock(() => 1);
    const result = await new RecoveryRunner(engine, { totalBudgetMs: 5000 }, clock)
      .run('t1', 0, noopEvents);
    expect(result).toEqual({ outcome: 'budget-exhausted' });
    // 预算共享口径：replay/getTask/subscribe 各调用次数一致（一链一 attempt）。
    // 5000 预算：a1 即时；1000 + 2000 + (4000 截到剩余 2000) 账平 → a4 不再启动
    expect(calls.replay).toHaveLength(3);
    expect(calls.getTask).toHaveLength(3);
    expect(calls.subscribe).toHaveLength(3);
    expect(sleeps).toEqual([1000, 2000, 2000]);
  });

  it('(h) 订阅面送到 final → terminal（竞态竞得正解）', async () => {
    const { engine, calls } = scriptEngine({
      replay: () => rejectWith(refusalError('cache_limit')),
      getTask: async () => ({ found: true, state: 'TASK_STATE_WORKING' }),
      subscribeFuture: () => genOf([finalEvent(9, 'TASK_STATE_FAILED')]),
    });
    const { clock, sleeps } = makeClock();
    const seen: WireEvent[] = [];
    const result = await new RecoveryRunner(engine, {}, clock)
      .run('t1', 0, (ev) => seen.push(ev));
    expect(result).toEqual({ outcome: 'terminal', state: 'TASK_STATE_FAILED' });
    expect(seen.map((e) => e.seq)).toEqual([9]);
    expect(calls.subscribe).toEqual(['t1']);
    expect(sleeps).toEqual([]);
  });
});

// ─── 取消面（provider 代际栅栏的 runner 侧接点） ─────────────────────────

describe('isCancelled（abort/disconnect/新 request 让位）', () => {
  it('退避 sleep 后观察到取消 → cancelled（不再发下一次请求）', async () => {
    let cancelled = false;
    const { engine, calls } = scriptEngine({ replay: () => rejectWith(transportError()) });
    let t = 0;
    const sleeps: number[] = [];
    const clock = {
      now: () => t,
      sleep: async (ms: number) => { t += ms; sleeps.push(ms); cancelled = true; },
      random: () => 1,
    };
    const result = await new RecoveryRunner(engine, {}, clock)
      .run('t1', 0, noopEvents, { isCancelled: () => cancelled });
    expect(result).toEqual({ outcome: 'cancelled' });
    expect(calls.replay).toHaveLength(1);   // 退避中取消：无第二次请求
    expect(sleeps).toEqual([1000]);
  });

  it('事件间取消 → cancelled，取消后不再拉取后续事件', async () => {
    let cancelled = false;
    let pulled = 0;
    async function* twoEvents(): AsyncGenerator<WireEvent> {
      yield artifactEvent(1, 'a');
      pulled += 1;                        // 第二 yield 只在被拉取时执行
      yield artifactEvent(2, 'b');
    }
    const { engine } = scriptEngine({ replay: () => twoEvents() });
    const { clock } = makeClock();
    const seen: WireEvent[] = [];
    const result = await new RecoveryRunner(engine, {}, clock)
      .run('t1', 0, (ev) => { seen.push(ev); cancelled = true; }, { isCancelled: () => cancelled });
    expect(result).toEqual({ outcome: 'cancelled' });
    expect(seen.map((e) => e.seq)).toEqual([1]);
    expect(pulled).toBe(0);                // 消费方在首事件取消 → 第二事件从未被拉取
  });
});

// ─── (i) 事件保真与游标续接（次序/参数） ─────────────────────────────────

describe('事件保真 + 游标续接', () => {
  it('断流后重连：事件次序贯通，两次 replay 的 sinceSeq 同为 run() 入参游标', async () => {
    // attempt1 交付 seq6 后 typed 断流；attempt2 接续同一游标重连成功（游标推进
    // 归调用方账（provider consumeWireEvent）；对 runner 而言 run() 入参即游标）
    const err = transportError();
    async function* attempt1(): AsyncGenerator<WireEvent> {
      yield artifactEvent(6, 'a');
      throw err;
    }
    async function* attempt2(): AsyncGenerator<WireEvent> {
      yield artifactEvent(7, 'b');
      yield finalEvent(8, 'TASK_STATE_COMPLETED');
    }
    const calls: Array<[string, number]> = [];
    let call = 0;
    const engine: RecoveryEngine = {
      async *replay(taskId: string, sinceSeq: number) {
        call += 1;
        calls.push([taskId, sinceSeq]);
        yield* (call === 1 ? attempt1() : attempt2());
      },
      async getTask() { return { found: false }; },
      async *subscribeFuture() { /* 不达 */ },
    };
    const { clock, sleeps } = makeClock(() => 0);
    const seen: WireEvent[] = [];
    const result = await new RecoveryRunner(engine, {}, clock)
      .run('t1', 5, (ev) => seen.push(ev));
    expect(result).toEqual({ outcome: 'terminal', state: 'TASK_STATE_COMPLETED' });
    expect(calls).toEqual([['t1', 5], ['t1', 5]]);
    expect(seen.map((e) => e.seq)).toEqual([6, 7, 8]);
    expect(sleeps).toEqual([500]);    // 随机下界 cap/2
  });

  it('run() 游标口径直钉：null → 0；数字透传（参数不可为 undefined）', async () => {
    const { engine, calls } = scriptEngine({
      replay: () => genOf([finalEvent(1, 'TASK_STATE_COMPLETED')]),
    });
    const { clock } = makeClock();
    await new RecoveryRunner(engine, {}, clock).run('task-X', null, noopEvents);
    await new RecoveryRunner(engine, {}, clock).run('task-X', 42, noopEvents);
    expect(calls.replay).toEqual([['task-X', 0], ['task-X', 42]]);
  });
});