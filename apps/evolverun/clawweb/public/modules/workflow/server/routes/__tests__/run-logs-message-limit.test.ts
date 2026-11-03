import { Buffer } from "node:buffer";
import type { AddressInfo } from "node:net";
import express from "express";
import { expect, it, vi } from "vitest";
import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import { RunLogRepository } from "../../repositories/run-log-repository.js";
import { createInternalRunLogsRouter } from "../internal/run-logs.js";

it("bounds legacy client messages through the batch HTTP route before database execution", async () => {
  const longMessage = "😀".repeat(17_000);
  const entries = [longMessage, "short log"].map((message, index) => ({
    flow_id: "flow-http",
    node_id: null,
    level: "error",
    source: "engine",
    message,
    timestamp: 123,
    seq: index + 1,
  }));
  const exec = vi.fn<IDatabase["exec"]>().mockImplementation(async (_sql, params = []) => {
    for (let index = 4; index < params.length; index += 7) {
      if (Buffer.byteLength(String(params[index]), "utf8") > 65_535) {
        throw new Error("Data too long for column 'message'");
      }
    }
    return { affectedRows: params.length / 7 };
  });
  const repository = new RunLogRepository({ dbType: "mysql", exec } as IDatabase);
  const log = vi.spyOn(console, "log").mockImplementation(() => {});
  const app = express();
  app.use(express.json());
  app.use("/api/internal/run-logs", createInternalRunLogsRouter(repository));
  const server = app.listen(0, "127.0.0.1");
  try {
    await new Promise<void>((resolve, reject) => {
      server.once("listening", resolve);
      server.once("error", reject);
    });
    const address = server.address() as AddressInfo;
    const response = await fetch(`http://127.0.0.1:${address.port}/api/internal/run-logs/batch`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(entries),
    });
    expect(response.status).toBe(201);
    expect(await response.json()).toEqual({ success: true, inserted: 2 });
    expect(exec).toHaveBeenCalledTimes(1);
    const [sql, params] = exec.mock.calls[0];
    expect(sql).toContain("INSERT INTO run_logs");
    const marker = "\n...[truncated]";
    const budget = 65_535 - Buffer.byteLength(marker, "utf8");
    expect(params?.[4]).toBe("😀".repeat(Math.floor(budget / 4)) + marker);
    expect(params?.[11]).toBe("short log");
    expect(entries[0].message).toBe(longMessage);
  } finally {
    log.mockRestore();
    server.closeAllConnections();
    await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
  }
});
