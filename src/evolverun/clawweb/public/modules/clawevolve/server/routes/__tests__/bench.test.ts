/**
 * Tests for Bench API routes — domain-aware template, upload, run, and result lifecycle.
 */
import { describe, it, expect, beforeAll, afterAll } from "vitest";
import http from "node:http";
import express from "express";
import Database from "better-sqlite3";
import JSZip from "jszip";
import { SqliteDatabase, runMigrations } from "@avernet/clawweb-shared/server/db";
import { BenchDomainRepository } from "../../repositories/bench-domain-repository.js";
import { BenchTemplateRepository } from "../../repositories/bench-template-repository.js";
import { BenchTemplateVersionRepository } from "../../repositories/bench-template-version-repository.js";
import { BenchRunRepository } from "../../repositories/bench-run-repository.js";
import { BenchTaskResultRepository } from "../../repositories/bench-task-result-repository.js";
import { BenchArtifactRepository } from "../../repositories/bench-artifact-repository.js";
import { createBenchRouter } from "../bench.js";

let testDb: SqliteDatabase;
let app: express.Express;
let server: http.Server;
let port: number;
const TEST_USER = "test_user";
const OTHER_USER = "197444";

async function initTestDb() {
  const raw = new Database(":memory:");
  raw.pragma("journal_mode = WAL");
  raw.pragma("foreign_keys = ON");
  testDb = new SqliteDatabase(raw);
  await runMigrations(testDb, "sqlite");
}

function makeApp() {
  const domainRepo = new BenchDomainRepository(testDb);
  const templateRepo = new BenchTemplateRepository(testDb);
  const versionRepo = new BenchTemplateVersionRepository(testDb);
  const runRepo = new BenchRunRepository(testDb);
  const resultRepo = new BenchTaskResultRepository(testDb);
  const artifactRepo = new BenchArtifactRepository(testDb);

  const a = express();
  a.use(express.json({ limit: "10mb" }));
  a.use((req, _res, next) => {
    req.isClawEvolveAdmin = req.header("X-Test-Evolve-Admin") === "true";
    next();
  });
  a.use("/api/bench", createBenchRouter(domainRepo, templateRepo, versionRepo, runRepo, resultRepo, testDb, artifactRepo));
  return a;
}

/** Helper: POST/PUT/PATCH/DELETE JSON and return { status, body } */
function requestJson(
  method: string,
  path: string,
  body?: Record<string, unknown>,
  userId: string = TEST_USER,
): Promise<{ status: number; body: unknown }> {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : undefined;
    const req = http.request(
      {
        hostname: "127.0.0.1",
        port,
        path,
        method,
        headers: {
          "Content-Type": "application/json",
          "X-User-Id": userId,
          ...(payload ? { "Content-Length": Buffer.byteLength(payload) } : {}),
        },
      },
      (res) => {
        let data = "";
        res.on("data", (chunk) => (data += chunk));
        res.on("end", () => {
          try {
            resolve({ status: res.statusCode ?? 0, body: JSON.parse(data) });
          } catch {
            resolve({ status: res.statusCode ?? 0, body: data });
          }
        });
      },
    );
    req.on("error", (err) => reject(err));
    if (payload) req.write(payload);
    req.end();
  });
}

/** Helper: GET and return { status, body } */
function requestGet(path: string, userId: string = TEST_USER, evolveAdmin = false): Promise<{ status: number; body: unknown }> {
  return new Promise((resolve, reject) => {
    http.get({ hostname: "127.0.0.1", port, path, headers: { "X-User-Id": userId, "X-Test-Evolve-Admin": evolveAdmin ? "true" : "false" } }, (res) => {
      let data = "";
      res.on("data", (chunk) => (data += chunk));
      res.on("end", () => {
        try {
          resolve({ status: res.statusCode ?? 0, body: JSON.parse(data) });
        } catch {
          resolve({ status: res.statusCode ?? 0, body: data });
        }
      });
    }).on("error", reject);
  });
}

/** Helper: send multipart form-data */
function sendMultipart(
  path: string,
  files: Array<{ fieldName: string; filename: string; content: Buffer; contentType: string }>,
  userId: string = TEST_USER,
): Promise<{ status: number; body: unknown }> {
  return new Promise((resolve, reject) => {
    const boundary = `----FormBoundary${Math.random().toString(36).slice(2)}`;
    const parts: Buffer[] = [];

    for (const file of files) {
      parts.push(Buffer.from(
        `--${boundary}\r\nContent-Disposition: form-data; name="${file.fieldName}"; filename="${file.filename}"\r\nContent-Type: ${file.contentType}\r\n\r\n`,
      ));
      parts.push(file.content);
      parts.push(Buffer.from("\r\n"));
    }

    parts.push(Buffer.from(`--${boundary}--\r\n`));
    const body = Buffer.concat(parts);

    const req = http.request(
      {
        hostname: "127.0.0.1",
        port,
        path,
        method: "POST",
        headers: {
          "Content-Type": `multipart/form-data; boundary=${boundary}`,
          "Content-Length": body.length,
          "X-User-Id": userId,
        },
      },
      (res) => {
        let data = "";
        res.on("data", (chunk) => (data += chunk));
        res.on("end", () => {
          try {
            resolve({ status: res.statusCode ?? 0, body: JSON.parse(data) });
          } catch {
            resolve({ status: res.statusCode ?? 0, body: data });
          }
        });
      },
    );
    req.on("error", reject);
    req.write(body);
    req.end();
  });
}

