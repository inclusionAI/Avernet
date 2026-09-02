import assert from "node:assert/strict";
import test from "node:test";

import { Router } from "express";

import { createRuntimeHost } from "../src/create-host.js";
import type { RuntimeModule } from "../src/types.js";

function testModule(
  events: string[],
  overrides: Partial<RuntimeModule> = {},
): RuntimeModule {
  const router = Router();
  router.get("/ping", (_request, response) => response.json({ pong: true }));
  return {
    id: "test",
    apiBasePath: "/api/test",
    router,
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
      return { status: "healthy" };
    },
    ...overrides,
  };
}

test("starts modules before serving traffic and stops them", async () => {
  const events: string[] = [];
  const host = createRuntimeHost({ modules: [testModule(events)] });
  const server = await host.start({ port: 0, hostname: "127.0.0.1" });

  const address = server.address();
  assert.ok(address && typeof address !== "string");
  const baseUrl = `http://127.0.0.1:${address.port}`;

  const response = await fetch(`${baseUrl}/api/test/ping`);
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { pong: true });

  const ready = await fetch(`${baseUrl}/ready`);
  assert.equal(ready.status, 200);
  assert.equal(host.ready, true);
  assert.deepEqual(events, ["migrate", "start"]);

  await host.stop();
  assert.equal(host.ready, false);
  assert.deepEqual(events, ["migrate", "start", "stop"]);
});

test("reports unhealthy modules without exposing thrown health errors", async () => {
  const host = createRuntimeHost({
    modules: [testModule([], {
      async health() {
        throw new Error("private failure detail");
      },
    })],
  });
  const server = await host.start({ port: 0, hostname: "127.0.0.1" });
  const address = server.address();
  assert.ok(address && typeof address !== "string");

  const response = await fetch(`http://127.0.0.1:${address.port}/health`);
  assert.equal(response.status, 503);
  const body = await response.text();
  assert.match(body, /HEALTH_CHECK_FAILED/);
  assert.doesNotMatch(body, /private failure detail/);
  await host.stop();
});

test("cleans up already-started modules when a later module fails", async () => {
  const events: string[] = [];
  const first = testModule(events, { id: "first", apiBasePath: "/api/first" });
  const second = testModule(events, {
    id: "second",
    apiBasePath: "/api/second",
    async start() {
      events.push("second-start");
      throw new Error("start failed");
    },
  });
  const host = createRuntimeHost({ modules: [first, second] });

  await assert.rejects(host.start({ port: 0 }), /start failed/);
  assert.deepEqual(events, [
    "migrate",
    "migrate",
    "start",
    "second-start",
    "stop",
    "stop",
  ]);
  assert.equal(host.ready, false);
  assert.equal(host.server, null);
});

test("rejects duplicate module registration", () => {
  const first = testModule([]);
  assert.throws(
    () => createRuntimeHost({ modules: [first, testModule([])] }),
    /Duplicate runtime module id/,
  );
});
