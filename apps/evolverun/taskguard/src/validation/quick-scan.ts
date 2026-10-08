import yaml from "js-yaml";
import * as fsSync from "node:fs";
import path from "node:path";
import { isSaveAsCapableExecutor } from "../legacy-runtime.js";
import { validateSaveAsTarget } from "../actions/template.js";

// 与 src/validation/workflow.ts:2186 同集(彼处未 export,此处重声明避免跨模块耦合)。
const EXTERNAL_OUTPUT_EXECUTORS = new Set([
  "mcp-call", "cli-script", "baas-call", "action", "subworkflow",
]);

// 与 src/validation/workflow.ts:1407 outputContractSchemaTypes 同集(彼处未 export)。
// 引擎 normalizeOutputContractSchema 行 1413-1415:typeof type !== "string" → 数组/联合类型
// 直接 fail("must be one of object, array, string, number, boolean")。load 期 fail-fast 一次一个,
// 多节点同类错会叠成多次 deploy。这里只读静态扫,把所有节点的数组/非法 type 一次列全(D2 减 round-trip)。
const OUTPUT_CONTRACT_SCHEMA_TYPES = new Set(["object", "array", "string", "number", "boolean"]);

// 递归走 outputContract.schema,对每个 schema 子对象的 type 做只读判定。
// 自包含:只看 schema 自身;单项 try/catch,坏子树跳过续走,不污染同节点其它项。
function scanSchemaTypes(raw: unknown, id: string, add: (severity: "warning", message: string) => void): void {
  if (raw == null || typeof raw !== "object") return;
  const schema = raw as Record<string, unknown>;
  const type = schema.type;
  if (Array.isArray(type)) {
    add("warning", `outputContract.schema.type 用了数组/联合类型 [${type.map(String).join(", ")}]——引擎只接受单一类型(object/array/string/number/boolean)。改成单一类型;若要表达可空用 nullable: true。`);
  } else if (typeof type === "string" && !OUTPUT_CONTRACT_SCHEMA_TYPES.has(type)) {
    add("warning", `outputContract.schema.type 值 "${type}" 非合法类型——引擎只接受 object/array/string/number/boolean。`);
  }
  // 下钻 properties / items(与 workflow.ts:1435-1444 递归结构对齐)
  try {
    const props = schema.properties;
    if (props != null && typeof props === "object") {
      for (const v of Object.values(props)) scanSchemaTypes(v, id, add);
    }
  } catch { /* 自包含,忽略 */ }
  try {
    const items = schema.items;
    if (items !== undefined) scanSchemaTypes(items, id, add);
  } catch { /* 忽略 */ }
}

export interface ScanFinding {
  node: string;
  phase?: string;
  severity: "warning" | "error";
  message: string;
}

export interface ScanResult {
  ok: boolean;
  parseError?: string;
  findings: ScanFinding[];
}

