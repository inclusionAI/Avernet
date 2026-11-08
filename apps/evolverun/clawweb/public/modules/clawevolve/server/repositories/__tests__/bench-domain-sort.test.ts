import { afterEach, expect, it } from "vitest";
import Database from "better-sqlite3";
import { SqliteDatabase } from "@avernet/clawweb-shared/server/db";
import { BenchDomainRepository } from "../bench-domain-repository.js";

let raw: Database.Database;
afterEach(() => raw?.close());

it("orders space Domains by latest modification, with stable ID ties, including legacy lists", async () => {
  raw = new Database(":memory:");
  raw.exec(`CREATE TABLE cm_bench_domains (
    id INTEGER PRIMARY KEY, domain_id TEXT, name TEXT, description TEXT,
    status TEXT, created_by TEXT, owner_user_id TEXT,
    gmt_create INTEGER, gmt_modified INTEGER, space_id TEXT
  )`);
  const insert = raw.prepare("INSERT INTO cm_bench_domains VALUES (?, ?, ?, NULL, ?, 'alice', ?, 1, ?, ?)");
  insert.run(1, "old", "A", "active", "alice", 10, "team");
  insert.run(2, "new", "Z", "active", "alice", 30, "team");
  insert.run(3, "tie", "B", "active", "alice", 30, "team");
  insert.run(4, "other-space", "C", "active", "alice", 50, "other");
  insert.run(5, "archived", "D", "archived", "alice", 40, "team");
  insert.run(6, "historical", "E", "active", "alice", 20, null);
  insert.run(7, "personal", "F", "active", "alice", 40, "personal");
  insert.run(8, "other-owner", "G", "active", "bob", 50, null);
  const repo = new BenchDomainRepository(new SqliteDatabase(raw));
  const ids = (rows: Array<{domain_id: string}>) => rows.map((row) => row.domain_id);

  expect(ids(await repo.listInSpace("team"))).toEqual(["tie", "new", "old"]);
  expect(ids(await repo.listInSpace("team", undefined, { includeArchived: true })))
    .toEqual(["archived", "tie", "new", "old"]);
  expect(ids(await repo.listInSpace("team", undefined, { status: "archived" })))
    .toEqual(["archived"]);
  expect(ids(await repo.listInSpace("personal", "alice"))).toEqual(["personal", "historical"]);
  expect(ids(await repo.listAll("alice")))
    .toEqual(["other-space", "personal", "tie", "new", "historical", "old"]);
  expect(ids(await repo.listAll()))
    .toEqual(["other-owner", "other-space", "personal", "tie", "new", "historical", "old"]);
  expect(ids(await repo.listAll(undefined, { includeArchived: true })))
    .toEqual(["other-owner", "other-space", "personal", "archived", "tie", "new", "historical", "old"]);
});
