import { EventEmitter, once } from "node:events";
import { createServer, get, type Server, type ServerResponse } from "node:http";
import { afterEach, describe, expect, it, vi } from "vitest";
import { closeHttpServer, registerShutdown } from "../shutdown.js";

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}

const turn = () => new Promise<void>((done) => setImmediate(done));
const servers: Server[] = [];

afterEach(async () => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  for (const server of servers.splice(0)) {
    server.closeAllConnections();
    if (server.listening) await new Promise<void>((done) => server.close(() => done()));
  }
});

describe("registerShutdown", () => {
  it.each(["SIGINT", "SIGTERM"] as const)("awaits stop after %s and ignores concurrent/repeated signals", async (first) => {
    const signals = new EventEmitter();
    const pending = deferred<void>();
    const stop = vi.fn(() => pending.promise);
    const exit = vi.fn();
    const logError = vi.fn();
    registerShutdown({ stop }, { signals, exit, logError });

    signals.emit(first);
    signals.emit(first === "SIGINT" ? "SIGTERM" : "SIGINT");
    signals.emit(first);
    expect(stop).toHaveBeenCalledOnce();
    await turn();
    expect(exit).not.toHaveBeenCalled();
    expect(logError).not.toHaveBeenCalled();

    pending.resolve();
    await turn();
    expect(exit).toHaveBeenCalledExactlyOnceWith(0);
    signals.emit("SIGINT");
    signals.emit("SIGTERM");
    await turn();
    expect(stop).toHaveBeenCalledOnce();
    expect(exit).toHaveBeenCalledOnce();
  });

  it("logs a rejected stop and exits 1 without an unhandled rejection", async () => {
    const signals = new EventEmitter();
    const pending = deferred<void>();
    const error = new Error("database shutdown failed");
    const stop = vi.fn(() => pending.promise);
    const exit = vi.fn();
    const logError = vi.fn();
    registerShutdown({ stop }, { signals, exit, logError });
    signals.emit("SIGINT");
    signals.emit("SIGTERM");
    expect(exit).not.toHaveBeenCalled();
    pending.reject(error);
    await turn();
    expect(stop).toHaveBeenCalledOnce();
    expect(logError).toHaveBeenCalledExactlyOnceWith(error);
    expect(exit).toHaveBeenCalledExactlyOnceWith(1);
  });

  it("also reports synchronous stop errors as failed shutdown", async () => {
    const signals = new EventEmitter();
    const error = new Error("stop threw");
    const exit = vi.fn();
    const logError = vi.fn();
    registerShutdown({ stop: () => { throw error; } }, { signals, exit, logError });
    signals.emit("SIGTERM");
    await turn();
    expect(logError).toHaveBeenCalledExactlyOnceWith(error);
    expect(exit).toHaveBeenCalledExactlyOnceWith(1);
  });

  it.each([undefined, {}, { stop: () => {} }])("preserves optional lifecycle.stop compatibility (%j)", async (lifecycle) => {
    const signals = new EventEmitter();
    const exit = vi.fn();
    const logError = vi.fn();
    registerShutdown(lifecycle, { signals, exit, logError });
    signals.emit("SIGTERM");
    await turn();
    expect(exit).toHaveBeenCalledExactlyOnceWith(0);
    expect(logError).not.toHaveBeenCalled();
  });

  it("can remove only the listeners it owns", () => {
    const signals = new EventEmitter();
    const other = vi.fn();
    const stop = vi.fn();
    signals.on("SIGINT", other);
    const unregister = registerShutdown({ stop }, { signals, exit: vi.fn(), logError: vi.fn() });
    expect(signals.listenerCount("SIGINT")).toBe(2);
    expect(signals.listenerCount("SIGTERM")).toBe(1);
    unregister();
    unregister();
    expect(signals.listenerCount("SIGINT")).toBe(1);
    expect(signals.listenerCount("SIGTERM")).toBe(0);
    signals.emit("SIGINT");
    expect(other).toHaveBeenCalledOnce();
    expect(stop).not.toHaveBeenCalled();
  });
});

