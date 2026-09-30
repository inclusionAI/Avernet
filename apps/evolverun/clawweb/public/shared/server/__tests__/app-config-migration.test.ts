import { readFileSync } from "node:fs";
import { expect, it } from "vitest";
import Database from "better-sqlite3";
import { migrations } from "../schema.js";
import { dialectFor } from "../db/dialect.js";

const original = migrations.find((item) => item.version === 71)!;
const current = migrations.find((item) => item.version === 134)!;

it("matches the existing application config schema except table/value names, preserving existing rows", () => {
  const db = new Database(":memory:");
  try {
    original.sql.forEach((sql) => db.exec(sql));
    db.exec("INSERT INTO cm_app_config (config_key, config_yaml) VALUES ('keep', 'value: original')");
    current.sql.forEach((sql) => db.exec(sql));
    db.exec("INSERT INTO ce_app_config (config_key, config_json) VALUES ('example', '{\"value\":true}')");
    current.sql.forEach((sql) => db.exec(sql));
    const old = db.prepare("PRAGMA table_info(cm_app_config)").all() as Array<Record<string, unknown>>;
    expect(db.prepare("PRAGMA table_info(ce_app_config)").all()).toEqual(
      old.map((column) => ({ ...column, name: column.name === "config_yaml" ? "config_json" : column.name })),
    );
    expect(db.prepare("SELECT config_yaml FROM cm_app_config").get()).toEqual({ config_yaml: "value: original" });
    expect(db.prepare("SELECT config_json, version, enabled FROM ce_app_config").get())
      .toEqual({ config_json: '{"value":true}', version: 1, enabled: 1 });
    expect(() => db.exec("INSERT INTO ce_app_config (config_key, config_json) VALUES ('example', 'null')")).toThrow();
    const indexes = db.prepare("PRAGMA index_list(ce_app_config)").all() as Array<{ name: string; unique: number }>;
    expect(indexes).toEqual(expect.arrayContaining([
      expect.objectContaining({ name: "uk_ce_app_config_key", unique: 1 }),
      expect.objectContaining({ name: "idx_ce_app_config_enabled", unique: 0 }),
    ]));
  } finally { db.close(); }
});

it.each(["mysql", "zdas"] as const)("delivers matching config column definitions for %s", (engine) => {
  const rendered = dialectFor(engine).renderDdl(current.sql[0]);
  const delivered = readFileSync(new URL("../../../../../docs/clawweb/evolve-schema-v133/04-app-config.mysql.sql", import.meta.url), "utf8");
  expect(rendered).not.toMatch(/AUTOINCREMENT|unixepoch/);
  const columns = rendered.split("\n").slice(1, -1).map((line) => line.trim().replace(/,$/, ""));
  for (const column of columns) expect(delivered).toContain(column);
  expect(delivered).toContain("UNIQUE KEY uk_ce_app_config_key (config_key)");
  expect(delivered).toContain("KEY idx_ce_app_config_enabled (enabled)");
});
