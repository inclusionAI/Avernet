import { execFile } from "node:child_process";
import { randomUUID } from "node:crypto";
import { createServer } from "node:http";
import { parseArgs, promisify } from "node:util";
import { setTimeout as sleep } from "node:timers/promises";
import Database from "better-sqlite3";
import { parseEffectResumeConfig, recoveryEffectWait, effectResumeTiming } from "../../public/modules/clawinsight/dist/server/services/repair/session-recovery/effect-resume.js";
import { resolveTaskControlPlaneEnvironment } from "../../public/modules/clawinsight/dist/server/services/repair/common/task-control-plane.js";
import { render } from "./dashboard.mjs";

const exec = promisify(execFile);
const definitive = effect => ["recovered", "not_recovered"].includes(effect);
const { values: args, positionals: [mode] } = parseArgs({ allowPositionals: true, options: {
  db: { type: "string", default: "effect-recheck.sqlite" }, from: { type: "string" }, to: { type: "string" },
  plane: { type: "string" }, owner: { type: "string" }, port: { type: "string", default: "8732" },
  concurrent: { type: "string", default: "2" },
} });
process.umask(0o077);
const db = new Database(args.db);
db.pragma("journal_mode = WAL");
db.pragma("busy_timeout = 5000");
db.exec(`
  CREATE TABLE IF NOT EXISTS task (
    id TEXT PRIMARY KEY, created TEXT NOT NULL, plane TEXT NOT NULL, source_status TEXT NOT NULL,
    request TEXT NOT NULL, state TEXT NOT NULL, reason TEXT, execution TEXT, effect TEXT, result_version INTEGER,
    callback TEXT, updated INTEGER NOT NULL, failures INTEGER NOT NULL DEFAULT 0, retry_at INTEGER NOT NULL DEFAULT 0
  );
  CREATE TABLE IF NOT EXISTS scan (id TEXT PRIMARY KEY, state TEXT NOT NULL, count INTEGER NOT NULL);
`);
const slots = Number(args.concurrent);
if (!Number.isSafeInteger(slots) || slots < 1 || slots > 5) throw new Error("--concurrent must be 1–5");
const byId = db.prepare("SELECT * FROM task WHERE id = ?");
function patch(id, fields) {
  const entries = Object.entries({ ...fields, updated: Date.now() });
  db.prepare(`UPDATE task SET ${entries.map(([key]) => key + "=?").join(",")} WHERE id=?`).run(...entries.map(([, value]) => value), id);
}