describe("closeHttpServer", () => {
  it("does not resolve until the close callback, then permits dependent cleanup", async () => {
    const server = createServer();
    let callback!: (error?: Error) => void;
    vi.spyOn(server, "close").mockImplementation((done) => { callback = done!; return server; });
    const closeModules = vi.fn();
    const closeDatabase = vi.fn();
    const shutdown = (async () => {
      await closeHttpServer(server);
      await closeModules();
      await closeDatabase();
    })();
    await turn();
    expect(closeModules).not.toHaveBeenCalled();
    expect(closeDatabase).not.toHaveBeenCalled();
    callback();
    await shutdown;
    expect(closeModules).toHaveBeenCalledOnce();
    expect(closeDatabase).toHaveBeenCalledOnce();
  });

  it("rejects errors delivered through the close callback", async () => {
    const server = createServer();
    const error = new Error("cannot close listener");
    vi.spyOn(server, "close").mockImplementation((done) => { done!(error); return server; });
    await expect(closeHttpServer(server)).rejects.toBe(error);
  });

  it("rejects synchronous close failures", async () => {
    const server = createServer();
    const error = new Error("close threw");
    vi.spyOn(server, "close").mockImplementation(() => { throw error; });
    await expect(closeHttpServer(server)).rejects.toBe(error);
  });

  it("uses a 10-second default deadline and rejects even if forced close invokes the callback", async () => {
    vi.useFakeTimers();
    const server = createServer();
    let callback!: (error?: Error) => void;
    vi.spyOn(server, "close").mockImplementation((done) => { callback = done!; return server; });
    const force = vi.spyOn(server, "closeAllConnections").mockImplementation(() => callback());
    const closing = closeHttpServer(server);
    const rejection = expect(closing).rejects.toThrow(/timed out.*10000/i);
    await vi.advanceTimersByTimeAsync(9_999);
    expect(force).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    await rejection;
    expect(force).toHaveBeenCalledOnce();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("rejects forced-connection-close failures instead of throwing out of the timer", async () => {
    vi.useFakeTimers();
    const server = createServer();
    const error = new Error("forced close failed");
    vi.spyOn(server, "close").mockImplementation(() => server);
    vi.spyOn(server, "closeAllConnections").mockImplementation(() => { throw error; });
    const rejection = expect(closeHttpServer(server, 50)).rejects.toBe(error);
    await vi.advanceTimersByTimeAsync(50);
    await rejection;
    expect(vi.getTimerCount()).toBe(0);
  });

  it("clears the deadline after normal close", async () => {
    vi.useFakeTimers();
    const server = createServer();
    vi.spyOn(server, "close").mockImplementation((done) => { done!(); return server; });
    const force = vi.spyOn(server, "closeAllConnections");
    await closeHttpServer(server, 50);
    expect(vi.getTimerCount()).toBe(0);
    await vi.advanceTimersByTimeAsync(50);
    expect(force).not.toHaveBeenCalled();
  });

  it("drains an actual in-flight HTTP request without terminating it", async () => {
    const received = deferred<ServerResponse>();
    const server = createServer((_request, response) => received.resolve(response));
    servers.push(server);
    server.listen(0, "127.0.0.1");
    await once(server, "listening");
    const address = server.address() as { port: number };
    const body = new Promise<string>((done, fail) => {
      get({ host: "127.0.0.1", port: address.port, agent: false }, (response) => {
        let text = "";
        response.setEncoding("utf8");
        response.on("data", (chunk) => { text += chunk; });
        response.on("end", () => done(text));
        response.on("error", fail);
      }).on("error", fail);
    });
    const response = await received.promise;
    const force = vi.spyOn(server, "closeAllConnections");
    let closed = false;
    const closing = closeHttpServer(server, 1_000).then(() => { closed = true; });
    await turn();
    expect(closed).toBe(false);
    response.end("request completed");
    expect(await body).toBe("request completed");
    await closing;
    expect(closed).toBe(true);
    expect(force).not.toHaveBeenCalled();
  });

  it("bounds a genuinely stuck HTTP request and forcibly closes connections on deadline", async () => {
    const received = deferred<void>();
    const server = createServer(() => received.resolve());
    servers.push(server);
    server.listen(0, "127.0.0.1");
    await once(server, "listening");
    const address = server.address() as { port: number };
    const clientError = new Promise<Error>((done) => {
      get({ host: "127.0.0.1", port: address.port, agent: false }).on("error", done);
    });
    await received.promise;
    const force = vi.spyOn(server, "closeAllConnections");
    const start = Date.now();
    await expect(closeHttpServer(server, 100)).rejects.toThrow(/timed out.*100/i);
    expect(Date.now() - start).toBeGreaterThanOrEqual(80);
    expect(force).toHaveBeenCalledOnce();
    expect((await clientError).message).toMatch(/socket hang up|reset/i);
  });
});
