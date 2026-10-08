#!/usr/bin/env node
/**
 * community-packs.mjs — shared pack filtering for open-source packaging.
 *
 * Only community/demo packs ship with the open-source build. Internal business
 * packs carry real credentials, employee PII, and internal endpoints and MUST
 * NOT be packaged. Keeping the blocklist in one place ensures every packer
 * (dist_pack.mjs, dist_pack_teclaw.mjs, ...) filters identically.
 */

import { cpSync, existsSync, mkdirSync, readdirSync } from "node:fs";
import { join } from "node:path";

/** Internal business packs — excluded from every open-source package. */
export const INTERNAL_PACKS = new Set([
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
]);

/**
 * Copy the community packs under <root>/packs into destPacksDir, skipping
 * internal packs. Logs each skipped pack so packaging output is auditable.
 *
 * @param {string} rootDir  project root containing the packs/ directory
 * @param {string} destPacksDir  target directory to copy packs into
 * @param {string} [label]  optional log label (default "packs/")
 */
export function copyCommunityPacks(rootDir, destPacksDir, label = "packs/") {
  const srcPacksDir = join(rootDir, "packs");
  if (!existsSync(srcPacksDir)) {
    console.warn(`  ${label} not found, skipping`);
    return;
  }
  mkdirSync(destPacksDir, { recursive: true });
  let copied = 0;
  let skipped = 0;
  const entries = readdirSync(srcPacksDir, { withFileTypes: true });
  for (const entry of entries) {
    if (INTERNAL_PACKS.has(entry.name)) {
      console.log(`  [packs] SKIP internal pack: ${entry.name}`);
      skipped++;
      continue;
    }
    const src = join(srcPacksDir, entry.name);
    if (entry.isDirectory()) {
      cpSync(src, join(destPacksDir, entry.name), { recursive: true });
    } else {
      cpSync(src, join(destPacksDir, entry.name));
    }
    copied++;
  }
  console.log(`  Copied ${label} (${copied} community packs, ${skipped} internal skipped)`);
}