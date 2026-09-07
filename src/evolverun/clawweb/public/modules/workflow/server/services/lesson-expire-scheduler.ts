/**
 * LessonExpireScheduler — proposal §7.3 / T10 stage-3 "90 天不命中 → 失效".
 *
 * Drives a single SQL UPDATE through LessonRepository.retireStale(days) on a
 * fixed interval (default: daily). The scheduler is intentionally tiny:
 *   - no cron string parsing (just setInterval)
 *   - no parallel safety — only one scheduler should run per clawweb process
 *   - logs each run's affectedRows for auditability
 *
 * The default `inactiveDays` (90) matches the proposal. Override via the
 * constructor for shorter/cleaner behaviors, but NEVER set it < 7 — that
 * would prematurely expire still-relevant lessons during the boot run.
 */
import type { LessonRepository } from "../repositories/lesson-repository.js";

export class LessonExpireScheduler {
  private timer: NodeJS.Timeout | null = null;
  private running = false;

  constructor(
    private lessonRepo: LessonRepository,
    private inactiveDays: number = 90,
    private intervalMs: number = 24 * 60 * 60 * 1000,
  ) {
    if (inactiveDays < 7) {
      throw new Error(`LessonExpireScheduler: inactiveDays=${inactiveDays} rejected — must be >= 7`);
    }
  }

  /** Start the scheduler. Optionally run an immediate `runOnce()` on start. */
  start(bootRun = true): void {
    if (this.timer) return;
    if (bootRun) {
      // Defer the boot run 30s so we don't race with migrations on a cold boot.
      const t = setTimeout(() => {
        this.runOnce().catch((err) => {
          const msg = err instanceof Error ? err.message : String(err);
          console.warn(`[lesson-expire] boot run failed: ${msg}`);
        });
      }, 30_000);
      t.unref?.();
    }
    this.timer = setInterval(() => {
      this.runOnce().catch((err) => {
        const msg = err instanceof Error ? err.message : String(err);
        console.warn(`[lesson-expire] scheduled run failed: ${msg}`);
      });
    }, this.intervalMs);
    this.timer.unref?.();
    console.log(`[lesson-expire] scheduler started (inactiveDays=${this.inactiveDays}, intervalMs=${this.intervalMs}${bootRun ? ", +boot run" : ""})`);
  }

  /** Stop the scheduler. Safe to call multiple times. */
  stop(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
      console.log("[lesson-expire] scheduler stopped");
    }
  }

  /**
   * Run one sweep: call LessonRepository.retireStale(inactiveDays). Returns the
   * number of rows retired. Re-entrant-safe — if a prior run is still in
   * flight, the call is a no-op returning 0.
   */
  async runOnce(): Promise<number> {
    if (this.running) {
      console.warn("[lesson-expire] prior run still in flight, skipping");
      return 0;
    }
    this.running = true;
    try {
      const affected = await this.lessonRepo.retireStale(this.inactiveDays);
      if (affected > 0) {
        console.log(`[lesson-expire] retired ${affected} stale lesson row(s) (cutoff ${this.inactiveDays}d inactive)`);
      }
      return affected;
    } finally {
      this.running = false;
    }
  }
}