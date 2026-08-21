/**
 * CronScheduler — polls for due triggers and fires workflows.
 *
 * Manages the poll loop, concurrency checks, missed-fire recovery,
 * and consecutive-failure auto-disable.
 */
import type { SchedulerConfig } from "../config/types.js";
import type { ExecutionMode } from "../types.js";
import type { ScheduledTrigger } from "./types.js";
import { ScheduledTriggerRepository } from "./trigger-store.js";
import { computeNextFireTime, getMissedFireTimes } from "./cron-parser.js";

// ── Reserved param keys injected by the scheduler ──
// These are always overridden in scheduler-fired params to prevent
// user-supplied trigger params from impersonating scheduler metadata.
const RESERVED_PARAM_KEYS = ["triggerSource", "scheduledTime", "triggerId"] as const;

// ── Types ──

/** Callback interface for firing a workflow. Decoupled from Controller for testability. */
export type WorkflowLauncher = (options: {
  workflowId: string;
  packId: string;
  params: Record<string, string>;
  executionMode: ExecutionMode;
  chatInjectLevel?: import("../inject-level.js").InjectLevel;
}) => Promise<string | null>;

export type CronSchedulerDeps = {
  config: SchedulerConfig;
  triggerStore: ScheduledTriggerRepository;
  /** Called to launch a workflow. Returns flowId or null on failure. */
  launchWorkflow: WorkflowLauncher;
};

// ── Scheduler ──

export class CronScheduler {
  private timer: ReturnType<typeof setInterval> | null = null;
  private running = false;
  private consecutiveFailures = new Map<string, number>();
  private inProgressFires = 0;

  constructor(private deps: CronSchedulerDeps) {}

  /** Whether the scheduler is currently running. */
  isRunning(): boolean {
    return this.running;
  }

  /** Start the scheduler poll loop. */
  async start(): Promise<void> {
    if (this.running) return;

    // Verify DB availability by attempting to list enabled triggers
    try {
      await this.deps.triggerStore.listEnabled();
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.error(`[scheduler] Failed to initialize: database unavailable — ${msg}`);
      return;
    }

    this.running = true;
    const { pollIntervalMs, missedFirePolicy } = this.deps.config;
    const activeTriggers = await this.countActiveTriggers();
    const disabledTriggers = await this.countDisabledTriggers();

    console.info(
      `[scheduler] Started: pollInterval=${Math.round(pollIntervalMs / 1000)}s, ` +
        `missedFirePolicy=${missedFirePolicy}, ` +
        `${activeTriggers} active trigger(s) loaded` +
        (disabledTriggers > 0 ? ` (${disabledTriggers} disabled)` : ""),
    );

    // Recover missed fires before starting the poll loop
    await this.recoverMissedFires();

    this.timer = setInterval(() => {
      this.poll().catch((err) => {
        const msg = err instanceof Error ? err.message : String(err);
        console.error(`[scheduler] Poll error: ${msg}`);
      });
    }, pollIntervalMs);
  }

