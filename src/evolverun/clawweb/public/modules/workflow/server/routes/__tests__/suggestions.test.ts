import { describe, it, expect, vi } from "vitest";
import { SuggestionPanelService, applyRepairToSpec } from "../../services/suggestion-panel-service.js";
import { LessonRepository } from "../../repositories/lesson-repository.js";
import { WorkflowSpecRepository } from "../../repositories/workflow-spec-repository.js";
import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import { sqliteDialect } from "@avernet/clawweb-shared/server/db/dialect";
import Database from "better-sqlite3";
import type { LessonRow } from "../../repositories/lesson-repository.js";

function createTestDb(): IDatabase {
  const raw = new Database(":memory:");
  raw.exec(`
    CREATE TABLE lessons (
      id INTEGER PRIMARY KEY AUTOINCREMENT, failure_signature VARCHAR(256) NOT NULL,
      error_class VARCHAR(64), executor_type VARCHAR(64), tool_or_node VARCHAR(128),
      repair_type VARCHAR(32) NOT NULL, repair_content TEXT NOT NULL,
      confidence_score DECIMAL(5,4) NOT NULL DEFAULT 0.5, hit_count INTEGER DEFAULT 0,
      success_count INTEGER DEFAULT 0, fail_count INTEGER DEFAULT 0, status VARCHAR(16) DEFAULT 'draft',
      evidence_run_ids TEXT, source VARCHAR(32), related_workflow_ids TEXT,
      metrics_before TEXT, metrics_after TEXT, bench_domain_id INTEGER,
      last_hit_at INTEGER, last_hit_success INTEGER,
      gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
      gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
    );
    CREATE UNIQUE INDEX uk_lessons_sig_type ON lessons (failure_signature, repair_type);

    CREATE TABLE suggestion_outcomes (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      lesson_id INTEGER NOT NULL, workflow_id VARCHAR(255) NOT NULL, node_id VARCHAR(255),
      failure_signature VARCHAR(256) NOT NULL, adopted INTEGER NOT NULL DEFAULT 0,
      applied_version VARCHAR(64), metrics_before TEXT, metrics_after TEXT,
      verdict VARCHAR(16) NOT NULL, source VARCHAR(32) NOT NULL,
      gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
      gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
    );

    CREATE TABLE weakness_list (
      id INTEGER PRIMARY KEY AUTOINCREMENT, failure_signature VARCHAR(256) NOT NULL UNIQUE,
      error_class VARCHAR(64), workflow_ids TEXT, occurrence_count INT,
      affected_workflows_count INT, repairability VARCHAR(16), priority_score DECIMAL(5,2),
      evidence_diagnosis_ids TEXT, latest_occurrence INTEGER, first_occurrence INTEGER,
      matched_lesson_ids TEXT, status VARCHAR(16) DEFAULT 'active',
      gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
      gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
    );

    CREATE TABLE workflow_specs (
      id INTEGER PRIMARY KEY AUTOINCREMENT, workflow_id VARCHAR(255) NOT NULL,
      pack_id VARCHAR(255), spec_json TEXT NOT NULL, title VARCHAR(255),
      version INTEGER DEFAULT 1,
      gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
      gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
    );
  `);
  return { dbType: "sqlite", dialect: sqliteDialect,
    query: async <T>(sql: string, params?: unknown[]) => params ? raw.prepare(sql).all(...params as []) as T[] : raw.prepare(sql).all() as T[],
    exec: async (sql: string, params?: unknown[]) => { const s = raw.prepare(sql); const r = params ? s.run(...params as []) : s.run(); return { affectedRows: r.changes, insertId: r.lastInsertRowid as number }; },
    transaction: async <T>(fn: (db: IDatabase) => Promise<T>) => raw.transaction(() => fn(createTestDb()))(),
    close: async () => { raw.close(); },
  };
}

function seedValidatedPromptPatchLesson(db: IDatabase): number {
  const stmt = (db as unknown as { exec: (sql: string, params?: unknown[]) => Promise<unknown> });
  // We delegate to LessonRepository.insert for consistency.
  return -1; // placeholder, set by caller
}

