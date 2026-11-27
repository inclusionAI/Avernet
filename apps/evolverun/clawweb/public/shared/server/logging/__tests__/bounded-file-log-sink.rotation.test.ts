import { it, expect } from "vitest";
import { mkdtemp, readFile, rename, rm } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { createBoundedFileLogSink } from "../bounded-file-log-sink.js";

it("reopens the fixed filename after external rotation instead of retaining its old descriptor", async () => {
  const root = await mkdtemp(join(tmpdir(), "bounded-log-rotate-"));
  const path = join(root, "nested", "api.log");
  const rotated = path + ".1";
  const sink = createBoundedFileLogSink({ filePath: path });
  try {
    sink.write("old file\n"); await sink.flush();
    await rename(path, rotated);
    sink.write("new file\n"); await sink.flush();
    expect(await readFile(rotated, "utf8")).toBe("old file\n");
    expect(await readFile(path, "utf8")).toBe("new file\n");
    expect(sink.getStats()).toEqual({ queuedBytes: 0, droppedLines: 0, writeFailures: 0 });
  } finally {
    await sink.flush();
    await rm(root, { recursive: true, force: true });
  }
});
