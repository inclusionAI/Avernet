#!/usr/bin/env node
/**
 * import-app-config.mjs — Import application.yaml sections into cm_app_config table.
 *
 * Usage:
 *   node scripts/import-app-config.mjs --config configs/application.yaml --mode sqlite
 *   node scripts/import-app-config.mjs --config configs/application.yaml --mode api
 *
 * Reads the local application.yaml, extracts non-bootstrap top-level sections,
 * and INSERTs them into the cm_app_config table (SQLite) or via clawweb API.
 *
 * Bootstrap sections (app_name, version, database, api) are skipped — they stay
 * in the local file.
 *
 * Idempotent: if a config_key already exists, it is skipped (not overwritten).
 */

import { readFileSync } from "node:fs";
import { parse } from "yaml";
import { Database } from "node:sqlite";

// ── Bootstrap sections that stay in local application.yaml ──
const BOOTSTRAP_KEYS = new Set(["app_name", "version", "engine", "database", "api"]);

// ── Parse CLI args ──
const args = process.argv.slice(2);
function getArg(name) {
  const idx = args.indexOf(`--${name}`);
  return idx >= 0 ? args[idx + 1] : null;
}

const configPath = getArg("config") ?? "configs/application.yaml";
const mode = getArg("mode") ?? "sqlite";
const sqlitePath = getArg("sqlite-path") ?? "~/.openclaw/workflow/engine.db";
const apiUrl = getArg("api-url") ?? "http://localhost:3001";

// ── Read and parse application.yaml ──
console.log(`[import] Reading ${configPath}...`);
const raw = readFileSync(configPath, "utf-8");
const yaml = parse(raw) ?? {};

// ── Extract non-bootstrap sections ──
const sections = [];
for (const [key, value] of Object.entries(yaml)) {
  if (BOOTSTRAP_KEYS.has(key)) continue;
  if (value === null || typeof value !== "object") continue;

  // Serialize section content as YAML (without the top-level key)
  const sectionYaml = serializeYamlSection(value);
  sections.push({ config_key: key, config_yaml: sectionYaml });
  console.log(`[import] Found section: ${key} (${sectionYaml.split("\n").length} lines)`);
}

if (sections.length === 0) {
  console.log("[import] No non-bootstrap sections found. Nothing to import.");
  process.exit(0);
}

console.log(`[import] ${sections.length} sections to import via ${mode} mode...`);

// ── Import ──
if (mode === "sqlite") {
  await importToSqlite(sections);
} else if (mode === "api") {
  await importViaApi(sections);
} else {
  console.error(`[import] Unknown mode: ${mode}. Use --mode sqlite or --mode api`);
  process.exit(1);
}

// ── Helpers ──

function serializeYamlSection(value) {
  // Serialize the section value as YAML (no top-level key wrapper)
  // Use a wrapper object then strip the top-level key line
  const wrapped = { _section: value };
  const fullYaml = stringifyYaml(wrapped);
  // Remove first line ("_section:") and de-indent
  const lines = fullYaml.split("\n");
  // First line is "_section:", rest is the section content indented by 2 spaces
  const contentLines = lines.slice(1).map((line) => line.replace(/^  /, ""));
  return contentLines.join("\n").trim();
}

function stringifyYaml(obj) {
  // Simple YAML stringify using the yaml package
  return stringify(obj);
}

import { stringify } from "yaml";

async function importToSqlite(sections) {
  const dbPath = sqlitePath.replace(/^~/, process.env.HOME || "/home");
  console.log(`[import] Opening SQLite at ${dbPath}...`);
  const db = new Database(dbPath);

  let inserted = 0;
  let skipped = 0;

  for (const section of sections) {
    // Check if already exists
    const existing = db.prepare("SELECT config_key FROM cm_app_config WHERE config_key = ?").get(section.config_key);
    if (existing) {
      console.log(`[import] SKIP "${section.config_key}" — already exists`);
      skipped++;
      continue;
    }
    const now = Math.floor(Date.now() / 1000);
    db.prepare(
      "INSERT INTO cm_app_config (config_key, config_yaml, version, enabled, description, updated_by, gmt_create, gmt_modified) VALUES (?, ?, 1, 1, ?, 'import-script', ?, ?)",
    ).run(section.config_key, section.config_yaml, `Imported from application.yaml`, now, now);
    console.log(`[import] INSERT "${section.config_key}" — OK`);
    inserted++;
  }

  db.close();
  console.log(`[import] Done: ${inserted} inserted, ${skipped} skipped`);
}

async function importViaApi(sections) {
  const baseUrl = apiUrl;
  let inserted = 0;
  let skipped = 0;

  for (const section of sections) {
    try {
      const res = await fetch(`${baseUrl}/api/app-config`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          config_key: section.config_key,
          config_yaml: section.config_yaml,
          description: "Imported from application.yaml",
        }),
      });
      if (res.status === 409) {
        console.log(`[import] SKIP "${section.config_key}" — already exists (409)`);
        skipped++;
        continue;
      }
      if (!res.ok) {
        const body = await res.text().catch(() => "unknown error");
        console.error(`[import] FAIL "${section.config_key}" — HTTP ${res.status}: ${body}`);
        continue;
      }
      console.log(`[import] INSERT "${section.config_key}" — OK`);
      inserted++;
    } catch (err) {
      console.error(`[import] FAIL "${section.config_key}" — ${err.message}`);
    }
  }

  console.log(`[import] Done: ${inserted} inserted, ${skipped} skipped`);
}
