import type { IDatabase } from "@avernet/clawweb-shared/server/db";

// --- Types ---

export interface OverviewRow {
  workflowCount: number;
  totalRuns: number;
  succeededCount: number;
  failedCount: number;
  runningCount: number;
  terminalCount: number;
  nonTerminalCount: number;
  avgDurationMs: number | null;
  totalTokenUsage: number;
  statusDistribution: Record<string, number>;
  prevSucceededCount: number;
  prevFailedCount: number;
  // ── 三主线(可算;缺字段者在 getOverview 里留 null)──
  completionSuccessRate: number | null;       // 长程完成率
  prevCompletionSuccessRate: number | null;
  machineDurationP50: number | null;          // 端到端完成耗时 P50(ms)
  machineDurationP95: number | null;
  prevMachineDurationP50: number | null;
  durationSampleCount: number;
  dau: number | null;                         // 近 1 天 distinct user_id
  wau: number | null;                         // 近 7 天
  prevDau: number | null;
  // ── 发布相关(MySQL only;SQLite 留 null)──
  releasedWorkflowCount: number | null;
  newReleasedThisWeek: number | null;
  monthlyReleasedCount: number | null;
  windowReleasedCount: number | null;      // 窗口 [from,to] 内 deploy 过的 distinct 工作流数(跟页面 timeRange)
}

export interface EvolutionMetricsRow {
  available: boolean;
  verificationAvailable: boolean;
  diagnosisCount: number;
  diagnosedRunCount: number;
  issueClusterCount: number;
  suggestionCount: number;
  applicationAttemptCount: number;
  applicationSucceededCount: number;
  applicationFailedCount: number;
  applicationSuccessRate: number | null;
  appliedUnverifiedCount: number | null;
  recurrenceDetectedCount: number | null;
  verifiedCount: number | null;
}

// 长程任务判定:node_count≥8 或 端到端耗时≥5min(spec §3.1)
const LONG_RUN_NODE_COUNT = 8;
const LONG_RUN_DURATION_MS = 5 * 60 * 1000;

// Keep dashboard status semantics aligned with all known ClawMind/ClawWeb writers.
// `cancelled` is the engine spelling; `canceled` and `aborted` are retained for
// historical and cross-engine rows.
const UNSUCCESSFUL_TERMINAL_STATUSES = ["failed", "aborted", "cancelled", "canceled"] as const;
const TERMINAL_STATUSES = ["succeeded", ...UNSUCCESSFUL_TERMINAL_STATUSES] as const;
const sqlStatusList = (statuses: readonly string[]) => statuses.map((status) => `'${status}'`).join(",");
const unsuccessfulTerminalSql = sqlStatusList(UNSUCCESSFUL_TERMINAL_STATUSES);
const terminalSql = sqlStatusList(TERMINAL_STATUSES);

/**
 * Historical ClawMind versions persisted an already-ms duration multiplied by
 * 1000. Use the same wall-clock guard as the runs API before any aggregation.
 */
const normalizedDurationSql = `CASE
  WHEN completed_at IS NOT NULL
   AND completed_at > started_at
   AND total_duration_ms > (completed_at - started_at) * 2000
  THEN total_duration_ms / 1000
  ELSE total_duration_ms
END`;

/** 客户端分位(跨库);sorted 升序 ms。p=0.5 / 0.95 */
function percentile(sortedAsc: number[], p: number): number | null {
  if (sortedAsc.length === 0) return null;
  if (sortedAsc.length === 1) return sortedAsc[0];
  const rank = p * (sortedAsc.length - 1);
  const lo = Math.floor(rank);
  const hi = Math.ceil(rank);
  if (lo === hi) return sortedAsc[lo];
  const frac = rank - lo;
  return Math.round(sortedAsc[lo] + (sortedAsc[hi] - sortedAsc[lo]) * frac);
}

