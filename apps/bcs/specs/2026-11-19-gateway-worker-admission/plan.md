# Gateway–Worker admission and output implementation

Goal: integrate #4245 with current dev for A2A output, A03, A04 and A05.
User authorized implementation on 2026-11-19 after the recorded discussion.
Base: #4245 00b5cb053a, rebased onto github/dev a2433cbf2b -> 66976a7f2a.
Prior draft worktree remains untouched. Public source belongs in Avernet.

## Binding scope

- Distributed code only. Keep scope/group authorization and reliable Inbox activation.
- Worker-side public admission initializes tasks; Gateway sends stable business commands.
- Existing canonical request survives retries and first-request races. Changed business intent conflicts.
- No unit/cumulative/full-snapshot output byte quotas this phase. Keep persistence mechanics internal.
- Neutral display persistence and one injected protocol-output adapter; A2A first, extension point for other formats.
- Gateway keeps JSON-RPC/SSE/auth/envelope, receives converted output. No new message history store.
- A01/A02/A06 fixes, cursor redesign and list order redesign are deferred. Necessary compiling integration only.
- Preserve prior coverage gates. No local load test, no new OSS. TBase PROXY evidence cannot be replaced by memory tests.

## Tasks

- [x] Rebase current PR head onto authoritative dev and retain backup source ref.
- [x] Public admission contract: replace Gateway full initial snapshot with semantic start intent; add canonical lookup.
- [x] Worker admission initializes once, compares caller intent, recovers original request and concurrent selection.
- [x] Output byte policy removal, including TaskFeed reads and restore (independent task).
- [x] Pure A2A adapter, Gateway passthrough and adapter contract tests (independent task).
- [x] Assemble Gateway with shared Inbox, real Worker and TaskFeed; test retry/conflict/concurrency and output.
- [x] Update owning contracts/SPEC/context, review whole change, run affected tests/Clippy and required gates.

## Verification

Write meaningful regressions before implementation. Observe retry changing time/config fail, then pass;
force both lookups to miss for concurrent same-ID input and prove one canonical business input;
changed body/target remains conflict, unknown writes remain retryable and repairable.
Exercise real HTTP router through shared services and SQLite Result/Blob, stable Upsert/AppendText
artifacts and GetTask agree. Over-old-size output tests belong to the output policy task.

## Rulings

- github/dev is authoritative; origin is the internal fetch mirror. No mirror pushes.
- Keep neutral DisplaySnapshot persistence. Output adapter runs over restored views/events so incremental and snapshot reads share semantics.
- No fabricated history/status.message. Their lack of source remains explicit; interaction/profile work is A02, outside this implementation.
- Admission wrapper lives in Worker module; no network submission service or dependency on concrete Worker from Gateway core.
