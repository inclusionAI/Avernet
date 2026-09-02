import assert from "node:assert/strict";
import test from "node:test";

import { createClawevolveRuntimeModule } from "../../clawevolve/src/runtime/create-runtime-module.js";
import { createRuntimeHost } from "../src/create-host.js";

test("runs the Clawevolve module through the public lifecycle contract", async () => {
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
    },
  });
  const host = createRuntimeHost({ modules: [module] });
  const server = await host.start({ port: 0, hostname: "127.0.0.1" });
  const address = server.address();
  assert.ok(address && typeof address !== "string");

  const response = await fetch(
    `http://127.0.0.1:${address.port}/api/evolve/task-definitions`,
  );
  assert.equal(response.status, 200);
  const payload = await response.json() as { tasks: Array<{ type: string }> };
  assert.ok(payload.tasks.some((task) => task.type === "diagnose"));

  await host.stop();
  assert.deepEqual(events, ["migrate", "start", "stop"]);
});
