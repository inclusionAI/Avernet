/**
 * RunLogUploader — 独立的内存队列上报模块
 *
 * 将"写本地日志文件"和"上报到 run_logs 表"两条链路彻底分离：
 *   - console.log → 本地日志文件（不变）
 *   - enqueueRunLog() → 内存队列 → 每 30 秒批量上报 → run_logs 表
 *
 * 核心设计：
 *   - 固定 30 秒定时批量上报
 *   - 上报后立即清除队列（不管成功失败），防止内存无限增长
 *   - 上报结果写本地日志（console.log / console.warn）
 *   - 上报过程完全不影响工作流执行（异步、非阻塞）
 *   - 如果 apiClient 初始化失败，不创建 RunLogUploader，不积累内存队列
 */

import type { IRunLogRepository, RunLogInsert } from "../db/repositories/types.js";

const UPLOAD_INTERVAL_MS = 30_000;
const FLUSH_ALL_TIMEOUT_MS = 10_000;

export interface RunLogUploaderOptions {
  /** Maximum entries per flow (currently unused, reserved for future). */
  maxEntriesPerFlow?: number;
}

export class RunLogUploader {
  private queue: RunLogInsert[] = [];
  private timer: ReturnType<typeof setInterval> | null = null;
  private repo: IRunLogRepository;
  private seqCounter = 0;
  private uploadCount = 0;
  private lastDiagTime = 0;

  constructor(repo: IRunLogRepository, _options?: RunLogUploaderOptions) {
    this.repo = repo;
  }

  /**
   * 追加一条日志到内存队列末尾。
   * 同步操作，不阻塞，不抛异常。
   */
  enqueue(entry: RunLogInsert): void {
    if (entry.seq == null) {
      entry.seq = ++this.seqCounter;
    }
    this.queue.push(entry);
  }

  /**
   * 启动定时上报。每 30 秒自动将队列中所有条目批量发送后清除。
   */
  start(): void {
    if (this.timer) return;
    this.timer = setInterval(() => {
      this.uploadLoop().catch((err) => {
        const msg = err instanceof Error ? err.message : String(err);
        console.warn(`[RunLogUploader] uploadLoop crash: ${msg}`);
      });
    }, UPLOAD_INTERVAL_MS);
    this.timer.unref?.(); // 不阻止进程退出
    console.log(`[RunLogUploader] started: interval=${UPLOAD_INTERVAL_MS}ms`);
  }

  /**
   * 停止定时器。
   */
  shutdown(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
      console.log("[RunLogUploader] shutdown: timer stopped");
    }
  }

  /**
   * 立即触发上报，等待完成（最多 10 秒超时）。
   * 在 SIGINT/SIGTERM 信号处理中调用。
   */
  async flushAll(): Promise<void> {
    if (this.queue.length === 0) {
      console.log("[RunLogUploader] flushAll: no pending entries");
      return;
    }

    console.log(`[RunLogUploader] flushAll: flushing ${this.queue.length} pending entries...`);

    try {
      await Promise.race([
        this.uploadLoop(),
        new Promise<void>((_resolve) => {
          setTimeout(() => {
            console.warn(`[RunLogUploader] flushAll timed out after ${FLUSH_ALL_TIMEOUT_MS}ms, ${this.queue.length} entries dropped`);
            this.queue = [];
            _resolve();
          }, FLUSH_ALL_TIMEOUT_MS);
        }),
      ]);
    } catch {
      console.warn(`[RunLogUploader] flushAll error, ${this.queue.length} entries dropped`);
      this.queue = [];
    }
  }

  /**
   * 核心上报循环：取队列全部条目，调用 repo.insertBatch()，
   * 不管成功失败都清除队列，防止内存无限增长。
   */
  private async uploadLoop(): Promise<void> {
    if (this.queue.length === 0) {
      return; // 无数据，跳过
    }

    // ── Diagnostic heartbeat: log queue state every 5 minutes ──
    const now = Date.now();
    if (now - this.lastDiagTime > 300_000) {
      this.lastDiagTime = now;
      console.log(
        `[RunLogUploader] diag: queueSize=${this.queue.length} ` +
        `uploadCount=${this.uploadCount}`,
      );
    }

    // 取出当前队列全部条目，清空队列
    const batch = this.queue;
    this.queue = [];
    const sent = batch.length;

    try {
      const count = await this.repo.insertBatch(batch);
      if (count >= sent) {
        this.uploadCount += sent;
        console.log(`[RunLogUploader] upload: sent=${sent}, ok=true`);
      } else if (count > 0) {
        this.uploadCount += count;
        console.warn(
          `[RunLogUploader] upload: sent=${sent}, ok=partial, ` +
          `confirmed=${count}, dropped=${sent - count}`,
        );
      } else {
        console.warn(
          `[RunLogUploader] upload: sent=${sent}, ok=false, ` +
          `error=insertBatch returned ${count}, all ${sent} entries dropped`,
        );
      }
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : String(err);
      console.warn(
        `[RunLogUploader] upload: sent=${sent}, ok=false, error=${errMsg}, ` +
        `all ${sent} entries dropped`,
      );
    }
  }
}