function required(name) {
  const value = process.env[name];
  if (!value) throw new Error(`Missing ${name}`);
  return value;
}
async function query(sql, limit = 100) {
  const params = JSON.parse(required("CW_RECHECK_ODC_ARGS"));
  if (!Array.isArray(params) || params.some(x => typeof x !== "string")) throw new Error("CW_RECHECK_ODC_ARGS must be a JSON array");
  const { stdout } = await exec("odc-cli", ["sql", ...params, "--format", "json", "--limit", String(limit), sql],
    { timeout: 45000, maxBuffer: 16 * 1024 * 1024 }).catch(() => { throw new Error("ODC query unavailable"); });
  const rows = JSON.parse(stdout);
  if (!Array.isArray(rows) || rows.some(row => row["#"] === "-")) throw new Error("ODC query failed");
  return rows;
}
async function policy(plane) {
  const [row] = await query(`SELECT enabled, config_json FROM ce_app_config WHERE config_key='session_recovery_effect_resume_${plane}'`, 1);
  return Number(row?.enabled) === 1 ? parseEffectResumeConfig(JSON.parse(row.config_json)) : null;
}
class ApiError extends Error {
  constructor(status, code) { super(code); this.status = status; this.code = code; }
}
async function api(plane, id, request) {
  const base = new URL(required(`CW_RECHECK_${plane.toUpperCase()}_URL`));
  if (base.protocol !== "https:" || base.username || base.password) throw new Error("ClawWeb URL must use HTTPS");
  const headers = JSON.parse(required(`CW_RECHECK_${plane.toUpperCase()}_HEADERS`));
  const suffix = request ? "/effect-recheck" : "";
  const response = await fetch(new URL(`/api/insight/v1/monitoring/session-recoveries/${encodeURIComponent(id)}${suffix}`, base), {
    method: request ? "POST" : "GET", headers: { ...headers, ...(request ? { "content-type": "application/json" } : {}) },
    ...(request ? { body: JSON.stringify({ request_id: request }) } : {}), signal: AbortSignal.timeout(45000), redirect: "error",
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new ApiError(response.status, /^[a-z_]+$/.test(data.error ?? "") ? data.error : `http_${response.status}`);
  }
  const data = await response.json();
  if (request ? data.task_id !== id : data.taskId !== id) throw new Error("Unexpected task response");
  return data;
}
function eligibility(detail, currentPolicy) {
  if (!detail.canOperate) return "no_permission";
  if (detail.simulation || detail.level === "debug" || detail.completionReason === "no_recovery_needed") return "not_a_live_intervention";
  const facts = detail.effectRecheckFacts;
  if (!facts) return "source_unavailable";
  if (facts.executionActive || (detail.executions ?? []).some(step => !["succeeded", "failed", "canceled"].includes(step.status))) return "execution_pending";
  if (detail.status === "waiting_effect") {
    if (currentPolicy && ["observe", "verify"].includes(detail.phase)) {
      const wait = recoveryEffectWait({ effectWait: facts.effectWait, timeline: detail.timeline });
      if (!wait) return "wait_history_unverified";
      if (effectResumeTiming(wait, Date.now(), currentPolicy).remaining) return "online_resume_available";
    }
  } else if (!["completed", "failed", "canceled"].includes(detail.status)) return "online_task";
  if (definitive(detail.judgment?.effect)) return "result_delivery_only";
  if (facts.interventionStatus === "none" || detail.judgment?.effect === "not_intervened") return "not_a_live_intervention";
  return facts.interventionStatus === "performed" ? "eligible" : "intervention_needs_evidence";
}

function date(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value ?? "") || !Number.isFinite(Date.parse(value)) || new Date(value).toISOString().slice(0, 10) !== value) {
    throw new Error("Use YYYY-MM-DD dates");
  }
  return value;
}
async function scan() {
  const from = date(args.from), to = date(args.to), plane = args.plane;
  if (from >= to || !["pre", "prod"].includes(plane) || !/^\d{1,32}$/.test(args.owner ?? "")) throw new Error("Use an exclusive --to date, --plane and --owner");
  const key = [from, to, plane, args.owner].join("/");
  db.prepare("INSERT OR REPLACE INTO scan VALUES (?, 'scanning', 0)").run(key);
  let cursor = 0, count = 0;
  try {
    for (;;) {
      const currentPolicy = await policy(plane);
      const rows = await query(`SELECT t.id, t.task_id, t.status, CAST(t.gmt_create AS CHAR) created,
          JSON_UNQUOTE(JSON_EXTRACT(t.config_json,'$.controlPlaneEnvironment')) plane,
          JSON_UNQUOTE(JSON_EXTRACT(t.config_json,'$.publicBaseUrl')) base,
          JSON_UNQUOTE(JSON_EXTRACT(t.config_json,'$.level')) level,
          d.self_healing_results results
        FROM ce_tasks t LEFT JOIN insight_monitoring_diagnose d ON d.event_id=JSON_UNQUOTE(JSON_EXTRACT(t.config_json,'$.request.event_id'))
        WHERE t.task_type='session_recovery' AND t.user_id='${args.owner}' AND t.id > ${cursor}
          AND t.gmt_create >= '${from} 00:00:00' AND t.gmt_create < '${to} 00:00:00'
        ORDER BY t.id LIMIT 100`);
      for (const row of rows) {
        if (!Number.isSafeInteger(Number(row.id)) || Number(row.id) <= cursor) throw new Error("Invalid scan cursor");
        cursor = Number(row.id);
        count++;
        if (resolveTaskControlPlaneEnvironment({ controlPlaneEnvironment: row.plane, publicBaseUrl: row.base }) !== plane) continue;
        const prior = byId.get(row.task_id);
        if (prior && !["excluded", "unresolved", "ready"].includes(prior.state)) continue;
        let reason;
        try {
          const report = JSON.parse(row.results ?? "[]").filter(item => item.task_id === row.task_id)
            .sort((a, b) => b.result_version - a.result_version)[0];
          reason = definitive(report?.effect) ? "effect_already_known" : row.level === "debug" ? "not_a_live_intervention"
            : eligibility(await api(plane, row.task_id), currentPolicy);
        } catch (err) {
          if (err.status === 401 || err.status === 403) throw err;
          reason = "source_unavailable";
        }
        const state = ["eligible", "result_delivery_only", "intervention_needs_evidence"].includes(reason) ? "ready"
          : ["source_unavailable", "wait_history_unverified", "execution_pending"].includes(reason) ? "unresolved" : "excluded";
        db.prepare(`INSERT INTO task (id,created,plane,source_status,request,state,reason,updated) VALUES (?,?,?,?,?,?,?,?)
          ON CONFLICT(id) DO UPDATE SET state=excluded.state,reason=excluded.reason,source_status=excluded.source_status,updated=excluded.updated
          WHERE task.state IN ('excluded','unresolved','ready')`)
          .run(row.task_id, row.created, plane, row.status, randomUUID(), state, reason, Date.now());
      }
      db.prepare("UPDATE scan SET count=? WHERE id=?").run(count, key);
      if (rows.length < 100) break;
    }
    db.prepare("UPDATE scan SET state='finished' WHERE id=?").run(key);
  } catch (error) {
    db.prepare("UPDATE scan SET state='paused' WHERE id=?").run(key);
    throw error;
  }
}
const claim = db.transaction(() => {
  const { count } = db.prepare("SELECT COUNT(*) count FROM task WHERE state IN ('starting','running')").get();
  if (count >= slots) return;
  const task = db.prepare("SELECT * FROM task WHERE state='ready' ORDER BY created,id LIMIT 1").get();
  if (!task) return;
  patch(task.id, { state: "starting" });
  return byId.get(task.id);
}).immediate;

