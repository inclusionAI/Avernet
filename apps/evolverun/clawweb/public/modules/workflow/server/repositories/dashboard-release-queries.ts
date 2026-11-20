import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import { productionRunsSql } from "@avernet/clawweb-shared/server/services/run-purpose";
import type { DashboardWorkflowFilter } from "./dashboard-query-scope.js";
import type {
  ReleaseEfficiencyRow, ReleaseEventRow, ReleaseQualityPoint, ReleaseQualityTrendRow,
  TrendGranularity, WorkflowReleaseStatRow, WorkflowReleaseStatsResult,
} from "./dashboard-types.js";

export class DashboardReleaseQueries {
  constructor(private readonly db: IDatabase, private readonly filter: DashboardWorkflowFilter) {}

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

    const { and, params } = this.filter;
    const clause = and();

    try {
      const rows = await this.db.query<{
        workflow_id: string;
        action: string;
        deploy_ts: number;
      }>(
        `SELECT workflow_id, action, UNIX_TIMESTAMP(gmt_create) AS deploy_ts
         FROM workflow_deploy_history
         WHERE action IN ('deploy', 'rollback')
           AND gmt_create BETWEEN FROM_UNIXTIME(?) AND FROM_UNIXTIME(?)${clause}
         ORDER BY gmt_create DESC
         LIMIT 200`,
        [from, to, ...params],
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
          `SELECT title FROM workflow_specs WHERE workflow_id = ?${clause}`,
          [d.workflow_id, ...params],
        );
        const workflowTitle = titleRows[0]?.title || d.workflow_id;

        // 最近一次 save(edit)→ 交付拖期
        const editRows = await this.db.query<{ last_edit: number | null }>(
          `SELECT UNIX_TIMESTAMP(MAX(gmt_create)) AS last_edit
           FROM workflow_deploy_history
           WHERE workflow_id = ? AND action = 'edit' AND gmt_create < FROM_UNIXTIME(?)${clause}`,
          [d.workflow_id, d.deploy_ts, ...params],
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
          `SELECT status FROM ${productionRunsSql()} flow_runs
           WHERE workflow_id = ? AND started_at > ?${clause}
           ORDER BY started_at ASC LIMIT 10`,
          [d.workflow_id, d.deploy_ts, ...params],
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
             AND gmt_create >= FROM_UNIXTIME(?)${clause}`,
          [d.workflow_id, d.deploy_ts, ...params],
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
      const msg = this.filter.restricted ? "scoped query unavailable" : error instanceof Error ? error.message : String(error);
      console.warn(`[db] DashboardRepository.getReleaseEfficiency failed: ${msg}`);
      return unavailable;
    }
  }

  /**
   * 发布事件按桶聚合(MySQL only):每桶 {deploy 数, distinct 工作流, 回滚数, 成功样本数, 拖期合计/样本数}。
   * 发布成功率口径 = deploy 后前 10 条 run 成功率≥0.9;交付拖期 = deploy − 最近一次 save(edit)。
   * 供 getReleaseQualityTrend 与 getMetricTrend 的 release 系 metric 共用。
   */
  async releaseBuckets(
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
    const { and, params } = this.filter;
    const clause = and();
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
         AND gmt_create BETWEEN FROM_UNIXTIME(?) AND FROM_UNIXTIME(?)${clause}
       ORDER BY gmt_create DESC
       LIMIT 300`,
      [from, to, ...params],
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
        `SELECT status FROM ${productionRunsSql()} flow_runs
         WHERE workflow_id = ? AND started_at > ?${clause}
         ORDER BY started_at ASC LIMIT 10`,
        [d.workflow_id, d.ts, ...params],
      );
      const fr = runRows.length;
      const fs = runRows.filter((r) => r.status === "succeeded").length;
      const rate = fr > 0 ? fs / fr : null;
      if (rate != null && rate >= 0.9) e.successN += 1;

      // 最近一次 save(edit)→ 交付拖期
      const editRows = await this.db.query<{ last_edit: number | null }>(
        `SELECT UNIX_TIMESTAMP(MAX(gmt_create)) AS last_edit
         FROM workflow_deploy_history
         WHERE workflow_id = ? AND action = 'edit' AND gmt_create < FROM_UNIXTIME(?)${clause}`,
        [d.workflow_id, d.ts, ...params],
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
      const msg = this.filter.restricted ? "scoped query unavailable" : error instanceof Error ? error.message : String(error);
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

    const { and, params } = this.filter;
    const clause = and();

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
           WHERE 1 = 1${clause}
           GROUP BY workflow_id
         ) dh ON dh.workflow_id = ws.workflow_id
         WHERE 1 = 1${and("ws.workflow_id")}
         ORDER BY COALESCE(dh.deploy_count, 0) DESC, ws.gmt_create DESC
         LIMIT 300`,
        [from, to, from, to, ...params, ...params],
      );

      // 窗口内的 deploy 事件 → 每工作流批均成功率
      const winDeploys = await this.db.query<{ workflow_id: string; ts: number }>(
        `SELECT workflow_id, UNIX_TIMESTAMP(gmt_create) AS ts
         FROM workflow_deploy_history
         WHERE action = 'deploy' AND gmt_create BETWEEN FROM_UNIXTIME(?) AND FROM_UNIXTIME(?)${clause}
         ORDER BY workflow_id, gmt_create ASC`,
        [from, to, ...params],
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
            `SELECT status FROM ${productionRunsSql()} flow_runs
             WHERE workflow_id = ? AND started_at > ?${clause}
             ORDER BY started_at ASC LIMIT 10`,
            [r.workflow_id, ts, ...params],
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
      const msg = this.filter.restricted ? "scoped query unavailable" : error instanceof Error ? error.message : String(error);
      console.warn(`[db] DashboardRepository.getWorkflowReleaseStats failed: ${msg}`);
      return { available: false, workflows: [] };
    }
  }
}
