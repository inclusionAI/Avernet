import { Router } from "express";
import type { IDatabase } from "../db.js";
import type {
  DashboardRepository,
  MetricKey,
} from "../repositories/dashboard-repository.js";
import type { FlowRunRepository } from "../repositories/flow-run-repository.js";
import { asyncHandler } from "../middleware/async-handler.js";

/** Validate and parse from/to query params with sensible defaults. */
function parseTimeRange(
  queryFrom: unknown,
  queryTo: unknown,
): { from: number; to: number } | { error: string } {
  const nowSec = Math.floor(Date.now() / 1000);
  const defaultFrom = nowSec - 30 * 24 * 3600;

  const fromStr = Array.isArray(queryFrom) ? queryFrom[0] : queryFrom;
  const toStr = Array.isArray(queryTo) ? queryTo[0] : queryTo;
  const from = fromStr != null ? Number(fromStr) : defaultFrom;
  const to = toStr != null ? Number(toStr) : nowSec;

  if (!Number.isFinite(from) || !Number.isFinite(to)) {
    return { error: "Invalid from/to parameters: must be finite numbers" };
  }
  if (from < 0 || to < 0) {
    return { error: "Invalid from/to parameters: must be non-negative" };
  }
  if (from > to) {
    return { error: "Invalid time range: from must be <= to" };
  }

  return { from, to };
}

