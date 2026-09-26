import { describe, expect, it } from "vitest";
import express from "express";
import Database from "better-sqlite3";
import { join } from "node:path";
import { SqliteDatabase, runMigrations } from "@avernet/clawweb-shared/server/db";
import { InsightImprovementRepository } from "../../repositories/insight-improvement-repository.js";
import { FixtureInsightReadProvider } from "../../services/insight/providers/fixture-insight-read-provider.js";
import { FileEvidenceProvider } from "../../services/insight/providers/file-evidence-provider.js";
import { InsightService } from "../../services/insight/insight-service.js";
import { parseGovernanceGuidance } from "../../services/insight/governance-item.js";
import { createInsightRouter } from "../insight.js";

describe("rejection feedback fields", () => {
  it.each([
    ["管理员驳回", "ADMIN", "EXPECTED_BUSINESS_FAILURE"],
    ["Admin驳回", "ADMIN", "ADMIN_REJECTED"],
    ["用户驳回", "OWNER", "MISIDENTIFIED"],
  ])("parses %s without losing its reason", (title, rejectedBy, reason) => {
    const guidance = `[${title}]\n原因：${reason}\n说明：业务反馈\n时间：2026-09-10 16:16:16`;
    expect(parseGovernanceGuidance(guidance)).toMatchObject({
      rejectedBy,
      rejectReasonCode: reason,
      rejectComment: "业务反馈",
      rejectedAt: "2026-09-10 16:16:16",
    });
  });

  it.each([
    ["用户驳回", "管理员驳回", "ADMIN"],
    ["管理员驳回", "用户驳回", "OWNER"],
    ["Admin驳回", "管理员驳回", "ADMIN"],
    ["管理员驳回", "Admin驳回", "ADMIN"],
  ])("uses the latest %s then %s event consistently", (first, last, actor) => {
    const text = `[${first}]\n原因：OTHER\n说明：旧反馈\n时间：2026-09-09 10:00:00\n\n[${last}]\n原因：MISIDENTIFIED\n说明：最新反馈\n时间：2026-09-10 10:00:00`;
    expect(parseGovernanceGuidance(text)).toMatchObject({
      rejectedBy: actor, rejectReasonCode: "MISIDENTIFIED",
      rejectComment: "最新反馈", rejectedAt: "2026-09-10 10:00:00",
    });
  });

  it("does not treat ordinary guidance as a rejection", () => {
    expect(parseGovernanceGuidance("说明：普通反馈")).toMatchObject({
      rejectedBy: null, rejectReasonCode: null, rejectComment: null, rejectedAt: null,
    });
  });

  it.each(["ADMIN", "OWNER"] as const)(
    "returns complete feedback after the real %s reject action",
    async (actor) => {
      const db = new SqliteDatabase(new Database(":memory:"));
      await runMigrations(db, "sqlite");
      const fixtureRoot = join(process.cwd(), "server/fixtures/insight/v1");
      const service = new InsightService(
        new FixtureInsightReadProvider(fixtureRoot),
        new FileEvidenceProvider(fixtureRoot),
        new InsightImprovementRepository(db),
      );
      const app = express();
      app.use(express.json());
      app.use((req, _res, next) => {
        req.isAdmin = req.header("X-User-Id") === "admin-1";
        next();
      });
      app.use("/api/insight/v1", createInsightRouter(service));
      const server = await new Promise<ReturnType<typeof app.listen>>((resolve) => {
        const instance = app.listen(0, () => resolve(instance));
      });
      const base = `http://127.0.0.1:${(server.address() as { port: number }).port}/api/insight/v1`;
      try {
        const create = await fetch(`${base}/improvements`, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-User-Id": "dev_local", "Idempotency-Key": `rejection-${actor}` },
          body: JSON.stringify({
            botId: "20260603_fp6to0gv",
            title: "业务预期结果回归测试",
            userGuidance: "请核验该任务的业务预期。",
            selectedTasks: [{ sessionId: "7e82d8f2-a7f9-40ab-b0ac-7a6142ce3ca0", taskIndex: 0 }],
          }),
        });
        const item = await create.json();
        expect(create.status, JSON.stringify(item)).toBe(201);
        const path = actor === "ADMIN" ? "/admin/improvements" : "/improvements";
        const reject = await fetch(`${base}${path}/${item.improvementId}/reject`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-User-Id": actor === "ADMIN" ? "admin-1" : "dev_local",
          },
          body: JSON.stringify({
            version: item.version,
            reasonCode: "EXPECTED_BUSINESS_FAILURE",
            comment: "业务预期效果，不属于任务失败",
          }),
        });
        expect(reject.status).toBe(200);
        const expected = {
          improvementId: item.improvementId,
          rejectedBy: actor,
          rejectReasonCode: "EXPECTED_BUSINESS_FAILURE",
          rejectComment: "业务预期效果，不属于任务失败",
          rejectedAt: expect.any(String),
        };
        expect(await reject.json()).toMatchObject(expected);
        for (const filter of ["ANY", actor]) {
          const response = await fetch(`${base}/internal/governance/rejections/all?rejectedBy=${filter}`);
          expect(response.status).toBe(200);
          const result = await response.json();
          expect(result.total).toBe(1);
          expect(result.items).toEqual([expect.objectContaining(expected)]);
        }
        const opposite = actor === "ADMIN" ? "OWNER" : "ADMIN";
        const excluded = await fetch(`${base}/internal/governance/rejections/all?rejectedBy=${opposite}`);
        expect(await excluded.json()).toMatchObject({ total: 0, items: [] });
      } finally {
        await new Promise<void>((resolve) => server.close(() => resolve()));
        await db.close();
      }
    },
  );
});
