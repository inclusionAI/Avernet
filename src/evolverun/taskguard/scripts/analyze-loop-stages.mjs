#!/usr/bin/env node
// Analyze `debug_loop_stage` marks to decide whether inter-node gaps are
// caused by DB persistence (boundTaskFlow writes) or by chat.inject back-pressure.
//
// Usage:
//   node scripts/analyze-loop-stages.mjs <clawmind.log> [flowId]
//
// If flowId is omitted, every flow that emitted loop_stage marks is analyzed.
//
// A node transition gap (node_succeeded A -> node_ready B) is decomposed into:
//   handle_result_post(A)            rest of handleNodeResult after emit
//   persist_sequential_pre -> post   persistFinalizeOutcome (sequential)   [DB candidate]
//   loop_top(B)                      next iteration begins
//   persist_skips_pre -> post        persistFinalizeOutcome (skips)        [DB candidate]
//   ready_found(B)  ~= node_ready(B) ready nodes computed
//
// If a [persist_*_pre -> post] interval grows to seconds under concurrency,
// the gap is DB (boundTaskFlow write contention). If all marks are
// sub-100ms but the gap remains multi-second, it is driven by chat.inject
// (inject subprocess saturation starving the sync segments on a single
// event loop) or an uncovered await — the per-interval in-flight inject
// count flags the former.

import { readFileSync } from "node:fs";
import { argv, exit } from "node:process";

const logPath = argv[2];
const filterFlow = argv[3];
if (!logPath) {
  console.error("Usage: node scripts/analyze-loop-stages.mjs <clawmind.log> [flowId]");
  exit(1);
}

function parseTime(s) {
  return new Date(s).getTime();
}

// --- Load & filter ----------------------------------------------------------
const raw = readFileSync(logPath, "utf-8").split("\n");
const records = [];
for (const line of raw) {
  if (!line.trim()) continue;
  let o;
  try { o = JSON.parse(line); } catch { continue; }
  records.push(o);
}

// Injects carry flowId inside idempotency_key (first UUID segment);
// node_* / debug_loop_stage carry it in the top-level flow_id field.
function flowOf(o) {
  if (o.flow_id && /[0-9a-f]{8}-/.test(o.flow_id)) return o.flow_id;
  const d = o.details || {};
  const ik = d.idempotency_key || "";
  const m = ik.match(/^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/);
  return m ? m[1] : null;
}

const flows = new Map();
for (const o of records) {
  const fid = flowOf(o);
  if (!fid) continue;
  if (filterFlow && fid !== filterFlow) continue;
  if (!flows.has(fid)) flows.set(fid, []);
  flows.get(fid).push(o);
}

if (flows.size === 0) {
  console.error("No flow records found (with loop_stage marks) in", logPath);
  exit(1);
}

// --- Per-flow analysis ------------------------------------------------------
let printed = 0;
for (const [flowId, events] of flows) {
  const hasLoopStage = events.some((o) => o.event_type === "debug_loop_stage");
  if (!hasLoopStage) continue;
  printed++;
  events.sort((a, b) => parseTime(a.time) - parseTime(b.time));

  console.log("\n================================================================");
  console.log(`FLOW ${flowId}  (events=${events.length})`);
  console.log("================================================================");

  // Build inject in-flight intervals for overlap checking.
  const injects = [];
  {
    const pending = [];
    for (const o of events) {
      if (o.event_type !== "debug_inject_trace") continue;
      const s = o.details?.stage;
      const t = parseTime(o.time);
      if (s === "chat_inject_start") pending.push(t);
      else if (s === "chat_inject_done" || s === "chat_inject_failed") {
        const st = pending.shift();
        if (st !== undefined) injects.push([st, t]);
      }
    }
  }
  function inflightCount(t1, t2) {
    return injects.filter(([a, b]) => a < t2 && b > t1).length;
  }

  // Stage durations grouped by loop_iter.
  const stagesByIter = new Map();
  const ordered = [];
  for (const o of events) {
    if (o.event_type !== "debug_loop_stage") continue;
    const it = o.details?.loop_iter;
    if (!stagesByIter.has(it)) { stagesByIter.set(it, []); ordered.push(it); }
    stagesByIter.get(it).push({ stage: o.details.stage, nodeId: o.details.node_id, t: parseTime(o.time) });
  }

  // Aggregate per-stage duration stats (pre->post pairs).
  const agg = new Map();
  function aggAdd(stage, dur, injectInflight) {
    if (!agg.has(stage)) agg.set(stage, { n: 0, sum: 0, max: 0, injectSum: 0 });
    const a = agg.get(stage);
    a.n++; a.sum += dur; if (dur > a.max) a.max = dur; a.injectSum += injectInflight;
  }

  for (const it of ordered) {
    const marks = stagesByIter.get(it);
    if (marks.length === 0) continue;
    let target = marks[0].nodeId || "(none)";
    for (let i = 0; i < marks.length; i++) {
      const cur = marks[i];
      const next = marks[i + 1];
      if (!next) break;
      const dur = next.t - cur.t;
      if (cur.nodeId) target = cur.nodeId;
      const inflight = inflightCount(cur.t, next.t);
      const label = `${cur.stage} -> ${next.stage}`;
      aggAdd(label, dur, inflight);
    }
  }

  // Per-node gap table (node_succeeded A -> node_ready B), mapped to marks.
  // Find mark sequences: handle_result_post(A) ... loop_top ... ready_found(B)
  const allMarks = [];
  for (const it of ordered) {
    for (const m of stagesByIter.get(it)) allMarks.push({ ...m, iter: it });
  }

  // Print aggregated stage timings sorted by total time desc.
  console.log("\n--- Per-stage duration (across all iterations) ---");
  console.log("stage                                                        n     sum(s)  max(s)  avgInflightInjects");
  const aggArr = [...agg.entries()].sort((a, b) => b[1].sum - a[1].sum);
  for (const [stage, a] of aggArr) {
    console.log(
      stage.padEnd(58) +
      String(a.n).padStart(4) + "  " +
      (a.sum / 1000).toFixed(2).padStart(8) + "  " +
      (a.max / 1000).toFixed(2).padStart(6) + "  " +
      (a.injectSum / a.n).toFixed(2).padStart(6),
    );
  }

  // Per-iteration timeline for the slowest iterations.
  console.log("\n--- Per-iteration detail (sorted by total iteration time, top 12) ---");
  const iterTotals = ordered.map((it) => {
    const m = stagesByIter.get(it);
    return { it, dur: m[m.length - 1].t - m[0].t, marks: m };
  }).sort((a, b) => b.dur - a.dur).slice(0, 12);

  for (const { it, marks } of iterTotals) {
    console.log(`\n  loop_iter=${it}  node=${marks[0].nodeId || "(none)"}`);
    for (let i = 0; i < marks.length; i++) {
      const cur = marks[i];
      const next = marks[i + 1];
      const gap = next ? `${((next.t - cur.t) / 1000).toFixed(2).padStart(7)}s` : "      -";
      const inj = next ? `inj=${inflightCount(cur.t, next.t)}` : "";
      console.log(`    ${gap}  ${cur.stage.padEnd(28)} ${inj}`);
    }
  }
}

if (printed === 0) {
  console.error("No `debug_loop_stage` marks found in", logPath);
  console.error("These marks are emitted by executeLoop() (controller.ts). Rebuild & rerun the workflow on a build that includes them, then re-run this script.");
  exit(1);
}