export function createDashboardRouter(
  dashboardRepo: DashboardRepository | null,
  _flowRunRepo: FlowRunRepository | null,
  db: IDatabase | null,
): Router {
  const router = Router();

  router.use((req, res, next) => {
    if (!req.isAdmin) {
      res.status(403).json({
        error: "Forbidden",
        code: "FORBIDDEN",
        message: "只有管理员可以查看全局数据大盘",
      });
      return;
    }
    next();
  });

  /**
   * GET /api/dashboard/overview?from=&to=
   * Core KPI aggregation: workflow count, total runs, success rate, running, avg duration, token usage.
   */
  router.get(
    "/overview",
    asyncHandler(async (req, res) => {
      if (!dashboardRepo) {
        res
          .status(503)
          .json({
            error: "Service Unavailable",
            message: "Database not configured",
          });
        return;
      }

      const range = parseTimeRange(req.query.from, req.query.to);
      if ("error" in range) {
        res.status(400).json({ error: "Bad Request", message: range.error });
        return;
      }
      const { from, to } = range;

      const [overview, evolution] = await Promise.all([
        dashboardRepo.getOverview(from, to),
        dashboardRepo.getEvolutionMetrics(from, to),
      ]);

      const completedCount = overview.succeededCount + overview.failedCount;
      const successRate =
        completedCount > 0 ? overview.succeededCount / completedCount : null;

      const prevCompleted =
        overview.prevSucceededCount + overview.prevFailedCount;
      const prevSuccessRate =
        prevCompleted > 0
          ? overview.prevSucceededCount / prevCompleted
          : null;

      res.json({
        workflowCount: overview.workflowCount,
        totalRuns: overview.totalRuns,
        succeededCount: overview.succeededCount,
        failedCount: overview.failedCount,
        runningCount: overview.runningCount,
        terminalCount: overview.terminalCount,
        nonTerminalCount: overview.nonTerminalCount,
        successRate,
        prevSuccessRate,
        avgDurationMs: overview.avgDurationMs,
        totalTokenUsage: overview.totalTokenUsage,
        statusDistribution: overview.statusDistribution,
        // ── 三主线(可算)──
        completionSuccessRate: overview.completionSuccessRate,
        prevCompletionSuccessRate: overview.prevCompletionSuccessRate,
        machineDurationP50: overview.machineDurationP50,
        machineDurationP95: overview.machineDurationP95,
        prevMachineDurationP50: overview.prevMachineDurationP50,
        durationSampleCount: overview.durationSampleCount,
        dau: overview.dau,
        wau: overview.wau,
        prevDau: overview.prevDau,
        // ── 发布相关(MySQL only;SQLite 返 null)──
        releasedWorkflowCount: overview.releasedWorkflowCount,
        newReleasedThisWeek: overview.newReleasedThisWeek,
        monthlyReleasedCount: overview.monthlyReleasedCount,
        windowReleasedCount: overview.windowReleasedCount,
        // ── 缺字段/缺表的指标:恒 null,前端显示"暂不可算"──
        offlineThisWeek: null,
        onlineRuns: null,
        testRuns: null,
        onlineSuccessRate: null,
        prevOnlineSuccessRate: null,
        onlineStatusDistribution: {},
        sceneCoverage: null,
        estimatedPersonDays: null,
        selfHealTriggeredRuns: null,
        selfHealSuccessRate: null,
        prevSelfHealSuccessRate: null,
        selfHealRecoveredRuns: null,
        scheduledRunCount: null,
        evolution,
      });
    }),
  );

  /**
   * GET /api/dashboard/daily-trend?from=&to=
   * Daily time-series: run count, success count, fail count, avg duration, token usage per day.
   */
  router.get(
    "/daily-trend",
    asyncHandler(async (req, res) => {
      if (!dashboardRepo) {
        res
          .status(503)
          .json({
            error: "Service Unavailable",
            message: "Database not configured",
          });
        return;
      }

      const range = parseTimeRange(req.query.from, req.query.to);
      if ("error" in range) {
        res.status(400).json({ error: "Bad Request", message: range.error });
        return;
      }
      const { from, to } = range;

      const days = await dashboardRepo.getDailyTrend(from, to);

      res.json({
        from,
        to,
        dates: days.map((d) => ({
          date: d.date,
          runCount: d.runCount,
          succeededCount: d.succeededCount,
          failedCount: d.failedCount,
          successRate:
            d.succeededCount + d.failedCount > 0
              ? d.succeededCount / (d.succeededCount + d.failedCount)
              : null,
          avgDurationMs: d.avgDurationMs,
          tokenUsage: d.tokenUsage,
          // 三主线(运行质量演进图):completion/machineP50 真实可算;selfHeal 缺字段恒 null
          completionSuccessRate: d.completionSuccessRate ?? null,
          machineDurationP50: d.machineDurationP50 ?? null,
          selfHealSuccessRate: d.selfHealSuccessRate ?? null,
        })),
      });
    }),
  );

  /**
   * GET /api/dashboard/duration-distribution?from=&to=
   * Histogram buckets of run durations.
   */
  router.get(
    "/duration-distribution",
    asyncHandler(async (req, res) => {
      if (!dashboardRepo) {
        res
          .status(503)
          .json({
            error: "Service Unavailable",
            message: "Database not configured",
          });
        return;
      }

      const range = parseTimeRange(req.query.from, req.query.to);
      if ("error" in range) {
        res.status(400).json({ error: "Bad Request", message: range.error });
        return;
      }
      const { from, to } = range;

      const buckets = await dashboardRepo.getDurationDistribution(from, to);
      res.json({ from, to, buckets });
    }),
  );

  /**
   * GET /api/dashboard/top-workflows?from=&to=&limit=10
   * Top N workflows by run count with success rate.
   */
  router.get(
    "/top-workflows",
    asyncHandler(async (req, res) => {
      if (!dashboardRepo) {
        res
          .status(503)
          .json({
            error: "Service Unavailable",
            message: "Database not configured",
          });
        return;
      }

      const range = parseTimeRange(req.query.from, req.query.to);
      if ("error" in range) {
        res.status(400).json({ error: "Bad Request", message: range.error });
        return;
      }
      const { from, to } = range;
      const limit = Math.min(Math.max(Number(req.query.limit) || 10, 1), 50);

      const workflows = await dashboardRepo.getTopWorkflows(from, to, limit);
      res.json({ from, to, workflows });
    }),
  );

  /**
   * GET /api/dashboard/subsystem-summary
   * Quick counts from subsystem tables for the overview cards.
   */
  router.get(
    "/subsystem-summary",
    asyncHandler(async (_req, res) => {
      if (!db) {
        res
          .status(503)
          .json({
            error: "Service Unavailable",
            message: "Database not configured",
          });
        return;
      }

      try {
        // Parallel lightweight queries across subsystem tables
        const [
          approvalRows,
          alertRows,
          schedulerRows,
          flowControlSlotRows,
          flowControlQueueRows,
        ] = await Promise.all([
          db
            .query<{ status: string; cnt: number }>(
              `SELECT status, COUNT(*) AS cnt FROM approval_cards GROUP BY status`,
            )
            .catch(() => [] as { status: string; cnt: number }[]),
          db
            .query<{ severity: string; cnt: number }>(
              `SELECT severity, COUNT(*) AS cnt FROM triggered_alerts WHERE acknowledged = 0 GROUP BY severity`,
            )
            .catch(() => [] as { severity: string; cnt: number }[]),
          db
            .query<{ enabled: number; cnt: number }>(
              `SELECT enabled, COUNT(*) AS cnt FROM scheduled_triggers GROUP BY enabled`,
            )
            .catch(() => [] as { enabled: number; cnt: number }[]),
          db
            .query<{ cnt: number }>(
              `SELECT COUNT(*) AS cnt FROM flow_control_slots WHERE status = 'active'`,
            )
            .catch(() => [{ cnt: 0 }] as { cnt: number }[]),
          db
            .query<{ cnt: number }>(
              `SELECT COUNT(*) AS cnt FROM flow_control_queue WHERE status = 'queued'`,
            )
            .catch(() => [{ cnt: 0 }] as { cnt: number }[]),
        ]);

        // Approval aggregation
        const approval = { pending: 0, approved: 0, rejected: 0, other: 0 };
        for (const row of approvalRows) {
          if (row.status === "pending") approval.pending = row.cnt;
          else if (row.status === "approved") approval.approved = row.cnt;
          else if (row.status === "rejected") approval.rejected = row.cnt;
          else approval.other += row.cnt;
        }

        // Alert aggregation
        const alerts = { unacknowledged: 0, critical: 0, warning: 0, info: 0 };
        for (const row of alertRows) {
          alerts.unacknowledged += row.cnt;
          if (row.severity === "critical") alerts.critical = row.cnt;
          else if (row.severity === "warning") alerts.warning = row.cnt;
          else alerts.info += row.cnt;
        }

        // Scheduler aggregation
        const scheduler = { enabled: 0, disabled: 0 };
        for (const row of schedulerRows) {
          if (row.enabled) scheduler.enabled = row.cnt;
          else scheduler.disabled = row.cnt;
        }

        // Flow control
        const flowControl = {
          activeSlots: flowControlSlotRows[0]?.cnt ?? 0,
          queuedItems: flowControlQueueRows[0]?.cnt ?? 0,
        };

        res.json({ approval, alerts, scheduler, flowControl });
      } catch (error) {
        const msg = error instanceof Error ? error.message : String(error);
        console.warn(`[dashboard] subsystem-summary failed: ${msg}`);
        res.json({
          approval: { pending: 0, approved: 0, rejected: 0, other: 0 },
          alerts: { unacknowledged: 0, critical: 0, warning: 0, info: 0 },
          scheduler: { enabled: 0, disabled: 0 },
          flowControl: { activeSlots: 0, queuedItems: 0 },
        });
      }
    }),
  );

  /**
   * GET /api/dashboard/failure-hotspots?from=&to=&limit=10
   * 失败归因 Top:node_executions.error_text 聚类(真实,两库通用)。
   */
  router.get(
    "/failure-hotspots",
    asyncHandler(async (req, res) => {
      if (!dashboardRepo) {
        res
          .status(503)
          .json({ error: "Service Unavailable", message: "Database not configured" });
        return;
      }
      const range = parseTimeRange(req.query.from, req.query.to);
      if ("error" in range) {
        res.status(400).json({ error: "Bad Request", message: range.error });
        return;
      }
      const { from, to } = range;
      const limit = Math.min(Math.max(Number(req.query.limit) || 10, 1), 50);
      const hotspots = await dashboardRepo.getFailureHotspots(from, to, limit);
      res.json({ from, to, hotspots });
    }),
  );

  /**
   * GET /api/dashboard/release-efficiency?from=&to=
   * 发布效能(MySQL only;SQLite 返 available:false)。
   */
  router.get(
    "/release-efficiency",
    asyncHandler(async (req, res) => {
      if (!dashboardRepo) {
        res
          .status(503)
          .json({ error: "Service Unavailable", message: "Database not configured" });
        return;
      }
      const range = parseTimeRange(req.query.from, req.query.to);
      if ("error" in range) {
        res.status(400).json({ error: "Bad Request", message: range.error });
        return;
      }
      const { from, to } = range;
      const data = await dashboardRepo.getReleaseEfficiency(from, to);
      res.json(data);
    }),
  );

  /**
   * GET /api/dashboard/metric-trend?metric=&granularity=&from=&to=
   * 单指标趋势(KPI 点钻):runs/activeWorkflows/dau(两库)/releases(MySQL only),day/week/month 分桶。
   */
  router.get(
    "/metric-trend",
    asyncHandler(async (req, res) => {
      if (!dashboardRepo) {
        res
          .status(503)
          .json({ error: "Service Unavailable", message: "Database not configured" });
        return;
      }
      const range = parseTimeRange(req.query.from, req.query.to);
      if ("error" in range) {
        res.status(400).json({ error: "Bad Request", message: range.error });
        return;
      }
      const metric = String(req.query.metric ?? "runs");
      const granularity = String(req.query.granularity ?? "day");
      const allowedMetrics = [
        "runs", "activeWorkflows", "dau", "releases",
        "completionRate", "successRate", "machineP50",
        "deploys", "releaseSuccessRate", "rollbackRate", "deliveryLagHours",
        "selfHealRate", "onlineCompletionRate", "onlineRuns", "testRuns",
      ];
      const allowedGran = ["day", "week", "month"];
      if (!allowedMetrics.includes(metric) || !allowedGran.includes(granularity)) {
        res
          .status(400)
          .json({ error: "Bad Request", message: "Invalid metric or granularity" });
        return;
      }
      const { from, to } = range;
      const data = await dashboardRepo.getMetricTrend(
        metric as MetricKey,
        granularity as "day" | "week" | "month",
        from,
        to,
      );
      res.json(data);
    }),
  );

/**
   * GET /api/dashboard/workflow-release-stats?from=&to=
   * 工作流发布情况表(研发效能):全量工作流为底,含未发布。
   * 部署次数/回滚次数/批均成功率 跟窗口走;研发周期/最近部署/是否发布过 为全周期口径。
   * MySQL only;SQLite 返 available:false。
   */
  router.get(
    "/workflow-release-stats",
    asyncHandler(async (req, res) => {
      if (!dashboardRepo) {
        res
          .status(503)
          .json({ error: "Service Unavailable", message: "Database not configured" });
        return;
      }
      const range = parseTimeRange(req.query.from, req.query.to);
      if ("error" in range) {
        res.status(400).json({ error: "Bad Request", message: range.error });
        return;
      }
      const data = await dashboardRepo.getWorkflowReleaseStats(range.from, range.to);
      res.json(data);
    }),
  );

  /**
   * GET /api/dashboard/scene-breakdown?from=&to=
   * 按业务线切片 —— 暂不可算(business_scenes 表未建),返 available:false 占位,避免 404。
   */
  router.get(
    "/scene-breakdown",
    asyncHandler(async (req, res) => {
      const range = parseTimeRange(req.query.from, req.query.to);
      if ("error" in range) {
        res.status(400).json({ error: "Bad Request", message: range.error });
        return;
      }
      const { from, to } = range;
      res.json({ from, to, available: false, scenes: [] });
    }),
  );

  /**
   * GET /api/dashboard/workflow-health?from=&to=
   * L2 工作流健康表:per-workflow 完成率(长程)/速度P50/avgDuration/released;
   * 自愈缺字段留 null。两库通用(flow_runs);released 仅 MySQL 可判。
   */
  router.get(
    "/workflow-health",
    asyncHandler(async (req, res) => {
      if (!dashboardRepo) {
        res
          .status(503)
          .json({ error: "Service Unavailable", message: "Database not configured" });
        return;
      }
      const range = parseTimeRange(req.query.from, req.query.to);
      if ("error" in range) {
        res.status(400).json({ error: "Bad Request", message: range.error });
        return;
      }
      const { from, to } = range;
      const data = await dashboardRepo.getWorkflowHealth(from, to);
      res.json(data);
    }),
  );

  /**
   * GET /api/dashboard/release-quality-trend?from=&to=&granularity=
   * 发布质量趋势:每桶 发布数/回滚数/发布成功率/回滚率(MySQL only;SQLite available:false)。
   */
  router.get(
    "/release-quality-trend",
    asyncHandler(async (req, res) => {
      if (!dashboardRepo) {
        res
          .status(503)
          .json({ error: "Service Unavailable", message: "Database not configured" });
        return;
      }
      const range = parseTimeRange(req.query.from, req.query.to);
      if ("error" in range) {
        res.status(400).json({ error: "Bad Request", message: range.error });
        return;
      }
      const granularity = String(req.query.granularity ?? "week");
      if (!["day", "week", "month"].includes(granularity)) {
        res.status(400).json({ error: "Bad Request", message: "Invalid granularity" });
        return;
      }
      const { from, to } = range;
      const data = await dashboardRepo.getReleaseQualityTrend(
        from,
        to,
        granularity as "day" | "week" | "month",
      );
      res.json(data);
    }),
  );

  /**
   * GET /api/dashboard/workflow-metrics?workflowId=&from=&to=
   * L3 单工作流详情 —— 暂未真实化,返 available:false 占位(下一版接真)。
   */
  router.get(
    "/workflow-metrics",
    asyncHandler(async (req, res) => {
      const range = parseTimeRange(req.query.from, req.query.to);
      if ("error" in range) {
        res.status(400).json({ error: "Bad Request", message: range.error });
        return;
      }
      const workflowId = String(req.query.workflowId ?? "");
      res.json({
        available: false,
        workflowId,
        workflowTitle: "",
        sceneName: "",
        released: false,
        runCount: 0,
        completionSuccessRate: null,
        selfHealTriggeredRuns: 0,
        selfHealSuccessRate: null,
        machineDurationP50: null,
        trend: [],
        nodeHealth: [],
        failureHotspots: [],
      });
    }),
  );

  return router;
}
