import { spawn, type ChildProcess } from "node:child_process";
import { createServer } from "node:net";
import { afterEach, describe, expect, it } from "vitest";

const children = new Set<ChildProcess>();

async function reservePort(): Promise<number> {
  return await new Promise((resolve, reject) => {
    const server = createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (!address || typeof address === "string") {
        server.close();
        reject(new Error("failed to reserve a TCP port"));
        return;
      }
      server.close((error) => error ? reject(error) : resolve(address.port));
    });
  });
}

async function waitForHealth(port: number, child: ChildProcess): Promise<void> {
  const deadline = Date.now() + 10_000;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) {
      throw new Error(`evolvetrace exited with code ${child.exitCode}`);
    }
    try {
      const response = await fetch(`http://127.0.0.1:${port}/health`);
      if (response.ok) return;
    } catch {
      // The server may still be starting.
    }
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error("timed out waiting for evolvetrace health endpoint");
}

afterEach(async () => {
  for (const child of children) {
    if (child.exitCode === null) child.kill("SIGTERM");
  }
  children.clear();
});

describe("Evolvetrace CORS configuration", () => {
  it("allows a configured origin without admitting other origins", async () => {
    const port = await reservePort();
    const child = spawn(
      process.execPath,
      ["--import", "tsx", "server/index.ts"],
      {
        cwd: process.cwd(),
        env: {
          ...process.env,
          PORT: String(port),
          DATABASE_MODE: "noop",
          CORS_ALLOWED_ORIGINS: "https://avernet.alipay.com",
        },
        stdio: "ignore",
      },
    );
    children.add(child);
    await waitForHealth(port, child);

    const response = await fetch(`http://127.0.0.1:${port}/health`, {
      headers: { Origin: "https://avernet.alipay.com" },
    });

    expect(response.status).toBe(200);
    expect(response.headers.get("access-control-allow-origin"))
      .toBe("https://avernet.alipay.com");

    const rejected = await fetch(`http://127.0.0.1:${port}/health`, {
      headers: { Origin: "https://untrusted.example.com" },
    });
    expect(rejected.status).toBe(500);
    expect(rejected.headers.get("access-control-allow-origin")).toBeNull();
  }, 15_000);
});