const busy = new Set();
const maxPolls = 8;
async function advance(task) {
  try {
    if (task.state === "starting") {
      // On an uncertain submission always replay the same request before releasing the slot.
      const currentPolicy = await policy(task.plane);
      const detail = await api(task.plane, task.id);
      const prior = detail.executions?.find(item => item.effectRecheckRequestId === task.request);
      const queued = detail.timeline?.find(item => item.payload?.effect_recheck_request_id === task.request && item.payload?.result_version);
      if (prior) patch(task.id, { execution: prior.executionId, state: "running", failures: 0, reason: null, retry_at: 0 });
      else if (queued) patch(task.id, { state: "done", result_version: queued.payload.result_version, effect: detail.judgment?.effect ?? null,
        callback: "pending", failures: 0, retry_at: 0, reason: "result_delivery_only" });
      else {
        const reason = eligibility(detail, currentPolicy);
        if (!["eligible", "result_delivery_only", "intervention_needs_evidence"].includes(reason)) {
          // A timed-out POST may be concurrently committing. Let the backend arbitrate that request ID.
          if (!task.failures) { patch(task.id, { state: "excluded", reason }); return; }
        }
        const ack = await api(task.plane, task.id, task.request);
        if (ack.status === "result_queued") patch(task.id, { state: "done", result_version: ack.result_version,
          effect: detail.judgment?.effect ?? null, callback: "pending", reason: "result_delivery_only", failures: 0, retry_at: 0 });
        else if (ack.status === "accepted" && typeof ack.execution_id === "string") patch(task.id, { state: "running", execution: ack.execution_id, failures: 0, retry_at: 0, reason: null });
        else throw new Error("Unexpected recheck acknowledgement");
      }
      return;
    }
    const detail = await api(task.plane, task.id);
    if (task.state === "running") {
      const step = detail.executions?.find(item => item.executionId === task.execution);
      if (!step || !["succeeded", "failed", "canceled"].includes(step.status) || (step.status === "canceled" && step.termination?.status !== "remote_stopped")) {
        patch(task.id, { reason: step?.status ?? "waiting_for_execution_record", retry_at: Date.now() + 5000 });
        return;
      }
      const report = detail.timeline?.find(item => item.payload?.execution_id === task.execution && item.payload?.effect_recheck_outcome);
      const version = report?.payload?.result_version ?? null;
      const effect = report?.payload?.effect_recheck_outcome ?? null;
      patch(task.id, { state: report ? "done" : "failed", reason: report?.payload?.reason ?? (report ? null : step.status),
        result_version: version, effect, callback: version ? "pending" : null, retry_at: 0, failures: 0 });
      return;
    }
    const version = task.result_version;
    const delivered = detail.timeline?.find(item => item.event_id === `result-${version}` || item.event_id === `result-stopped-${version}`);
    patch(task.id, { callback: !delivered ? "pending" : delivered.event_id.startsWith("result-stopped-") ? "exhausted" : "accepted",
      retry_at: Date.now() + 10000, failures: 0 });
  } catch (error) {
    if (task.state === "starting" && error instanceof ApiError && error.status === 409
      && !["recheck_conflict", "recovery_execution_conflict", "execution_pending"].includes(error.code)) {
      patch(task.id, { state: "excluded", reason: error.code }); return;
    }
    const failures = task.failures + 1;
    patch(task.id, { failures, reason: error instanceof ApiError ? error.code : "dependency_unavailable",
      retry_at: Date.now() + Math.min(60000, 1000 * 2 ** Math.min(failures, 6)) });
  }
}

