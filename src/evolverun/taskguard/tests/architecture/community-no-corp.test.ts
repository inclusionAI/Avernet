import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import * as path from "node:path";

const PROJECT_ROOT = path.resolve(import.meta.dirname, "../../src");

const FORBIDDEN_IMPORT_PATTERNS = [
  /from\s+["']@ocb\//,
  /from\s+["']@alipay\//,
  /from\s+["']@clawmind\//,
  /from\s+["'].*mysql2["']/,
  /from\s+["'].*mysql2\/promise["']/,
  /from\s+["'].*dingtalk-enterprise["']/,
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

/** Recursively collect files under dir, filtering by suffix. */
async function collectFiles(dir: string, suffix: string): Promise<string[]> {
  const results: string[] = [];
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      results.push(...await collectFiles(full, suffix));
    } else if (entry.name.endsWith(suffix)) {
      results.push(full);
    }
  }
  return results;
}

describe("Architecture boundary: community code must not import corp modules", () => {
  it("no source file imports forbidden internal packages", async () => {
    const files = await collectFiles(PROJECT_ROOT, ".ts");
    // Skip community/ directory itself (it provides defaults, not restrictions)
    const filtered = files.filter(f => !path.relative(PROJECT_ROOT, f).startsWith("community/"));

    const violations: string[] = [];
    for (const fullPath of filtered) {
      const file = path.relative(PROJECT_ROOT, fullPath);
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
