import { afterEach, expect, it, vi } from "vitest";
import express from "express";
import { createEvolveSpacesRouter } from "../evolve-spaces.js";

let server: ReturnType<express.Application["listen"]>;
afterEach(async () => { if (server) await new Promise<void>((resolve) => server.close(() => resolve())); });

it.each([
  { referer: "https://workbench.example/evolve/new", origin: "https://workbench.example" },
  { referer: "https://workbench.example/evolve/new", origin: undefined },
  { referer: undefined, origin: "https://workbench.example" },
  { referer: undefined, origin: undefined },
])("preserves browser authentication context for space access: %j", async ({ referer, origin }) => {
  const listAccessibleSpaces = vi.fn(async () => []);
  const app = express();
  app.use(createEvolveSpacesRouter({ listAccessibleSpaces }));
  server = await new Promise((resolve) => { const instance = app.listen(0, () => resolve(instance)); });
  const response = await fetch(`http://127.0.0.1:${(server.address() as { port: number }).port}/spaces`, {
    headers: {
      "X-User-Id": "alice",
      Authorization: "test-authorization",
      Cookie: "session=test-session",
      ...(referer ? { Referer: referer } : {}),
      ...(origin ? { Origin: origin } : {}),
    },
  });
  expect(response.status).toBe(200);
  expect(await response.json()).toEqual({ items: [] });
  expect(listAccessibleSpaces).toHaveBeenCalledExactlyOnceWith({ identity: {
    userId: "alice", authorization: "test-authorization", cookie: "session=test-session", referer, origin,
  } });
});

it("lists the caller's host Personal and joined Team spaces without exposing another user's spaces", async () => {
  const app = express();
  app.use(createEvolveSpacesRouter({ listAccessibleSpaces: async ({ identity }) => identity.userId === "alice" ? [
    { id: "101", name: "Alice", type: "PERSONAL", role: "ADMIN" },
    { id: "208", name: "97", type: "TEAM", role: "MEMBER" },
  ] : [] }));
  server = await new Promise((resolve) => { const instance = app.listen(0, () => resolve(instance)); });
  const url = `http://127.0.0.1:${(server.address() as { port: number }).port}/spaces`;
  expect((await fetch(url)).status).toBe(401);
  expect(await (await fetch(url, { headers: { "X-User-Id": "alice" } })).json()).toEqual({ items: [
    { id: "101", name: "Alice", type: "PERSONAL", role: "ADMIN" },
    { id: "208", name: "97", type: "TEAM", role: "MEMBER" },
  ] });
  expect(await (await fetch(url, { headers: { "X-User-Id": "bob" } })).json()).toEqual({ items: [] });
});