async function dispatch() {
  for (;;) {
    const tasks = db.prepare(`SELECT * FROM task WHERE retry_at<=? AND (state IN ('starting','running') OR callback='pending')
      ORDER BY CASE WHEN state IN ('starting','running') THEN 0 ELSE 1 END, retry_at, id LIMIT 20`).all(Date.now());
    for (const task of tasks) if (task.state !== 'done' || busy.size < maxPolls) schedule(task);
    for (let index = 0; index < slots; index++) {
      const next = claim();
      if (!next) break;
      schedule(next);
    }
    await sleep(2000);
  }
}
function schedule(task) {
  if (busy.has(task.id)) return;
  busy.add(task.id);
  void advance(task).finally(() => busy.delete(task.id)).catch(() => {
    console.error("Cannot persist dispatcher progress"); process.exitCode = 1; process.kill(process.pid, "SIGTERM");
  });
}
function serve() {
  const port = Number(args.port);
  if (!Number.isSafeInteger(port) || port < 1 || port > 65535) throw new Error("Invalid port");
  createServer((request, response) => {
    const hosts = [`127.0.0.1:${port}`, `localhost:${port}`];
    if (request.method !== "GET" || !hosts.includes(request.headers.host) || request.url?.split("?")[0] !== "/") {
      response.writeHead(404).end(); return;
    }
    const page = Number(new URL(request.url, "http://localhost").searchParams.get("page") || "1");
    const safePage = Number.isSafeInteger(page) ? Math.max(1, Math.min(page, 100000)) : 1;
    const scans = db.prepare("SELECT * FROM scan ORDER BY id DESC").all();
    const counts = db.prepare("SELECT state, COUNT(*) count FROM task GROUP BY state").all();
    const rows = db.prepare("SELECT * FROM task ORDER BY updated DESC, id LIMIT 100 OFFSET ?").all((safePage - 1) * 100);
    response.writeHead(200, { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store",
      "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'" });
    response.end(render(scans, counts, rows, safePage));
  }).listen(port, "127.0.0.1", () => console.log(`Progress: http://127.0.0.1:${port}/`));
}

if (mode === "scan") await scan();
else if (mode === "view") serve();
else if (mode === "run") { serve(); await dispatch(); }
else throw new Error("Expected scan, run or view");