describe("SuggestionPanelService.applyOne", () => {
  it("returns lesson_not_found when the lesson id does not exist", async () => {
    const db = createTestDb();
    const lessonRepo = new LessonRepository(db);
    const specRepo = new WorkflowSpecRepository(db);
    const outcomes = new (await import("../../repositories/suggestion-outcome-repository.js")).SuggestionOutcomeRepository(db);
    const weakness = new (await import("../../repositories/weakness-list-repository.js")).WeaknessListRepository(db);
    const service = new SuggestionPanelService(db, lessonRepo, weakness, outcomes, specRepo, null);
    const r = await service.applyOne(99999, "wf-x", { id: "u", name: "n" });
    expect(r.deployed).toBe(false);
    expect(r.error).toBe("lesson_not_found");
  });

  it("returns lesson_not_in_validated_state when the lesson is still draft", async () => {
    const db = createTestDb();
    const lessonRepo = new LessonRepository(db);
    const specRepo = new WorkflowSpecRepository(db);
    const outcomes = new (await import("../../repositories/suggestion-outcome-repository.js")).SuggestionOutcomeRepository(db);
    const weakness = new (await import("../../repositories/weakness-list-repository.js")).WeaknessListRepository(db);
    const service = new SuggestionPanelService(db, lessonRepo, weakness, outcomes, specRepo, null);
    const id = await lessonRepo.insert({
      failure_signature: "sig-x", repair_type: "prompt_patch",
      repair_content: "be explicit", confidence_score: 0.5, status: "draft", source: "manual",
      tool_or_node: "fetch",
    });
    const r = await service.applyOne(id, "wf-x", { id: "u", name: "n" });
    expect(r.deployed).toBe(false);
    expect(r.error).toBe("lesson_not_in_validated_state");
  });

  it("returns deployed=true and deploy_number=null for kb_hint (no spec change)", async () => {
    const db = createTestDb();
    const lessonRepo = new LessonRepository(db);
    const specRepo = new WorkflowSpecRepository(db);
    const { SuggestionOutcomeRepository } = await import("../../repositories/suggestion-outcome-repository.js");
    const { WeaknessListRepository } = await import("../../repositories/weakness-list-repository.js");
    const outcomes = new SuggestionOutcomeRepository(db);
    const weakness = new WeaknessListRepository(db);
    const service = new SuggestionPanelService(db, lessonRepo, weakness, outcomes, specRepo, null);
    const id = await lessonRepo.insert({
      failure_signature: "sig-kb", repair_type: "kb_hint",
      repair_content: "domain_id must be int", confidence_score: 0.7, status: "validated", source: "manual",
      tool_or_node: "fetch",
    });
    const r = await service.applyOne(id, "wf-kb", { id: "u", name: "n" });
    expect(r.deployed).toBe(true);
    expect(r.deploy_number).toBe(null);
  });

  it("patches the spec and marks lesson live for a validated prompt_patch lesson", async () => {
    const db = createTestDb();
    const lessonRepo = new LessonRepository(db);
    const specRepo = new WorkflowSpecRepository(db);
    const { SuggestionOutcomeRepository } = await import("../../repositories/suggestion-outcome-repository.js");
    const { WeaknessListRepository } = await import("../../repositories/weakness-list-repository.js");
    const outcomes = new SuggestionOutcomeRepository(db);
    const weakness = new WeaknessListRepository(db);
    const service = new SuggestionPanelService(db, lessonRepo, weakness, outcomes, specRepo, null);

    const specYaml = "nodes:\n  - id: fetch\n    executor:\n      type: mcp-call\n      prompt: old prompt\n";
    await specRepo.upsert("wf-p", "pack-x", specYaml);

    const id = await lessonRepo.insert({
      failure_signature: "sig-p", repair_type: "prompt_patch",
      repair_content: "Output strict JSON only", confidence_score: 0.8, status: "validated", source: "manual",
      tool_or_node: "fetch",
    });

    const r = await service.applyOne(id, "wf-p", { id: "u1", name: "User One" });
    expect(r.deployed).toBe(true);
    // deploy_number stays null because deployHistoryRepo is null in this test.
    expect(r.deploy_number).toBe(null);

    const updated = await specRepo.findByWorkflowId("wf-p");
    expect(updated?.spec_json).toContain("Output strict JSON only");
    expect(updated?.spec_json).not.toContain("old prompt");

    const lesson = await lessonRepo.getById(id);
    expect(lesson?.status).toBe("live");

    const outcomesRows = await outcomes.byLesson(id);
    expect(outcomesRows.length).toBe(1);
    expect(outcomesRows[0].verdict).toBe("neutral");
    expect(outcomesRows[0].source).toBe("batch_patch");
  });
});

describe("applyRepairToSpec", () => {
  it("replaces prompt for prompt_patch on the matching tool_or_node", () => {
    const spec = {
      nodes: [
        { id: "n1", executor: { type: "mcp-call", prompt: "old", toolName: "fetch" } },
        { id: "n2", executor: { type: "mcp-call", prompt: "untouched" } },
      ],
    };
    const lesson = {
      repair_type: "prompt_patch", repair_content: "new explicit prompt",
      tool_or_node: "fetch",
    } as unknown as LessonRow;
    const out = applyRepairToSpec(spec, lesson);
    expect((out.nodes[0].executor as { prompt: string }).prompt).toBe("new explicit prompt");
    expect((out.nodes[1].executor as { prompt: string }).prompt).toBe("untouched");
  });

  it("merges args for arg_template_fix on the matching node id", () => {
    const spec = {
      nodes: [
        { id: "fetch", executor: { type: "mcp-call", args: { a: "1", b: "2" } } },
      ],
    };
    const lesson = {
      repair_type: "arg_template_fix",
      repair_content: JSON.stringify({ b: "3", c: "4" }),
      tool_or_node: "fetch",
    } as unknown as LessonRow;
    const out = applyRepairToSpec(spec, lesson);
    const args = (out.nodes[0].executor as { args: Record<string, string> }).args;
    expect(args).toEqual({ a: "1", b: "3", c: "4" });
  });

  it("leaves spec untouched for kb_hint", () => {
    const spec = { nodes: [{ id: "x", executor: { type: "t", prompt: "p" } }] };
    const lesson = { repair_type: "kb_hint", repair_content: "x", tool_or_node: "x" } as unknown as LessonRow;
    const out = applyRepairToSpec(spec, lesson);
    expect(out).toBe(spec);
  });
});

// Avoid an unused-warnings lint warning for the seeded helper (kept for clarity).
void seedValidatedPromptPatchLesson;
void vi;