beforeAll(async () => {
  await initTestDb();
  app = makeApp();
  server = app.listen(0);
  const addr = server.address();
  if (typeof addr === "string" || !addr) {
    throw new Error("Failed to get server address");
  }
  port = addr.port;
});

afterAll(async () => {
  server.close();
  await testDb.close();
});

describe("Bench Domain-Aware API", () => {
  describe("Domains", () => {
    it("creates a domain", async () => {
      const res = await requestJson("POST", "/api/bench/domains", {
        domainId: "yuque_bench",
        name: "Yuque Bench",
        description: "Yuque MCP benchmark domain",
      });
      expect(res.status).toBe(201);
      const body = res.body as Record<string, unknown>;
      expect(body.domainId).toBe("yuque_bench");
      expect(body.name).toBe("Yuque Bench");
      expect(body.ownerUserId).toBe(TEST_USER);
    });

    it("creates a numeric-prefix domain without treating it as an owner ref", async () => {
      const res = await requestJson("POST", "/api/bench/domains", {
        domainId: "0715_test",
        name: "0715 Test",
      }, "542527");
      expect(res.status).toBe(201);
      const body = res.body as Record<string, unknown>;
      expect(body.domainId).toBe("0715_test");
      expect(body.ownerUserId).toBe("542527");
    });

    it("returns 401 without user identity on POST", async () => {
      const res = await requestJson("POST", "/api/bench/domains", {
        domainId: "test_bench",
        name: "Test Bench",
      }, "");
      expect(res.status).toBe(401);
    });

    it("returns 401 without user identity on GET list", async () => {
      const res = await requestGet("/api/bench/domains", "");
      expect(res.status).toBe(401);
    });

    it("returns 409 for duplicate domainId by same user", async () => {
      const res = await requestJson("POST", "/api/bench/domains", {
        domainId: "yuque_bench",
        name: "Duplicate",
      });
      expect(res.status).toBe(409);
    });

    it("allows same domainId for different users", async () => {
      const res = await requestJson("POST", "/api/bench/domains", {
        domainId: "yuque_bench",
        name: "Yuque Bench Other",
      }, OTHER_USER);
      expect(res.status).toBe(201);
      const body = res.body as Record<string, unknown>;
      expect(body.domainId).toBe("yuque_bench");
      expect(body.ownerUserId).toBe(OTHER_USER);
    });

    it("lists domains with template counts for current owner only", async () => {
      const res = await requestGet("/api/bench/domains");
      expect(res.status).toBe(200);
      const body = res.body as Array<Record<string, unknown>>;
      // Should only return TEST_USER's domains, not OTHER_USER's
      expect(body.every((d) => d.ownerUserId === TEST_USER)).toBe(true);
      const yuque = body.find((d) => d.domainId === "yuque_bench");
      expect(yuque).toBeDefined();
      expect(yuque?.templateCount).toBe(0);
    });

    it("gets a domain by id for current user", async () => {
      const res = await requestGet("/api/bench/domains/yuque_bench");
      expect(res.status).toBe(200);
      const body = res.body as Record<string, unknown>;
      expect(body.domainId).toBe("yuque_bench");
      expect(body.ownerUserId).toBe(TEST_USER);
    });

    it("gets other user's domain via prefixed domainId", async () => {
      const res = await requestGet(`/api/bench/domains/${OTHER_USER}_yuque_bench`);
      expect(res.status).toBe(200);
      const body = res.body as Record<string, unknown>;
      expect(body.domainId).toBe("yuque_bench");
      expect(body.ownerUserId).toBe(OTHER_USER);
    });

    it("updates a domain", async () => {
      const res = await requestJson("PUT", "/api/bench/domains/yuque_bench", {
        name: "Yuque Bench Updated",
      });
      expect(res.status).toBe(200);
      const body = res.body as Record<string, unknown>;
      expect(body.name).toBe("Yuque Bench Updated");
    });

    it("returns 403 when updating other user's domain", async () => {
      const res = await requestJson("PUT", `/api/bench/domains/${OTHER_USER}_yuque_bench`, {
        name: "Hacked",
      });
      expect(res.status).toBe(403);
    });
  });

  describe("Upload single markdown", () => {
    it("uploads and imports a single .md file", async () => {
      const content = Buffer.from("---\nid: task_01_user_info\nname: 获取用户团队和知识库列表\n---\n\n## Prompt\n查询我加入的所有yuque团队。\n");
      const scanRes = await sendMultipart("/api/bench/domains/yuque_bench/uploads/scan", [
        { fieldName: "files", filename: "task_01_user_info.md", content, contentType: "text/markdown" },
      ]);
      expect(scanRes.status).toBe(200);
      const scanBody = scanRes.body as Record<string, unknown>;
      expect(scanBody.domainId).toBe("yuque_bench");
      const summary = scanBody.summary as Record<string, number>;
      expect(summary.new).toBe(1);
      expect(summary.conflict).toBe(0);
      expect(summary.imported).toBe(1);

      const items = scanBody.items as Array<Record<string, unknown>>;
      expect(items[0].action).toBe("new");
      expect(items[0].templateName).toBe("task_01_user_info");
      expect(items[0].displayName).toBe("获取用户团队和知识库列表");
      expect(items[0].imported).toBe(true);
    });
  });

  describe("Upload multiple markdowns", () => {
    it("uploads and imports multiple .md files", async () => {
      const files = [
        { filename: "task_00_sanity.md", content: "---\nid: task_00_sanity\nname: Sanity Check\n---\n## Prompt\nTest.\n" },
        { filename: "task_01_user_info.md", content: "---\nid: task_01_user_info\nname: User Info\n---\n## Prompt\nGet user info.\n" },
      ];
      const scanRes = await sendMultipart("/api/bench/domains/yuque_bench/uploads/scan", files.map((f) => ({
        fieldName: "files",
        filename: f.filename,
        content: Buffer.from(f.content),
        contentType: "text/markdown",
      })));
      expect(scanRes.status).toBe(200);
      const scanBody = scanRes.body as Record<string, unknown>;
      const summary = scanBody.summary as Record<string, number>;
      // task_01_user_info already exists from previous test, should be update (since content changed)
      expect(summary.new + summary.update).toBe(2);
      expect(summary.conflict).toBe(0);
      expect(summary.imported).toBe(2);
    });
  });

  describe("Upload zip", () => {
    it("uploads and imports a .zip containing .md files", async () => {
      const zip = new JSZip();
      zip.file("task_02_book_list.md", "---\nid: task_02_book_list\nname: Book List\n---\n## Prompt\nList books.\n");
      zip.file("README.md", "# README\n"); // should be skipped
      const zipBuffer = await zip.generateAsync({ type: "nodebuffer" });

      const scanRes = await sendMultipart("/api/bench/domains/yuque_bench/uploads/scan", [
        { fieldName: "files", filename: "templates.zip", content: zipBuffer, contentType: "application/zip" },
      ]);
      expect(scanRes.status).toBe(200);
      const scanBody = scanRes.body as Record<string, unknown>;
      const items = scanBody.items as Array<Record<string, unknown>>;
      expect(items.length).toBe(1);
      expect(items[0].templateName).toBe("task_02_book_list");
      expect(items[0].action).toBe("new");
    });
  });

  describe("Templates List", () => {
    it("returns 401 without user identity on GET /templates", async () => {
      const res = await requestGet("/api/bench/templates", "");
      expect(res.status).toBe(401);
    });

    it("lists only current owner's templates on GET /templates", async () => {
      const res = await requestGet("/api/bench/templates");
      expect(res.status).toBe(200);
      const body = res.body as Array<Record<string, unknown>>;
      expect(body.every((t) => t.ownerUserId === TEST_USER)).toBe(true);
    });
  });

  describe("Template Uniqueness & Versioning", () => {
    it("same templateName in same domain updates draft on re-upload", async () => {
      const content = Buffer.from("---\nid: task_reupload_skip\nname: Reupload Skip\n---\n\n## Prompt\nSame content.\n");
      const firstScanRes = await sendMultipart("/api/bench/domains/yuque_bench/uploads/scan", [
        { fieldName: "files", filename: "task_reupload_skip.md", content, contentType: "text/markdown" },
      ]);
      expect(firstScanRes.status).toBe(200);

      const secondScanRes = await sendMultipart("/api/bench/domains/yuque_bench/uploads/scan", [
        { fieldName: "files", filename: "task_reupload_skip.md", content, contentType: "text/markdown" },
      ]);
      expect(secondScanRes.status).toBe(200);
      const scanBody = secondScanRes.body as Record<string, unknown>;
      const items = scanBody.items as Array<Record<string, unknown>>;
      const item = items.find((i) => i.templateName === "task_reupload_skip");
      // Content hash unchanged → skip
      expect(item?.action).toBe("skip");
    });

    it("re-uploading an archived template restores it instead of skipping unchanged content", async () => {
      const content = Buffer.from("---\nid: task_archived_reupload\nname: Archived Reupload\n---\n\n## Prompt\nSame content.\n");
      const firstScanRes = await sendMultipart("/api/bench/domains/yuque_bench/uploads/scan", [
        { fieldName: "files", filename: "task_archived_reupload.md", content, contentType: "text/markdown" },
      ]);
      expect(firstScanRes.status).toBe(200);

      const archiveRes = await requestJson("DELETE", "/api/bench/domains/yuque_bench/templates/task_archived_reupload");
      expect(archiveRes.status).toBe(200);

      const secondScanRes = await sendMultipart("/api/bench/domains/yuque_bench/uploads/scan", [
        { fieldName: "files", filename: "task_archived_reupload.md", content, contentType: "text/markdown" },
      ]);
      expect(secondScanRes.status).toBe(200);
      const scanBody = secondScanRes.body as Record<string, unknown>;
      const items = scanBody.items as Array<Record<string, unknown>>;
      const item = items.find((i) => i.templateName === "task_archived_reupload");
      expect(item?.action).toBe("update");
      expect(item?.imported).toBe(true);
      expect(item?.reason).toBe("archived template will be restored");

      const templateRes = await requestGet("/api/bench/domains/yuque_bench/templates/task_archived_reupload");
      expect(templateRes.status).toBe(200);
      const template = templateRes.body as Record<string, unknown>;
      expect(template.status).toBe("draft");
    });

    it("same templateName in different domain is allowed", async () => {
      await requestJson("POST", "/api/bench/domains", {
        domainId: "another_bench",
        name: "Another Bench",
      });

      const content = Buffer.from("---\nid: task_01_user_info\nname: 另一个域名的同名模板\n---\n## Prompt\nTest.\n");
      const scanRes = await sendMultipart("/api/bench/domains/another_bench/uploads/scan", [
        { fieldName: "files", filename: "task_01_user_info.md", content, contentType: "text/markdown" },
      ]);
      expect(scanRes.status).toBe(200);
      const scanBody = scanRes.body as Record<string, unknown>;
      const items = scanBody.items as Array<Record<string, unknown>>;
      expect(items[0].action).toBe("new");
    });

    it("publishes a draft version", async () => {
      const res = await requestJson("POST", "/api/bench/domains/yuque_bench/templates/task_01_user_info/publish");
      expect(res.status).toBe(200);
      const body = res.body as Record<string, unknown>;
      expect(body.publishedVersion).toBe(1);
      expect(body.status).toBe("published");
    });

    it("batch publishes draft templates", async () => {
      const uploadRes = await sendMultipart("/api/bench/domains/yuque_bench/uploads/scan", [
        { fieldName: "files", filename: "task_batch_publish.md", content: Buffer.from("---\nid: task_batch_publish\nname: Batch Publish\n---\n## Prompt\nBatch.\n"), contentType: "text/markdown" },
        { fieldName: "files", filename: "task_batch_invalid.md", content: Buffer.from("---\nid: task_batch_invalid\nname: Batch Invalid\n---\n# Prompt\nBroken.\n"), contentType: "text/markdown" },
      ]);
      expect(uploadRes.status).toBe(200);

      const res = await requestJson("POST", "/api/bench/domains/yuque_bench/templates/batch-publish", {
        templates: [{ templateName: "task_batch_publish" }, { templateName: "task_batch_invalid" }, { templateName: "not_exists" }],
      });
      expect(res.status).toBe(200);
      const body = res.body as Record<string, unknown>;
      expect(body.published).toBe(1);
      expect(body.failed).toBe(2);
      const items = body.items as Array<Record<string, unknown>>;
      expect(items.find((item) => item.templateName === "task_batch_publish")?.success).toBe(true);
      const invalidItem = items.find((item) => item.templateName === "task_batch_invalid");
      expect(invalidItem?.success).toBe(false);
      expect(invalidItem?.validator_error_message).toContain("# Prompt");
      expect(invalidItem?.validator_error_message).toContain("## Prompt");
      expect(items.find((item) => item.templateName === "not_exists")?.success).toBe(false);
    });

    it("rejects publishing a template that old ClawBench cannot parse into a prompt", async () => {
      const uploadRes = await sendMultipart("/api/bench/domains/yuque_bench/uploads/scan", [
        { fieldName: "files", filename: "task_invalid_prompt_heading.md", content: Buffer.from("---\nid: task_invalid_prompt_heading\nname: Invalid Prompt Heading\n---\n# Prompt\nBroken.\n"), contentType: "text/markdown" },
      ]);
      expect(uploadRes.status).toBe(200);

      const res = await requestJson("POST", "/api/bench/domains/yuque_bench/templates/task_invalid_prompt_heading/publish");

      expect(res.status).toBe(400);
      const body = res.body as Record<string, unknown>;
      expect(body.error).toBe("Template validation failed");
      expect(body.validator_error_message).toContain("# Prompt");
      expect(body.validator_error_message).toContain("## Prompt");
    });

    it("published version cannot be edited in place", async () => {
      // Try to PUT the published template with new content — should create v2 draft
      const nextContent = "---\nid: task_01_user_info\nname: 获取用户团队和知识库列表\n---\n## Prompt\nModified prompt.\n";
      const res = await requestJson("PUT", "/api/bench/domains/yuque_bench/templates/task_01_user_info", {
        contentMd: nextContent,
      });
      expect(res.status).toBe(200);
      const body = res.body as Record<string, unknown>;
      expect(body.latestVersion).toBe(2);
      expect(body.status).toBe("draft");
      const versions = body.versions as Array<Record<string, unknown>>;
      expect(versions.find((v) => v.version === 2)?.status).toBe("draft");
      expect(versions.find((v) => v.version === 2)?.contentMd).toBe(nextContent);

      const getRes = await requestGet("/api/bench/domains/yuque_bench/templates/task_01_user_info");
      expect(getRes.status).toBe(200);
      const getBody = getRes.body as Record<string, unknown>;
      expect(getBody.latestVersion).toBe(2);
      const getVersions = getBody.versions as Array<Record<string, unknown>>;
      expect(getVersions.find((v) => v.version === 2)?.contentMd).toBe(nextContent);
    });

    it("version repo update throws for published version", async () => {
      const versionRepo = new BenchTemplateVersionRepository(testDb);
      await expect(
        versionRepo.update(TEST_USER, "yuque_bench", "task_01_user_info", 1, {
          contentMd: "should fail",
        }),
      ).rejects.toThrow(/published/);
    });
  });

  describe("Conflict Detection", () => {
    it("detects conflicts when two files resolve to same templateName in one batch", async () => {
      const files = [
        { filename: "a.md", content: "---\nid: same_name\n---\n# A\n" },
        { filename: "b.md", content: "---\nid: same_name\n---\n# B\n" },
      ];
      const scanRes = await sendMultipart("/api/bench/domains/yuque_bench/uploads/scan", files.map((f) => ({
        fieldName: "files",
        filename: f.filename,
        content: Buffer.from(f.content),
        contentType: "text/markdown",
      })));
      expect(scanRes.status).toBe(200);
      const scanBody = scanRes.body as Record<string, unknown>;
      const items = scanBody.items as Array<Record<string, unknown>>;
      expect(items.length).toBe(2);
      expect(items[0].action).toBe("conflict");
      expect(items[1].action).toBe("conflict");
    });
  });

  describe("Skip Excluded Files", () => {
    it("skips README.md and TASK_TEMPLATE.md", async () => {
      const files = [
        { filename: "README.md", content: "# README\n" },
        { filename: "TASK_TEMPLATE.md", content: "# Template\n" },
        { filename: "valid_task.md", content: "---\nid: valid_task\n---\n# Valid\n" },
      ];
      const scanRes = await sendMultipart("/api/bench/domains/yuque_bench/uploads/scan", files.map((f) => ({
        fieldName: "files",
        filename: f.filename,
        content: Buffer.from(f.content),
        contentType: "text/markdown",
      })));
      expect(scanRes.status).toBe(200);
      const scanBody = scanRes.body as Record<string, unknown>;
      const items = scanBody.items as Array<Record<string, unknown>>;
      expect(items.length).toBe(1);
      expect(items[0].templateName).toBe("valid_task");
    });
  });

  describe("Runs", () => {
    it("creates a bench run with domainId + templateName + templateVersion", async () => {
      const res = await requestJson("POST", "/api/bench/runs", {
        domainId: "yuque_bench",
        templateName: "task_01_user_info",
        templateVersion: 1,
        model: "antchat/GLM-5",
        suite: "all",
      });
      expect(res.status).toBe(201);
      const body = res.body as { benchRunId: string; detailUrl: string };
      expect(body.benchRunId).toMatch(/^bench_/);
      expect(body.detailUrl).toContain("/bench/runs/");
    });

    it("uses explicit template owner independently of the request user", async () => {
      const res = await requestJson("POST", "/api/bench/runs", {
        ownerId: OTHER_USER,
        domainId: "shared-domain",
        templateName: "__domain__",
        templateVersion: 0,
      }, TEST_USER);
      expect(res.status).toBe(201);
      const { benchRunId } = res.body as { benchRunId: string };
      const rows = await testDb.query<{ owner_user_id: string; domain_id: string }>(
        "SELECT owner_user_id, domain_id FROM cm_bench_runs WHERE bench_run_id = ?",
        [benchRunId],
      );
      expect(rows[0]).toEqual({ owner_user_id: OTHER_USER, domain_id: "shared-domain" });
    });

    it("returns 400 when missing required run fields", async () => {
      const res = await requestJson("POST", "/api/bench/runs", {
        domainId: "yuque_bench",
        templateName: "task_01_user_info",
        // missing templateVersion
      });
      expect(res.status).toBe(400);
    });

    it("gets a bench run", async () => {
      const createRes = await requestJson("POST", "/api/bench/runs", {
        domainId: "yuque_bench",
        templateName: "task_01_user_info",
        templateVersion: 1,
      });
      const { benchRunId } = createRes.body as { benchRunId: string };

      const res = await requestGet(`/api/bench/runs/${benchRunId}`);
      expect(res.status).toBe(200);
      const body = res.body as Record<string, unknown>;
      expect(body.benchRunId).toBe(benchRunId);
      expect(body.status).toBe("running");
      expect(body.domainId).toBe("yuque_bench");
      expect(body.templateName).toBe("task_01_user_info");
      expect(body.templateVersion).toBe(1);
    });

    it("updates a bench run", async () => {
      const createRes = await requestJson("POST", "/api/bench/runs", {
        domainId: "yuque_bench",
        templateName: "task_01_user_info",
        templateVersion: 1,
      });
      const { benchRunId } = createRes.body as { benchRunId: string };

      const res = await requestJson("PUT", `/api/bench/runs/${benchRunId}`, {
        status: "succeeded",
        score: 0.82,
        maxScore: 1.0,
        passRate: 0.82,
        summary: { taskCount: 5, succeededCount: 4, failedCount: 1 },
      });
      expect(res.status).toBe(200);
      const body = res.body as Record<string, unknown>;
      expect(body.status).toBe("succeeded");
      expect(body.score).toBe(0.82);
    });

    it("derives token usage from run summary efficiency", async () => {
      const createRes = await requestJson("POST", "/api/bench/runs", {
        domainId: "yuque_bench",
        templateName: "task_01_user_info",
        templateVersion: 1,
      });
      const { benchRunId } = createRes.body as { benchRunId: string };

      const res = await requestJson("PUT", `/api/bench/runs/${benchRunId}`, {
        status: "succeeded",
        summary: {
          efficiency: {
            total_tokens: 191136,
            total_input_tokens: 82073,
            total_output_tokens: 1479,
            total_requests: 4,
          },
        },
      });
      expect(res.status).toBe(200);
      const body = res.body as Record<string, unknown>;
      const tokenUsage = body.tokenUsage as Record<string, unknown>;
      expect(tokenUsage.totalTokens).toBe(191136);
      expect(tokenUsage.inputTokens).toBe(82073);
      expect(tokenUsage.requestCount).toBe(4);
    });

    it("lists runs by domain and template", async () => {
      const res = await requestGet("/api/bench/domains/yuque_bench/templates/task_01_user_info/runs");
      expect(res.status).toBe(200);
      const body = res.body as Array<Record<string, unknown>>;
      expect(body.length).toBeGreaterThanOrEqual(1);
      expect(body[0].domainId).toBe("yuque_bench");
      expect(body[0].templateName).toBe("task_01_user_info");
    });

    it("returns 401 without user identity on GET /runs", async () => {
      const res = await requestGet("/api/bench/runs", "");
      expect(res.status).toBe(401);
    });

    it("lists only current owner's runs on GET /runs", async () => {
      // Create a run as OTHER_USER first
      await requestJson("POST", "/api/bench/runs", {
        domainId: "yuque_bench",
        templateName: "task_01_user_info",
        templateVersion: 1,
      }, OTHER_USER);

      const res = await requestGet("/api/bench/runs");
      expect(res.status).toBe(200);
      const body = res.body as { runs: Array<Record<string, unknown>>; total: number };
      expect(body.runs.every((r) => r.ownerUserId === TEST_USER)).toBe(true);
    });

    it("lists runs by explicit ownerUserId for current owner", async () => {
      const res = await requestGet(`/api/bench/runs?ownerUserId=${TEST_USER}&domainId=yuque_bench`);
      expect(res.status).toBe(200);
      const body = res.body as { runs: Array<Record<string, unknown>>; total: number };
      expect(body.runs.length).toBeGreaterThanOrEqual(1);
      expect(body.runs.every((r) => r.ownerUserId === TEST_USER && r.domainId === "yuque_bench")).toBe(true);
    });

    it("rejects explicit ownerUserId for another owner on GET /runs", async () => {
      const res = await requestGet(`/api/bench/runs?ownerUserId=${OTHER_USER}&domainId=yuque_bench`);
      expect(res.status).toBe(403);
    });

    it("keeps admin run listing on a separate endpoint", async () => {
      const forbidden = await requestGet("/api/bench/admin/runs");
      expect(forbidden.status).toBe(403);

      const allRuns = await requestGet("/api/bench/admin/runs?limit=200", TEST_USER, true);
      expect(allRuns.status).toBe(200);
      const allBody = allRuns.body as { runs: Array<Record<string, unknown>>; total: number };
      expect(allBody.runs.some((run) => run.ownerUserId === TEST_USER)).toBe(true);
      expect(allBody.runs.some((run) => run.ownerUserId === OTHER_USER)).toBe(true);

      const filtered = await requestGet(`/api/bench/admin/runs?ownerUserId=${OTHER_USER}`, TEST_USER, true);
      expect(filtered.status).toBe(200);
      const filteredBody = filtered.body as { runs: Array<Record<string, unknown>> };
      expect(filteredBody.runs.length).toBeGreaterThanOrEqual(1);
      expect(filteredBody.runs.every((run) => run.ownerUserId === OTHER_USER)).toBe(true);
    });

    it("creates a domain run with templateName=__domain__ and templateVersion=0", async () => {
      const res = await requestJson("POST", "/api/bench/runs", {
        domainId: "yuque_bench",
        templateName: "__domain__",
        templateVersion: 0,
        model: "antchat/GLM-5",
        suite: "all",
        runConfig: {
          runScope: "domain",
          templateCount: 3,
          templates: [
            { templateName: "task_00_sanity", templateVersion: 1 },
            { templateName: "task_01_user_info", templateVersion: 1 },
          ],
        },
      });
      expect(res.status).toBe(201);
      const body = res.body as { benchRunId: string; detailUrl: string };
      expect(body.benchRunId).toMatch(/^bench_/);

      // Verify GET returns runScope and templateCount
      const getRes = await requestGet(`/api/bench/runs/${body.benchRunId}`);
      expect(getRes.status).toBe(200);
      const run = getRes.body as Record<string, unknown>;
      expect(run.runScope).toBe("domain");
      expect(run.templateCount).toBe(3);
      expect(run.templateName).toBe("__domain__");
      expect(run.templateVersion).toBe(0);
    });

    it("includes domain runs in template recent runs when run config contains the template", async () => {
      const createRes = await requestJson("POST", "/api/bench/runs", {
        domainId: "yuque_bench",
        templateName: "__domain__",
        templateVersion: 0,
        runConfig: {
          runScope: "domain",
          templateCount: 2,
          templates: [
            { templateName: "task_00_sanity", templateVersion: 1 },
            { templateName: "task_01_user_info", templateVersion: 1 },
          ],
        },
      });
      const { benchRunId } = createRes.body as { benchRunId: string };

      const res = await requestGet(`/api/bench/domains/${TEST_USER}/yuque_bench/templates/task_00_sanity/runs`);
      expect(res.status).toBe(200);
      const body = res.body as Array<Record<string, unknown>>;
      const run = body.find((r) => r.benchRunId === benchRunId);
      expect(run).toBeTruthy();
      expect(run?.runScope).toBe("domain");
      expect(run?.templateName).toBe("__domain__");
    });

    it("defaults runScope to template for regular runs", async () => {
      const res = await requestJson("POST", "/api/bench/runs", {
        domainId: "yuque_bench",
        templateName: "task_01_user_info",
        templateVersion: 1,
      });
      expect(res.status).toBe(201);
      const { benchRunId } = res.body as { benchRunId: string };

      const getRes = await requestGet(`/api/bench/runs/${benchRunId}`);
      expect(getRes.status).toBe(200);
      const run = getRes.body as Record<string, unknown>;
      expect(run.runScope).toBe("template");
      expect(run.templateCount).toBeNull();
    });
  });

  describe("Results", () => {
    it("batch creates task results", async () => {
      const createRes = await requestJson("POST", "/api/bench/runs", {
        domainId: "yuque_bench",
        templateName: "task_01_user_info",
        templateVersion: 1,
      });
      const { benchRunId } = createRes.body as { benchRunId: string };

      const res = await requestJson("POST", `/api/bench/runs/${benchRunId}/results`, {
        results: [
          {
            resultId: "res_1",
            taskId: "task_01",
            taskName: "Task One",
            status: "succeeded",
            score: 0.8,
            maxScore: 1.0,
            gradingType: "hybrid",
            executionTimeMs: 43000,
            transcriptPath: "/path/to/transcript.jsonl",
            resultJson: {
              usage: {
                input_tokens: 100,
                output_tokens: 20,
                cache_read_tokens: 30,
                total_tokens: 150,
                request_count: 2,
              },
            },
            breakdown: { tool_used: 1.0, answer_quality: 0.6 },
            notes: "Minor completeness issues",
          },
        ],
      });
      expect(res.status).toBe(201);
      const body = res.body as { created: number; results: Array<Record<string, unknown>> };
      expect(body.created).toBe(1);
      expect(body.results[0].taskId).toBe("task_01");
      const tokenUsage = body.results[0].tokenUsage as Record<string, unknown>;
      expect(tokenUsage.totalTokens).toBe(150);
    });

    it("upserts task results with the same resultId", async () => {
      const createRes = await requestJson("POST", "/api/bench/runs", {
        domainId: "yuque_bench",
        templateName: "task_01_user_info",
        templateVersion: 1,
      });
      const { benchRunId } = createRes.body as { benchRunId: string };

      const firstRes = await requestJson("POST", `/api/bench/runs/${benchRunId}/results`, {
        results: [{ resultId: "res_incremental_1", taskId: "task_01", status: "running", score: 0.2, maxScore: 1 }],
      });
      expect(firstRes.status).toBe(201);

      const secondRes = await requestJson("POST", `/api/bench/runs/${benchRunId}/results`, {
        results: [{ resultId: "res_incremental_1", taskId: "task_01", status: "succeeded", score: 1, maxScore: 1 }],
      });
      expect(secondRes.status).toBe(201);

      const listRes = await requestGet(`/api/bench/runs/${benchRunId}/results`);
      expect(listRes.status).toBe(200);
      const rows = listRes.body as Array<Record<string, unknown>>;
      const row = rows.find((item) => item.resultId === "res_incremental_1");
      expect(row?.status).toBe("succeeded");
      expect(row?.score).toBe(1);
      expect(rows.filter((item) => item.resultId === "res_incremental_1").length).toBe(1);
    });

    it("lists task results for a run", async () => {
      const createRes = await requestJson("POST", "/api/bench/runs", {
        domainId: "yuque_bench",
        templateName: "task_01_user_info",
        templateVersion: 1,
      });
      const { benchRunId } = createRes.body as { benchRunId: string };

      await requestJson("POST", `/api/bench/runs/${benchRunId}/results`, {
        results: [{ taskId: "task_02", status: "failed", errorText: "Timeout" }],
      });

      const res = await requestGet(`/api/bench/runs/${benchRunId}/results`);
      expect(res.status).toBe(200);
      const body = res.body as Array<Record<string, unknown>>;
      expect(body.length).toBeGreaterThanOrEqual(1);
    });

    it("creates task results with templateName/templateVersion/templateTaskId in resultJson", async () => {
      const createRes = await requestJson("POST", "/api/bench/runs", {
        domainId: "yuque_bench",
        templateName: "__domain__",
        templateVersion: 0,
        runConfig: {
          runScope: "domain",
          templateCount: 2,
          templates: [
            { taskId: "task_00_sanity", templateName: "task_00_sanity", templateVersion: 1 },
            { taskId: "custom_task_id", templateName: "task_01_user_info", templateVersion: 2 },
          ],
        },
      });
      const { benchRunId } = createRes.body as { benchRunId: string };

      const res = await requestJson("POST", `/api/bench/runs/${benchRunId}/results`, {
        results: [
          {
            taskId: "task_00_sanity",
            status: "succeeded",
            score: 1,
            maxScore: 1,
            resultJson: { score: 1, templateName: "task_00_sanity", templateVersion: 1, templateTaskId: "task_00_sanity" },
          },
          {
            taskId: "custom_task_id",
            status: "failed",
            score: 0,
            maxScore: 1,
            resultJson: { score: 0, templateName: "task_01_user_info", templateVersion: 2, templateTaskId: "custom_task_id" },
          },
        ],
      });
      expect(res.status).toBe(201);
      const body = res.body as { created: number; results: Array<Record<string, unknown>> };
      expect(body.created).toBe(2);
      const r0 = body.results[0].resultJson as Record<string, unknown>;
      expect(r0.templateName).toBe("task_00_sanity");
      expect(r0.templateVersion).toBe(1);
      expect(r0.templateTaskId).toBe("task_00_sanity");
      const r1 = body.results[1].resultJson as Record<string, unknown>;
      expect(r1.templateName).toBe("task_01_user_info");
      expect(r1.templateVersion).toBe(2);
      expect(r1.templateTaskId).toBe("custom_task_id");
    });

    it("domain run GET does not expose misleading templateVersion=1", async () => {
      const createRes = await requestJson("POST", "/api/bench/runs", {
        domainId: "yuque_bench",
        templateName: "__domain__",
        templateVersion: 0,
        runConfig: { runScope: "domain", templateCount: 3, templates: [{ templateName: "task_00_sanity", templateVersion: 1 }] },
      });
      const { benchRunId } = createRes.body as { benchRunId: string };

      const getRes = await requestGet(`/api/bench/runs/${benchRunId}`);
      expect(getRes.status).toBe(200);
      const run = getRes.body as Record<string, unknown>;
      expect(run.runScope).toBe("domain");
      expect(run.templateCount).toBe(3);
      expect(run.templateName).toBe("__domain__");
      expect(run.templateVersion).toBe(0);
    });
  });

  describe("Artifacts and Sessions", () => {
    it("uploads benchmark report and session artifact, then lists sessions", async () => {
      const createRes = await requestJson("POST", "/api/bench/runs", {
        domainId: "yuque_bench",
        templateName: "task_01_user_info",
        templateVersion: 1,
      });
      const { benchRunId } = createRes.body as { benchRunId: string };

      const reportRes = await requestJson("POST", `/api/bench/runs/${benchRunId}/artifacts`, {
        artifactType: "benchmark_report",
        filename: "benchmark_report.json",
        contentJson: {
          tasks: [{ task_id: "task_01_user_info" }],
          efficiency: { total_tokens: 100 },
        },
      });
      expect(reportRes.status).toBe(201);

      const jsonl = [
        JSON.stringify({ type: "session", timestamp: "2026-06-16T00:00:00.000Z" }),
        JSON.stringify({ type: "message", timestamp: "2026-06-16T00:00:01.000Z", message: { role: "assistant", usage: { input: 10, output: 2, totalTokens: 12 } } }),
      ].join("\n");
      const sessionRes = await requestJson("POST", `/api/bench/runs/${benchRunId}/artifacts`, {
        artifactType: "session_trace",
        taskId: "task_01_user_info",
        filename: "task_01_user_info.jsonl",
        contentType: "application/jsonl",
        contentText: jsonl,
      });
      expect(sessionRes.status).toBe(201);
      const sessionArtifact = sessionRes.body as Record<string, unknown>;

      const listRes = await requestGet(`/api/bench/runs/${benchRunId}/sessions`);
      expect(listRes.status).toBe(200);
      const listBody = listRes.body as { sessions: Array<Record<string, unknown>> };
      expect(listBody.sessions.length).toBe(1);
      expect(listBody.sessions[0].taskId).toBe("task_01_user_info");
      expect(listBody.sessions[0].eventCount).toBe(2);
      expect(listBody.sessions[0].totalTokens).toBe(12);

      const detailRes = await requestGet(`/api/bench/runs/${benchRunId}/sessions/task_01_user_info`);
      expect(detailRes.status).toBe(200);
      const detail = detailRes.body as Record<string, unknown>;
      expect((detail.events as unknown[]).length).toBe(2);

      const artifactDetailRes = await requestGet(`/api/bench/runs/${benchRunId}/sessions/artifacts/${sessionArtifact.artifactId}`);
      expect(artifactDetailRes.status).toBe(200);
      const artifactDetail = artifactDetailRes.body as Record<string, unknown>;
      expect(artifactDetail.artifactId).toBe(sessionArtifact.artifactId);
      expect((artifactDetail.events as unknown[]).length).toBe(2);

      const forbidden = await requestGet(`/api/bench/artifacts/${sessionArtifact.artifactId}/content`, OTHER_USER);
      expect(forbidden.status).toBe(403);
    });

    it("dedupes identical session artifact uploads", async () => {
      const createRes = await requestJson("POST", "/api/bench/runs", {
        domainId: "yuque_bench",
        templateName: "task_01_user_info",
        templateVersion: 1,
      });
      const { benchRunId } = createRes.body as { benchRunId: string };
      const payload = {
        artifactType: "session",
        taskId: "task_01_user_info",
        filename: "task_01_user_info.jsonl",
        contentType: "application/x-ndjson",
        contentText: JSON.stringify({ type: "session" }),
      };

      const firstRes = await requestJson("POST", `/api/bench/runs/${benchRunId}/artifacts`, payload);
      expect(firstRes.status).toBe(201);
      const secondRes = await requestJson("POST", `/api/bench/runs/${benchRunId}/artifacts`, payload);
      expect(secondRes.status).toBe(200);
      expect((secondRes.body as Record<string, unknown>).artifactId).toBe((firstRes.body as Record<string, unknown>).artifactId);

      const listRes = await requestGet(`/api/bench/runs/${benchRunId}/sessions`);
      expect(listRes.status).toBe(200);
      const listBody = listRes.body as { sessions: Array<Record<string, unknown>> };
      expect(listBody.sessions.filter((item) => item.taskId === "task_01_user_info").length).toBe(1);
    });

    it("rejects invalid artifact content", async () => {
      const createRes = await requestJson("POST", "/api/bench/runs", {
        domainId: "yuque_bench",
        templateName: "task_01_user_info",
        templateVersion: 1,
      });
      const { benchRunId } = createRes.body as { benchRunId: string };
      const res = await requestJson("POST", `/api/bench/runs/${benchRunId}/artifacts`, {
        artifactType: "session_trace",
        contentText: { bad: true },
      });
      expect(res.status).toBe(400);
    });

    it("returns empty session list for old runs without artifacts", async () => {
      const createRes = await requestJson("POST", "/api/bench/runs", {
        domainId: "yuque_bench",
        templateName: "task_01_user_info",
        templateVersion: 1,
      });
      const { benchRunId } = createRes.body as { benchRunId: string };
      const res = await requestGet(`/api/bench/runs/${benchRunId}/sessions`);
      expect(res.status).toBe(200);
      const body = res.body as { sessions: unknown[] };
      expect(body.sessions).toEqual([]);
    });
  });

  describe("503 when DB unavailable", () => {
    it("returns 503 when repos are null", async () => {
      const a = express();
      a.use(express.json());
      a.use("/api/bench", createBenchRouter(null, null, null, null, null, null));
      const s = a.listen(0);
      const addr = s.address();
      const p = typeof addr === "string" || !addr ? 0 : addr.port;

      const res = await new Promise<{ status: number; body: unknown }>((resolve, reject) => {
        http.get({ hostname: "127.0.0.1", port: p, path: "/api/bench/domains" }, (r) => {
          let data = "";
          r.on("data", (c) => (data += c));
          r.on("end", () => {
            try {
              resolve({ status: r.statusCode ?? 0, body: JSON.parse(data) });
            } catch {
              resolve({ status: r.statusCode ?? 0, body: data });
            }
          });
        }).on("error", reject);
      });

      expect(res.status).toBe(503);
      s.close();
    });
  });
});
