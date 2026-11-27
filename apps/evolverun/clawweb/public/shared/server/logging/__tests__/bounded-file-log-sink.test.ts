import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const fs = vi.hoisted(() => ({ mkdir: vi.fn(), appendFile: vi.fn() }));
vi.mock("node:fs/promises", () => fs);
import { createBoundedFileLogSink } from "../bounded-file-log-sink.js";

function deferred() {
  let resolve!: () => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<void>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}
const path = "/logs/service/api.log";
beforeEach(() => {
  vi.useFakeTimers();
  fs.mkdir.mockReset().mockResolvedValue(undefined);
  fs.appendFile.mockReset().mockResolvedValue(undefined);
});
afterEach(() => { vi.useRealTimers(); });

describe("bounded asynchronous file sink", () => {
  it("batches lines without starting filesystem work or one timer per write", async () => {
    const sink = createBoundedFileLogSink({ filePath: path });
    for (let i = 0; i < 100; i++) expect(sink.write("line\n")).toBe(true);
    expect(fs.mkdir).not.toHaveBeenCalled();
    expect(fs.appendFile).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(1);
    await vi.advanceTimersByTimeAsync(50);
    expect(fs.mkdir).toHaveBeenCalledExactlyOnceWith("/logs/service", { recursive: true });
    expect(fs.appendFile).toHaveBeenCalledOnce();
    expect(fs.appendFile.mock.calls[0][1].toString()).toBe("line\n".repeat(100));
    expect(sink.getStats()).toEqual({ queuedBytes: 0, droppedLines: 0, writeFailures: 0 });
    expect(vi.getTimerCount()).toBe(0);
  });

  it("bounds queued PLUS in-flight UTF-8 bytes and permits only one slow writer", async () => {
    const slow = deferred();
    let active = 0, maximum = 0;
    fs.appendFile.mockImplementation(async () => {
      maximum = Math.max(maximum, ++active);
      await slow.promise;
      active--;
    });
    const warning = vi.fn();
    const sink = createBoundedFileLogSink({ filePath: path, maxQueuedBytes: 12, maxBatchBytes: 8, onWarning: warning });
    expect(sink.write("😀\n")).toBe(true); // Five bytes, not three UTF-16 units.
    const flush = sink.flush();
    await vi.advanceTimersByTimeAsync(0);
    expect(sink.flush()).toBe(flush);
    expect(sink.write("second\n")).toBe(true); // Seven bytes: queue is now full.
    for (let i = 0; i < 100; i++) expect(sink.write("x")).toBe(false);
    expect(sink.getStats()).toEqual({ queuedBytes: 12, droppedLines: 100, writeFailures: 0 });
    expect(fs.appendFile).toHaveBeenCalledOnce();
    expect(warning).toHaveBeenCalledOnce();
    expect(vi.getTimerCount()).toBe(0);
    const all = sink.flush();
    slow.resolve();
    await all;
    await flush;
    expect(maximum).toBe(1);
    expect(fs.appendFile.mock.calls.map(call => call[1].toString())).toEqual(["😀\n", "second\n"]);
    expect(sink.getStats().queuedBytes).toBe(0);
  });

  it("rejects oversized strings before encoding, including multi-byte lines, without splitting lines", async () => {
    const warning = vi.fn();
    const sink = createBoundedFileLogSink({ filePath: path, maxBatchBytes: 8, onWarning: warning });
    expect(sink.write("x".repeat(1_000_000))).toBe(false);
    expect(sink.write("😀😀\n")).toBe(false);
    expect(sink.write("")).toBe(true);
    expect(sink.write("abc\n")).toBe(true);
    expect(sink.write("def\n")).toBe(true);
    expect(sink.write("ghi\n")).toBe(true);
    await sink.flush();
    expect(fs.appendFile.mock.calls.map(call => call[1].toString())).toEqual(["abc\ndef\n", "ghi\n"]);
    expect(sink.getStats()).toEqual({ queuedBytes: 0, droppedLines: 2, writeFailures: 0 });
    expect(warning).toHaveBeenCalledOnce();
  });

  it("counts and drops failed batches, recovers its directory, and rate-limits failure warnings", async () => {
    fs.appendFile.mockRejectedValue(new Error("disk unavailable"));
    const warning = vi.fn();
    const sink = createBoundedFileLogSink({ filePath: path, onWarning: warning });
    sink.write("a\n"); sink.write("b\n");
    await sink.flush();
    sink.write("c\n");
    await sink.flush();
    expect(sink.getStats()).toEqual({ queuedBytes: 0, droppedLines: 3, writeFailures: 2 });
    expect(warning).toHaveBeenCalledOnce();
    expect(warning.mock.calls[0][0]).not.toContain("disk unavailable");
    await vi.advanceTimersByTimeAsync(30_000);
    sink.write("d\n"); await sink.flush();
    expect(warning).toHaveBeenCalledTimes(2);
    fs.appendFile.mockResolvedValue(undefined);
    sink.write("recovered\n"); await sink.flush();
    expect(fs.mkdir).toHaveBeenCalledTimes(4);
    expect(sink.getStats()).toEqual({ queuedBytes: 0, droppedLines: 4, writeFailures: 3 });
    expect(vi.getTimerCount()).toBe(0);
  });

  it("handles mkdir failure and broken warning observers without unhandled rejection", async () => {
    fs.mkdir.mockRejectedValueOnce(new Error("permission denied"));
    const sink = createBoundedFileLogSink({ filePath: path, maxBatchBytes: 4, onWarning: () => { throw new Error("observer"); } });
    expect(() => sink.write("too large")).not.toThrow();
    sink.write("a\n");
    await expect(sink.flush()).resolves.toBeUndefined();
    expect(fs.appendFile).not.toHaveBeenCalled();
    sink.write("b\n"); await sink.flush();
    expect(fs.appendFile).toHaveBeenCalledExactlyOnceWith(path, expect.any(Buffer));
    expect(sink.getStats()).toEqual({ queuedBytes: 0, droppedLines: 2, writeFailures: 1 });
  });

  it("shares concurrent flush completion, drains lines added during I/O, then supports another flush", async () => {
    const slow = deferred();
    fs.appendFile.mockReturnValueOnce(slow.promise);
    const sink = createBoundedFileLogSink({ filePath: path });
    sink.write("first\n");
    const first = sink.flush();
    expect(sink.flush()).toBe(first);
    await vi.advanceTimersByTimeAsync(0);
    sink.write("second\n");
    const second = sink.flush();
    expect(second).not.toBe(first);
    expect(sink.flush()).toBe(second);
    let done = false;
    void first.then(() => { done = true; });
    await vi.advanceTimersByTimeAsync(0);
    expect(done).toBe(false);
    slow.resolve();
    await second;
    await first;
    expect(fs.appendFile.mock.calls.map(call => call[1].toString())).toEqual(["first\n", "second\n"]);
    sink.write("third\n");
    expect(sink.flush()).not.toBe(first);
    await sink.flush();
    expect(sink.getStats().queuedBytes).toBe(0);
    expect(vi.getTimerCount()).toBe(0);
  });

  it.each([false, true])("joins pre-call lines even during writer settlement (second append fails=%s)", async fail => {
    const firstAppend = deferred();
    const secondAppend = deferred();
    fs.appendFile.mockReturnValueOnce(firstAppend.promise).mockReturnValueOnce(secondAppend.promise);
    const sink = createBoundedFileLogSink({ filePath: path, onWarning: vi.fn() });
    sink.write("first\n");
    const flush1 = sink.flush();
    await vi.advanceTimersByTimeAsync(0);
    let flush2!: Promise<void>;
    let done = false;
    // Registered after drain's await: this runs after its final head check but
    // before writer.finally. The old flush reused a settling writer here.
    void firstAppend.promise.then(() => {
      expect(sink.write("second\n")).toBe(true);
      flush2 = sink.flush();
      void flush2.then(() => { done = true; });
    });
    firstAppend.resolve();
    await flush1;
    await vi.advanceTimersByTimeAsync(0);
    expect(fs.appendFile.mock.calls.map(call => call[1].toString())).toEqual(["first\n", "second\n"]);
    expect(sink.getStats().queuedBytes).toBe(7);
    expect(done).toBe(false);
    expect(vi.getTimerCount()).toBe(0);
    if (fail) secondAppend.reject(new Error("late append failure"));
    else secondAppend.resolve();
    await flush2;
    expect(done).toBe(true);
    expect(sink.getStats()).toEqual({ queuedBytes: 0, droppedLines: fail ? 1 : 0, writeFailures: fail ? 1 : 0 });
    expect(vi.getTimerCount()).toBe(0);
  });

  it("returns independent stats snapshots and validates fixed queue/timer bounds", async () => {
    const sink = createBoundedFileLogSink({ filePath: path });
    sink.getStats().droppedLines = 999;
    expect(sink.getStats().droppedLines).toBe(0);
    for (const option of ["maxQueuedBytes", "maxBatchBytes", "flushIntervalMs"]) {
      for (const value of [0, -1, NaN, Infinity, 1.2]) {
        expect(() => createBoundedFileLogSink({ filePath: path, [option]: value })).toThrow(RangeError);
      }
    }
    expect(() => createBoundedFileLogSink({ filePath: path, flushIntervalMs: 2 ** 31 })).toThrow(RangeError);
    await sink.flush();
    expect(fs.appendFile).not.toHaveBeenCalled();
  });
});
