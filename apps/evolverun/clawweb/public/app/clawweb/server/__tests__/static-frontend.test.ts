import express from "express";
import { once } from "node:events";
import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import type { Server } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { mountStaticFrontend } from "../static-frontend.js";

const index = "<!doctype html><html><body>current deployment</body></html>";
let directory: string;
let server: Server;
let baseUrl: string;

beforeEach(async () => {
  directory = await mkdtemp(join(tmpdir(), "clawweb-static-"));
  await mkdir(join(directory, "assets"));
  await mkdir(join(directory, "api"));
  await writeFile(join(directory, "index.html"), index);
  await writeFile(join(directory, "help.html"), "<!doctype html><title>Help</title>");
  await writeFile(join(directory, "robots.txt"), "User-agent: *");
  await writeFile(join(directory, "assets", "app-123abc.js"), "export const release = 2;");
  await writeFile(join(directory, "assets", "app-123abc.css"), "body { display: block; }");
  await writeFile(join(directory, "assets", "page.html"), "<!doctype html><title>Asset HTML</title>");
  await writeFile(join(directory, "api", "private.json"), '{"notAnApi":true}');
  const app = express();
  app.get("/api/known", (_request, response) => response.json({ ok: true }));
  mountStaticFrontend(app, directory);
  server = app.listen(0, "127.0.0.1");
  await once(server, "listening");
  baseUrl = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
});

afterEach(async () => {
  if (server) {
    server.closeAllConnections();
    await new Promise<void>((done) => server.close(() => done()));
  }
  if (directory) await rm(directory, { recursive: true, force: true });
});

describe("mountStaticFrontend", () => {
  it.each(["/", "/index.html", "/index.html?release=old", "/workflows/deployments/42", "/workflows?tab=runs"])(
    "serves the current index with no-cache at %s", async (path) => {
      const response = await fetch(baseUrl + path);
      expect(response.status).toBe(200);
      expect(response.headers.get("content-type")).toMatch(/text\/html/);
      expect(response.headers.get("cache-control")).toBe("no-cache");
      expect(await response.text()).toBe(index);
    },
  );

  it.each([
    "/evolve/bench/domains/alice/TEST/templates/case.v1",
    "/evolve/bench/domains/alice.blog/TEST/templates/case.js",
    "/bench/domains/alice/TEST/templates/case.v1",
    "/runs/run.v2",
    "/workflow/domain.v1/metrics",
  ])("preserves dotted client navigation at %s", async (path) => {
    const response = await fetch(baseUrl + path);
    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toBe("no-cache");
    expect(await response.text()).toBe(index);
  });

  it.each(["/help.html", "/assets/page.html"])("never makes HTML immutable at %s", async (path) => {
    const response = await fetch(baseUrl + path);
    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toBe("no-cache");
    expect(await response.text()).toContain("<!doctype html>");
  });

  it.each(["/assets/app-123abc.js", "/assets/app-123abc.css"])("serves known assets immutably for a year at %s", async (path) => {
    const response = await fetch(baseUrl + path);
    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toBe("public, max-age=31536000, immutable");
    expect(response.headers.get("content-type")).not.toMatch(/html/);
    expect(await response.text()).not.toBe(index);
  });

  it.each(["/assets", "/assets/", "/assets/missing.js", "/assets/missing.js?release=old", "/assets/missing", "/assets/nested/missing.css", "/a%73sets/missing"])(
    "returns a no-store 404 instead of the SPA for asset miss %s", async (path) => {
      const response = await fetch(baseUrl + path);
      expect(response.status).toBe(404);
      expect(response.headers.get("cache-control")).toBe("no-store");
      expect(response.headers.get("content-type")).not.toMatch(/html/);
      expect(await response.text()).not.toContain("current deployment");
    },
  );

  it.each(["/missing.js", "/missing.css", "/missing.svg", "/manifest.webmanifest", "/nested/missing.js", "/missing.js/", "/.env", "/missing%2Ejs"])(
    "does not turn a missing nonasset file into HTML at %s", async (path) => {
      const response = await fetch(baseUrl + path);
      expect(response.status).toBe(404);
      expect(response.headers.get("cache-control")).toBe("no-store");
      expect(response.headers.get("content-type")).not.toMatch(/html/);
      expect(await response.text()).not.toContain("current deployment");
    },
  );

  it.each(["/api", "/api/unknown", "/api/private.json", "/api/index.html", "/API/unknown", "/a%70i/unknown"])(
    "keeps unknown API route %s a JSON 404 rather than frontend content", async (path) => {
      const response = await fetch(baseUrl + path);
      expect(response.status).toBe(404);
      expect(response.headers.get("content-type")).toMatch(/application\/json/);
      expect(response.headers.get("cache-control")).toBe("no-store");
      expect(await response.json()).toEqual({ error: "Not Found" });
    },
  );

  it("preserves earlier API handlers", async () => {
    const response = await fetch(baseUrl + "/api/known");
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ ok: true });
  });

  it("keeps unknown API writes a JSON 404", async () => {
    const response = await fetch(baseUrl + "/api/unknown", { method: "POST" });
    expect(response.status).toBe(404);
    expect(await response.json()).toEqual({ error: "Not Found" });
  });

  it("does not serve index for POST frontend paths", async () => {
    const response = await fetch(baseUrl + "/workflows", { method: "POST" });
    expect(response.status).toBe(404);
    expect(await response.text()).not.toContain("current deployment");
  });

  it("retains no-cache semantics for HEAD navigations", async () => {
    const response = await fetch(baseUrl + "/workflows", { method: "HEAD" });
    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toBe("no-cache");
    expect(await response.text()).toBe("");
  });

  it("still serves existing nonasset files", async () => {
    const response = await fetch(baseUrl + "/robots.txt");
    expect(response.status).toBe(200);
    expect(await response.text()).toBe("User-agent: *");
  });
});
