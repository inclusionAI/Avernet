import { afterEach, beforeEach, expect, it } from "vitest";
import Database from "better-sqlite3";
import { SqliteDatabase, runMigrations } from "@avernet/clawweb-shared/server/db";
import { AppConfigRepository } from "../app-config-repository.js";

let db: SqliteDatabase;
let repo: AppConfigRepository;
beforeEach(async () => {
  db = new SqliteDatabase(new Database(":memory:"));
  await runMigrations(db, "sqlite");
  repo = new AppConfigRepository(db);
});
afterEach(async () => { await db.close(); });

it("stores arbitrary JSON configuration, enforces unique keys and tracks revisions", async () => {
  expect(await repo.findByKey("arbitrary_config")).toBeNull();
  const input = { config_key: "arbitrary_config", config_json: '{"any":[1,true]}', updated_by: "admin" };
  expect(await repo.create(input)).toMatchObject({ ...input, enabled: 1, version: 1, description: null });
  await expect(repo.create(input)).rejects.toThrow();
  expect(await repo.update(input.config_key, { config_json: '["changed"]', enabled: 0, description: "example", updated_by: "editor" }))
    .toMatchObject({ config_json: '["changed"]', enabled: 0, description: "example", updated_by: "editor", version: 2 });
  expect(await repo.listAll(true)).toEqual([]);
  expect(await repo.listAll()).toHaveLength(1);
  expect(await repo.update(input.config_key, {})).toMatchObject({ version: 2 });
  await runMigrations(db, "sqlite");
  expect(await repo.findByKey(input.config_key)).toMatchObject({ version: 2, config_json: '["changed"]' });
  expect(await repo.delete(input.config_key)).toBe(true);
  expect(await repo.delete(input.config_key)).toBe(false);
  expect(await repo.update("absent", { enabled: 1 })).toBeNull();
});

it("rejects malformed JSON without modifying an existing value", async () => {
  await expect(repo.create({ config_key: "example", config_json: "broken" })).rejects.toThrow();
  await repo.create({ config_key: "example", config_json: "null" });
  await expect(repo.update("example", { config_json: "broken" })).rejects.toThrow();
  expect(await repo.findByKey("example")).toMatchObject({ config_json: "null", version: 1 });
});
