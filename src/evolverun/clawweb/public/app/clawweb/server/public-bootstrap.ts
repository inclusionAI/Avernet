import express from "express";
import compression from "compression";
import cookieParser from "cookie-parser";
import { existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createClawevolveModule } from "@avernet/clawevolve";
import { createInsightRuntime, createInsightRouter } from "@avernet/clawinsight";
import { closeDatabase, initDatabase } from "@avernet/clawweb-shared/server/db";
import type { ClawWebBootstrap } from "./bootstrap.js";

export function createClawWebBootstrap(): ClawWebBootstrap {
  return {
    async start(context) {
      const port = Number.parseInt(process.env.PORT ?? "3001", 10);
      const { db } = await initDatabase();
      if (db.dbType === "noop") throw new Error("Public ClawWeb requires an available database");
      const insight = createInsightRuntime(db);
      const clawevolve = createClawevolveModule({ db });
      await clawevolve.start();
      const app = express();
      app.use(compression());
      app.use(express.json({ limit: "10mb" }));
      app.use(cookieParser());
      app.get("/health", (_request, response) => {
        response.json({
          status: "ok",
          db: db.dbType,
          clawevolveImplementation: "avernet",
          profile: context.profile,
          environment: context.environment,
        });
      });
      app.use("/api/evolve", clawevolve.publicRouter);
      app.use("/api/bench", clawevolve.benchRouter);
      app.use("/api/insight", createInsightRouter(insight.service));
      const staticDir = resolve(dirname(fileURLToPath(import.meta.url)), "../web");
      if (existsSync(staticDir)) {
        app.use(express.static(staticDir));
        app.get("{*path}", (request, response) => {
          if (request.path.startsWith("/api/")) return response.status(404).json({ error: "Not Found" });
          return response.sendFile(join(staticDir, "index.html"));
        });
      }
      const server = app.listen(port, "127.0.0.1", () => {
        console.info(`[clawweb] Public runner listening on http://127.0.0.1:${port}`);
      });
      return {
        stop: async () => {
          await new Promise<void>((done) => server.close(() => done()));
          await clawevolve.stop();
          await closeDatabase();
        },
      };
    },
  };
}
