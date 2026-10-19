/**
 * GroupQuestionForm —— 单个 ask_user 交互的作答表单（由面板按 Bot 分组后渲染）。
 *
 * - 多题用按钮切换 + 「下一题」前进，支持回看已填答案；「确认提交」统一整组提交。
 * - 选择提交 option 的原始 value（不是 label）；自定义 / 自由文本 trim 后合并进 values；
 *   单选与自定义互斥，多选可组合选项与自定义；任何作答修改都会清除跳过标记。
 * - 「跳过此题」记录显式跳过并前进；末题跳过仅在全部题目处理完毕后自动提交（跳过题 values: []）。
 * - 确认时经领域 builder 校验并定位 / 聚焦首个无效题目；取消整组需要确认，
 *   失败保留输入与确认框（错误提示由 useGroupChatQuestions 统一 toast 一次）。
 */
import type {
  GroupQuestion,
  GroupQuestionInteraction,
  GroupQuestionResolution,
  QuestionDraft,
  QuestionDrafts,
} from '@/domain/collaboration/groupQuestions';
import {
  buildGroupQuestionAnswers,
  GroupQuestionValidationError,
} from '@/domain/collaboration/groupQuestions';
import {
  DEFAULT_DRAFT,
  withDraftOverride,
} from '@/domain/collaboration/groupQuestionFormDrafts';
import { Button, ConfirmDialog, Textarea } from '@/components/ui';
import { GroupQuestionNav } from './GroupQuestionNav';
import { cn } from '@/utils/cn';
import { useEffect, useRef, useState } from 'react';

export interface GroupQuestionFormProps {
  interaction: GroupQuestionInteraction;
  /** 交互草稿（questionId → draft），由 useGroupChatQuestions 集中保存；折叠不丢。 */
  drafts: QuestionDrafts;
  /** 按 questionId 更新草稿（面板负责绑定交互 key）。 */
  onDraftChange: (questionId: string, draft: QuestionDraft) => void;
  /** 提交 / 取消整组；失败返回 false（hook 已 toast 一次）。 */
  onResolve: (resolution: GroupQuestionResolution) => Promise<boolean>;
  /** 本交互正在等待服务端响应（其他交互不受影响）。 */
  submitting: boolean;
  /** 连接是否可用；断线时禁用全部作答操作。 */
  connected: boolean;
}

