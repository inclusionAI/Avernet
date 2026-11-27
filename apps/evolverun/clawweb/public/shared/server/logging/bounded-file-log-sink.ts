import { appendFile, mkdir } from "node:fs/promises";
import { dirname } from "node:path";

type SinkOptions = {
  filePath: string;
  maxQueuedBytes?: number;
  maxBatchBytes?: number;
  flushIntervalMs?: number;
  onWarning?: (message: string) => void;
};

type QueuedLine = { data: Buffer; next?: QueuedLine };
type SinkStats = { queuedBytes: number; droppedLines: number; writeFailures: number };

function positiveInteger(value: number | undefined, fallback: number, name: string): number {
  if (value === undefined) return fallback;
  if (!Number.isSafeInteger(value) || value < 1) throw new RangeError(`${name} must be a positive safe integer`);
  return value;
}

/**
 * Best-effort, single-writer file logging. write() never starts a promise per line.
 * queuedBytes includes the batch in flight; oversized/full-queue lines are rejected.
 * Lines must include their own newline and fit maxBatchBytes (they are never split).
 * Failed batches are counted/dropped, not retried. flush() waits for settlement;
 * getStats() reports losses. Each append opens/closes the fixed path for logrotate.
 */
export function createBoundedFileLogSink(options: SinkOptions): {
  write(line: string): boolean;
  flush(): Promise<void>;
  getStats(): SinkStats;
} {
  const { filePath } = options;
  const maxQueuedBytes = positiveInteger(options.maxQueuedBytes, 1024 * 1024, "maxQueuedBytes");
  const maxBatchBytes = Math.min(positiveInteger(options.maxBatchBytes, 64 * 1024, "maxBatchBytes"), maxQueuedBytes);
  const flushIntervalMs = positiveInteger(options.flushIntervalMs, 50, "flushIntervalMs");
  if (flushIntervalMs > 2 ** 31 - 1) throw new RangeError("flushIntervalMs exceeds the timer limit");
  const directory = dirname(filePath);
  const onWarning = options.onWarning ?? ((message: string) => console.warn(message));
  const stats: SinkStats = { queuedBytes: 0, droppedLines: 0, writeFailures: 0 };
  let head: QueuedLine | undefined;
  let tail: QueuedLine | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let writer: Promise<void> | undefined;
  let accepted = 0;
  let processed = 0;
  const barriers: { target: number; promise: Promise<void>; resolve: () => void }[] = [];
  let directoryReady = false;
  let lastDropWarning = -Infinity;
  let lastFailureWarning = -Infinity;

  function warn(kind: "drop" | "failure"): void {
    const now = Date.now();
    const last = kind === "drop" ? lastDropWarning : lastFailureWarning;
    if (now - last < 30_000) return;
    if (kind === "drop") lastDropWarning = now;
    else lastFailureWarning = now;
    try {
      onWarning(`[bounded-file-log-sink] ${kind === "drop" ? "Dropped log line" : "Cannot append log batch"} to ${filePath}`
        + ` (queuedBytes=${stats.queuedBytes}, droppedLines=${stats.droppedLines}, writeFailures=${stats.writeFailures})`);
    } catch { /* A broken warning observer must not break requests or the writer. */ }
  }

  function takeBatch(): { data: Buffer; lines: number } {
    const chunks: Buffer[] = [];
    let bytes = 0;
    while (head && bytes + head.data.byteLength <= maxBatchBytes) {
      chunks.push(head.data);
      bytes += head.data.byteLength;
      head = head.next;
    }
    if (!head) tail = undefined;
    return { data: chunks.length === 1 ? chunks[0] : Buffer.concat(chunks, bytes), lines: chunks.length };
  }

  async function drain(): Promise<void> {
    while (head) {
      const batch = takeBatch();
      try {
        if (!directoryReady) {
          await mkdir(directory, { recursive: true });
          directoryReady = true;
        }
        await appendFile(filePath, batch.data);
      } catch {
        directoryReady = false;
        stats.writeFailures += 1;
        stats.droppedLines += batch.lines;
        warn("failure");
      } finally {
        stats.queuedBytes -= batch.data.byteLength;
        processed += batch.lines;
        while (barriers.length && barriers[0].target <= processed) barriers.shift()!.resolve();
      }
    }
  }

  function startWriter(): void {
    if (writer || !head) return;
    writer = drain().finally(() => {
      writer = undefined;
      // A write/flush can arrive after drain's last head check, while its
      // promise is still settling. Continue the SAME single-writer lane.
      if (head) startWriter();
    });
  }

  function flush(): Promise<void> {
    if (timer) {
      clearTimeout(timer);
      timer = undefined;
    }
    if (processed === accepted) return Promise.resolve();
    const last = barriers[barriers.length - 1];
    if (last?.target === accepted) return last.promise;
    let resolve!: () => void;
    const promise = new Promise<void>(done => { resolve = done; });
    barriers.push({ target: accepted, promise, resolve });
    startWriter();
    return promise;
  }

  function schedule(): void {
    if (writer || timer) return;
    timer = setTimeout(() => {
      timer = undefined;
      void flush();
    }, flushIntervalMs);
    timer.unref();
  }

  function write(line: string): boolean {
    // Check UTF-16 length first so a huge rejected line is not scanned/encoded.
    if (line.length > maxBatchBytes) {
      stats.droppedLines += 1;
      warn("drop");
      return false;
    }
    const bytes = Buffer.byteLength(line, "utf8");
    if (bytes > maxBatchBytes || bytes > maxQueuedBytes - stats.queuedBytes) {
      stats.droppedLines += 1;
      warn("drop");
      return false;
    }
    if (bytes === 0) return true;
    const item: QueuedLine = { data: Buffer.from(line, "utf8") };
    if (tail) tail.next = item;
    else head = item;
    tail = item;
    stats.queuedBytes += bytes;
    accepted += 1;
    schedule();
    return true;
  }

  return { write, flush, getStats: () => ({ ...stats }) };
}
