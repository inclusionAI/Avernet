import { createHash } from "node:crypto";
import JSZip from "jszip";
import {
  findOfficialStage,
  isStageExtensionMode,
  officialEvolveCatalog,
  stageRuntimeInputSchema,
  type StageExtensionMode,
  type StageKey,
} from "./stage-catalog.js";

export const STAGE_SKILL_SCHEMA_VERSION = "clawevolve.stage-skill/v1";
const MAX_ARCHIVE_BYTES = 5 * 1024 * 1024;
const MAX_EXPANDED_BYTES = 20 * 1024 * 1024;
const MAX_FILES = 300;

export type StageSkillManifest = {
  schema_version: typeof STAGE_SKILL_SCHEMA_VERSION;
  stage: StageKey;
  mode: StageExtensionMode;
  entrypoint: "implementation/SKILL.md";
};

export type StagePackageCheck = {
  id: "archive-safety" | "manifest" | "stage-binding" | "entrypoint";
  label: string;
  status: "passed" | "failed";
  message: string;
};

export type StageSkillPackageInspection = {
  status: "passed" | "failed";
  packageSha256: string;
  manifest: StageSkillManifest | null;
  checks: StagePackageCheck[];
};

function check(id: StagePackageCheck["id"], label: string, error = ""): StagePackageCheck {
  return error
    ? { id, label, status: "failed", message: error }
    : { id, label, status: "passed", message: "通过" };
}

function unsafePath(path: string): boolean {
  return path.startsWith("/") || path.includes("\\")
    || path.split("/").some((part) => part === ".." || part === "");
}

function isSymlink(entry: JSZip.JSZipObject): boolean {
  const permission = typeof entry.unixPermissions === "string"
    ? Number.parseInt(entry.unixPermissions, 8)
    : entry.unixPermissions;
  return typeof permission === "number" && (permission & 0o170000) === 0o120000;
}

function declaredUncompressedSize(entry: JSZip.JSZipObject): number | null {
  const value = (entry as unknown as { _data?: { uncompressedSize?: unknown } })._data?.uncompressedSize;
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : null;
}

function parseManifest(value: unknown): StageSkillManifest | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const row = value as Record<string, unknown>;
  if (Object.keys(row).some((key) => !new Set([
    "schema_version", "stage", "mode", "entrypoint",
  ]).has(key))) return null;
  const stage = findOfficialStage(String(row.stage ?? ""));
  if (row.schema_version !== STAGE_SKILL_SCHEMA_VERSION || !stage
    || !isStageExtensionMode(row.mode)
    || !stage.extensionModes.includes(row.mode)
    || row.entrypoint !== "implementation/SKILL.md") return null;
  return row as StageSkillManifest;
}

export async function inspectStageSkillPackage(
  archive: Buffer,
  expected: { stage: StageKey; mode: StageExtensionMode },
): Promise<StageSkillPackageInspection> {
  const packageSha256 = createHash("sha256").update(archive).digest("hex");
  let archiveError = archive.byteLength > MAX_ARCHIVE_BYTES
    ? `ZIP 不能超过 ${MAX_ARCHIVE_BYTES / 1024 / 1024} MB`
    : "";
  let zip: JSZip;
  try {
    zip = await JSZip.loadAsync(archive);
  } catch {
    return {
      status: "failed", packageSha256, manifest: null,
      checks: [check("archive-safety", "压缩包安全", archiveError || "文件不是有效 ZIP")],
    };
  }
  const entries = Object.values(zip.files).filter((entry) => !entry.dir
    && !entry.name.startsWith("__MACOSX/"));
  if (!entries.length) archiveError ||= "ZIP 中没有文件";
  if (entries.length > MAX_FILES) archiveError ||= `文件数不能超过 ${MAX_FILES}`;
  if (entries.some((entry) => unsafePath(entry.name) || isSymlink(entry))) {
    archiveError ||= "ZIP 包含越界路径、反斜杠路径或软链";
  }
  const declaredExpandedBytes = entries.reduce((sum, entry) =>
    sum + (declaredUncompressedSize(entry) ?? 0), 0);
  if (declaredExpandedBytes > MAX_EXPANDED_BYTES) {
    archiveError ||= `解压后不能超过 ${MAX_EXPANDED_BYTES / 1024 / 1024} MB`;
  }
  if (archiveError) {
    return {
      status: "failed", packageSha256, manifest: null,
      checks: [check("archive-safety", "压缩包安全", archiveError)],
    };
  }
  const contents = new Map<string, Buffer>();
  let expandedBytes = 0;
  for (const entry of entries) {
    const content = await entry.async("nodebuffer");
    expandedBytes += content.byteLength;
    contents.set(entry.name, content);
  }
  if (expandedBytes > MAX_EXPANDED_BYTES) archiveError = `解压后不能超过 ${MAX_EXPANDED_BYTES / 1024 / 1024} MB`;
  let manifest: StageSkillManifest | null = null;
  let manifestError = "";
  const manifestBytes = contents.get("stage-skill.json");
  if (!manifestBytes) manifestError = "根目录缺少 stage-skill.json";
  else {
    try { manifest = parseManifest(JSON.parse(manifestBytes.toString("utf8"))); }
    catch { manifest = null; }
    if (!manifest) manifestError = "stage-skill.json 不符合平台协议";
  }
  const bindingError = manifest?.stage === expected.stage && manifest.mode === expected.mode
    ? "" : `开发结果必须对应 ${expected.stage} 的 ${expected.mode}`;
  const entrypoint = contents.get("implementation/SKILL.md")?.toString("utf8").trim() ?? "";
  const entrypointError = !entrypoint
    ? "implementation/SKILL.md 不存在或为空"
    : entrypoint.includes("TODO: 请让本地 Agent")
      ? "implementation/SKILL.md 仍是未开发的占位内容" : "";
  const checks = [
    check("archive-safety", "压缩包安全", archiveError),
    check("manifest", "Stage Skill 声明", manifestError),
    check("stage-binding", "Stage 与接入方式", bindingError),
    check("entrypoint", "Skill 内容", entrypointError),
  ];
  return {
    status: checks.some((item) => item.status === "failed") ? "failed" : "passed",
    packageSha256, manifest, checks,
  };
}