export function GroupQuestionForm({
  interaction,
  drafts,
  onDraftChange,
  onResolve,
  submitting,
  connected,
}: GroupQuestionFormProps) {
  const { questions } = interaction;
  const [activeIndex, setActiveIndex] = useState(0);
  const [validation, setValidation] = useState<string | null>(null);
  const [cancelOpen, setCancelOpen] = useState(false);
  const [focusQuestionId, setFocusQuestionId] = useState<string | null>(null);
  /** questionId → 该题第一个输入控件（redirect 校验错误时定位聚焦）。 */
  const firstControlRefs = useRef<Map<string, HTMLElement>>(new Map());

  const disabled = !connected || submitting;
  const activeIndexSafe = Math.min(activeIndex, Math.max(questions.length - 1, 0));
  const active = questions[activeIndexSafe];

  const draftOf = (questionId: string): QuestionDraft => drafts[questionId] ?? DEFAULT_DRAFT;

  const isHandled = (question: GroupQuestion): boolean => {
    const draft = draftOf(question.questionId);
    return draft.skipped || draft.values.length > 0 || draft.other.trim().length > 0;
  };

  useEffect(() => {
    if (!focusQuestionId) return;
    const element = firstControlRefs.current.get(focusQuestionId);
    element?.focus();
    setFocusQuestionId(null);
  }, [focusQuestionId]);

  const registerFirstControl = (questionId: string) => (element: HTMLElement | null) => {
    if (element) firstControlRefs.current.set(questionId, element);
    else firstControlRefs.current.delete(questionId);
  };

  const handleToggleOption = (question: GroupQuestion, optionValue: string) => {
    if (disabled) return;
    const draft = draftOf(question.questionId);
    let values: string[];
    if (question.multiSelect) {
      values = draft.values.includes(optionValue)
        ? draft.values.filter((value) => value !== optionValue)
        : [...draft.values, optionValue];
    } else {
      values = draft.values.includes(optionValue) ? [] : [optionValue];
    }
    // 单选与自定义互斥：选中选项清空自定义文本；多选允许选项与自定义组合提交。
    const other = question.multiSelect ? draft.other : '';
    onDraftChange(question.questionId, { values, other, skipped: false });
    setValidation(null);
  };

  const handleOtherChange = (question: GroupQuestion, text: string) => {
    if (disabled) return;
    const draft = draftOf(question.questionId);
    // 单选互斥：输入自定义内容清空已选选项；多选 / 纯文本题保留已有 values。
    const keepValues = question.multiSelect || question.options.length === 0;
    onDraftChange(question.questionId, {
      values: keepValues ? draft.values : [],
      other: text,
      skipped: false,
    });
    setValidation(null);
  };

  const focusQuestion = (questionId: string) => {
    const index = questions.findIndex((question) => question.questionId === questionId);
    if (index >= 0 && index !== activeIndexSafe) setActiveIndex(index);
    setFocusQuestionId(questionId);
  };

  const submitAnswers = (override?: { questionId: string; draft: QuestionDraft }) => {
    const effectiveDrafts = override
      ? withDraftOverride(drafts, override.questionId, override.draft)
      : drafts;
    try {
      const answers = buildGroupQuestionAnswers(questions, effectiveDrafts);
      void onResolve({ action: 'submit', answers });
    } catch (error) {
      if (!(error instanceof GroupQuestionValidationError)) throw error;
      setValidation(error.message);
      focusQuestion(error.questionId);
    }
  };

  const handleSkip = () => {
    if (disabled || !active) return;
    const skippedDraft: QuestionDraft = { values: [], other: '', skipped: true };
    onDraftChange(active.questionId, skippedDraft);
    setValidation(null);
    if (activeIndexSafe < questions.length - 1) {
      setActiveIndex(activeIndexSafe + 1);
      return;
    }
    // 末题跳过：全部题目已回答或跳过 → 整组提交（跳过题 values: []）；
    // 否则跳回首个未完成题，不发送请求。
    const allHandled = questions.every(
      (question) => question.questionId === active.questionId || isHandled(question),
    );
    if (!allHandled) {
      const firstOpenIndex = questions.findIndex(
        (question) => question.questionId !== active.questionId && !isHandled(question),
      );
      if (firstOpenIndex >= 0) setActiveIndex(firstOpenIndex);
      return;
    }
    submitAnswers({ questionId: active.questionId, draft: skippedDraft });
  };

  const handleCancelConfirm = async () => {
    if (disabled) return;
    const ok = await onResolve({ action: 'cancel' });
    // 失败保留确认框与已填内容（错误原因已由 hook toast 一次）
    if (ok) setCancelOpen(false);
  };

  if (!active) return null;

  const activeDraft = draftOf(active.questionId);
  const showOtherInput = active.options.length === 0 || active.allowOther;
  const otherLabel = active.options.length > 0 ? '自定义回答' : '你的回答';

  return (
    <div className="space-y-2 rounded-lg bg-muted/30 p-3">
      {questions.length > 1 ? (
        <GroupQuestionNav
          questions={questions}
          activeIndex={activeIndexSafe}
          disabled={disabled}
          onSelect={(index) => {
            setActiveIndex(index);
            setValidation(null);
          }}
        />
      ) : null}

      <div className="rounded-md border border-input bg-background p-3">
        <div className="flex items-center gap-2">
          <p className="text-sm font-medium text-foreground">{active.question}</p>
          {activeDraft.skipped ? (
            <span className="rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground">
              已跳过
            </span>
          ) : null}
        </div>
        {active.options.length > 0 ? (
          <div role="group" aria-label={active.question} className="mt-2 space-y-1">
            {active.options.map((option, index) => (
              <label
                key={option.value}
                className={cn(
                  'flex items-start gap-2 rounded-md border border-input px-3 py-2 text-sm transition-colors',
                  activeDraft.values.includes(option.value)
                    ? 'border-primary/60 bg-primary/5'
                    : 'bg-background',
                  disabled ? 'opacity-60' : 'cursor-pointer hover:bg-muted/50',
                )}
              >
                <input
                  ref={index === 0 ? registerFirstControl(active.questionId) : undefined}
                  type={active.multiSelect ? 'checkbox' : 'radio'}
                  name={`${interaction.key}:${active.questionId}`}
                  value={option.value}
                  checked={activeDraft.values.includes(option.value)}
                  onChange={() => handleToggleOption(active, option.value)}
                  disabled={disabled}
                  className="mt-0.5"
                />
                <span className="leading-snug">
                  {option.label}
                  {option.description ? (
                    <span className="block text-xs text-muted-foreground">{option.description}</span>
                  ) : null}
                </span>
              </label>
            ))}
          </div>
        ) : null}
        {showOtherInput ? (
          <label className="mt-2 block">
            <span className="mb-1 block text-xs text-muted-foreground">{otherLabel}</span>
            <Textarea
              ref={active.options.length === 0 ? registerFirstControl(active.questionId) : undefined}
              aria-label={otherLabel}
              rows={2}
              size="sm"
              value={activeDraft.other}
              onChange={(event) => handleOtherChange(active, event.target.value)}
              disabled={disabled}
              placeholder={active.options.length > 0 ? '填写选项以外的回答' : '填写你的回答'}
            />
          </label>
        ) : null}
      </div>

      <footer className="flex flex-wrap items-center justify-end gap-2">
        {validation ? (
          <p role="alert" className="mr-auto text-xs text-destructive">
            {validation}
          </p>
        ) : null}
        {!connected ? (
          <p className="mr-auto text-xs text-muted-foreground">连接不可用，暂时无法回答</p>
        ) : null}
        <Button type="button" variant="ghost" size="sm" onClick={handleSkip} disabled={disabled}>
          跳过此题
        </Button>
        {activeIndexSafe < questions.length - 1 ? (
          <Button
            type="button"
            variant="secondary"
            size="sm"
            onClick={() => setActiveIndex(activeIndexSafe + 1)}
            disabled={disabled || !isHandled(active)}
          >
            下一题
          </Button>
        ) : null}
        <Button
          type="button"
          variant="primary"
          size="sm"
          loading={submitting}
          disabled={disabled}
          onClick={() => submitAnswers()}
        >
          确认提交
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={disabled}
          onClick={() => setCancelOpen(true)}
        >
          取消问答
        </Button>
        <ConfirmDialog
          title="确认取消问答？"
          description="取消后 Bot 将收到取消通知，已填写内容不会提交。"
          open={cancelOpen}
          loading={submitting}
          onConfirm={handleCancelConfirm}
          onCancel={() => setCancelOpen(false)}
          confirmText="确认取消"
          confirmVariant="destructive"
        />
      </footer>
    </div>
  );
}