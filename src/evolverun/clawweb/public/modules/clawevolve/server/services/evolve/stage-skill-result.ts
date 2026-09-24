export type StageSkillLegacyQuestion = {
  tag: string;
  format: "text" | "html";
  content: string;
};

export type StageSkillFormOption = {
  value: string;
  label: string;
  description?: string;
  recommended?: boolean;
};

export type StageSkillFormItem = {
  id: string;
  type: "single_choice" | "multiple_choice" | "short_text" | "long_text";
  title: string;
  description?: string;
  required: boolean;
  options?: StageSkillFormOption[];
  placeholder?: string;
  visibleWhen?: { questionId: string; operator: "equals" | "includes"; value: string };
  validation?: { minSelections?: number; maxSelections?: number; minLength?: number; maxLength?: number };
};

export type StageSkillFormContent = {
  id: string;
  title: string;
  format: "markdown";
  content: string;
};

export type StageSkillFormQuestion = {
  format: "form";
  title: string;
  description?: string;
  contents?: StageSkillFormContent[];
  questions: StageSkillFormItem[];
};

export type StageSkillQuestion = StageSkillLegacyQuestion | StageSkillFormQuestion;

export type StageLoopRequest = {
  action: "request_feedback";
  prompt: string;
  accepts: { text: boolean; files: string[] };
};

export type StageLoopFeedbackQuestion = StageLoopRequest & { kind: "loop_feedback" };

export type StageLoopFeedbackFile = {
  name: string;
  content_type: string;
  size: number;
  sha256: string;
  ref: string;
};

export type StageLoopFeedbackAnswer =
  | { action: "accept" }
  | { action: "continue"; feedback: { text?: string; files: StageLoopFeedbackFile[] } };

export type StageSkillResult =
  | { kind: "waiting"; question: StageSkillQuestion }
  | { kind: "completed"; result: Record<string, unknown>; loopRequest?: StageLoopRequest };

export type StageSkillFormAnswer = {
  answers: Record<string, { value: string | string[]; comment?: string }>;
};

const MAX_QUESTION_BYTES = 256 * 1024;
const MAX_FORM_ITEMS = 100;
const MAX_ANSWER_LENGTH = 4000;
const ID_PATTERN = /^[A-Za-z0-9._-]{1,128}$/;
const FILE_EXTENSION_PATTERN = /^\.[A-Za-z0-9]{1,10}$/;

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function optionalText(value: unknown, field: string, maxLength = MAX_ANSWER_LENGTH): string | undefined {
  if (value == null) return undefined;
  if (typeof value !== "string") throw new Error(`${field} 必须是字符串`);
  const text = value.trim();
  if (!text) return undefined;
  if (text.length > maxLength) throw new Error(`${field} 不能超过 ${maxLength} 个字符`);
  return text;
}

function requiredText(value: unknown, field: string, maxLength = 500): string {
  const text = optionalText(value, field, maxLength);
  if (!text) throw new Error(`${field} 不能为空`);
  return text;
}

function optionalInteger(value: unknown, field: string): number | undefined {
  if (value == null) return undefined;
  if (!Number.isInteger(value) || Number(value) < 0) throw new Error(`${field} 必须是非负整数`);
  return Number(value);
}

