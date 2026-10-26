import express, { type Express, type Response } from "express";
import { extname, join, resolve } from "node:path";
import type { ServerResponse } from "node:http";

function noCacheHtml(response: ServerResponse, path: string): void {
  if (extname(path).toLowerCase() === ".html") response.setHeader("Cache-Control", "no-cache");
}

function notFound(response: Response): void {
  response.status(404).set("Cache-Control", "no-store").json({ error: "Not Found" });
}

/**
 * Mount after API handlers. Build assets live exclusively under /assets;
 * file-like paths outside known client navigation never fall back to index.
 */
export function mountStaticFrontend(app: Express, staticDir: string): void {
  const root = resolve(staticDir);
  app.use("/api", (_request, response) => notFound(response));
  app.use("/assets", express.static(join(root, "assets"), {
    immutable: true,
    maxAge: "1y",
    index: false,
    redirect: false,
    setHeaders: noCacheHtml,
  }));
  app.use("/assets", (_request, response) => notFound(response));
  app.use(express.static(root, { index: false, redirect: false, setHeaders: noCacheHtml }));
  app.get("/{*path}", (request, response) => {
    const path = decodeURIComponent(request.path);
    // Client IDs and template names may contain dots, even a .js suffix.
    // Their route namespaces are navigations; build modules stay in /assets.
    const clientNavigation = /^\/(?:evolve|bench|runs|approval|workflows?|insight)(?:\/|$)/i.test(path);
    if (/^\/(api|assets)(\/|$)/i.test(path) || (!clientNavigation && path.split("/").some((segment) => segment.includes(".")))) {
      notFound(response);
      return;
    }
    response.sendFile(join(root, "index.html"), { headers: { "Cache-Control": "no-cache" } });
  });
}