const MODE_NAMES: Record<StageExtensionMode, string> = {
  preprocess: "预处理", postprocess: "后处理", replace: "整体替换",
};

export async function createStageDevelopmentPackage(input: {
  stage: StageKey;
  mode: StageExtensionMode;
}): Promise<Buffer> {
  const stage = findOfficialStage(input.stage);
  if (!stage || !stage.extensionModes.includes(input.mode)) throw new Error("Stage 或接入方式不存在");
  const manifest: StageSkillManifest = {
    schema_version: STAGE_SKILL_SCHEMA_VERSION,
    stage: stage.stage,
    mode: input.mode,
    entrypoint: "implementation/SKILL.md",
  };
  const resultRule = input.mode === "preprocess"
    ? "完成时 result 返回 summary；如修改目标 Skill，直接修改平台提供的本次任务候选目录。"
    : `完成时 result 必须符合 contract.json 中 ${stage.name} 的最终结果结构。`;
  const runtimeInput = stageRuntimeInputSchema(stage, input.mode);
  const guide = [
    `# ${stage.name} · ${MODE_NAMES[input.mode]} Skill 开发任务`, "",
    "## 完整背景", "",
    `当前模板是“${officialEvolveCatalog.template.name}”：${officialEvolveCatalog.template.description}`,
    `模板顺序：${officialEvolveCatalog.template.steps.map((item) => item.name).join(" → ")}。`, "",
    "## 当前要开发什么", "", `${stage.name}的职责：${stage.description}`,
    `本实现接入在“${MODE_NAMES[input.mode]}”位置。`,
    input.mode === "preprocess"
      ? `它在平台内置${stage.name}开始前执行，可检查输入并准备或补齐候选资源；完成后平台继续运行内置${stage.name}。`
      : input.mode === "postprocess"
        ? `它在平台内置${stage.name}完成后执行，可复核或整理结果；它交付的结果将作为${stage.name}最终结果。`
        : `它代替平台内置${stage.name}完成这一整步；它交付的结果将直接进入模板下一步。`,
    "", "平台负责准备任务信息、上游结果和资源目录，负责暂停与恢复用户交互，并在完成后校验结果。",
    `你只需要实现当前${stage.name}开放在${MODE_NAMES[input.mode]}位置的处理逻辑，不要自行创建下一 Stage 或下一轮。`,
    "", "## 输入与交付", "",
    "运行时输入以 contract.json 为准。Skill 从平台给出的 input.json 读取；路径字段指向本次任务隔离目录中的真实文件。",
    resultRule, "", "统一交互方式：",
    "- 可以继续完成：输出 `{\"hitl\": false, \"result\": {...}}`。",
    "- 需要用户补充：输出 `{\"hitl\": true, \"question\": {\"tag\": \"...\", \"format\": \"text\", \"content\": \"...\"}}`。",
    "- 需要动态表单时，将 format 改为 html，并把完整 HTML 放入 content；平台会在隔离的 iframe 中展示。",
    "- 平台展示问题、收集回答后，会把 human_input 加入输入并再次执行同一个 Stage；文本回答放在 human_input.content，HTML 表单字段放在 human_input.fields，二者都带原 question.tag；Skill 不要挂起进程等待。",
    "", "## 开发与交付步骤", "",
    "1. 完整阅读 AGENT_TASK.md、contract.json 和 stage-skill.json。",
    "2. 在 implementation/SKILL.md 直接编写本 Stage 的 Skill；可以在 implementation/ 下增加脚本和说明文件。",
    "3. 不要修改 stage-skill.json 中的平台绑定信息。",
    "4. 将本目录重新压缩为 ZIP，回到平台上传。平台先做确定性的结构与安全校验，再可选运行真实 Agent 集成测试。", "",
  ].join("\n");
  const contract = {
    schema_version: "clawevolve.stage-contract/v1",
    template: officialEvolveCatalog.template,
    stage: {
      name: stage.name, description: stage.description, mode: input.mode,
      input: runtimeInput,
      result: input.mode === "preprocess"
        ? { type: "object", required: ["summary"], properties: {
          summary: { type: "string", description: "本次预处理完成了什么" },
        } } : stage.resultSchema,
    },
    hitl: {
      waiting: { hitl: true, question: { tag: "string", format: "text | html", content: "string" } },
      completed: { hitl: false, result: "按 stage.result" },
    },
  };
  const zip = new JSZip();
  zip.file("AGENT_TASK.md", guide);
  zip.file("contract.json", JSON.stringify(contract, null, 2));
  zip.file("stage-skill.json", JSON.stringify(manifest, null, 2));
  zip.folder("implementation")!.file("SKILL.md",
    `# ${stage.name} ${MODE_NAMES[input.mode]}\n\nTODO: 请让本地 Agent 按 AGENT_TASK.md 完成这里的 Skill。\n`);
  return zip.generateAsync({ type: "nodebuffer", compression: "DEFLATE", compressionOptions: { level: 6 } });
}
