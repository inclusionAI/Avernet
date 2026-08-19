import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFile, readdir, glob, stat } from "node:fs/promises";
import * as path from "node:path";

const PROJECT_ROOT = path.resolve(import.meta.dirname, "../..");

async function* filesUnder(root: string, pattern = "**/*"): AsyncGenerator<string> {
  for await (const rel of glob(pattern, { cwd: root })) {
    const full = path.join(root, rel);
    if ((await stat(full)).isFile()) yield full;
  }
}

/**
 * Community packs that ship with the open-source build. Everything else under
 * packs/ is internal business and MUST be filtered out by the packers.
 */
const COMMUNITY_PACKS = [
  "dynamic-workflow-demo",
  "nl-branching-demo",
  "workflow-dispatcher",
  "clawmind-loop-debug",
];

/**
 * Real-credential / internal-info patterns that must never appear in shipped
 * open-source files (packs/, configs/, src/, scripts/).
 *
 * Deliberately checks for hardcoded values (long high-entropy strings under a
 * secret-ish key) rather than placeholder/example docs.
 */
const FORBIDDEN_SECRET_PATTERNS: RegExp[] = [
  // JWT (header.payload.signature)
  /eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}/,
  // PEM private keys
  /-----BEGIN [A-Z ]*PRIVATE KEY-----/,
  // High-entropy secrets assigned to secret-ish keys (YAML, Python, JSON)
  /(appSecret|app_secret|client_secret|apiKey|api_key|app_key|appKey|DEFAULT_TOKEN|DEFAULT_APP_KEY|access_token|sign_secret|privateKey|secret_key)\s*[:=]\s*["'][^"'$]{16,}/,
];

/**
 * Internal endpoints / hostnames that leak corporate infra. /home/admin is a
 * normal Linux admin home used by generic install scripts, so it is NOT listed
 * here — only true corporate hosts / credential endpoints are flagged.
 */
const FORBIDDEN_INTERNAL_PATTERNS: RegExp[] = [
  /antgroup-inc\.cn/,
  /langfuse\.antfin\.com/,
  /aivision\.alipay\.com/,
  /webgw\.alipay\.com/,
  /secbaas/,
];

const ALL_FORBIDDEN = [...FORBIDDEN_SECRET_PATTERNS, ...FORBIDDEN_INTERNAL_PATTERNS];

describe("Open-source hygiene: no hardcoded secrets or internal info", () => {
  it("packers reference the internal-pack blocklist (dist_pack.mjs, dist_pack_teclaw.mjs)", async () => {
    const packers = [
      "scripts/dist_pack.mjs",
      "scripts/dist_pack_teclaw.mjs",
    ];
    for (const packer of packers) {
      const content = await readFile(path.join(PROJECT_ROOT, packer), "utf-8");
      assert.ok(
        content.includes("copyCommunityPacks"),
        `${packer} must use copyCommunityPacks() to filter internal packs`,
      );
      assert.ok(
        content.includes("community-packs.mjs"),
        `${packer} must import the shared blocklist from community-packs.mjs`,
      );
    }

    // The single source of truth for the blocklist lives in community-packs.mjs.
    const blocklist = await readFile(
      path.join(PROJECT_ROOT, "scripts/community-packs.mjs"),
      "utf-8",
    );
    for (const internalPack of [
      "teamclaw-kf",
      "risk-review-pipeline",
      "trade-risk-analysis-flow",
      "marketing-flow-dispatch",
      "approval-node-pipeline",
      "buzz-crawling-pipeline",
      "privacy-odps-approval-v1",
      "repeat-audit-analysis",
      "activity-legalrisk-review",
      "camp-pingshen-2604-assessment",
      "run-archive",
    ]) {
      assert.ok(
        blocklist.includes(internalPack),
        `community-packs.mjs blocklist must include "${internalPack}"`,
      );
    }
  });

  it("shipped community packs contain no hardcoded credentials or internal info", async () => {
    const violations: string[] = [];
    for (const packName of COMMUNITY_PACKS) {
      const dir = path.join(PROJECT_ROOT, "packs", packName);
      for await (const full of filesUnder(dir)) {
        const content = await readFile(full, "utf-8");
        for (const pattern of ALL_FORBIDDEN) {
          if (pattern.test(content)) {
            violations.push(`${packName}/${path.relative(dir, full)}: matches ${pattern.source}`);
          }
        }
      }
    }
    assert.equal(
      violations.length, 0,
      `Community packs contain forbidden content:\n${violations.join("\n")}`,
    );
  });

  it("source/config/script tree contains no hardcoded credentials or internal info", async () => {
    const roots = ["src", "configs", "scripts"];
    const violations: string[] = [];
    const skippedFiles = new Set([
      // self-signed-cert.ts builds PEM strings at runtime from regex fragments,
      // it does not embed a key.
      path.join(PROJECT_ROOT, "src/platform/self-signed-cert.ts"),
      // this test itself only defines detection patterns, not secrets.
    ]);
    for (const root of roots) {
      const base = path.join(PROJECT_ROOT, root);
      for await (const full of filesUnder(base, "**/*.{ts,tsx,js,mjs,sh,py,yaml,yml,json,md}")) {
        const rel = path.relative(base, full);
        if (rel.includes("__tests__") || rel.includes("node_modules")) continue;
        if (skippedFiles.has(full)) continue;
        const content = await readFile(full, "utf-8");
        for (const pattern of ALL_FORBIDDEN) {
          if (pattern.test(content)) {
            violations.push(`${root}/${rel}: matches ${pattern.source}`);
          }
        }
      }
    }
    assert.equal(
      violations.length, 0,
      `Open-source tree contains forbidden content:\n${violations.join("\n")}`,
    );
  });

  it("configs/application.yaml must not enable internal integrations", async () => {
    const cfg = await readFile(
      path.join(PROJECT_ROOT, "configs/application.yaml"),
      "utf-8",
    );
    // Community config must not reference BaaS / IAM / corp / ZDAS credentials.
    for (const pattern of [/baas/i, /iamtoken/i, /zdas/i, /corpId/i, /privateKeyB64/i]) {
      assert.ok(
        !pattern.test(cfg.replace(/#.*$/gm, "")), // ignore comment lines
        `application.yaml should not configure internal integration "${pattern}"`,
      );
    }
  });
});