/**
 * GroupQuestionPanel —— 群聊问答面板：把当前会话 pending 的 ask_user 交互按 Bot 分组展示。
 *
 * - Bot 名称取会话成员（按 actorId 匹配 ParticipantView），缺失时回退 Bot ID。
 * - 待处理数量出现（0→N）时自动展开面板与首个 Bot 分组；用户折叠后，普通状态更新
 *   不会反复展开；全部处理完成后新出现的问答重新自动展开。
 * - 折叠不丢草稿（草稿归 useGroupChatQuestions 所有，面板只读模型）。
 * - 内容区限高 min(38vh,360px) 并内部滚动；面板不替代普通聊天输入框。
 */
import type { ParticipantView } from '@/domain/collaboration';
import type { GroupQuestionInteraction, QuestionDrafts } from '@/domain/collaboration/groupQuestions';
import { Button } from '@/components/ui';
import { cn } from '@/utils/cn';
import { ChevronDown, MessageCircleQuestion } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import type { GroupChatQuestionsModel } from '@/pages/Workspace/hooks/useGroupChatQuestions';
import { GroupQuestionForm } from './GroupQuestionForm';

interface BotQuestionGroup {
  botId: string;
  botName: string;
  interactions: GroupQuestionInteraction[];
}

export interface GroupQuestionPanelProps {
  /** useGroupChatQuestions 输出的问答模型。 */
  model: GroupChatQuestionsModel;
  /** 当前会话成员：用于把 botId 映射为展示名称。 */
  participants: ParticipantView[];
}

/** 空草稿只读字典：无草稿交互的兜底输入（空原型， '_' 等任意 questionId 安全）。 */
const EMPTY_QUESTION_DRAFTS: QuestionDrafts = Object.freeze(Object.create(null)) as QuestionDrafts;

/** 按交互到达顺序分组：Bot 名取 participants.actorId 匹配项，缺失回退 botId。 */
function buildBotGroups(
  interactions: GroupQuestionInteraction[],
  participants: ParticipantView[],
): BotQuestionGroup[] {
  const groups = new Map<string, BotQuestionGroup>();
  for (const interaction of interactions) {
    let group = groups.get(interaction.botId);
    if (!group) {
      const name =
        participants.find((participant) => participant.actorId === interaction.botId)?.name ||
        interaction.botId;
      group = { botId: interaction.botId, botName: name, interactions: [] };
      groups.set(interaction.botId, group);
    }
    group.interactions.push(interaction);
  }
  return Array.from(groups.values());
}

export function GroupQuestionPanel({ model, participants }: GroupQuestionPanelProps) {
  const groups = useMemo(
    () => buildBotGroups(model.interactions, participants),
    [model.interactions, participants],
  );
  const [open, setOpen] = useState(false);
  const [openBots, setOpenBots] = useState<Record<string, boolean>>({});
  const pendingBeforeRef = useRef(0);

  const toggleBot = (botId: string) => {
    setOpenBots((prev) => ({ ...prev, [botId]: !(prev[botId] ?? false) }));
  };

  useEffect(() => {
    const before = pendingBeforeRef.current;
    const after = model.interactions.length;
    pendingBeforeRef.current = after;
    if (before === 0 && after > 0 && groups.length > 0) {
      // 新问答出现：自动展开面板与首个 Bot 分组；此后普通更新不再打扰用户。
      setOpen(true);
      const firstBotId = groups[0]?.botId;
      if (firstBotId) setOpenBots((prev) => ({ ...prev, [firstBotId]: true }));
    }
  }, [model.interactions, groups]);

  if (groups.length === 0) return null;

  const totalPending = model.interactions.length;

  return (
    <section className="flex-shrink-0 border-t border-input px-2 py-2">
      <Button
        type="button"
        variant="ghost"
        className="flex h-auto w-full items-center justify-between rounded-xl px-3 py-2 text-left"
        aria-expanded={open}
        onClick={() => setOpen((prev) => !prev)}
      >
        <span className="flex min-w-0 items-center gap-2">
          <MessageCircleQuestion aria-hidden className="h-4 w-4 shrink-0 text-primary" />
          <span className="min-w-0">
            <span className="block truncate text-sm font-medium text-foreground">
              {groups.length} 个 Bot 等待回答
            </span>
            <span className="block truncate text-xs text-muted-foreground">
              共 {totalPending} 个问答 · 输入框仍可发送消息
            </span>
          </span>
        </span>
        <span className="flex shrink-0 items-center gap-2">
          <span className="rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary">
            {totalPending}
          </span>
          <ChevronDown
            aria-hidden
            className={cn(
              'h-4 w-4 text-muted-foreground transition-transform',
              open && 'rotate-180',
            )}
          />
        </span>
      </Button>
      {open ? (
        <div
          data-testid="question-panel-scroll"
          className="mt-2 max-h-[min(38vh,360px)] space-y-2 overflow-y-auto overscroll-contain pr-1"
        >
          {groups.map((group) => {
            const groupOpen = openBots[group.botId] ?? false;
            return (
              <section
                key={group.botId}
                aria-label={`${group.botName} 的问答`}
                className="rounded-xl border border-input"
              >
                <Button
                  type="button"
                  variant="ghost"
                  className="flex h-auto w-full items-center justify-between rounded-xl px-3 py-2 text-left"
                  aria-expanded={groupOpen}
                  onClick={() => toggleBot(group.botId)}
                >
                  <span className="flex min-w-0 flex-col items-start">
                    <span className="truncate text-sm font-medium text-foreground">
                      {group.botName}
                    </span>
                    <span className="text-xs text-muted-foreground">
                      {group.interactions.length} 个待处理问答
                    </span>
                  </span>
                  <span className="flex items-center gap-2">
                    <span className="rounded-full bg-muted px-2 py-0.5 text-xs font-medium text-foreground">
                      {group.interactions.length}
                    </span>
                    <ChevronDown
                      aria-hidden
                      className={cn(
                        'h-4 w-4 text-muted-foreground transition-transform',
                        groupOpen && 'rotate-180',
                      )}
                    />
                  </span>
                </Button>
                {groupOpen ? (
                  <div className="space-y-2 p-2">
                    {group.interactions.map((interaction) => (
                      <GroupQuestionForm
                        key={interaction.key}
                        interaction={interaction}
                        drafts={model.drafts[interaction.key] ?? EMPTY_QUESTION_DRAFTS}
                        onDraftChange={(questionId, draft) =>
                          model.setDraft(interaction.key, questionId, draft)
                        }
                        onResolve={(resolution) => model.resolve(interaction.key, resolution)}
                        submitting={model.submittingKeys.includes(interaction.key)}
                        connected={model.connected}
                      />
                    ))}
                  </div>
                ) : null}
              </section>
            );
          })}
        </div>
      ) : null}
    </section>
  );
}