/** MySQL/ZDAS aggregate functions are commonly returned as strings. */
function asNumber(value: unknown, fallback = 0): number {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

export interface DailyTrendPoint {
  date: string;
  runCount: number;
  succeededCount: number;
  failedCount: number;
  avgDurationMs: number | null;
  tokenUsage: number;
  // 三主线(运行质量演进图):长程完成率 / 耗时 P50 真实可算;自愈缺字段恒 null
  completionSuccessRate?: number | null;
  machineDurationP50?: number | null;
  selfHealSuccessRate?: number | null;
}

export interface DurationBucket {
  label: string;
  minMs: number;
  maxMs: number | null;
  count: number;
  percentage: number;
}

export interface TopWorkflowRow {
  workflowId: string;
  workflowTitle: string;
  runCount: number;
  succeededCount: number;
  failedCount: number;
  successRate: number | null;
  avgDurationMs: number | null;
}

export interface FailureHotspotRow {
  nodeLabel: string;
  workflowTitle: string;
  sceneName: string;
  count: number;
  sharePct: number;
  exampleError: string;
  ticketRef?: string;
}

export interface ReleaseEventRow {
  workflowId: string;
  workflowTitle: string;
  sceneName: string;
  releasedAt: number;
  releaseDurationMs: number;
  firstRuns: number;
  firstRunSuccessCount: number;
  firstRunSuccessRate: number | null;
  rolledBack: boolean;
}

export interface ReleaseEfficiencyRow {
  available: boolean;
  from: number;
  to: number;
  releaseCount: number;
  rollbackCount: number;
  rollbackRate: number | null;
  successRate: number | null;
  avgReleaseDurationMs: number | null;
  releases: ReleaseEventRow[];
}

export type MetricKey =
  | "runs"
  | "activeWorkflows"
  | "dau"
  | "releases"
  | "completionRate"
  | "successRate"
  | "machineP50"
  | "deploys"
  | "releaseSuccessRate"
  | "rollbackRate"
  | "deliveryLagHours"
  // 字段未落地(self_heal_triggered / track),恒 available:false;前端 demo 有 mock
  | "selfHealRate"
  | "onlineCompletionRate"
  | "onlineRuns"
  | "testRuns";
export type TrendGranularity = "day" | "week" | "month";

export interface MetricTrendPoint {
  bucket: string;
  value: number | null;   // null = 该桶无样本(比率类/拖期类可能出现),图上断点
}

export interface MetricTrendRow {
  available: boolean;
  metric: MetricKey;
  granularity: TrendGranularity;
  from: number;
  to: number;
  points: MetricTrendPoint[];
}

export interface WorkflowHealthRow {
  workflowId: string;
  workflowTitle: string;
  sceneName: string;
  released: boolean;
  runCount: number;
  completionSuccessRate: number | null;
  selfHealTriggeredRuns: number;
  selfHealSuccessRate: number | null;
  machineDurationP50: number | null;
  avgDurationMs: number | null;
}

export interface WorkflowHealthResult {
  available: boolean;
  from: number;
  to: number;
  workflows: WorkflowHealthRow[];
}

export interface ReleaseQualityPoint {
  bucket: string;
  releaseCount: number;
  rollbackCount: number;
  successRate: number | null;   // 该桶内 deploy 的"前 10 条 run 成功率≥0.9"占比
  rollbackRate: number | null;
}

export interface ReleaseQualityTrendRow {
  available: boolean;
  from: number;
  to: number;
  granularity: TrendGranularity;
  points: ReleaseQualityPoint[];
}

export interface WorkflowReleaseStatRow {
  workflowId: string;
  workflowTitle: string;
  createdAt: number | null;          // 工作流创建时间(workflow_specs.gmt_create)
  released: boolean;                 // deployCount > 0
  deployCount: number;
  rollbackCount: number;
  devCycleMs: number | null;         // 研发周期 = 首次部署 − 首次保存(含排期等待,参考量)
  lastDeployAt: number | null;
  firstRunSuccessRate: number | null; // 首次部署后前 10 条 run 成功率
}

export interface WorkflowReleaseStatsResult {
  available: boolean;                // false = 非 MySQL,无 deploy 历史表
  workflows: WorkflowReleaseStatRow[];
}

// --- Repository ---

export class DashboardRepository {
  constructor(private db: IDatabase) {}

  async getEvolutionMetrics(from: number, to: number): Promise<EvolutionMetricsRow> {
    const unavailable: EvolutionMetricsRow = {
      available: false,
      verificationAvailable: false,
      diagnosisCount: 0,
      diagnosedRunCount: 0,
      issueClusterCount: 0,
      suggestionCount: 0,
      applicationAttemptCount: 0,
      applicationSucceededCount: 0,
      applicationFailedCount: 0,
      applicationSuccessRate: null,
      appliedUnverifiedCount: null,
      recurrenceDetectedCount: null,
      verifiedCount: null,
    };
    try {
      const fromDb = this.db.dialect.epochToDb(from);
      const toDb = this.db.dialect.epochToDb(to);
      const [diagnoses, diagnosedRuns, issueClusters, suggestions, applications] = await Promise.all([
        this.db.query<{ cnt: number }>(
          "SELECT COUNT(*) AS cnt FROM workflow_healing_diagnoses WHERE gmt_create BETWEEN ? AND ?",
          [fromDb, toDb],
        ),
        this.db.query<{ cnt: number }>(
          "SELECT COUNT(DISTINCT flow_id) AS cnt FROM workflow_healing_diagnoses WHERE gmt_create BETWEEN ? AND ?",
          [fromDb, toDb],
        ),
        this.db.query<{ cnt: number }>(
          `SELECT COUNT(*) AS cnt FROM (
             SELECT workflow_id, failure_signature
             FROM workflow_healing_diagnoses
             WHERE gmt_create BETWEEN ? AND ?
             GROUP BY workflow_id, failure_signature
           ) issue_clusters`,
          [fromDb, toDb],
        ),
        this.db.query<{ cnt: number }>(
          "SELECT COUNT(*) AS cnt FROM workflow_healing_suggestions WHERE gmt_create BETWEEN ? AND ?",
          [fromDb, toDb],
        ),
        this.db.query<{ total: number; succeeded: number }>(
          `SELECT COUNT(*) AS total,
                  COALESCE(SUM(CASE WHEN succeeded = 1 THEN 1 ELSE 0 END), 0) AS succeeded
           FROM workflow_healing_outcomes
           WHERE action = 'suggestion_apply' AND gmt_create BETWEEN ? AND ?`,
          [fromDb, toDb],
        ),
      ]);

      let verificationAvailable = true;
      let appliedUnverifiedCount: number | null = null;
      let recurrenceDetectedCount: number | null = null;
      let verifiedCount: number | null = null;
      try {
        const [backlog, verified] = await Promise.all([
          this.db.query<{ pending: number; recurrence: number }>(
          `SELECT
             COALESCE(SUM(CASE WHEN status = 'applied_unverified' THEN 1 ELSE 0 END), 0) AS pending,
             COALESCE(SUM(CASE WHEN verification_status = 'recurrence_detected' THEN 1 ELSE 0 END), 0) AS recurrence
           FROM workflow_healing_suggestions`,
          ),
          this.db.query<{ cnt: number }>(
          `SELECT COUNT(*) AS cnt FROM workflow_healing_suggestions
           WHERE verification_status = 'verified' AND verification_checked_at BETWEEN ? AND ?`,
          [from, to],
          ),
        ]);
        appliedUnverifiedCount = asNumber(backlog[0]?.pending);
        recurrenceDetectedCount = asNumber(backlog[0]?.recurrence);
        verifiedCount = asNumber(verified[0]?.cnt);
      } catch (error) {
        verificationAvailable = false;
        const msg = error instanceof Error ? error.message : String(error);
        console.warn(`[db] DashboardRepository.getEvolutionMetrics verification fields unavailable: ${msg}`);
      }

      const total = asNumber(applications[0]?.total);
      const succeeded = asNumber(applications[0]?.succeeded);
      return {
        available: true,
        verificationAvailable,
        diagnosisCount: asNumber(diagnoses[0]?.cnt),
        diagnosedRunCount: asNumber(diagnosedRuns[0]?.cnt),
        issueClusterCount: asNumber(issueClusters[0]?.cnt),
        suggestionCount: asNumber(suggestions[0]?.cnt),
        applicationAttemptCount: total,
        applicationSucceededCount: succeeded,
        applicationFailedCount: Math.max(total - succeeded, 0),
        applicationSuccessRate: total > 0 ? succeeded / total : null,
        appliedUnverifiedCount,
        recurrenceDetectedCount,
        verifiedCount,
      };
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] DashboardRepository.getEvolutionMetrics failed: ${msg}`);
      return unavailable;
    }
  }

  async getOverview(from: number, to: number): Promise<OverviewRow> {
    const periodDuration = to - from;
    const prevFrom = from - periodDuration;
    const prevTo = from;
    const daySec = 86400;
    const dauFrom = to - daySec;          // DAU: 近 1 天
    const wauFrom = to - 7 * daySec;      // WAU: 近 7 天
    const prevDauFrom = to - 2 * daySec;  // prevDau: 前 1 天
    const prevDauTo = to - daySec;

    // 长程任务过滤片断(两库通用)
    const longRunClause = `(node_count >= ? OR (${normalizedDurationSql}) >= ?)`;
    const longRunParams = [LONG_RUN_NODE_COUNT, LONG_RUN_DURATION_MS];

    try {
      const [
        workflowRows, statusRows, prevRows, avgRows, tokenRows,
        completionCurRows, completionPrevRows,
        machineCurRows, machinePrevRows,
        dauRows, wauRows, prevDauRows,
      ] = await Promise.all([
        this.db.query<{ cnt: number }>(
          `SELECT COUNT(DISTINCT workflow_id) AS cnt FROM flow_runs WHERE started_at BETWEEN ? AND ?`,
          [from, to],
        ),
        this.db.query<{ status: string; cnt: number }>(
          `SELECT status, COUNT(*) AS cnt FROM flow_runs WHERE started_at BETWEEN ? AND ? GROUP BY status`,
          [from, to],
        ),
        this.db.query<{ succeeded_count: number; failed_count: number }>(
          `SELECT
            SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END) AS succeeded_count,
            SUM(CASE WHEN status IN (${unsuccessfulTerminalSql}) THEN 1 ELSE 0 END) AS failed_count
           FROM flow_runs WHERE started_at BETWEEN ? AND ?
             AND status IN (${terminalSql})`,
          [prevFrom, prevTo],
        ),
        this.db.query<{ avg_ms: number | null }>(
          `SELECT AVG(${normalizedDurationSql}) AS avg_ms FROM flow_runs
           WHERE status = 'succeeded' AND started_at BETWEEN ? AND ? AND total_duration_ms IS NOT NULL`,
          [from, to],
        ),
        this.db.query<{ total_tokens: number }>(
          `SELECT COALESCE(SUM(total_token_usage), 0) AS total_tokens FROM flow_runs WHERE started_at BETWEEN ? AND ?`,
          [from, to],
        ),
        // 长程完成率(当前窗口)
        this.db.query<{ s: number; t: number }>(
          `SELECT
            SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END) AS s,
            SUM(CASE WHEN status IN (${terminalSql}) THEN 1 ELSE 0 END) AS t
           FROM flow_runs WHERE ${longRunClause} AND started_at BETWEEN ? AND ?`,
          [...longRunParams, from, to],
        ),
        // 长程完成率(上一周期,用于环比)
        this.db.query<{ s: number; t: number }>(
          `SELECT
            SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END) AS s,
            SUM(CASE WHEN status IN (${terminalSql}) THEN 1 ELSE 0 END) AS t
           FROM flow_runs WHERE ${longRunClause} AND started_at BETWEEN ? AND ?`,
          [...longRunParams, prevFrom, prevTo],
        ),
        // 完成耗时原始值(当前,client-side 分位)
        this.db.query<{ d: number }>(
          `SELECT ${normalizedDurationSql} AS d FROM flow_runs
           WHERE status = 'succeeded' AND total_duration_ms IS NOT NULL AND started_at BETWEEN ? AND ?`,
          [from, to],
        ),
        // 完成耗时原始值(上一周期,取 P50 环比)
        this.db.query<{ d: number }>(
          `SELECT ${normalizedDurationSql} AS d FROM flow_runs
           WHERE status = 'succeeded' AND total_duration_ms IS NOT NULL AND started_at BETWEEN ? AND ?`,
          [prevFrom, prevTo],
        ),
        // DAU / WAU / prevDau(distinct user_id;v34 起有该列,空值不计)
        this.db.query<{ cnt: number }>(
          `SELECT COUNT(DISTINCT user_id) AS cnt FROM flow_runs
           WHERE user_id IS NOT NULL AND started_at BETWEEN ? AND ?`,
          [dauFrom, to],
        ),
        this.db.query<{ cnt: number }>(
          `SELECT COUNT(DISTINCT user_id) AS cnt FROM flow_runs
           WHERE user_id IS NOT NULL AND started_at BETWEEN ? AND ?`,
          [wauFrom, to],
        ),
        this.db.query<{ cnt: number }>(
          `SELECT COUNT(DISTINCT user_id) AS cnt FROM flow_runs
           WHERE user_id IS NOT NULL AND started_at BETWEEN ? AND ?`,
          [prevDauFrom, prevDauTo],
        ),
      ]);

      const workflowCount = asNumber(workflowRows[0]?.cnt);
      const statusDistribution: Record<string, number> = {};
      let totalRuns = 0;
      let succeededCount = 0;
      let failedCount = 0;
      let runningCount = 0;

      for (const row of statusRows) {
        const count = asNumber(row.cnt);
        statusDistribution[row.status] = count;
        totalRuns += count;
        if (row.status === "succeeded") succeededCount = count;
        if ((UNSUCCESSFUL_TERMINAL_STATUSES as readonly string[]).includes(row.status))
          failedCount += count;
        if (row.status === "running") runningCount = count;
      }

      const avgDurationMs = avgRows[0]?.avg_ms == null ? null : asNumber(avgRows[0].avg_ms);
      const totalTokenUsage = asNumber(tokenRows[0]?.total_tokens);
      const prevSucceededCount = asNumber(prevRows[0]?.succeeded_count);
      const prevFailedCount = asNumber(prevRows[0]?.failed_count);
      const terminalCount = succeededCount + failedCount;
      const nonTerminalCount = Math.max(totalRuns - terminalCount, 0);

      // 长程完成率
      const curT = asNumber(completionCurRows[0]?.t);
      const prevT = asNumber(completionPrevRows[0]?.t);
      const completionSuccessRate =
        curT > 0 ? asNumber(completionCurRows[0]?.s) / curT : null;
      const prevCompletionSuccessRate =
        prevT > 0 ? asNumber(completionPrevRows[0]?.s) / prevT : null;

      // 完成耗时 P50/P95(client-side 分位,跨库)
      const machineCur = machineCurRows.map((r) => asNumber(r.d)).sort((a, b) => a - b);
      const machinePrev = machinePrevRows.map((r) => asNumber(r.d)).sort((a, b) => a - b);
      const machineDurationP50 = percentile(machineCur, 0.5);
      const machineDurationP95 = percentile(machineCur, 0.95);
      const prevMachineDurationP50 = percentile(machinePrev, 0.5);

      const dau = dauRows[0]?.cnt == null ? null : asNumber(dauRows[0].cnt);
      const wau = wauRows[0]?.cnt == null ? null : asNumber(wauRows[0].cnt);
      const prevDau = prevDauRows[0]?.cnt == null ? null : asNumber(prevDauRows[0].cnt);

      // 发布相关:仅 MySQL/ZDAS(workflow_deploy_history 为 mysqlOnly,SQLite 库无此表)
      let releasedWorkflowCount: number | null = null;
      let newReleasedThisWeek: number | null = null;
      let monthlyReleasedCount: number | null = null;
      let windowReleasedCount: number | null = null;
      if (this.db.dbType === "mysql" || this.db.dbType === "zdas") {
        try {
          const weekFrom = to - 7 * daySec;
          const monthFrom = to - 30 * daySec;
          const [allDeploys, weekDeploys, monthDeploys, windowDeploys] = await Promise.all([
            this.db.query<{ cnt: number }>(
              `SELECT COUNT(DISTINCT workflow_id) AS cnt FROM workflow_deploy_history WHERE action = 'deploy'`,
            ),
            this.db.query<{ cnt: number }>(
              `SELECT COUNT(DISTINCT workflow_id) AS cnt FROM workflow_deploy_history
               WHERE action = 'deploy' AND gmt_create >= FROM_UNIXTIME(?)`,
              [weekFrom],
            ),
            this.db.query<{ cnt: number }>(
              `SELECT COUNT(DISTINCT workflow_id) AS cnt FROM workflow_deploy_history
               WHERE action = 'deploy' AND gmt_create >= FROM_UNIXTIME(?)`,
              [monthFrom],
            ),
            this.db.query<{ cnt: number }>(
              `SELECT COUNT(DISTINCT workflow_id) AS cnt FROM workflow_deploy_history
               WHERE action = 'deploy' AND gmt_create BETWEEN FROM_UNIXTIME(?) AND FROM_UNIXTIME(?)`,
              [from, to],
            ),
          ]);
          releasedWorkflowCount = allDeploys[0]?.cnt ?? 0;
          newReleasedThisWeek = weekDeploys[0]?.cnt ?? 0;
          monthlyReleasedCount = monthDeploys[0]?.cnt ?? 0;
          windowReleasedCount = windowDeploys[0]?.cnt ?? 0;
        } catch (deployErr) {
          // deploy_history 未迁移或不可达 → 留 null,前端显示暂不可算
          const m = deployErr instanceof Error ? deployErr.message : String(deployErr);
          console.warn(`[db] getOverview deploy counts failed: ${m}`);
        }
      }

      return {
        workflowCount,
        totalRuns,
        succeededCount,
        failedCount,
        runningCount,
        terminalCount,
        nonTerminalCount,
        avgDurationMs,
        totalTokenUsage,
        statusDistribution,
        prevSucceededCount,
        prevFailedCount,
        completionSuccessRate,
        prevCompletionSuccessRate,
        machineDurationP50,
        machineDurationP95,
        prevMachineDurationP50,
        durationSampleCount: machineCur.length,
        dau,
        wau,
        prevDau,
        releasedWorkflowCount,
        newReleasedThisWeek,
        monthlyReleasedCount,
        windowReleasedCount,
      };
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] DashboardRepository.getOverview failed: ${msg}`);
      return {
        workflowCount: 0,
        totalRuns: 0,
        succeededCount: 0,
        failedCount: 0,
        runningCount: 0,
        terminalCount: 0,
        nonTerminalCount: 0,
        avgDurationMs: null,
        totalTokenUsage: 0,
        statusDistribution: {},
        prevSucceededCount: 0,
        prevFailedCount: 0,
        completionSuccessRate: null,
        prevCompletionSuccessRate: null,
        machineDurationP50: null,
        machineDurationP95: null,
        prevMachineDurationP50: null,
        durationSampleCount: 0,
        dau: null,
        wau: null,
        prevDau: null,
        releasedWorkflowCount: null,
        newReleasedThisWeek: null,
        monthlyReleasedCount: null,
        windowReleasedCount: null,
      };
    }
  }

  async getDailyTrend(from: number, to: number): Promise<DailyTrendPoint[]> {
    const dayExpr =
      this.db.dbType === "mysql" || this.db.dbType === "zdas"
        ? "DATE_FORMAT(FROM_UNIXTIME(started_at), '%Y-%m-%d')"
        : "date(started_at, 'unixepoch')";

    try {
      const [rows, durRows] = await Promise.all([
        this.db.query<{
          day: string;
          run_count: number;
          succeeded_count: number;
          failed_count: number;
          avg_duration_ms: number | null;
          token_usage: number;
          lr_s: number;
          lr_t: number;
        }>(
          `SELECT
            ${dayExpr} AS day,
            COUNT(*) AS run_count,
            SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END) AS succeeded_count,
            SUM(CASE WHEN status IN (${unsuccessfulTerminalSql}) THEN 1 ELSE 0 END) AS failed_count,
            AVG(CASE WHEN status = 'succeeded' THEN ${normalizedDurationSql} END) AS avg_duration_ms,
            COALESCE(SUM(total_token_usage), 0) AS token_usage,
            SUM(CASE WHEN (node_count >= ${LONG_RUN_NODE_COUNT} OR (${normalizedDurationSql}) >= ${LONG_RUN_DURATION_MS}) AND status = 'succeeded' THEN 1 ELSE 0 END) AS lr_s,
            SUM(CASE WHEN (node_count >= ${LONG_RUN_NODE_COUNT} OR (${normalizedDurationSql}) >= ${LONG_RUN_DURATION_MS}) AND status IN (${terminalSql}) THEN 1 ELSE 0 END) AS lr_t
           FROM flow_runs
           WHERE started_at BETWEEN ? AND ? AND started_at IS NOT NULL
           GROUP BY ${dayExpr}
           ORDER BY day ASC`,
          [from, to],
        ),
        // per-day 耗时 P50:原始值拉出,client 分位(跨库)
        this.db.query<{ day: string; d: number }>(
          `SELECT ${dayExpr} AS day, ${normalizedDurationSql} AS d FROM flow_runs
           WHERE status = 'succeeded' AND total_duration_ms IS NOT NULL
             AND started_at BETWEEN ? AND ? AND started_at IS NOT NULL`,
          [from, to],
        ),
      ]);

      const durByDay = new Map<string, number[]>();
      for (const r of durRows) {
        let arr = durByDay.get(r.day);
        if (!arr) { arr = []; durByDay.set(r.day, arr); }
        arr.push(asNumber(r.d));
      }

      return rows.map((r) => ({
        date: r.day,
        runCount: asNumber(r.run_count),
        succeededCount: asNumber(r.succeeded_count),
        failedCount: asNumber(r.failed_count),
        avgDurationMs: r.avg_duration_ms == null ? null : asNumber(r.avg_duration_ms),
        tokenUsage: asNumber(r.token_usage),
        completionSuccessRate: asNumber(r.lr_t) > 0 ? asNumber(r.lr_s) / asNumber(r.lr_t) : null,
        machineDurationP50: percentile((durByDay.get(r.day) ?? []).slice().sort((a, b) => a - b), 0.5),
        // 自愈缺 self_heal_triggered 字段,恒 null(图上断线)
        selfHealSuccessRate: null,
      }));
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] DashboardRepository.getDailyTrend failed: ${msg}`);
      return [];
    }
  }

  async getDurationDistribution(
    from: number,
    to: number,
  ): Promise<DurationBucket[]> {
    const buckets = [
      { label: "<1s", minMs: 0, maxMs: 1000 },
      { label: "1-5s", minMs: 1000, maxMs: 5000 },
      { label: "5-30s", minMs: 5000, maxMs: 30000 },
      { label: "30s-5m", minMs: 30000, maxMs: 300000 },
      { label: ">5m", minMs: 300000, maxMs: null },
    ];

    try {
      // Fetch raw durations and bin client-side to avoid string interpolation in SQL
      const rows = await this.db.query<{ duration_ms: number }>(
        `SELECT ${normalizedDurationSql} AS duration_ms FROM flow_runs
         WHERE started_at BETWEEN ? AND ? AND total_duration_ms IS NOT NULL`,
        [from, to],
      );

      const counts = buckets.map(() => 0);
      for (const row of rows) {
        const ms = row.duration_ms;
        for (let i = 0; i < buckets.length; i++) {
          const b = buckets[i];
          if (ms >= b.minMs && (b.maxMs === null || ms < b.maxMs)) {
            counts[i]++;
            break;
          }
        }
      }
      const total = counts.reduce((sum, c) => sum + c, 0);

      return buckets.map((b, i) => ({
        label: b.label,
        minMs: b.minMs,
        maxMs: b.maxMs,
        count: counts[i],
        percentage: total > 0 ? counts[i] / total : 0,
      }));
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(
        `[db] DashboardRepository.getDurationDistribution failed: ${msg}`,
      );
      return buckets.map((b) => ({
        label: b.label,
        minMs: b.minMs,
        maxMs: b.maxMs,
        count: 0,
        percentage: 0,
      }));
    }
  }

  async getTopWorkflows(
    from: number,
    to: number,
    limit: number,
  ): Promise<TopWorkflowRow[]> {
    try {
      const rows = await this.db.query<{
        workflow_id: string;
        workflow_title: string;
        run_count: number;
        succeeded_count: number;
        failed_count: number;
        avg_duration_ms: number | null;
      }>(
        `SELECT
          workflow_id,
          MAX(workflow_title) AS workflow_title,
          COUNT(*) AS run_count,
          SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END) AS succeeded_count,
          SUM(CASE WHEN status IN (${unsuccessfulTerminalSql}) THEN 1 ELSE 0 END) AS failed_count,
          AVG(CASE WHEN status = 'succeeded' THEN ${normalizedDurationSql} END) AS avg_duration_ms
         FROM flow_runs
         WHERE started_at BETWEEN ? AND ?
         GROUP BY workflow_id
         ORDER BY run_count DESC
         LIMIT ?`,
        [from, to, limit],
      );

      return rows.map((r) => {
        const completed = r.succeeded_count + r.failed_count;
        return {
          workflowId: r.workflow_id,
          workflowTitle: r.workflow_title ?? r.workflow_id,
          runCount: r.run_count,
          succeededCount: r.succeeded_count,
          failedCount: r.failed_count,
          successRate: completed > 0 ? r.succeeded_count / completed : null,
          avgDurationMs: r.avg_duration_ms,
        };
      });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(
        `[db] DashboardRepository.getTopWorkflows failed: ${msg}`,
      );
      return [];
    }
  }

  async getRunningCount(): Promise<number> {
    try {
      const rows = await this.db.query<{ cnt: number }>(
        `SELECT COUNT(*) AS cnt FROM flow_runs WHERE status = 'running'`,
      );
      return rows[0]?.cnt ?? 0;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(
        `[db] DashboardRepository.getRunningCount failed: ${msg}`,
      );
      return 0;
    }
  }

  /**
   * 失败归因 Top:node_executions 中 status='failed' 的 error_text 聚类。
   * 跨库:SQLite SUBSTR / MySQL SUBSTRING。sceneName 暂空(无场景实体)。
   */
  async getFailureHotspots(
    from: number,
    to: number,
    limit: number,
  ): Promise<FailureHotspotRow[]> {
    // Prefer diagnosis signatures: they include anomaly/degradation findings
    // even when the overall run eventually succeeded.
    try {
      const fromDb = this.db.dialect.epochToDb(from);
      const toDb = this.db.dialect.epochToDb(to);
      const diagnosisRows = await this.db.query<{
        failure_signature: string;
        node_id: string | null;
        workflow_id: string | null;
        workflow_title: string | null;
        example_error: string | null;
        cnt: number;
      }>(
        `SELECT
          d.failure_signature,
          MAX(COALESCE(d.weak_node_id, d.node_id)) AS node_id,
          d.workflow_id,
          MAX(fr.workflow_title) AS workflow_title,
          MAX(COALESCE(d.error_text, d.failure_mode)) AS example_error,
          COUNT(*) AS cnt
         FROM workflow_healing_diagnoses d
         LEFT JOIN (
           SELECT workflow_id, MAX(workflow_title) AS workflow_title
           FROM flow_runs GROUP BY workflow_id
         ) fr ON fr.workflow_id = d.workflow_id
         WHERE d.gmt_create BETWEEN ? AND ?
         GROUP BY d.workflow_id, d.failure_signature
         ORDER BY cnt DESC
         LIMIT ?`,
        [fromDb, toDb, limit],
      );
      if (diagnosisRows.length > 0) {
        const total = diagnosisRows.reduce((sum, row) => sum + asNumber(row.cnt), 0);
        return diagnosisRows.map((row) => ({
          nodeLabel: row.node_id || "(unknown)",
          workflowTitle: row.workflow_title || row.workflow_id || "(unknown)",
          sceneName: "",
          count: asNumber(row.cnt),
          sharePct: total > 0 ? Math.round((asNumber(row.cnt) / total) * 1000) / 10 : 0,
          exampleError: (row.example_error || row.failure_signature).slice(0, 200),
        }));
      }
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] diagnosis hotspots unavailable, falling back to failed nodes: ${msg}`);
    }

    const errExpr =
      this.db.dbType === "mysql" || this.db.dbType === "zdas"
        ? "SUBSTRING(error_text, 1, 120)"
        : "SUBSTR(error_text, 1, 120)";
    try {
      const rows = await this.db.query<{
        err_key: string;
        node_id: string | null;
        node_title: string | null;
        workflow_id: string | null;
        workflow_title: string | null;
        example_error: string | null;
        cnt: number;
      }>(
        `SELECT
          ${errExpr} AS err_key,
          MAX(ne.node_id) AS node_id,
          MAX(ne.node_title) AS node_title,
          MAX(ne.workflow_id) AS workflow_id,
          MAX(fr.workflow_title) AS workflow_title,
          MAX(ne.error_text) AS example_error,
          COUNT(*) AS cnt
         FROM node_executions ne
         LEFT JOIN (
           SELECT workflow_id, MAX(workflow_title) AS workflow_title
           FROM flow_runs GROUP BY workflow_id
         ) fr ON fr.workflow_id = ne.workflow_id
         WHERE ne.status = 'failed'
           AND ne.error_text IS NOT NULL
           AND ne.error_text <> ''
           AND ne.started_at BETWEEN ? AND ?
         GROUP BY ${errExpr}
         ORDER BY cnt DESC
         LIMIT ?`,
        [from, to, limit],
      );

      const total = rows.reduce((s, r) => s + asNumber(r.cnt), 0);
      return rows.map((r) => ({
        nodeLabel: r.node_title || r.node_id || "(unknown)",
        workflowTitle: r.workflow_title || r.workflow_id || "(unknown)",
        sceneName: "",
        count: asNumber(r.cnt),
        sharePct: total > 0 ? Math.round((asNumber(r.cnt) / total) * 1000) / 10 : 0,
        exampleError: (r.example_error ?? "").slice(0, 200),
      }));
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] DashboardRepository.getFailureHotspots failed: ${msg}`);
      return [];
    }
  }

  /**
   * 发布效能:从 workflow_deploy_history(mysqlOnly)算发布/回滚/交付拖期/发布成功率。
   * SQLite 库无此表 → 返 available:false(前端显示"暂不可算")。
   * 交付拖期 = deploy.gmt_create − 最近一次 save(edit)的 gmt_create。
   * 发布成功率 = deploy 后该工作流前 10 条 run 成功率 ≥0.9 的 deploy 行占比(spec §5.1,样本小)。
   */
  async getReleaseEfficiency(
    from: number,
    to: number,
  ): Promise<ReleaseEfficiencyRow> {
    const unavailable: ReleaseEfficiencyRow = {
      available: false,
      from,
      to,
      releaseCount: 0,
      rollbackCount: 0,
      rollbackRate: null,
      successRate: null,
      avgReleaseDurationMs: null,
      releases: [],
    };
    if (this.db.dbType !== "mysql") return unavailable;

    try {
      const rows = await this.db.query<{
        workflow_id: string;
        action: string;
        deploy_ts: number;
      }>(
        `SELECT workflow_id, action, UNIX_TIMESTAMP(gmt_create) AS deploy_ts
         FROM workflow_deploy_history
         WHERE action IN ('deploy', 'rollback')
           AND gmt_create BETWEEN FROM_UNIXTIME(?) AND FROM_UNIXTIME(?)
         ORDER BY gmt_create DESC
         LIMIT 200`,
        [from, to],
      );

      const deploys = rows.filter((r) => r.action === "deploy");
      const rollbackCount = rows.filter((r) => r.action === "rollback").length;
      const releaseCount = deploys.length;
      const rollbackRate = releaseCount > 0 ? rollbackCount / releaseCount : null;

      const releases: ReleaseEventRow[] = [];
      let lagSum = 0;
      let lagN = 0;
      let successN = 0;

      for (const d of deploys) {
        // 工作流标题:workflow_specs.title(mysqlOnly v32);取不到回退用 id
        const titleRows = await this.db.query<{ title: string | null }>(
          `SELECT title FROM workflow_specs WHERE workflow_id = ?`,
          [d.workflow_id],
        );
        const workflowTitle = titleRows[0]?.title || d.workflow_id;

        // 最近一次 save(edit)→ 交付拖期
        const editRows = await this.db.query<{ last_edit: number | null }>(
          `SELECT UNIX_TIMESTAMP(MAX(gmt_create)) AS last_edit
           FROM workflow_deploy_history
           WHERE workflow_id = ? AND action = 'edit' AND gmt_create < FROM_UNIXTIME(?)`,
          [d.workflow_id, d.deploy_ts],
        );
        const lastEdit = editRows[0]?.last_edit ?? null;
        const releaseDurationMs =
          lastEdit != null ? Math.max(0, (d.deploy_ts - lastEdit) * 1000) : 0;
        if (lastEdit != null) {
          lagSum += releaseDurationMs;
          lagN++;
        }

        // deploy 后该工作流前 10 条 run
        const runRows = await this.db.query<{ status: string }>(
          `SELECT status FROM flow_runs
           WHERE workflow_id = ? AND started_at > ?
           ORDER BY started_at ASC LIMIT 10`,
          [d.workflow_id, d.deploy_ts],
        );
        const firstRuns = runRows.length;
        const firstRunSuccessCount = runRows.filter(
          (r) => r.status === "succeeded",
        ).length;
        const firstRunSuccessRate =
          firstRuns > 0 ? firstRunSuccessCount / firstRuns : null;
        if (firstRunSuccessRate != null && firstRunSuccessRate >= 0.9) successN++;

        // 该 deploy 之后是否被回滚
        const rbRows = await this.db.query<{ cnt: number }>(
          `SELECT COUNT(*) AS cnt FROM workflow_deploy_history
           WHERE workflow_id = ? AND action = 'rollback'
             AND gmt_create >= FROM_UNIXTIME(?)`,
          [d.workflow_id, d.deploy_ts],
        );
        const rolledBack = (rbRows[0]?.cnt ?? 0) > 0;

        releases.push({
          workflowId: d.workflow_id,
          workflowTitle,
          sceneName: "",
          releasedAt: d.deploy_ts,
          releaseDurationMs,
          firstRuns,
          firstRunSuccessCount,
          firstRunSuccessRate,
          rolledBack,
        });
      }

      const successRate = releaseCount > 0 ? successN / releaseCount : null;
      const avgReleaseDurationMs = lagN > 0 ? Math.round(lagSum / lagN) : null;

      return {
        available: true,
        from,
        to,
        releaseCount,
        rollbackCount,
        rollbackRate,
        successRate,
        avgReleaseDurationMs,
        releases,
      };
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] DashboardRepository.getReleaseEfficiency failed: ${msg}`);
      return unavailable;
    }
  }

  /**
   * L2 工作流健康表:per-workflow 三主线(完成率/自愈/速度)。
   * - 完成率 = succeeded / terminal,与大盘主指标保持同一人群和时间口径。
   * - 速度 P50 = 该工作流 succeeded run 的 total_duration_ms 分位(client-side,跨库)。
   * - released(线上/测试)= 该 workflow_id 是否在 workflow_deploy_history 有 deploy 行(MySQL;SQLite 一律 false)。
   * - 自愈:缺字段,selfHealTriggeredRuns=0 / selfHealSuccessRate=null(前端显示 —)。
   * - sceneName:无场景实体表,留空。
   */
  async getWorkflowHealth(
    from: number,
    to: number,
  ): Promise<WorkflowHealthResult> {
    try {
      const rows = await this.db.query<{
        workflow_id: string;
        workflow_title: string | null;
        run_count: number;
        succeeded_count: number;
        terminal_count: number;
        avg_ms: number | null;
      }>(
        `SELECT
          workflow_id,
          MAX(workflow_title) AS workflow_title,
          COUNT(*) AS run_count,
          SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END) AS succeeded_count,
          SUM(CASE WHEN status IN (${terminalSql}) THEN 1 ELSE 0 END) AS terminal_count,
          AVG(CASE WHEN status = 'succeeded' THEN ${normalizedDurationSql} END) AS avg_ms
         FROM flow_runs
         WHERE started_at BETWEEN ? AND ? AND started_at IS NOT NULL
         GROUP BY workflow_id
         ORDER BY run_count DESC
         LIMIT 200`,
        [from, to],
      );

      // 每工作流 succeeded 耗时原始值 → client-side P50(一次查询,TS 分组)
      const durRows = await this.db.query<{ workflow_id: string; d: number }>(
        `SELECT workflow_id, ${normalizedDurationSql} AS d FROM flow_runs
         WHERE status = 'succeeded' AND total_duration_ms IS NOT NULL AND started_at BETWEEN ? AND ?`,
        [from, to],
      );
      const byWf = new Map<string, number[]>();
      for (const r of durRows) {
        let arr = byWf.get(r.workflow_id);
        if (!arr) { arr = []; byWf.set(r.workflow_id, arr); }
        arr.push(asNumber(r.d));
      }

      // 线上集合(MySQL/ZDAS only);SQLite 取不到 → 全部视为测试
      let releasedSet: Set<string> | null = null;
      if (this.db.dbType === "mysql" || this.db.dbType === "zdas") {
        try {
          const relRows = await this.db.query<{ workflow_id: string }>(
            `SELECT DISTINCT workflow_id FROM workflow_deploy_history WHERE action = 'deploy'`,
          );
          releasedSet = new Set(relRows.map((r) => r.workflow_id));
        } catch (relErr) {
          const m = relErr instanceof Error ? relErr.message : String(relErr);
          console.warn(`[db] getWorkflowHealth released-set failed: ${m}`);
        }
      }

      const workflows: WorkflowHealthRow[] = rows.map((r) => {
        const terminalCount = asNumber(r.terminal_count);
        const completion = terminalCount > 0 ? asNumber(r.succeeded_count) / terminalCount : null;
        const durs = (byWf.get(r.workflow_id) ?? []).slice().sort((a, b) => a - b);
        return {
          workflowId: r.workflow_id,
          workflowTitle: r.workflow_title ?? r.workflow_id,
          sceneName: "",
          released: releasedSet ? releasedSet.has(r.workflow_id) : false,
          runCount: asNumber(r.run_count),
          completionSuccessRate: completion,
          selfHealTriggeredRuns: 0,
          selfHealSuccessRate: null,
          machineDurationP50: percentile(durs, 0.5),
          avgDurationMs: r.avg_ms == null ? null : asNumber(r.avg_ms),
        };
      });

      return { available: true, from, to, workflows };
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] DashboardRepository.getWorkflowHealth failed: ${msg}`);
      return { available: false, from, to, workflows: [] };
    }
  }

  /**
   * 发布事件按桶聚合(MySQL only):每桶 {deploy 数, distinct 工作流, 回滚数, 成功样本数, 拖期合计/样本数}。
   * 发布成功率口径 = deploy 后前 10 条 run 成功率≥0.9;交付拖期 = deploy − 最近一次 save(edit)。
   * 供 getReleaseQualityTrend 与 getMetricTrend 的 release 系 metric 共用。
   */
  private async releaseBuckets(
    from: number,
    to: number,
    granularity: TrendGranularity,
  ): Promise<Map<string, {
    releaseCount: number;
    distinctWf: Set<string>;
    rollbackCount: number;
    successN: number;
    lagSumMs: number;
    lagN: number;
  }>> {
    const bExpr =
      granularity === "day"
        ? "DATE_FORMAT(gmt_create, '%Y-%m-%d')"
        : granularity === "week"
          ? "DATE_FORMAT(gmt_create, '%Y-%u')"
          : "DATE_FORMAT(gmt_create, '%Y-%m')";

    const rows = await this.db.query<{
      workflow_id: string;
      action: string;
      b: string;
      ts: number;
    }>(
      `SELECT workflow_id, action, ${bExpr} AS b, UNIX_TIMESTAMP(gmt_create) AS ts
       FROM workflow_deploy_history
       WHERE action IN ('deploy', 'rollback')
         AND gmt_create BETWEEN FROM_UNIXTIME(?) AND FROM_UNIXTIME(?)
       ORDER BY gmt_create DESC
       LIMIT 300`,
      [from, to],
    );

    const buckets = new Map<string, {
      releaseCount: number;
      distinctWf: Set<string>;
      rollbackCount: number;
      successN: number;
      lagSumMs: number;
      lagN: number;
    }>();
    const get = (b: string) => {
      let e = buckets.get(b);
      if (!e) {
        e = { releaseCount: 0, distinctWf: new Set(), rollbackCount: 0, successN: 0, lagSumMs: 0, lagN: 0 };
        buckets.set(b, e);
      }
      return e;
    };

    const deploys = rows.filter((r) => r.action === "deploy");
    for (const d of deploys) {
      const e = get(d.b);
      e.releaseCount += 1;
      e.distinctWf.add(d.workflow_id);

      // 该 deploy 后该工作流前 10 条 run → 发布成功率样本
      const runRows = await this.db.query<{ status: string }>(
        `SELECT status FROM flow_runs
         WHERE workflow_id = ? AND started_at > ?
         ORDER BY started_at ASC LIMIT 10`,
        [d.workflow_id, d.ts],
      );
      const fr = runRows.length;
      const fs = runRows.filter((r) => r.status === "succeeded").length;
      const rate = fr > 0 ? fs / fr : null;
      if (rate != null && rate >= 0.9) e.successN += 1;

      // 最近一次 save(edit)→ 交付拖期
      const editRows = await this.db.query<{ last_edit: number | null }>(
        `SELECT UNIX_TIMESTAMP(MAX(gmt_create)) AS last_edit
         FROM workflow_deploy_history
         WHERE workflow_id = ? AND action = 'edit' AND gmt_create < FROM_UNIXTIME(?)`,
        [d.workflow_id, d.ts],
      );
      const lastEdit = editRows[0]?.last_edit ?? null;
      if (lastEdit != null) {
        e.lagSumMs += Math.max(0, (d.ts - lastEdit) * 1000);
        e.lagN += 1;
      }
    }
    for (const r of rows) {
      if (r.action === "rollback") get(r.b).rollbackCount += 1;
    }
    return buckets;
  }

  /**
   * 发布质量趋势:按 day/week/month 分桶,每桶 {发布数, 回滚数, 发布成功率, 回滚率}。
   * 发布成功率 = 该桶 deploy 中"前 10 条 run 成功率≥0.9"的占比(同 getReleaseEfficiency 口径)。
   * MySQL only(workflow_deploy_history);SQLite 返 available:false。
   * 给"发布效能"区块看迭代效果用 —— 发布成功率/回滚率有没有随版本变好。
   */
  async getReleaseQualityTrend(
    from: number,
    to: number,
    granularity: TrendGranularity,
  ): Promise<ReleaseQualityTrendRow> {
    const unavailable: ReleaseQualityTrendRow = {
      available: false,
      from,
      to,
      granularity,
      points: [],
    };
    if (this.db.dbType !== "mysql") return unavailable;

    try {
      const buckets = await this.releaseBuckets(from, to, granularity);
      const points: ReleaseQualityPoint[] = [...buckets.entries()]
        .sort((a, b) => (a[0] < b[0] ? -1 : 1))
        .map(([b, e]) => ({
          bucket: b,
          releaseCount: e.releaseCount,
          rollbackCount: e.rollbackCount,
          successRate: e.releaseCount > 0 ? e.successN / e.releaseCount : null,
          rollbackRate: e.releaseCount > 0 ? e.rollbackCount / e.releaseCount : null,
        }));

      return { available: true, from, to, granularity, points };
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] DashboardRepository.getReleaseQualityTrend failed: ${msg}`);
      return unavailable;
    }
  }

  /**
   * 工作流发布统计(研发效能·发布情况表):以 workflow_specs 全部工作流为底,
   * LEFT JOIN deploy 历史聚合。含从未发布过的工作流("未发布")。
   * 窗口口径(跟页面 timeRange):deployCount / rollbackCount / firstRunSuccessRate
   *   (批均成功率 = 窗口内每次 deploy 后前 10 条 run 的合并成功率,窗口内无部署 → null)。
   * 全周期口径:devCycleMs(研发周期 = 首次部署 − 首次保存,含排期等待,参考量)、
   *   lastDeployAt、released(是否发布过,不受窗口影响)。
   * MySQL only(workflow_deploy_history);SQLite 返 available:false。
   */
  async getWorkflowReleaseStats(from: number, to: number): Promise<WorkflowReleaseStatsResult> {
    if (this.db.dbType !== "mysql") return { available: false, workflows: [] };

    try {
      const rows = await this.db.query<{
        workflow_id: string;
        title: string | null;
        created_ts: number | null;
        deploy_count: number | null;
        rollback_count: number | null;
        first_edit_ts: number | null;
        first_deploy_ts: number | null;
        last_deploy_ts: number | null;
      }>(
        `SELECT
          ws.workflow_id,
          ws.title,
          UNIX_TIMESTAMP(ws.gmt_create) AS created_ts,
          dh.deploy_count,
          dh.rollback_count,
          UNIX_TIMESTAMP(dh.first_edit) AS first_edit_ts,
          UNIX_TIMESTAMP(dh.first_deploy) AS first_deploy_ts,
          UNIX_TIMESTAMP(dh.last_deploy) AS last_deploy_ts
         FROM workflow_specs ws
         LEFT JOIN (
           SELECT
             workflow_id,
             SUM(CASE WHEN action = 'deploy' AND gmt_create BETWEEN FROM_UNIXTIME(?) AND FROM_UNIXTIME(?) THEN 1 ELSE 0 END) AS deploy_count,
             SUM(CASE WHEN action = 'rollback' AND gmt_create BETWEEN FROM_UNIXTIME(?) AND FROM_UNIXTIME(?) THEN 1 ELSE 0 END) AS rollback_count,
             MIN(CASE WHEN action = 'edit' THEN gmt_create END) AS first_edit,
             MIN(CASE WHEN action = 'deploy' THEN gmt_create END) AS first_deploy,
             MAX(CASE WHEN action = 'deploy' THEN gmt_create END) AS last_deploy
           FROM workflow_deploy_history
           GROUP BY workflow_id
         ) dh ON dh.workflow_id = ws.workflow_id
         ORDER BY COALESCE(dh.deploy_count, 0) DESC, ws.gmt_create DESC
         LIMIT 300`,
        [from, to, from, to],
      );

      // 窗口内的 deploy 事件 → 每工作流批均成功率
      const winDeploys = await this.db.query<{ workflow_id: string; ts: number }>(
        `SELECT workflow_id, UNIX_TIMESTAMP(gmt_create) AS ts
         FROM workflow_deploy_history
         WHERE action = 'deploy' AND gmt_create BETWEEN FROM_UNIXTIME(?) AND FROM_UNIXTIME(?)
         ORDER BY workflow_id, gmt_create ASC`,
        [from, to],
      );
      const deploysByWf = new Map<string, number[]>();
      for (const d of winDeploys) {
        const arr = deploysByWf.get(d.workflow_id) ?? [];
        arr.push(d.ts);
        deploysByWf.set(d.workflow_id, arr);
      }

      const workflows: WorkflowReleaseStatRow[] = [];
      for (const r of rows) {
        const deployCount = r.deploy_count ?? 0;
        const devCycleMs =
          r.first_deploy_ts != null && r.first_edit_ts != null
            ? Math.max(0, (r.first_deploy_ts - r.first_edit_ts) * 1000)
            : null;

        let firstRunSuccessRate: number | null = null;
        const tsList = deploysByWf.get(r.workflow_id) ?? [];
        let succAll = 0;
        let nAll = 0;
        for (const ts of tsList) {
          const runRows = await this.db.query<{ status: string }>(
            `SELECT status FROM flow_runs
             WHERE workflow_id = ? AND started_at > ?
             ORDER BY started_at ASC LIMIT 10`,
            [r.workflow_id, ts],
          );
          nAll += runRows.length;
          succAll += runRows.filter((x) => x.status === "succeeded").length;
        }
        if (nAll > 0) firstRunSuccessRate = succAll / nAll;

        workflows.push({
          workflowId: r.workflow_id,
          workflowTitle: r.title || r.workflow_id,
          createdAt: r.created_ts,
          released: r.first_deploy_ts != null,
          deployCount,
          rollbackCount: r.rollback_count ?? 0,
          devCycleMs,
          lastDeployAt: r.last_deploy_ts,
          firstRunSuccessRate,
        });
      }

      return { available: true, workflows };
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] DashboardRepository.getWorkflowReleaseStats failed: ${msg}`);
      return { available: false, workflows: [] };
    }
  }

  /**
   * 单指标趋势:按 day/week/month 分桶,给 KPI 点钻弹窗用。value 允许 null(该桶无样本,图上断点)。
   * flow_runs 系(两库通用):runs=实例数;activeWorkflows=distinct 工作流;dau=distinct user;
   *   completionRate=长程完成率;successRate=总完成率(终态口径);machineP50=成功 run 耗时 P50(ms,client 分位)。
   * release 系(MySQL only,workflow_deploy_history):releases=distinct 上线工作流;deploys=发布次数;
   *   releaseSuccessRate=发布成功率;rollbackRate=回滚率;deliveryLagHours=平均交付拖期(小时)。
   * 桶表达式跨库:MySQL DATE_FORMAT(FROM_UNIXTIME(...));SQLite date/strftime。
   */
  async getMetricTrend(
    metric: MetricKey,
    granularity: TrendGranularity,
    from: number,
    to: number,
  ): Promise<MetricTrendRow> {
    const unavailable: MetricTrendRow = {
      available: false,
      metric,
      granularity,
      from,
      to,
      points: [],
    };

    const isMysql = this.db.dbType === "mysql" || this.db.dbType === "zdas";
    const bExpr = isMysql
      ? granularity === "day"
        ? "DATE_FORMAT(FROM_UNIXTIME(started_at), '%Y-%m-%d')"
        : granularity === "week"
          ? "DATE_FORMAT(FROM_UNIXTIME(started_at), '%Y-%u')"
          : "DATE_FORMAT(FROM_UNIXTIME(started_at), '%Y-%m')"
      : granularity === "day"
        ? "date(started_at, 'unixepoch')"
        : granularity === "week"
          ? "strftime('%Y-W%W', started_at, 'unixepoch')"
          : "strftime('%Y-%m', started_at, 'unixepoch')";

    const RELEASE_METRICS: MetricKey[] = [
      "releases", "deploys", "releaseSuccessRate", "rollbackRate", "deliveryLagHours",
    ];
    // 字段未落地:selfHealRate 需 self_heal_triggered;online*/testRuns 需 track 分轨字段
    const UNAVAILABLE_METRICS: MetricKey[] = [
      "selfHealRate", "onlineCompletionRate", "onlineRuns", "testRuns",
    ];

    try {
      if (UNAVAILABLE_METRICS.includes(metric)) return unavailable;
      // ── release 系(MySQL only)──
      if (RELEASE_METRICS.includes(metric)) {
        if (!isMysql) return unavailable;
        const buckets = await this.releaseBuckets(from, to, granularity);
        const points: MetricTrendPoint[] = [...buckets.entries()]
          .sort((a, b) => (a[0] < b[0] ? -1 : 1))
          .map(([b, e]) => {
            let value: number | null;
            switch (metric) {
              case "releases": value = e.distinctWf.size; break;
              case "deploys": value = e.releaseCount; break;
              case "releaseSuccessRate":
                value = e.releaseCount > 0 ? e.successN / e.releaseCount : null; break;
              case "rollbackRate":
                value = e.releaseCount > 0 ? e.rollbackCount / e.releaseCount : null; break;
              default: // deliveryLagHours
                value = e.lagN > 0 ? Math.round((e.lagSumMs / e.lagN / 3_600_000) * 10) / 10 : null;
            }
            return { bucket: b, value };
          });
        return { available: true, metric, granularity, from, to, points };
      }

      // ── machineP50:per-bucket client 分位 ──
      if (metric === "machineP50") {
        const rows = await this.db.query<{ b: string; d: number }>(
          `SELECT ${bExpr} AS b, ${normalizedDurationSql} AS d FROM flow_runs
           WHERE status = 'succeeded' AND total_duration_ms IS NOT NULL
             AND started_at BETWEEN ? AND ? AND started_at IS NOT NULL`,
          [from, to],
        );
        const byBucket = new Map<string, number[]>();
        for (const r of rows) {
          let arr = byBucket.get(r.b);
          if (!arr) { arr = []; byBucket.set(r.b, arr); }
          arr.push(asNumber(r.d));
        }
        const points: MetricTrendPoint[] = [...byBucket.entries()]
          .sort((a, b) => (a[0] < b[0] ? -1 : 1))
          .map(([b, ds]) => ({ bucket: b, value: percentile(ds.slice().sort((x, y) => x - y), 0.5) }));
        return { available: true, metric, granularity, from, to, points };
      }

      // ── 其余 flow_runs 系聚合一趟出 ──
      const valueExpr =
        metric === "runs"
          ? "COUNT(*)"
          : metric === "activeWorkflows"
            ? "COUNT(DISTINCT workflow_id)"
            : metric === "dau"
              ? "COUNT(DISTINCT user_id)"
              : metric === "completionRate"
                ? `SUM(CASE WHEN (node_count >= ${LONG_RUN_NODE_COUNT} OR (${normalizedDurationSql}) >= ${LONG_RUN_DURATION_MS}) AND status = 'succeeded' THEN 1 ELSE 0 END) * 1.0
                   / NULLIF(SUM(CASE WHEN (node_count >= ${LONG_RUN_NODE_COUNT} OR (${normalizedDurationSql}) >= ${LONG_RUN_DURATION_MS}) AND status IN (${terminalSql}) THEN 1 ELSE 0 END), 0)`
                : `SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END) * 1.0
                   / NULLIF(SUM(CASE WHEN status IN (${terminalSql}) THEN 1 ELSE 0 END), 0)`;
      const extraCond = metric === "dau" ? "AND user_id IS NOT NULL" : "";
      const rows = await this.db.query<{ b: string; v: number | null }>(
        `SELECT ${bExpr} AS b, ${valueExpr} AS v
         FROM flow_runs
         WHERE started_at BETWEEN ? AND ? AND started_at IS NOT NULL ${extraCond}
         GROUP BY ${bExpr}
         ORDER BY b ASC`,
        [from, to],
      );
      return {
        available: true,
        metric,
        granularity,
        from,
        to,
        points: rows.map((r) => ({ bucket: r.b, value: r.v ?? null })),
      };
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] DashboardRepository.getMetricTrend failed: ${msg}`);
      return unavailable;
    }
  }
}
