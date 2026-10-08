/**
 * LLM 并发信号量 — 限制同时发出的 LLM 请求数。
 *
 * 解决问题：多个 workflow 实例并发时，embedded-agent 节点同时发出
 * LLM 请求导致 429 Too Many Requests，触发指数退避重试，使并发
 * 场景比串行更慢（延迟放大 2.5-4 倍）。
 *
 * 设计原则：
 * - 全局单例，跨所有 workflow 实例共享
 * - FIFO 公平调度，先进先出
 * - 可通过 application.yaml (llm.maxConcurrentCalls) 或环境变量
 *   MAX_CONCURRENT_LLM_CALLS 配置（环境变量优先）
 * - 每个许可带超时保护，防止泄漏
 * - 提供监控接口，方便诊断
 *
 * 配置优先级：环境变量 > application.yaml > 内置默认值 (3)
 *
 * @module llm-semaphore
 */

// ─── SimpleSemaphore (零依赖实现) ────────────────────────────────────────────

class SimpleSemaphore {
  private queue: Array<() => void> = [];
  private active = 0;

  constructor(private readonly max: number) {}

  async acquire(): Promise<() => void> {
    if (this.active < this.max) {
      this.active++;
      return this.createRelease();
    }
    return new Promise<() => void>((resolve) => {
      this.queue.push(() => {
        this.active++;
        resolve(this.createRelease());
      });
    });
  }

  private createRelease(): () => void {
    let released = false;
    return () => {
      if (released) return;
      released = true;
      this.active--;
      const next = this.queue.shift();
      if (next) next();
    };
  }

  get activeCount(): number {
    return this.active;
  }

  get waitingCount(): number {
    return this.queue.length;
  }

  get maxCount(): number {
    return this.max;
  }
}

// ─── 统计信息 ────────────────────────────────────────────────────────────────

export interface LlmSemaphoreStats {
  /** 当前正在执行的 LLM 请求数 */
  active: number;
  /** 当前排队等待的请求数 */
  waiting: number;
  /** 配置的最大并发数 */
  maxConcurrent: number;
  /** 历史总获取次数 */
  totalAcquired: number;
  /** 历史总排队等待时间 (ms) */
  totalWaitTimeMs: number;
  /** 历史总执行时间 (ms) */
  totalExecutionTimeMs: number;
}

// ─── 默认值与配置 ────────────────────────────────────────────────────────────

/** 默认最大并发 LLM 请求数 */
const DEFAULT_MAX_CONCURRENT = 3;

/** 许可最大持有时间 (ms)，超过后强制释放防止泄漏 */
const PERMIT_TTL_MS = 120_000; // 2 分钟

/**
 * YAML 配置中 llm.maxConcurrentCalls 的值。
 * 由 configureLlmSemaphore() 在引擎启动时设置（从 loadConfig() 读取）。
 * 优先级低于环境变量，高于默认值。
 */
let _yamlMaxConcurrent: number | null = null;

/**
 * 从 application.yaml 配置中设置 LLM 信号量参数。
 * 由引擎入口 (src/index.ts) 在 loadConfig() 后调用。
 *
 * @param config.maxConcurrentCalls - 来自 llm.maxConcurrentCalls 的值
 */
export function configureLlmSemaphore(config: { maxConcurrentCalls: number }): void {
  if (typeof config.maxConcurrentCalls === "number" && Number.isFinite(config.maxConcurrentCalls) && config.maxConcurrentCalls > 0) {
    _yamlMaxConcurrent = config.maxConcurrentCalls;
    // 如果信号量尚未初始化，下次 getLlmSemaphore() 将使用新值
    // 如果已初始化且配置值变化了，getLlmSemaphore() 将重建信号量
    console.log(`[llm-semaphore] configured from application.yaml: maxConcurrentCalls=${config.maxConcurrentCalls}`);
  }
}

/**
 * 获取配置的最大并发数。
 *
 * 优先级：环境变量 MAX_CONCURRENT_LLM_CALLS > application.yaml llm.maxConcurrentCalls > 默认值 3
 */
function resolveMaxConcurrent(): number {
  // 1. 最高优先级：环境变量
  const env = process.env.MAX_CONCURRENT_LLM_CALLS;
  if (env) {
    const val = parseInt(env, 10);
    if (Number.isFinite(val) && val > 0) return val;
  }

  // 2. 次优先级：application.yaml 中的 llm.maxConcurrentCalls
  //    由 configureLlmSemaphore() 在引擎启动时设置
  if (_yamlMaxConcurrent !== null) {
    return _yamlMaxConcurrent;
  }

  // 3. 兜底：内置默认值
  return DEFAULT_MAX_CONCURRENT;
}