  /** Stop the scheduler gracefully. Allows in-progress fires to complete. */
  async stop(): Promise<void> {
    if (!this.running) return;

    if (this.timer !== null) {
      clearInterval(this.timer);
      this.timer = null;
    }

    this.running = false;

    // Wait for in-progress fires to complete (up to 30s)
    const deadline = Date.now() + 30_000;
    while (this.inProgressFires > 0 && Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 200));
    }

    console.info("[scheduler] Stopped");
  }

  // ── Poll Loop ──

  /** Poll for due triggers and fire them. */
  private async poll(): Promise<void> {
    const now = Date.now();
    const dueTriggers = await this.deps.triggerStore.findDueTriggers(now);

    for (const trigger of dueTriggers) {
      await this.fireTrigger(trigger, trigger.next_fire_time ?? now);
    }
  }

  // ── Fire Logic ──

  /** Fire a single trigger. */
  private async fireTrigger(trigger: ScheduledTrigger, scheduledTime: number): Promise<void> {
    // Concurrency check
    if (!(await this.checkConcurrency(trigger))) {
      const nextFireTime = computeNextFireTime(trigger.cron_expression, trigger.timezone);
      await this.deps.triggerStore.updateFireTimes(trigger.trigger_id, trigger.last_fire_time ?? 0, nextFireTime);
      return;
    }

    this.inProgressFires++;
    try {
      // Build params with reserved keys injected/overridden
      const baseParams: Record<string, string> = trigger.params_json
        ? JSON.parse(trigger.params_json)
        : {};

      const params: Record<string, string> = {
        ...baseParams,
        triggerSource: "cron",
        scheduledTime: String(scheduledTime),
        triggerId: trigger.trigger_id,
      };

      const flowId = await this.deps.launchWorkflow({
        workflowId: trigger.workflow_id,
        packId: trigger.pack_id,
        params,
        executionMode: "private",
      });

      if (flowId) {
        // Success — reset consecutive failure count
        this.consecutiveFailures.delete(trigger.trigger_id);
        const nextFireTime = computeNextFireTime(trigger.cron_expression, trigger.timezone);
        await this.deps.triggerStore.updateFireTimes(trigger.trigger_id, scheduledTime, nextFireTime);
      } else {
        // Launch returned null (workflow not found or other error)
        await this.handleFireFailure(trigger, new Error("Workflow launch returned null"));
      }
    } catch (error) {
      await this.handleFireFailure(trigger, error instanceof Error ? error : new Error(String(error)));
    } finally {
      this.inProgressFires--;
    }
  }

  /** Handle a fire failure — track consecutive failures, auto-disable after 3. */
  private async handleFireFailure(trigger: ScheduledTrigger, error: Error): Promise<void> {
    const count = (this.consecutiveFailures.get(trigger.trigger_id) ?? 0) + 1;
    this.consecutiveFailures.set(trigger.trigger_id, count);

    console.warn(
      `[scheduler] Trigger ${trigger.trigger_id} fire failed (${count}/3): ${error.message}`,
    );

    if (count >= 3) {
      console.warn(
        `[scheduler] Trigger ${trigger.trigger_id} auto-disabled after 3 consecutive failures ` +
          `(workflow: ${trigger.workflow_id})`,
      );
      await this.deps.triggerStore.disable(trigger.trigger_id);
      this.consecutiveFailures.delete(trigger.trigger_id);
    }
    // Do NOT update last_fire_time or next_fire_time on failure — trigger stays due for next poll
  }

  // ── Concurrency ──

  /** Check if a trigger can fire based on max_concurrent. */
  private async checkConcurrency(trigger: ScheduledTrigger): Promise<boolean> {
    if (trigger.max_concurrent <= 0) return true; // 0 = no limit

    const runningCount = await this.deps.triggerStore.countRunningFlows(trigger.workflow_id);
    if (runningCount >= trigger.max_concurrent) {
      console.warn(
        `[scheduler] Trigger ${trigger.trigger_id} skipped: workflow ${trigger.workflow_id} ` +
          `has ${runningCount} running flow(s), max_concurrent=${trigger.max_concurrent}`,
      );
      return false;
    }

    return true;
  }

  // ── Missed Fire Recovery ──

  /** Recover missed fires at startup based on the configured policy. */
  private async recoverMissedFires(): Promise<void> {
    const { missedFirePolicy } = this.deps.config;
    const enabledTriggers = await this.deps.triggerStore.listEnabled();

    for (const trigger of enabledTriggers) {
      if (trigger.next_fire_time === null) continue;
      const now = Date.now();
      if (trigger.next_fire_time > now) continue; // Next fire is in the future

      try {
        const missedTimes = getMissedFireTimes(
          trigger.cron_expression,
          trigger.timezone,
          trigger.last_fire_time,
          now,
        );

        if (missedTimes.length === 0) {
          // No missed fires — just recompute next_fire_time
          const nextFireTime = computeNextFireTime(trigger.cron_expression, trigger.timezone);
          await this.deps.triggerStore.updateFireTimes(
            trigger.trigger_id,
            trigger.last_fire_time ?? 0,
            nextFireTime,
          );
          continue;
        }

        switch (missedFirePolicy) {
          case "skip": {
            console.warn(
              `[scheduler] Trigger ${trigger.trigger_id}: ${missedTimes.length} missed fire(s) skipped ` +
                `(last fire: ${trigger.last_fire_time ?? "never"})`,
            );
            const nextFireTime = computeNextFireTime(trigger.cron_expression, trigger.timezone);
            await this.deps.triggerStore.updateFireTimes(
              trigger.trigger_id,
              trigger.last_fire_time ?? 0,
              nextFireTime,
            );
            break;
          }
          case "fireLast": {
            const lastMissedTime = missedTimes[missedTimes.length - 1];
            console.warn(
              `[scheduler] Trigger ${trigger.trigger_id}: recovering last missed fire ` +
                `(${missedTimes.length} missed, firing scheduledTime=${lastMissedTime})`,
            );
            await this.fireTrigger(trigger, lastMissedTime);
            break;
          }
          case "fireAll": {
            console.warn(
              `[scheduler] Trigger ${trigger.trigger_id}: recovering ${missedTimes.length} missed fires`,
            );
            for (const missedTime of missedTimes) {
              await this.fireTrigger(trigger, missedTime);
            }
            break;
          }
        }
      } catch (error) {
        const msg = error instanceof Error ? error.message : String(error);
        console.error(
          `[scheduler] Error recovering missed fires for trigger ${trigger.trigger_id}: ${msg}`,
        );
      }
    }
  }

  // ── Helpers ──

  private async countActiveTriggers(): Promise<number> {
    const enabled = await this.deps.triggerStore.listEnabled();
    return enabled.length;
  }

  private async countDisabledTriggers(): Promise<number> {
    // Query all triggers — listEnabled gives us enabled; we need total
    // For simplicity, re-query with a broader approach
    try {
      const allRows = await this.deps.triggerStore.listEnabled();
      // This is approximate — we only have listEnabled and listByWorkflow
      // A full solution would add a listAll method; for now, return 0 for the startup log
      return 0;
    } catch {
      return 0;
    }
  }
}