import { afterEach, expect, it } from "vitest";
import express from "express";
import { createEvolveSpacesRouter } from "../evolve-spaces.js";

let server: ReturnType<express.Application["listen"]>;
afterEach(async () => { if (server) await new Promise<void>((resolve) => server.close(() => resolve())); });

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