function parseFormQuestion(raw: Record<string, unknown>): StageSkillFormQuestion {
  const title = requiredText(raw.title, "question.title");
  const description = optionalText(raw.description, "question.description");
  if (raw.contents != null && (!Array.isArray(raw.contents) || raw.contents.length > MAX_FORM_ITEMS)) {
    throw new Error(`question.contents 必须是且最多包含 ${MAX_FORM_ITEMS} 项只读内容`);
  }
  const contentIds = new Set<string>();
  const contents = (raw.contents ?? []).map((value, index): StageSkillFormContent => {
    const field = `question.contents[${index}]`;
    if (!isRecord(value)) throw new Error(`${field} 必须是对象`);
    const id = String(value.id ?? "").trim();
    if (!ID_PATTERN.test(id) || contentIds.has(id)) {
      throw new Error("question.contents 中的 id 必须合法且不能重复");
    }
    contentIds.add(id);
    if (value.format !== "markdown") throw new Error(`${field}.format 只支持 markdown`);
    return {
      id,
      title: requiredText(value.title, `${field}.title`),
      format: "markdown",
      content: requiredText(value.content, `${field}.content`, MAX_QUESTION_BYTES),
    };
  });
  if (!Array.isArray(raw.questions) || raw.questions.length > MAX_FORM_ITEMS) {
    throw new Error(`question.questions 必须是且最多包含 ${MAX_FORM_ITEMS} 个问题`);
  }
  if (contents.length === 0 && raw.questions.length === 0) {
    throw new Error("question.contents 和 question.questions 至少需要一项内容");
  }
  const ids = new Set<string>();
  const questions = raw.questions.map((value, index): StageSkillFormItem => {
    const field = `question.questions[${index}]`;
    if (!isRecord(value)) throw new Error(`${field} 必须是对象`);
    const id = String(value.id ?? "").trim();
    if (!ID_PATTERN.test(id) || ids.has(id)) throw new Error("question.questions 中的 id 必须合法且不能重复");
    ids.add(id);
    const type = String(value.type ?? "");
    if (!["single_choice", "multiple_choice", "short_text", "long_text"].includes(type)) {
      throw new Error(`${field}.type 不受支持`);
    }
    const choice = type === "single_choice" || type === "multiple_choice";
    let options: StageSkillFormOption[] | undefined;
    if (choice) {
      if (!Array.isArray(value.options) || value.options.length < 1 || value.options.length > 50) {
        throw new Error(`${field}.options 必须包含 1 到 50 个选项`);
      }
      const optionValues = new Set<string>();
      options = value.options.map((option, optionIndex) => {
        if (!isRecord(option)) throw new Error(`${field}.options[${optionIndex}] 必须是对象`);
        const optionValue = requiredText(option.value, `${field}.options[${optionIndex}].value`, 128);
        if (optionValue === "__other__" || optionValues.has(optionValue)) {
          throw new Error(`${field}.options 的 value 不能重复或使用平台保留值 __other__`);
        }
        optionValues.add(optionValue);
        const optionDescription = optionalText(option.description, `${field}.options[${optionIndex}].description`);
        return {
          value: optionValue,
          label: requiredText(option.label, `${field}.options[${optionIndex}].label`),
          ...(optionDescription ? { description: optionDescription } : {}),
          ...(option.recommended === true ? { recommended: true } : {}),
        };
      });
    } else if (value.options != null) {
      throw new Error(`${field}.options 只适用于选择题`);
    }
    let visibleWhen: StageSkillFormItem["visibleWhen"];
    if (value.visibleWhen != null) {
      if (!isRecord(value.visibleWhen)) throw new Error(`${field}.visibleWhen 必须是对象`);
      const questionId = String(value.visibleWhen.questionId ?? "").trim();
      const operator = String(value.visibleWhen.operator ?? "");
      if (!ID_PATTERN.test(questionId) || !["equals", "includes"].includes(operator)) {
        throw new Error(`${field}.visibleWhen 无效`);
      }
      visibleWhen = { questionId, operator: operator as "equals" | "includes", value: String(value.visibleWhen.value ?? "") };
    }
    let validation: StageSkillFormItem["validation"];
    if (value.validation != null) {
      if (!isRecord(value.validation)) throw new Error(`${field}.validation 必须是对象`);
      const minSelections = optionalInteger(value.validation.minSelections, `${field}.validation.minSelections`);
      const maxSelections = optionalInteger(value.validation.maxSelections, `${field}.validation.maxSelections`);
      const minLength = optionalInteger(value.validation.minLength, `${field}.validation.minLength`);
      const maxLength = optionalInteger(value.validation.maxLength, `${field}.validation.maxLength`);
      validation = {
        ...(minSelections != null ? { minSelections } : {}), ...(maxSelections != null ? { maxSelections } : {}),
        ...(minLength != null ? { minLength } : {}), ...(maxLength != null ? { maxLength } : {}),
      };
      if (minSelections != null && maxSelections != null && minSelections > maxSelections) {
        throw new Error(`${field}.validation 的最小值不能大于最大值`);
      }
      if (minLength != null && maxLength != null && minLength > maxLength) {
        throw new Error(`${field}.validation 的最小值不能大于最大值`);
      }
    }
    const itemDescription = optionalText(value.description, `${field}.description`);
    const placeholder = optionalText(value.placeholder, `${field}.placeholder`, 500);
    return {
      id,
      type: type as StageSkillFormItem["type"],
      title: requiredText(value.title, `${field}.title`),
      ...(itemDescription ? { description: itemDescription } : {}),
      required: value.required === true,
      ...(options ? { options } : {}), ...(placeholder ? { placeholder } : {}),
      ...(visibleWhen ? { visibleWhen } : {}),
      ...(validation && Object.keys(validation).length ? { validation } : {}),
    };
  });
  for (const item of questions) {
    if (item.visibleWhen && (!ids.has(item.visibleWhen.questionId) || item.visibleWhen.questionId === item.id)) {
      throw new Error(`question.questions 中 ${item.id} 的 visibleWhen 引用了无效问题`);
    }
  }
  const parsed = {
    format: "form" as const,
    title,
    ...(description ? { description } : {}),
    ...(contents.length ? { contents } : {}),
    questions,
  };
  if (Buffer.byteLength(JSON.stringify(parsed), "utf8") > MAX_QUESTION_BYTES) throw new Error("question 不能超过 256 KiB");
  return parsed;
}

