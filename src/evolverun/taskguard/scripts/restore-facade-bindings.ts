/**
 * ── BACKUP / ONE-SHOT RECOVERY SCRIPT — KEEP IN REPO ──
 * Last used 2026-07-22 to recover from an accidental facade_bindings purge.
 *
 * Recovery (external /api/facades path, cookie-auth) — rebuild facade_bindings
 * from the workflow_specs in ClawWeb.
 *
 * What happened: a buggy orphan-cleanup in handleDeploy issued DELETE for nearly
 * every row, emptying facade_bindings to a handful. workflow_specs was untouched
 * (187 workflows still present). This script rebuilds the command→workflow bindings
 * by, for each workflow, fetching its stored spec and using `spec.facade.command`
 * (falling back to the workflowId when the spec has no facade field), then POSTing
 * a new facade_bindings row — exactly as the user specified.
 *
 * Run (write a valid ClawWeb session cookie to /tmp/clawweb-cookie.txt first):
 *   node --import tsx scripts/restore-facade-bindings.ts --dry-run   # compute only
 *   node --import tsx scripts/restore-facade-bindings.ts             # write missing
 *
 * Safety:
 *   - Only POSTs MISSING bindings. Never deletes. Never overwrites an existing
 *     command bound to a *different* workflow (reports conflict instead).
 *   - Skips workflowIds whose name fails the command naming rule (kebab/snake),
 *     listing them for manual handling.
 *   - Idempotent: re-running only fills gaps.
 */
import { readFileSync } from "node:fs";

const BASE = "https://clawweb.antgroup-inc.cn";
const COOKIE = readFileSync("/tmp/clawweb-cookie.txt", "utf-8").trim();
const COMMAND_PATTERN = /^[a-z0-9][a-z0-9_-]*[a-z0-9]$|^[a-z0-9]$/;
const dryRun = process.argv.slice(2).includes("--dry-run");
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function get(path: string): Promise<any> {
  const r = await fetch(`${BASE}${path}`, { headers: { Cookie: COOKIE } });
  const txt = await r.text();
  if (!r.ok || txt.startsWith("<") || txt.includes("USER_NOT_LOGIN")) {
    throw new Error(`GET ${path} -> ${r.status} ${txt.slice(0, 120)}`);
  }
  return JSON.parse(txt);
}

async function postFacade(body: Record<string, unknown>): Promise<{ status: number; body: any }> {
  const r = await fetch(`${BASE}/api/facades`, {
    method: "POST",
    headers: { Cookie: COOKIE, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const txt = await r.text();
  let parsed: any = txt;
  try { parsed = JSON.parse(txt); } catch { /* keep text */ }
  return { status: r.status, body: parsed };
}

async function main(): Promise<void> {
  console.log(`[restore] base=${BASE} dry-run=${dryRun}`);

  // 1. Current facade_bindings (external read surface of the same table).
  const current: any[] = await get("/api/facades");
  const currentByCommand = new Map<string, string>();
  for (const row of current) currentByCommand.set(String(row.command), String(row.workflowId));
  console.log(`[restore] 当前 facade_bindings 表有 ${current.length} 行`);

  // 2. All workflows.
  const list: any[] = await get("/api/workflows?pageSize=10000");
  console.log(`[restore] spec 表有 ${list.length} 个 workflow`);

  // 3. For each, fetch detail → facade.command or workflowId.
  type Desired = { workflowId: string; command: string; packId: string | null };
  const desired: Desired[] = [];
  const skippedInvalid: string[] = [];
  const fetchErrors: string[] = [];
  for (let i = 0; i < list.length; i++) {
    const wfId = list[i].workflowId ?? list[i].id;
    if (!wfId) continue;
    let spec: any;
    try {
      spec = await get(`/api/workflows/${encodeURIComponent(wfId)}`);
    } catch (err) {
      fetchErrors.push(`${wfId}: ${(err as Error).message}`);
      continue;
    }
    const facadeCmd = spec?.facade?.command;
    let command = typeof facadeCmd === "string" && facadeCmd.trim() ? facadeCmd.trim().toLowerCase() : String(wfId).toLowerCase();
    if (!COMMAND_PATTERN.test(command)) {
      skippedInvalid.push(wfId);
      continue;
    }
    desired.push({ workflowId: String(wfId), command, packId: spec?.packId ?? null });
    if ((i + 1) % 25 === 0) console.log(`  ...已取 ${i + 1}/${list.length} 个 spec`);
  }
  console.log(`[restore] 应恢复(desired): ${desired.length} | 非法command跳过: ${skippedInvalid.length} | 取spec失败: ${fetchErrors.length}`);

  // 4. Diff against current: what needs POST, what conflicts, what's already ok.
  const toCreate: Desired[] = [];
  const conflicts: { command: string; currentWf: string; desiredWf: string }[] = [];
  const alreadyOk: Desired[] = [];
  for (const d of desired) {
    const cur = currentByCommand.get(d.command);
    if (cur === undefined) toCreate.push(d);
    else if (cur === d.workflowId) alreadyOk.push(d);
    else conflicts.push({ command: d.command, currentWf: cur, desiredWf: d.workflowId });
  }
  console.log(`[restore] 已存在且一致: ${alreadyOk.length} | 待新建(POST): ${toCreate.length} | 冲突(被别的workflow占用): ${conflicts.length}`);
  if (skippedInvalid.length) console.log(`[restore] 非法command(需手动处理): ${skippedInvalid.join(", ")}`);
  if (conflicts.length) {
    console.log(`[restore] 冲突明细:`);
    for (const c of conflicts) console.log(`   command="${c.command}" 表中绑=${c.currentWf} 期望=${c.desiredWf}`);
  }

  if (dryRun) {
    console.log(`\n[dry-run] 将 POST 新建 ${toCreate.length} 条。样例前 15:`);
    for (const d of toCreate.slice(0, 15)) console.log(`   POST command="${d.command}" -> workflow="${d.workflowId}" (pack=${d.packId})`);
    console.log(`\n[dry-run] 未写入。去掉 --dry-run 执行。`);
    return;
  }

  // 5. Write missing bindings (POST). Skip conflicts. Never delete.
  let created = 0, failed = 0;
  for (const d of toCreate) {
    const res = await postFacade({ command: d.command, workflowId: d.workflowId, packId: d.packId ?? d.workflowId, remark: null });
    if (res.status === 201) { created++; }
    else if (res.status === 409) { console.log(`   跳过(已存在) ${d.command}`); }
    else { failed++; console.warn(`   ⚠️ POST ${d.command} -> ${res.status} ${JSON.stringify(res.body).slice(0, 160)}`); }
    await sleep(80); // be gentle
  }
  console.log(`\n✅ 新建 ${created} 条, 失败 ${failed} 条。`);

  // 6. Verify.
  const after: any[] = await get("/api/facades");
  console.log(`[restore] 验证: 表现有 ${after.length} 行 (之前 ${current.length})。`);
}

main().catch((err) => { console.error("❌", err); process.exit(1); });