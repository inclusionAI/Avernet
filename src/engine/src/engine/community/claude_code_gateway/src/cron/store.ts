import fs from 'node:fs';
import fsp from 'node:fs/promises';
import path from 'node:path';
import type { CronJobRecord, CronRunRecord } from './types.js';

const DEFAULT_WRITE_DEBOUNCE_MS = 50;
const DEFAULT_MAX_RUNS_PER_JOB = 50;

export type CronStoreOptions = {
  /** Override debounce. Tests set this to 0 for deterministic writes. */
  writeDebounceMs?: number;
  /** Max run records kept per job; older ones are evicted on append. */
  maxRunsPerJob?: number;
  /**
   * Where to persist run records. Defaults to `<jobsPath dir>/cron-runs.json`.
   * Runs are kept in a separate file so a job-write doesn't serialize the
   * entire run-history on every update.
   */
  runsPath?: string;
};

/**
 * Cron persistence — jobs and run records, backed by two JSON files in
 * `.data/`. Mirrors `SessionStore`'s write pattern (debounced atomic writes
 * via tmp-file + rename). Tests may inject `writeDebounceMs: 0` for
 * deterministic flushes.
 *
 * Both Maps live in-memory; every mutating path marks the corresponding file
 * dirty and schedules a flush. On shutdown the caller must `await flush()`.
 */
export class CronStore {
  private readonly jobsPath: string;
  private readonly runsPath: string;
  private readonly writeDebounceMs: number;
  private readonly maxRunsPerJob: number;

  private readonly jobs = new Map<string, CronJobRecord>();
  private readonly runs = new Map<string, CronRunRecord[]>();

  private jobsDirty = false;
  private runsDirty = false;
  private jobsTimer: NodeJS.Timeout | null = null;
  private runsTimer: NodeJS.Timeout | null = null;
  private writeInFlight: Promise<void> | null = null;

  constructor(jobsPath: string, options: CronStoreOptions = {}) {
    this.jobsPath = jobsPath;
    this.runsPath = options.runsPath ?? path.join(path.dirname(jobsPath), 'cron-runs.json');
    this.writeDebounceMs = options.writeDebounceMs ?? DEFAULT_WRITE_DEBOUNCE_MS;
    this.maxRunsPerJob = options.maxRunsPerJob ?? DEFAULT_MAX_RUNS_PER_JOB;
    this.load();
  }

  private load() {
    if (fs.existsSync(this.jobsPath)) {
      try {
        const raw = fs.readFileSync(this.jobsPath, 'utf8');
        const items = JSON.parse(raw) as CronJobRecord[];
        for (const item of items) this.jobs.set(item.id, item);
      } catch {
        // Corrupted file — start empty. Subsequent flush will overwrite.
      }
    }
    if (fs.existsSync(this.runsPath)) {
      try {
        const raw = fs.readFileSync(this.runsPath, 'utf8');
        const items = JSON.parse(raw) as Record<string, CronRunRecord[]>;
        for (const [ jobId, records ] of Object.entries(items)) {
          this.runs.set(jobId, Array.isArray(records) ? records : []);
        }
      } catch {
        // Same fallback as above.
      }
    }
  }

  // ---- Jobs ----

  list(): CronJobRecord[] {
    // Stable ordering: newest first by createdAtMs.
    return [ ...this.jobs.values() ].sort((a, b) => b.createdAtMs - a.createdAtMs);
  }

  get(id: string): CronJobRecord | undefined {
    return this.jobs.get(id);
  }

  put(job: CronJobRecord): void {
    this.jobs.set(job.id, job);
    this.scheduleJobsSave();
  }

  delete(id: string): boolean {
    const had = this.jobs.delete(id);
    if (had) {
      this.scheduleJobsSave();
      // Drop run history with the job.
      if (this.runs.delete(id)) this.scheduleRunsSave();
    }
    return had;
  }

  updateState(id: string, patch: Record<string, unknown>): CronJobRecord | undefined {
    const job = this.jobs.get(id);
    if (!job) return undefined;
    job.state = { ...job.state, ...patch };
    job.updatedAtMs = Date.now();
    this.scheduleJobsSave();
    return job;
  }

  // ---- Runs ----

  appendRun(record: CronRunRecord): void {
    const list = this.runs.get(record.jobId) ?? [];
    list.unshift(record); // newest first
    if (list.length > this.maxRunsPerJob) list.length = this.maxRunsPerJob;
    this.runs.set(record.jobId, list);
    this.scheduleRunsSave();
  }

  getRuns(jobId: string, limit: number): CronRunRecord[] {
    const list = this.runs.get(jobId) ?? [];
    return list.slice(0, Math.max(0, limit));
  }

  // ---- Flush ----

  async flush(): Promise<void> {
    if (this.jobsTimer) { clearTimeout(this.jobsTimer); this.jobsTimer = null; }
    if (this.runsTimer) { clearTimeout(this.runsTimer); this.runsTimer = null; }
    await Promise.all([
      this.jobsDirty ? this.writeJobs() : Promise.resolve(),
      this.runsDirty ? this.writeRuns() : Promise.resolve(),
    ]);
    if (this.writeInFlight) await this.writeInFlight;
  }

  // ---- Internals ----

  private scheduleJobsSave() {
    this.jobsDirty = true;
    if (this.jobsTimer) return;
    if (this.writeDebounceMs === 0) {
      void this.writeJobs();
      return;
    }
    this.jobsTimer = setTimeout(() => {
      this.jobsTimer = null;
      void this.writeJobs();
    }, this.writeDebounceMs);
  }

  private scheduleRunsSave() {
    this.runsDirty = true;
    if (this.runsTimer) return;
    if (this.writeDebounceMs === 0) {
      void this.writeRuns();
      return;
    }
    this.runsTimer = setTimeout(() => {
      this.runsTimer = null;
      void this.writeRuns();
    }, this.writeDebounceMs);
  }

  private async writeJobs(): Promise<void> {
    if (!this.jobsDirty) return;
    this.jobsDirty = false;
    const snapshot = JSON.stringify([ ...this.jobs.values() ], null, 2);
    this.writeInFlight = this.writeAtomic(this.jobsPath, snapshot)
      .catch(err => { this.jobsDirty = true; throw err; })
      .finally(() => { this.writeInFlight = null; });
    await this.writeInFlight;
  }

  private async writeRuns(): Promise<void> {
    if (!this.runsDirty) return;
    this.runsDirty = false;
    const obj: Record<string, CronRunRecord[]> = {};
    for (const [ id, list ] of this.runs.entries()) obj[id] = list;
    const snapshot = JSON.stringify(obj, null, 2);
    this.writeInFlight = this.writeAtomic(this.runsPath, snapshot)
      .catch(err => { this.runsDirty = true; throw err; })
      .finally(() => { this.writeInFlight = null; });
    await this.writeInFlight;
  }

  private async writeAtomic(filePath: string, contents: string): Promise<void> {
    const dir = path.dirname(filePath);
    await fsp.mkdir(dir, { recursive: true });
    const tmp = `${filePath}.${process.pid}.${Date.now()}.${Math.random().toString(36).slice(2, 8)}.tmp`;
    await fsp.writeFile(tmp, contents, 'utf8');
    await fsp.rename(tmp, filePath);
  }
}
