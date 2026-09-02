import assert from "node:assert/strict";
import test from "node:test";

import { createClawevolveRuntimeModule } from "../src/runtime/create-runtime-module.js";

test("exposes a stable runtime descriptor and delegates lifecycle hooks", async () => {
  const events: string[] = [];
  const module = createClawevolveRuntimeModule({
    lifecycle: {
      async migrate() {
        events.push("migrate");
      },
      async start() {
        events.push("start");
      },
      async stop() {
        events.push("stop");
      },
      async health() {
        events.push("health");
        return { status: "healthy" };
      },
    },
  });

  assert.equal(module.id, "clawevolve");
  assert.equal(module.apiBasePath, "/api/evolve");
  await module.migrate();
  await module.start();
  assert.deepEqual(await module.health(), { status: "healthy" });
  await module.stop();
  assert.deepEqual(events, ["migrate", "start", "health", "stop"]);
});

test("uses no-op lifecycle defaults", async () => {
  const module = createClawevolveRuntimeModule();
  await module.migrate();
  await module.start();
  assert.deepEqual(await module.health(), { status: "healthy" });
  await module.stop();
});
