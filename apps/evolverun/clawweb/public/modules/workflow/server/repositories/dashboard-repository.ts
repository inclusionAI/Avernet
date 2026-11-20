import { productionRunsSql, productionNodesSql, productionDiagnosesSql } from "@avernet/clawweb-shared/server/services/run-purpose";
import type { IDatabase } from "@avernet/clawweb-shared/server/db";

import { dashboardWorkflowFilter, type DashboardWorkflowFilter } from "./dashboard-query-scope.js";
import { DashboardReleaseQueries } from "./dashboard-release-queries.js";
import type {
  DailyTrendPoint, DurationBucket, EvolutionMetricsRow, FailureHotspotRow,
  MetricKey, MetricTrendPoint, MetricTrendRow, OverviewRow, ReleaseEfficiencyRow,
  ReleaseQualityTrendRow, TopWorkflowRow, TrendGranularity, WorkflowHealthResult,
  WorkflowHealthRow, WorkflowReleaseStatsResult,
} from "./dashboard-types.js";

export type * from "./dashboard-types.js";

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

// --- Repository ---

export class DashboardRepository {
  private readonly filter: DashboardWorkflowFilter;
  private readonly releaseQueries: DashboardReleaseQueries;

  constructor(private readonly db: IDatabase, workflowIds: readonly string[] | null = null) {
    this.filter = dashboardWorkflowFilter(db.dbType, workflowIds);
    this.releaseQueries = new DashboardReleaseQueries(db, this.filter);
  }

  /** Create a request-local repository; no grants is distinct from admin access. */
  forWorkflows(workflowIds: readonly string[]): DashboardRepository {
    return new DashboardRepository(this.db, [...workflowIds]);
  }

