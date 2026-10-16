/**
 * BCS 群聊 ask_user 问答领域：类型、草稿校验与答案构造。
 *
 * 纯领域逻辑，无 I/O：输入为协议 questions 与表单草稿，输出为
 * interaction.resolve 提交所需的 answers。
 */

export interface GroupQuestion {
  questionId: string;
  question: string;
  header?: string;
  multiSelect: boolean;
  allowOther: boolean;
  options: Array<{ value: string; label: string; description?: string }>;
}

export interface GroupQuestionInteraction {
  key: string;
  runId: string;
  interactionId: string;
  botId: string;
  questions: GroupQuestion[];
}

export interface QuestionDraft {
  values: string[];
  other: string;
  skipped: boolean;
}

export type QuestionDrafts = Record<string, QuestionDraft>;

export type GroupQuestionAnswers = Record<string, { values: string[] }>;

export type GroupQuestionResolution = { action: 'submit'; answers: GroupQuestionAnswers } | { action: 'cancel' };

/** 校验失败错误：questionId 指向首个无效题目，供 UI 定位并聚焦。 */
export class GroupQuestionValidationError extends Error {
  readonly questionId: string;

  constructor(questionId: string, reason: string) {
    super(`问题 ${questionId} ${reason}`);
    this.name = 'GroupQuestionValidationError';
    this.questionId = questionId;
  }
}

function fail(questionId: string, reason: string): never {
  throw new GroupQuestionValidationError(questionId, reason);
}

/**
 * 由题目与草稿构造提交给 BCS 的 answers。
 *
 * 按题目顺序校验；结果覆盖全部 questionId，显式跳过的题目为 `values: []`。
 * 草稿只认自有键，继承键（如 Object.prototype.constructor）按未作答处理；
 * 结果为空原型字典，'__proto__' 等合法 questionId 的赋值与 JSON 序列化均
 * 按普通键处理。选择题提交原始 option value（不做 trim 改写），自定义/自由
 * 文本 trim 后直接合并进 values，不生成 customValues；提交的答案串必须
 * 非空白。无 options 的题目始终允许自由文本，allowOther=false 仅约束有
 * 选项时的自定义输入。任何校验失败抛出携带首个无效 questionId 的
 * GroupQuestionValidationError。
 */
export function buildGroupQuestionAnswers(questions: GroupQuestion[], drafts: QuestionDrafts): GroupQuestionAnswers {
  const answers: GroupQuestionAnswers = Object.create(null);
  for (const question of questions) {
    const { questionId } = question;
    const draft: QuestionDraft | undefined = Object.prototype.hasOwnProperty.call(drafts, questionId)
      ? drafts[questionId]
      : undefined;
    if (!draft) {
      fail(questionId, '未作答，请选择、填写或显式跳过');
    }
    if (draft.skipped) {
      answers[questionId] = { values: [] };
      continue;
    }

    const optionValues = question.options.map((option) => option.value);
    const otherText = draft.other.trim();
    if (optionValues.length > 0) {
      for (const value of draft.values) {
        if (!optionValues.includes(value)) {
          fail(questionId, `提交的选项值 ${JSON.stringify(value)} 不在候选列表中`);
        }
      }
      if (otherText && !question.allowOther) {
        fail(questionId, '不支持自定义回答');
      }
    }
    for (const value of draft.values) {
      if (value.trim() === '') {
        fail(questionId, '答案不能为空白');
      }
    }

    const values = otherText ? [...draft.values, otherText] : [...draft.values];
    if (values.length === 0) {
      fail(questionId, '未作答，请选择、填写或显式跳过');
    }
    if (!question.multiSelect && values.length > 1) {
      fail(questionId, '单选题最多提交一个答案');
    }
    answers[questionId] = { values };
  }
  return answers;
}
