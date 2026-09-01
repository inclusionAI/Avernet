import assert from "node:assert/strict";
import test from "node:test";

import express from "express";

import { createClawevolveModule } from "../src/server/create-module.js";

test("mounts Clawevolve in an existing Express process", async (context) => {
  const app = express();
  app.use("/api/evolve", createClawevolveModule());
  const server = app.listen(0);
  context.after(() => new Promise<void>((resolve) => server.close(() => resolve())));
  await new Promise<void>((resolve) => server.once("listening", () => resolve()));

  const address = server.address();
  assert.ok(address && typeof address !== "string");
  const response = await fetch(
    `http://127.0.0.1:${address.port}/api/evolve/task-definitions`,
  );

  assert.equal(response.status, 200);
  const payload = await response.json() as { tasks: Array<{ type: string }> };
  assert.ok(payload.tasks.some((task) => task.type === "diagnose"));
});