// ─── 全局信号量实例 ──────────────────────────────────────────────────────────

let _semaphore: SimpleSemaphore | null = null;
let _maxConcurrent = 0;

const _stats = {
  totalAcquired: 0,
  totalWaitTimeMs: 0,
  totalExecutionTimeMs: 0,
};

// ─── Public API ──────────────────────────────────────────────────────────────

/**
 * 获取或创建全局 LLM 信号量。
 * 支持运行时重新配置（通过环境变量变化）。
 */
export function getLlmSemaphore(): SimpleSemaphore {
  const maxConcurrent = resolveMaxConcurrent();
  if (_semaphore === null || _maxConcurrent !== maxConcurrent) {
    _semaphore = new SimpleSemaphore(maxConcurrent);
    _maxConcurrent = maxConcurrent;
    console.log(
      `[llm-semaphore] initialized: maxConcurrent=${maxConcurrent} ` +
      `(source: ${process.env.MAX_CONCURRENT_LLM_CALLS ? "env var" : "yaml/default"}, ` +
      `MAX_CONCURRENT_LLM_CALLS=${process.env.MAX_CONCURRENT_LLM_CALLS ?? "unset"})`,
    );
  }
  return _semaphore;
}

/**
 * 获取 LLM 许可。在信号量可用前会阻塞等待。
 * 返回一个释放函数，**必须在 finally 块中调用**。
 *
 * 许可带有 TTL 保护：如果持有超过 PERMIT_TTL_MS (2分钟) 未释放，
 * 将自动释放并打印泄漏警告，防止异常路径导致信号量耗尽。
 *
 * @param context - 用于日志的上下文信息（flowId、nodeId 等）
 * @returns 释放函数
 *
 * @example
 * ```typescript
 * const release = await acquireLlmPermit({ flowId: 'flow_abc', nodeId: 'ctx-enrich' });
 * try {
 *   const result = await callLlmProvider(prompt);
 *   return result;
 * } finally {
 *   release();
 * }
 * ```
 */
export async function acquireLlmPermit(
  context: { flowId?: string; nodeId?: string; nodeTitle?: string },
): Promise<() => void> {
  const semaphore = getLlmSemaphore();
  const waitStart = Date.now();

  const logPrefix =
    `[llm-semaphore] acquire` +
    (context.flowId ? ` flowId=${context.flowId}` : "") +
    (context.nodeId ? ` node=${context.nodeId}` : "");

  const release = await semaphore.acquire();

  const waitTime = Date.now() - waitStart;
  _stats.totalAcquired++;
  _stats.totalWaitTimeMs += waitTime;

  if (waitTime > 100) {
    console.log(
      `${logPrefix}: waited ${waitTime}ms for LLM permit ` +
      `(maxConcurrent=${_maxConcurrent}, active=${semaphore.activeCount})`,
    );
  }

  // 超时保护：如果持有许可超过 TTL 未释放，强制释放以防泄漏
  let released = false;
  const acquiredAt = Date.now();
  const ttlTimer = setTimeout(() => {
    if (!released) {
      console.error(
        `[llm-semaphore] PERMIT LEAK DETECTED: flowId=${context.flowId ?? "unknown"} ` +
        `node=${context.nodeId ?? "unknown"} held permit for ${PERMIT_TTL_MS}ms — force releasing`,
      );
      released = true;
      _stats.totalExecutionTimeMs += Date.now() - acquiredAt;
      release();
    }
  }, PERMIT_TTL_MS);

  // 使用 unref() 防止定时器阻止进程退出
  if (ttlTimer.unref) {
    ttlTimer.unref();
  }

  const releaseStart = Date.now();
  return () => {
    if (released) return;
    released = true;
    clearTimeout(ttlTimer);
    _stats.totalExecutionTimeMs += Date.now() - releaseStart;
    release();
  };
}

/** 获取信号量统计信息（用于监控端点和日志） */
export function getLlmSemaphoreStats(): LlmSemaphoreStats {
  const semaphore = getLlmSemaphore();
  return {
    active: semaphore.activeCount,
    waiting: semaphore.waitingCount,
    maxConcurrent: semaphore.maxCount,
    totalAcquired: _stats.totalAcquired,
    totalWaitTimeMs: _stats.totalWaitTimeMs,
    totalExecutionTimeMs: _stats.totalExecutionTimeMs,
  };
}

/** 重置信号量（仅测试用） */
export function resetLlmSemaphore(): void {
  _semaphore = null;
  _maxConcurrent = 0;
  _yamlMaxConcurrent = null;
  _stats.totalAcquired = 0;
  _stats.totalWaitTimeMs = 0;
  _stats.totalExecutionTimeMs = 0;
}