/** 每项规则只看该节点自身字段,自包含;单项 try/catch,不污染同节点其它项。 */
function scanNode(raw: any, idSet: Set<string>): ScanFinding[] {
  const out: ScanFinding[] = [];
  const id: string = raw?.id ?? "<unknown>";
  const phase: string | undefined = typeof raw?.phase === "string" ? raw.phase : undefined;
  const add = (severity: "warning" | "error", message: string) =>
    out.push({ node: id, phase, severity, message });

  // 局部规则 1:outputContract-on-external
  try {
    const etype = raw?.executor?.type;
    if (typeof etype === "string" && EXTERNAL_OUTPUT_EXECUTORS.has(etype) && raw?.outputContract != null) {
      add("warning", "本类 executor(mcp-call/cli-script/baas-call/action/subworkflow)不应写 outputContract——引擎无法静态校验其类型,易与实际返回不符。建议删 outputContract,下游用 {{nodeOutput." + id + ".<field>}}。");
    }
  } catch { /* 自包含,忽略 */ }

  // 局部规则 2:dead saveAs(节点级 saveAs 挂非 capable executor)
  try {
    const saveAs = raw?.saveAs;
    if (saveAs && typeof saveAs === "object" && !isSaveAsCapableExecutor(raw as any)) {
      add("warning", "节点级 saveAs 在此 executor 不执行(saveAs 仅 human/async-callback/approval 生效)。");
    }
  } catch { /* 忽略 */ }

  // 局部规则 3:saveAs target 语法(非 workflowData. 开头 → error)
  try {
    const saveAs = raw?.saveAs;
    if (saveAs && typeof saveAs === "object") {
      for (const target of Object.keys(saveAs)) {
        try {
          validateSaveAsTarget(target); // 合法返回 void,非法 throw
        } catch {
          add("error", `saveAs target "${target}" 须 workflowData. 开头。`);
        }
      }
    }
  } catch { /* 忽略 */ }

  // 局部规则 4:outputContract.schema.type 数组/非法值(递归 properties/items)
  // 治报告 activity-legalrisk-review-20260728 的 type: [string, number] 多次 deploy round-trip。
  try {
    const schema = raw?.outputContract?.schema;
    if (schema != null) scanSchemaTypes(schema, id, add);
  } catch { /* 自包含,忽略 */ }

  // 缺依赖(全局 idSet 提供,但归为该节点局部 finding)
  try {
    const deps: unknown = raw?.dependsOn;
    if (Array.isArray(deps)) {
      for (const dep of deps) {
        if (typeof dep === "string" && !idSet.has(dep)) {
          add("warning", `依赖 "${dep}" 不存在(不在节点 id 集合中)。`);
        }
      }
    }
  } catch { /* 忽略 */ }

  return out;
}

/**
 * 只读、容错的全 workflow 扫描。节点级 try/catch:结构坏节点只报首错、跳过续扫、
 * continue 下一节点——零级联误报(不因 A 坏推断 B 坏)。对 raw YAML 跑,不依赖 normalize。
 */
export function quickScanSpecFile(absPath: string): ScanResult {
  if (!fsSync.existsSync(absPath)) {
    return { ok: false, parseError: `文件不存在: ${absPath}`, findings: [] };
  }
  let raw: unknown;
  try {
    raw = yaml.load(fsSync.readFileSync(absPath, "utf-8"));
  } catch (err) {
    return { ok: false, parseError: `YAML 解析失败(${absPath}): ${err instanceof Error ? err.message : err}`, findings: [] };
  }
  if (typeof raw !== "object" || raw === null) {
    return { ok: false, parseError: "YAML 顶层非 object", findings: [] };
  }
  const top = raw as Record<string, unknown>;
  const nodes = top.nodes;
  if (!Array.isArray(nodes)) {
    return { ok: false, parseError: "无 nodes 数组", findings: [] };
  }

  const findings: ScanFinding[] = [];

  // 先建 id 集(容错)
  const idSet = new Set<string>();
  const idCount = new Map<string, number>();
  for (const n of nodes) {
    try {
      const id = (n as any)?.id;
      if (typeof id === "string") {
        idSet.add(id);
        idCount.set(id, (idCount.get(id) ?? 0) + 1);
      }
    } catch { /* 忽略 */ }
  }

  // 重复 id(全局规则,独立收集)
  for (const [id, cnt] of idCount) {
    if (cnt > 1) {
      findings.push({ node: id, severity: "warning", message: `重复 id: "${id}" 出现 ${cnt} 次。` });
    }
  }

  // 节点级扫描:每节点 try/catch,坏节点跳过续扫
  for (const n of nodes) {
    let id = "<unknown>";
    try {
      id = (n as any)?.id ?? "<unknown>";
      // 结构坏(executor 非对象等)→ 只报首错,跳过深检,continue
      const exec = (n as any)?.executor;
      if (exec != null && typeof exec !== "object") {
        findings.push({ node: id, phase: (n as any)?.phase, severity: "error", message: "节点 executor 结构异常(非 object),跳过该节点深检。" });
        continue;
      }
      if (n == null || typeof n !== "object") {
        findings.push({ node: id, severity: "error", message: "节点结构异常,跳过深检。" });
        continue;
      }
      findings.push(...scanNode(n, idSet));
    } catch {
      // 单节点扫描意外错误:只记一条,不污染其它节点
      findings.push({ node: id, severity: "warning", message: "节点扫描时异常,跳过。" });
    }
  }

  return { ok: true, findings };
}