export function parseStageSkillResult(value: unknown): StageSkillResult {
  if (!isRecord(value) || typeof value.hitl !== "boolean") throw new Error("Stage Skill Output 必须包含布尔值 hitl");
  if (value.hitl === false) {
    if (!isRecord(value.result)) throw new Error("hitl=false 时必须返回 JSON 对象 result");
    if (value.loop == null) return { kind: "completed", result: value.result };
    if (!isRecord(value.loop) || value.loop.action !== "request_feedback") {
      throw new Error("loop.action 只能是 request_feedback");
    }
    const prompt = requiredText(value.loop.prompt, "loop.prompt", 4000);
    if (!isRecord(value.loop.accepts) || typeof value.loop.accepts.text !== "boolean"
      || !Array.isArray(value.loop.accepts.files)) {
      throw new Error("loop.accepts 必须声明 text 和 files");
    }
    const files = [...new Set(value.loop.accepts.files.map((item) => String(item).toLowerCase()))];
    if (files.length > 20 || files.some((item) => !FILE_EXTENSION_PATTERN.test(item))) {
      throw new Error("loop.accepts.files 必须是合法文件扩展名且不超过 20 项");
    }
    if (!value.loop.accepts.text && files.length === 0) {
      throw new Error("loop.accepts 至少允许文本或一种文件");
    }
    return {
      kind: "completed",
      result: value.result,
      loopRequest: {
        action: "request_feedback",
        prompt,
        accepts: { text: value.loop.accepts.text, files },
      },
    };
  }
  if (!isRecord(value.question)) throw new Error("hitl=true 时必须返回 question");
  const format = String(value.question.format ?? "text");
  if (format === "form") return { kind: "waiting", question: parseFormQuestion(value.question) };
  const tag = String(value.question.tag ?? "").trim();
  const content = String(value.question.content ?? "").trim();
  if (!ID_PATTERN.test(tag)) throw new Error("question.tag 必须是 1 到 128 位字母、数字、点、下划线或横线");
  if (format !== "text" && format !== "html") throw new Error("question.format 只能是 text、html 或 form");
  if (!content) throw new Error("question.content 不能为空");
  if (Buffer.byteLength(content, "utf8") > MAX_QUESTION_BYTES) throw new Error("question.content 不能超过 256 KiB");
  return { kind: "waiting", question: { tag, format, content } };
}

function visible(item: StageSkillFormItem, answers: Record<string, { value: string | string[] }>): boolean {
  if (!item.visibleWhen) return true;
  const controlling = answers[item.visibleWhen.questionId]?.value;
  return item.visibleWhen.operator === "equals"
    ? controlling === item.visibleWhen.value
    : Array.isArray(controlling) && controlling.includes(item.visibleWhen.value);
}

