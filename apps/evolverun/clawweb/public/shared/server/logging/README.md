# Bounded operational logging

These helpers are available through the existing
`@avernet/clawweb-shared/server/logging/*` export. They install no process/signal
handlers; the composition root owns shutdown and any drain deadline.

## File sink

`createBoundedFileLogSink({ filePath, ...options })` returns:

- `write(line): boolean`: enqueue an already serialized line, including its
  newline. `false` means the oversized/full-queue line was dropped. Empty lines
  are accepted without I/O. No synchronous filesystem I/O or promise per line.
- `flush(): Promise<void>`: cancel the batching timer and wait for every line accepted
  before this call to be processed. Calls at the same accepted-line watermark
  share completion; later writes require a new flush barrier. This is **not durability** or
  `fsync`: failed batches are counted/dropped, not retried, and do not reject the
  drain. An unfinished filesystem operation can keep flush pending; callers
  needing bounded shutdown must race it against a rejecting deadline.
- `getStats()`: a snapshot of `queuedBytes`, `droppedLines`, `writeFailures`.
  `queuedBytes` includes queued **and in-flight** bytes; `writeFailures` counts
  failed batches (including directory creation), not individual lines.

Defaults: **1 MiB** total queued/in-flight UTF-8 bytes, **64 KiB** per batch/line,
**50 ms** batching timer. The timer is unreferenced and does not keep Node alive.
Override with positive safe-integer `maxQueuedBytes`, `maxBatchBytes`, and
`flushIntervalMs`; batch size is capped by queue capacity. Lines are never split.

A single writer appends each batch by filename, reopening the fixed path every
append so external log rotation is respected. Writes racing with writer
settlement are drained on the same single-writer lane, including a new flush
barrier accepted while the previous writer is settling. Oversize/full-queue drops and write failures have
separate **30-second** warning limits; `onWarning(message)` overrides console
warnings. Warning observer errors are contained. A process crash, queue pressure
or failed storage can lose entries: this is best-effort telemetry, not a durable
security-audit or business-commit log.

## Plain JSON value preview

`boundedLogValue(value, options?)` returns an inert JSON-serializable preview,
not a full clone followed by truncation. Defaults: **16,384 final JSON
characters** (including escaping/delimiters), **depth 6**, and **100 globally
visited entries**, shared across all descendants. `maxChars` has a minimum of 4;
`maxDepth` and `maxEntries` allow zero. Invalid options use defaults; depth is
always capped at 64. UTF-16 surrogate pairs are not split when clipping text.

Accessors, inherited values, `toJSON`, and coercion hooks are not invoked.
Returned objects have null prototypes, including own `__proto__` fields.
Cycles, unsupported primitives, depth limits and unreadable proxies become
bounded markers. Keys longer than 256 characters stop that object's preview.
The budgets limit captured entries/JSON; they cannot preempt malicious Proxy
traps or guarantee constant-time engine enumeration of arbitrary objects.

Credential-like keys, business-body keys, inline credentials and PII are
redacted. `redactKeys` adds key matching without mutating a caller's RegExp
state. Redaction is defense in depth, not permission to log secrets. Consumers
must still exclude endpoint-specific protected payloads.

## Diagnostic text / URLs

`redactLogText(text: string, maxChars = 16 * 1024): string` is also used by
`boundedLogValue` for strings and keys. It bounds sampling before regex work,
redacts raw Evidence access path capabilities and signed-query credentials
case-insensitively, and handles up to four ASCII percent-decoding layers.
Remaining encoded tokens are conservatively hidden. Clipped input drops its
final non-whitespace token before redaction so a partial secret prefix cannot
escape; output is surrogate-safe and bounded by `maxChars` (minimum 4, invalid
values use the default). Safe stack whitespace and console `%s`/`%d`/`%j`/`%%`
remain literal. Consumers must pass the result as a value to a fixed console
format string, not use untrusted text as the format string. This is diagnostic
normalization, not a URL parser or a general authorization/security boundary.
