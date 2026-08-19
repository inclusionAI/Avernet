import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { glob } from "node:fs/promises";
import * as path from "node:path";

const PROJECT_ROOT = path.resolve(import.meta.dirname, "../../src");

const FORBIDDEN_IMPORT_PATTERNS = [
  /from\s+["']@ocb\//,
  /from\s+["']@alipay\//,
  /from\s+["']@clawmind\//,
  /from\s+["'].*mysql2["']/,
  /from\s+["'].*mysql2\/promise["']/,
  /from\s+["'].*dingtalk-enterprise["']/,
  /from\s+["'].*api-client["']/,
  /from\s+["'].*zdas-database["']/,
  /from\s+["'].*yuque-adapter["']/,
  /from\s+["'].*agentmind-adapter["']/,
  /from\s+["'].*approval-card-web-poller["']/,
  /from\s+["'].*dev-workflow-callback["']/,
  /from\s+["'].*baas["']/,
  /from\s+["'].*baas-call["']/,
  /from\s+["'].*corp["']/,
  /from\s+["'].*internal["']/,
];

describe("Architecture boundary: community code must not import corp modules", () => {
  it("no source file imports forbidden internal packages", async () => {
    const files: string[] = [];
    for await (const entry of glob("**/*.ts", { cwd: PROJECT_ROOT })) {
      // Skip community/ directory itself (it provides defaults, not restrictions)
      if (entry.startsWith("community/")) continue;
      files.push(entry);
    }

    const violations: string[] = [];
    for (const file of files) {
      const fullPath = path.join(PROJECT_ROOT, file);
      const content = await readFile(fullPath, "utf-8");
      for (const pattern of FORBIDDEN_IMPORT_PATTERNS) {
        if (pattern.test(content)) {
          violations.push(`${file}: matches ${pattern.source}`);
        }
      }
    }

    assert.equal(
      violations.length, 0,
      `Found ${violations.length} forbidden imports in community code:\n${violations.join("\n")}`
    );
  });
});