export function validateStageInteractionAnswer(question: StageSkillQuestion, answer: unknown): unknown {
  if (question.format !== "form") return answer;
  if (!isRecord(answer) || !isRecord(answer.answers)) throw new Error("请提交结构化表单答案 answers");
  const unknownIds = Object.keys(answer.answers).filter((id) => !question.questions.some((item) => item.id === id));
  if (unknownIds.length) throw new Error(`答案包含未知问题：${unknownIds.join("、")}`);
  const normalized: StageSkillFormAnswer["answers"] = {};
  for (const item of question.questions) {
    if (!visible(item, normalized)) {
      if (item.id in answer.answers) throw new Error(`${item.title} 当前不可填写`);
      continue;
    }
    const raw = answer.answers[item.id];
    if (!isRecord(raw)) {
      if (item.required) throw new Error(`${item.title} 为必填项`);
      continue;
    }
    const comment = raw.comment == null ? "" : String(raw.comment).trim();
    if (comment.length > MAX_ANSWER_LENGTH) throw new Error(`${item.title} 的补充意见过长`);
    if (item.type === "single_choice") {
      const value = typeof raw.value === "string" ? raw.value : "";
      const allowed = new Set([...(item.options ?? []).map((option) => option.value), "__other__"]);
      if (value && !allowed.has(value)) throw new Error(`${item.title} 的选项无效`);
      if (item.required && !value) throw new Error(`${item.title} 为必填项`);
      if (value === "__other__" && !comment) throw new Error(`${item.title} 选择其他时必须补充说明`);
      normalized[item.id] = { value, comment };
      continue;
    }
    if (item.type === "multiple_choice") {
      const value = Array.isArray(raw.value) ? [...new Set(raw.value.filter((entry): entry is string => typeof entry === "string"))] : [];
      const allowed = new Set([...(item.options ?? []).map((option) => option.value), "__other__"]);
      if (value.some((entry) => !allowed.has(entry))) throw new Error(`${item.title} 的选项无效`);
      const minimum = item.validation?.minSelections ?? (item.required ? 1 : 0);
      const maximum = item.validation?.maxSelections ?? Number.MAX_SAFE_INTEGER;
      if (value.length < minimum || value.length > maximum) throw new Error(`${item.title} 的选择数量不符合要求`);
      if (value.includes("__other__") && !comment) throw new Error(`${item.title} 选择其他时必须补充说明`);
      normalized[item.id] = { value, comment };
      continue;
    }
    const value = typeof raw.value === "string" ? raw.value.trim() : "";
    const minimum = item.validation?.minLength ?? (item.required ? 1 : 0);
    const maximum = item.validation?.maxLength ?? MAX_ANSWER_LENGTH;
    if (value.length < minimum || value.length > maximum) throw new Error(`${item.title} 的文字长度不符合要求`);
    normalized[item.id] = { value };
  }
  return { answers: normalized } satisfies StageSkillFormAnswer;
}

export function validateStageLoopFeedbackAnswer(
  question: StageLoopFeedbackQuestion,
  answer: unknown,
): StageLoopFeedbackAnswer {
  if (!isRecord(answer) || !["accept", "continue"].includes(String(answer.action ?? ""))) {
    throw new Error("请选择接受当前结果或继续反馈");
  }
  if (answer.action === "accept") return { action: "accept" };
  if (!isRecord(answer.feedback)) throw new Error("继续处理时必须提交 feedback");
  const text = optionalText(answer.feedback.text, "feedback.text", 20_000);
  if (text && !question.accepts.text) throw new Error("当前 Stage 不接受文本反馈");
  if (!Array.isArray(answer.feedback.files) || answer.feedback.files.length > 10) {
    throw new Error("feedback.files 必须是且最多包含 10 个文件");
  }
  const files = answer.feedback.files.map((raw, index): StageLoopFeedbackFile => {
    if (!isRecord(raw)) throw new Error(`feedback.files[${index}] 必须是对象`);
    const name = requiredText(raw.name, `feedback.files[${index}].name`, 255);
    if (name.includes("/") || name.includes("\\") || name === "." || name === "..") {
      throw new Error(`feedback.files[${index}].name 不合法`);
    }
    const extension = name.includes(".") ? `.${name.split(".").at(-1)!.toLowerCase()}` : "";
    if (!question.accepts.files.includes(extension)) {
      throw new Error(`feedback.files[${index}] 的文件类型不受支持`);
    }
    const contentType = requiredText(raw.content_type, `feedback.files[${index}].content_type`, 255);
    const size = Number(raw.size);
    if (!Number.isSafeInteger(size) || size < 0 || size > 20 * 1024 * 1024) {
      throw new Error(`feedback.files[${index}].size 不合法`);
    }
    const sha256 = String(raw.sha256 ?? "").toLowerCase();
    if (!/^[0-9a-f]{64}$/.test(sha256)) throw new Error(`feedback.files[${index}].sha256 不合法`);
    const ref = requiredText(raw.ref, `feedback.files[${index}].ref`, 2000);
    return { name, content_type: contentType, size, sha256, ref };
  });
  if (!text && files.length === 0) throw new Error("请提交文本或文件反馈");
  return { action: "continue", feedback: { ...(text ? { text } : {}), files } };
}