  async getEvolutionMetrics(from: number, to: number): Promise<EvolutionMetricsRow> {
    const { and, params } = this.filter;
    const clause = and();
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
          `SELECT COUNT(*) AS cnt FROM ${productionDiagnosesSql()} workflow_healing_diagnoses WHERE gmt_create BETWEEN ? AND ?${clause}`,
          [fromDb, toDb, ...params],
        ),
        this.db.query<{ cnt: number }>(
          `SELECT COUNT(DISTINCT flow_id) AS cnt FROM ${productionDiagnosesSql()} workflow_healing_diagnoses WHERE gmt_create BETWEEN ? AND ?${clause}`,
          [fromDb, toDb, ...params],
        ),
        this.db.query<{ cnt: number }>(
          `SELECT COUNT(*) AS cnt FROM (
             SELECT workflow_id, failure_signature
             FROM ${productionDiagnosesSql()} workflow_healing_diagnoses
             WHERE gmt_create BETWEEN ? AND ?${clause}
             GROUP BY workflow_id, failure_signature
           ) issue_clusters`,
          [fromDb, toDb, ...params],
        ),
        this.db.query<{ cnt: number }>(
          `SELECT COUNT(*) AS cnt FROM workflow_healing_suggestions WHERE gmt_create BETWEEN ? AND ?${clause}`,
          [fromDb, toDb, ...params],
        ),
        this.db.query<{ total: number; succeeded: number }>(
          `SELECT COUNT(*) AS total,
                  COALESCE(SUM(CASE WHEN succeeded = 1 THEN 1 ELSE 0 END), 0) AS succeeded
           FROM workflow_healing_outcomes
           WHERE action = 'suggestion_apply' AND gmt_create BETWEEN ? AND ?${clause}`,
          [fromDb, toDb, ...params],
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
           FROM workflow_healing_suggestions WHERE 1 = 1${clause}`,
          [...params],
          ),
          this.db.query<{ cnt: number }>(
          `SELECT COUNT(*) AS cnt FROM workflow_healing_suggestions
           WHERE verification_status = 'verified' AND verification_checked_at BETWEEN ? AND ?${clause}`,
          [from, to, ...params],
          ),
        ]);
        appliedUnverifiedCount = asNumber(backlog[0]?.pending);
        recurrenceDetectedCount = asNumber(backlog[0]?.recurrence);
        verifiedCount = asNumber(verified[0]?.cnt);
      } catch (error) {
        verificationAvailable = false;
        const msg = this.filter.restricted ? "scoped query unavailable" : error instanceof Error ? error.message : String(error);
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
      const msg = this.filter.restricted ? "scoped query unavailable" : error instanceof Error ? error.message : String(error);
      console.warn(`[db] DashboardRepository.getEvolutionMetrics failed: ${msg}`);
      return unavailable;
    }
  }

  async getOverview(from: number, to: number): Promise<OverviewRow> {
    const { and, params } = this.filter;
    const clause = and();
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
          `SELECT COUNT(DISTINCT workflow_id) AS cnt FROM ${productionRunsSql()} flow_runs WHERE started_at BETWEEN ? AND ?${clause}`,
          [from, to, ...params],
        ),
        this.db.query<{ status: string; cnt: number }>(
          `SELECT status, COUNT(*) AS cnt FROM ${productionRunsSql()} flow_runs WHERE started_at BETWEEN ? AND ?${clause} GROUP BY status`,
          [from, to, ...params],
        ),
        this.db.query<{ succeeded_count: number; failed_count: number }>(
          `SELECT
            SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END) AS succeeded_count,
            SUM(CASE WHEN status IN (${unsuccessfulTerminalSql}) THEN 1 ELSE 0 END) AS failed_count
           FROM ${productionRunsSql()} flow_runs WHERE started_at BETWEEN ? AND ?${clause}
             AND status IN (${terminalSql})`,
          [prevFrom, prevTo, ...params],
        ),
        this.db.query<{ avg_ms: number | null }>(
          `SELECT AVG(${normalizedDurationSql}) AS avg_ms FROM ${productionRunsSql()} flow_runs
           WHERE status = 'succeeded' AND started_at BETWEEN ? AND ?${clause} AND total_duration_ms IS NOT NULL`,
          [from, to, ...params],
        ),
        this.db.query<{ total_tokens: number }>(
          `SELECT COALESCE(SUM(total_token_usage), 0) AS total_tokens FROM ${productionRunsSql()} flow_runs WHERE started_at BETWEEN ? AND ?${clause}`,
          [from, to, ...params],
        ),
        // 长程完成率(当前窗口)
        this.db.query<{ s: number; t: number }>(
          `SELECT
            SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END) AS s,
            SUM(CASE WHEN status IN (${terminalSql}) THEN 1 ELSE 0 END) AS t
           FROM ${productionRunsSql()} flow_runs WHERE ${longRunClause} AND started_at BETWEEN ? AND ?${clause}`,
          [...longRunParams, from, to, ...params],
        ),
        // 长程完成率(上一周期,用于环比)
        this.db.query<{ s: number; t: number }>(
          `SELECT
            SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END) AS s,
            SUM(CASE WHEN status IN (${terminalSql}) THEN 1 ELSE 0 END) AS t
           FROM ${productionRunsSql()} flow_runs WHERE ${longRunClause} AND started_at BETWEEN ? AND ?${clause}`,
          [...longRunParams, prevFrom, prevTo, ...params],
        ),
        // 完成耗时原始值(当前,client-side 分位)
        this.db.query<{ d: number }>(
          `SELECT ${normalizedDurationSql} AS d FROM ${productionRunsSql()} flow_runs
           WHERE status = 'succeeded' AND total_duration_ms IS NOT NULL AND started_at BETWEEN ? AND ?${clause}`,
          [from, to, ...params],
        ),
        // 完成耗时原始值(上一周期,取 P50 环比)
        this.db.query<{ d: number }>(
          `SELECT ${normalizedDurationSql} AS d FROM ${productionRunsSql()} flow_runs
           WHERE status = 'succeeded' AND total_duration_ms IS NOT NULL AND started_at BETWEEN ? AND ?${clause}`,
          [prevFrom, prevTo, ...params],
        ),
        // DAU / WAU / prevDau(distinct user_id;v34 起有该列,空值不计)
        this.db.query<{ cnt: number }>(
          `SELECT COUNT(DISTINCT user_id) AS cnt FROM ${productionRunsSql()} flow_runs
           WHERE user_id IS NOT NULL AND started_at BETWEEN ? AND ?${clause}`,
          [dauFrom, to, ...params],
        ),
        this.db.query<{ cnt: number }>(
          `SELECT COUNT(DISTINCT user_id) AS cnt FROM ${productionRunsSql()} flow_runs
           WHERE user_id IS NOT NULL AND started_at BETWEEN ? AND ?${clause}`,
          [wauFrom, to, ...params],
        ),
        this.db.query<{ cnt: number }>(
          `SELECT COUNT(DISTINCT user_id) AS cnt FROM ${productionRunsSql()} flow_runs
           WHERE user_id IS NOT NULL AND started_at BETWEEN ? AND ?${clause}`,
          [prevDauFrom, prevDauTo, ...params],
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
              `SELECT COUNT(DISTINCT workflow_id) AS cnt FROM workflow_deploy_history WHERE action = 'deploy'${clause}`,
              [...params],
            ),
            this.db.query<{ cnt: number }>(
              `SELECT COUNT(DISTINCT workflow_id) AS cnt FROM workflow_deploy_history
               WHERE action = 'deploy' AND gmt_create >= FROM_UNIXTIME(?)${clause}`,
              [weekFrom, ...params],
            ),
            this.db.query<{ cnt: number }>(
              `SELECT COUNT(DISTINCT workflow_id) AS cnt FROM workflow_deploy_history
               WHERE action = 'deploy' AND gmt_create >= FROM_UNIXTIME(?)${clause}`,
              [monthFrom, ...params],
            ),
            this.db.query<{ cnt: number }>(
              `SELECT COUNT(DISTINCT workflow_id) AS cnt FROM workflow_deploy_history
               WHERE action = 'deploy' AND gmt_create BETWEEN FROM_UNIXTIME(?) AND FROM_UNIXTIME(?)${clause}`,
              [from, to, ...params],
            ),
          ]);
          releasedWorkflowCount = allDeploys[0]?.cnt ?? 0;
          newReleasedThisWeek = weekDeploys[0]?.cnt ?? 0;
          monthlyReleasedCount = monthDeploys[0]?.cnt ?? 0;
          windowReleasedCount = windowDeploys[0]?.cnt ?? 0;
        } catch (deployErr) {
          // deploy_history 未迁移或不可达 → 留 null,前端显示暂不可算
          const m = this.filter.restricted ? "scoped query unavailable" : deployErr instanceof Error ? deployErr.message : String(deployErr);
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
      const msg = this.filter.restricted ? "scoped query unavailable" : error instanceof Error ? error.message : String(error);
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
    const { and, params } = this.filter;
    const clause = and();
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
           FROM ${productionRunsSql()} flow_runs
           WHERE started_at BETWEEN ? AND ? AND started_at IS NOT NULL${clause}
           GROUP BY ${dayExpr}
           ORDER BY day ASC`,
          [from, to, ...params],
        ),
        // per-day 耗时 P50:原始值拉出,client 分位(跨库)
        this.db.query<{ day: string; d: number }>(
          `SELECT ${dayExpr} AS day, ${normalizedDurationSql} AS d FROM ${productionRunsSql()} flow_runs
           WHERE status = 'succeeded' AND total_duration_ms IS NOT NULL
             AND started_at BETWEEN ? AND ? AND started_at IS NOT NULL${clause}`,
          [from, to, ...params],
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
      const msg = this.filter.restricted ? "scoped query unavailable" : error instanceof Error ? error.message : String(error);
      console.warn(`[db] DashboardRepository.getDailyTrend failed: ${msg}`);
      return [];
    }
  }

  async getDurationDistribution(
    from: number,
    to: number,
  ): Promise<DurationBucket[]> {
    const { and, params } = this.filter;
    const clause = and();
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
        `SELECT ${normalizedDurationSql} AS duration_ms FROM ${productionRunsSql()} flow_runs
         WHERE started_at BETWEEN ? AND ? AND total_duration_ms IS NOT NULL${clause}`,
        [from, to, ...params],
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
      const msg = this.filter.restricted ? "scoped query unavailable" : error instanceof Error ? error.message : String(error);
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
    const { and, params } = this.filter;
    const clause = and();
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
         FROM ${productionRunsSql()} flow_runs
         WHERE started_at BETWEEN ? AND ?${clause}
         GROUP BY workflow_id
         ORDER BY run_count DESC
         LIMIT ?`,
        [from, to, ...params, limit],
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
      const msg = this.filter.restricted ? "scoped query unavailable" : error instanceof Error ? error.message : String(error);
      console.warn(
        `[db] DashboardRepository.getTopWorkflows failed: ${msg}`,
      );
      return [];
    }
  }

  async getRunningCount(): Promise<number> {
    const { and, params } = this.filter;
    const clause = and();
    try {
      const rows = await this.db.query<{ cnt: number }>(
        `SELECT COUNT(*) AS cnt FROM ${productionRunsSql()} flow_runs WHERE status = 'running'${clause}`,
        [...params],
      );
      return rows[0]?.cnt ?? 0;
    } catch (error) {
      const msg = this.filter.restricted ? "scoped query unavailable" : error instanceof Error ? error.message : String(error);
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
    const { and, params, restricted } = this.filter;
    const titleClause = and();
    // Runtime error text and signatures can contain inputs or credentials. Keep the
    // grouping, but do not return raw examples to viewers of the scoped dashboard.
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
          ${restricted ? "''" : "d.failure_signature"} AS failure_signature,
          MAX(COALESCE(d.weak_node_id, d.node_id)) AS node_id,
          d.workflow_id,
          MAX(fr.workflow_title) AS workflow_title,
          ${restricted ? "''" : "MAX(COALESCE(d.error_text, d.failure_mode))"} AS example_error,
          COUNT(*) AS cnt
         FROM ${productionDiagnosesSql()} d
         LEFT JOIN (
           SELECT workflow_id, MAX(workflow_title) AS workflow_title
           FROM ${productionRunsSql()} flow_runs WHERE 1 = 1${titleClause} GROUP BY workflow_id
         ) fr ON fr.workflow_id = d.workflow_id
         WHERE d.gmt_create BETWEEN ? AND ?${and("d.workflow_id")}
         GROUP BY d.workflow_id, d.failure_signature
         ORDER BY cnt DESC
         LIMIT ?`,
        [...params, fromDb, toDb, ...params, limit],
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
      const msg = this.filter.restricted ? "scoped query unavailable" : error instanceof Error ? error.message : String(error);
      console.warn(`[db] diagnosis hotspots unavailable, falling back to failed nodes: ${msg}`);
    }

    const errExpr =
      this.db.dbType === "mysql" || this.db.dbType === "zdas"
        ? "SUBSTRING(error_text, 1, 120)"
        : "SUBSTR(error_text, 1, 120)";
    try {
      const rows = await this.db.query<{
        node_id: string | null;
        node_title: string | null;
        workflow_id: string | null;
        workflow_title: string | null;
        example_error: string | null;
        cnt: number;
      }>(
        `SELECT
          MAX(ne.node_id) AS node_id,
          MAX(ne.node_title) AS node_title,
          MAX(ne.workflow_id) AS workflow_id,
          MAX(fr.workflow_title) AS workflow_title,
          ${restricted ? "''" : "MAX(ne.error_text)"} AS example_error,
          COUNT(*) AS cnt
         FROM ${productionNodesSql()} ne
         LEFT JOIN (
           SELECT workflow_id, MAX(workflow_title) AS workflow_title
           FROM ${productionRunsSql()} flow_runs WHERE 1 = 1${titleClause} GROUP BY workflow_id
         ) fr ON fr.workflow_id = ne.workflow_id
         WHERE ne.status = 'failed'
           AND ne.error_text IS NOT NULL
           AND ne.error_text <> ''
           AND ne.started_at BETWEEN ? AND ?${and("ne.workflow_id")}
         GROUP BY ${errExpr}
         ORDER BY cnt DESC
         LIMIT ?`,
        [...params, from, to, ...params, limit],
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
      const msg = this.filter.restricted ? "scoped query unavailable" : error instanceof Error ? error.message : String(error);
      console.warn(`[db] DashboardRepository.getFailureHotspots failed: ${msg}`);
      return [];
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
    const { and, params } = this.filter;
    const clause = and();
    try {
      const rows = await this.db.query<{
        workflow_id: string;
        workflow_title: string | null;
        run_count: number;
        succeeded_count: number;
        failed_count: number;
        terminal_count: number;
        avg_ms: number | null;
      }>(
        `SELECT
          workflow_id,
          MAX(workflow_title) AS workflow_title,
          COUNT(*) AS run_count,
          SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END) AS succeeded_count,
          SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_count,
          SUM(CASE WHEN status IN (${terminalSql}) THEN 1 ELSE 0 END) AS terminal_count,
          AVG(CASE WHEN status = 'succeeded' THEN ${normalizedDurationSql} END) AS avg_ms
         FROM ${productionRunsSql()} flow_runs
         WHERE started_at BETWEEN ? AND ? AND started_at IS NOT NULL${clause}
         GROUP BY workflow_id
         ORDER BY run_count DESC
         LIMIT 200`,
        [from, to, ...params],
      );

      // 每工作流 succeeded 耗时原始值 → client-side P50(一次查询,TS 分组)
      const durRows = await this.db.query<{ workflow_id: string; d: number }>(
        `SELECT workflow_id, ${normalizedDurationSql} AS d FROM ${productionRunsSql()} flow_runs
         WHERE status = 'succeeded' AND total_duration_ms IS NOT NULL AND started_at BETWEEN ? AND ?${clause}`,
        [from, to, ...params],
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
            `SELECT DISTINCT workflow_id FROM workflow_deploy_history WHERE action = 'deploy'${clause}`,
            [...params],
          );
          releasedSet = new Set(relRows.map((r) => r.workflow_id));
        } catch (relErr) {
          const m = this.filter.restricted ? "scoped query unavailable" : relErr instanceof Error ? relErr.message : String(relErr);
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
          failedCount: asNumber(r.failed_count),
          completionSuccessRate: completion,
          selfHealTriggeredRuns: 0,
          selfHealSuccessRate: null,
          machineDurationP50: percentile(durs, 0.5),
          avgDurationMs: r.avg_ms == null ? null : asNumber(r.avg_ms),
        };
      });

      return { available: true, from, to, workflows };
    } catch (error) {
      const msg = this.filter.restricted ? "scoped query unavailable" : error instanceof Error ? error.message : String(error);
      console.warn(`[db] DashboardRepository.getWorkflowHealth failed: ${msg}`);
      return { available: false, from, to, workflows: [] };
    }
  }

  getReleaseEfficiency(from: number, to: number): Promise<ReleaseEfficiencyRow> {
    return this.releaseQueries.getReleaseEfficiency(from, to);
  }

  getReleaseQualityTrend(from: number, to: number, granularity: TrendGranularity): Promise<ReleaseQualityTrendRow> {
    return this.releaseQueries.getReleaseQualityTrend(from, to, granularity);
  }

  getWorkflowReleaseStats(from: number, to: number): Promise<WorkflowReleaseStatsResult> {
    return this.releaseQueries.getWorkflowReleaseStats(from, to);
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
    const { and, params } = this.filter;
    const clause = and();
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
        const buckets = await this.releaseQueries.releaseBuckets(from, to, granularity);
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
          `SELECT ${bExpr} AS b, ${normalizedDurationSql} AS d FROM ${productionRunsSql()} flow_runs
           WHERE status = 'succeeded' AND total_duration_ms IS NOT NULL
             AND started_at BETWEEN ? AND ? AND started_at IS NOT NULL${clause}`,
          [from, to, ...params],
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
         FROM ${productionRunsSql()} flow_runs
         WHERE started_at BETWEEN ? AND ? AND started_at IS NOT NULL${clause} ${extraCond}
         GROUP BY ${bExpr}
         ORDER BY b ASC`,
        [from, to, ...params],
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
      const msg = this.filter.restricted ? "scoped query unavailable" : error instanceof Error ? error.message : String(error);
      console.warn(`[db] DashboardRepository.getMetricTrend failed: ${msg}`);
      return unavailable;
    }
  }
}
