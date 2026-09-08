export type StageSkillQuestion = {
  tag: string;
  format: "text" | "html";
  content: string;
};

export type StageSkillResult =
  | { kind: "waiting"; question: StageSkillQuestion }
  | { kind: "completed"; result: Record<string, unknown> };

const MAX_QUESTION_BYTES = 256 * 1024;

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

export function parseStageSkillResult(value: unknown): StageSkillResult {
  if (!isRecord(value) || typeof value.hitl !== "boolean") {
    throw new Error("Stage Skill Output 必须包含布尔值 hitl");
  }
  if (value.hitl === false) {
    if (!isRecord(value.result)) throw new Error("hitl=false 时必须返回 JSON 对象 result");
    return { kind: "completed", result: value.result };
  }
  if (!isRecord(value.question)) throw new Error("hitl=true 时必须返回 question");
  const tag = String(value.question.tag ?? "").trim();
  const format = String(value.question.format ?? "text");
  const content = String(value.question.content ?? "").trim();
  if (!/^[A-Za-z0-9._-]{1,128}$/.test(tag)) {
    throw new Error("question.tag 必须是 1 到 128 位字母、数字、点、下划线或横线");
  }
  if (format !== "text" && format !== "html") {
    throw new Error("question.format 只能是 text 或 html");
  }
  if (!content) throw new Error("question.content 不能为空");
  if (Buffer.byteLength(content, "utf8") > MAX_QUESTION_BYTES) {
    throw new Error("question.content 不能超过 256 KiB");
  }
  return { kind: "waiting", question: { tag, format, content } };
}
