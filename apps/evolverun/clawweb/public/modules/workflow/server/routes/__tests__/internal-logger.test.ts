// @vitest-environment node
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";

const files = vi.hoisted(() => ({
  appendFileSync: vi.fn(), mkdirSync: vi.fn(), existsSync: vi.fn(() => true),
  statSync: vi.fn(() => ({ size: 100 * 1024 * 1024 })), renameSync: vi.fn(),
}));
vi.mock("node:fs", () => files);
vi.mock("node:os", () => ({ homedir: () => "/home/admin" }));

async function loadLogger() {
  return import("../internal-logger.js");
}

beforeEach(() => { vi.resetModules(); vi.clearAllMocks(); });
afterEach(() => { vi.restoreAllMocks(); files.appendFileSync.mockReset(); });

describe("internal API file log retention integration", () => {
  it("keeps writing the fixed log managed by logrotate instead of creating timestamp backups", async () => {
    vi.spyOn(console, "log").mockImplementation(() => {});
    const { apiLog } = await loadLogger();
    apiLog("WRITE", "/test", { status: 200 });
    apiLog("WRITE", "/test", { status: 200 });
    expect(files.mkdirSync).toHaveBeenCalledWith("/home/admin/logs/clawweb", { recursive: true });
    expect(files.appendFileSync.mock.calls.map(call => call[0])).toEqual([
      "/home/admin/logs/clawweb/clawweb-internal-api.log", "/home/admin/logs/clawweb/clawweb-internal-api.log",
    ]);
    expect(files.renameSync).not.toHaveBeenCalled();
    expect(files.statSync).not.toHaveBeenCalled();
  });

  it("reports an unavailable destination once and never writes to the root-volume legacy path", async () => {
    vi.spyOn(console, "log").mockImplementation(() => {});
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    const { apiLog } = await loadLogger();
    files.appendFileSync.mockImplementation(() => { throw new Error("disk unavailable"); });
    apiLog("WRITE", "/test", {}); apiLog("WRITE", "/test", {});
    expect(error).toHaveBeenCalledTimes(1);
    expect(files.appendFileSync.mock.calls.every(call => call[0] === "/home/admin/logs/clawweb/clawweb-internal-api.log")).toBe(true);
  });
});
