import { createHash } from "node:crypto";
import JSZip from "jszip";
import {
  findOfficialStage,
  isStageExtensionMode,
  type StageExtensionMode,
  type StageKey,
} from "./stage-catalog.js";
import type { EvolutionFlowKey } from "./evolution-flow.js";
import { stageDevelopmentGuide } from "./stage-development-guide.js";

export const STAGE_SKILL_SCHEMA_VERSION = "clawevolve.stage-skill/v1";
const MAX_ARCHIVE_BYTES = 5 * 1024 * 1024;
const MAX_EXPANDED_BYTES = 20 * 1024 * 1024;
const MAX_FILES = 300;

export type StageSkillManifest = {
  schema_version: typeof STAGE_SKILL_SCHEMA_VERSION;
  display_name: string;
  stage: StageKey;
  mode: StageExtensionMode;
  entrypoint: string;
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
    || path.split("/").some((part) => part === "." || part === ".." || part === "");
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

const DEVELOPMENT_GUIDE_MARKER = "<!-- clawevolve:stage-development-instructions -->";

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
  if (entries.some((entry) => unsafePath(entry.name)
    || unsafePath((entry as JSZip.JSZipObject & { unsafeOriginalName?: string }).unsafeOriginalName ?? entry.name)
    || isSymlink(entry))) {
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
  const stage = findOfficialStage(expected.stage);
  let bindingError = stage && isStageExtensionMode(expected.mode) && stage.extensionModes.includes(expected.mode)
    ? "" : "平台选择的 Stage 或接入方式不存在";
  // Old archives remain readable, but their display identity and entrypoint are
  // not authoritative. Only retain the old wrong-slot upload check.
  const legacyBytes = contents.get("stage-skill.json");
  if (legacyBytes) {
    try {
      const legacy = JSON.parse(legacyBytes.toString("utf8"));
      if (legacy && typeof legacy === "object" && !Array.isArray(legacy)
        && ((legacy.stage !== undefined && legacy.stage !== expected.stage)
          || (legacy.mode !== undefined && legacy.mode !== expected.mode))) {
        bindingError = `开发结果必须对应 ${expected.stage} 的 ${expected.mode}`;
      }
    } catch { /* Legacy metadata is optional, not the package identity. */ }
  }
  const candidates = contents.has("SKILL.md")
    ? ["SKILL.md"]
    : [...contents.keys()].filter((name) => /^[^/]+\/SKILL\.md$/.test(name));
  const entrypoint = candidates.length === 1 ? candidates[0]! : "";
  const skill = contents.get(entrypoint)?.toString("utf8").trim() ?? "";
  const entrypointError = candidates.length > 1
    ? "ZIP 中有多个 Skill 入口，请只上传一个 Skill"
    : !skill
      ? "缺少根目录或唯一包装目录下非空的 SKILL.md"
      : skill.includes("TODO: 请让本地 Agent") || skill.includes(DEVELOPMENT_GUIDE_MARKER)
        || (skill.includes("## 完整背景") && skill.includes("## 当前要开发什么")
          && skill.includes("## 开发与交付步骤"))
        ? "SKILL.md 仍是未开发的说明或占位内容" : "";
  const manifest: StageSkillManifest | null = stage && !bindingError && entrypoint ? {
    schema_version: STAGE_SKILL_SCHEMA_VERSION,
    display_name: `${stage.name}${MODE_NAMES[expected.mode]}自定义实现`,
    stage: stage.stage,
    mode: expected.mode,
    entrypoint,
  } : null;
  const checks = [
    check("archive-safety", "压缩包安全", archiveError),
    check("stage-binding", "Stage 绑定", bindingError),
    check("entrypoint", "Skill 入口文件", entrypointError),
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
  flow?: EvolutionFlowKey;
}): Promise<Buffer> {
  const stage = findOfficialStage(input.stage);
  if (!stage || !stage.extensionModes.includes(input.mode)) throw new Error("Stage 或接入方式不存在");
  const zip = new JSZip();
  zip.file("SKILL.md", `${DEVELOPMENT_GUIDE_MARKER}\n\n${stageDevelopmentGuide(stage, input.mode, input.flow)}`);
  return zip.generateAsync({ type: "nodebuffer", compression: "DEFLATE", compressionOptions: { level: 6 } });
}
