import { useState } from 'react';
import { Plus } from 'lucide-react';
import { AgentFieldCard } from './AgentFieldCard';
import { IdentityBotPicker } from './IdentityBotPicker';
import { RunOptionsPanel } from './RunOptionsPanel';
import { Button } from '@/components/ui/Button';
import { Label } from '@/components/ui/Label';
import { Textarea } from '@/components/ui/Textarea';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import {
  createAgentDraft,
  hasBotAgent,
  validateDraft,
  type AgentDraft,
  type ComputeDraft,
  type DraftIssue,
} from '@/domain/agentswarm/draft';
import { AGENT_EXAMPLES } from '@/domain/agentswarm/examples';
import type { IdentityOption } from '@/domain/agentswarm/identityOptions';

export interface ComputeConfigFormProps {
  draft: ComputeDraft;
  onChange: (draft: ComputeDraft) => void;
  identityOptions: IdentityOption[];
  identityLoading?: boolean;
  identityError?: string | null;
  submitting?: boolean;
  /** Issues from a refused submit, shown alongside local validation. */
  submitIssues?: DraftIssue[];
  onSubmit: () => void;
}

/**
 * The configuration a user confirms before a run is submitted.
 *
 * Structure follows the PRD: goal, then the Agent definitions (each a collapsed
 * `<details>` whose summary reads "name · role"), then the execution limits and
 * the run-level identity source. Errors are shown at the field that owns them,
 * not only in the footer.
 */
export function ComputeConfigForm({
  draft,
  onChange,
  identityOptions,
  identityLoading = false,
  identityError = null,
  submitting = false,
  submitIssues = [],
  onSubmit,
}: ComputeConfigFormProps) {
  const [touched, setTouched] = useState(false);
  const issues = [...validateDraft(draft), ...submitIssues];
  const issueFor = (field: DraftIssue['field']) => issues.find((issue) => issue.field === field);
  const botRelevant = hasBotAgent(draft.agents);
  // Field errors appear once the user has tried to submit, so an untouched form
  // is not a wall of red.
  const showFieldErrors = touched || submitIssues.length > 0;

  const updateAgent = (key: string, patch: Partial<AgentDraft>) =>
    onChange({ ...draft, agents: draft.agents.map((agent) => (agent.key === key ? { ...agent, ...patch } : agent)) });

  const uniqueAgentName = () => {
    const taken = new Set(draft.agents.map((agent) => agent.name.trim()));
    let n = draft.agents.length + 1;
    while (taken.has(`agent_${n}`)) n += 1;
    return `agent_${n}`;
  };

  const addAgent = (source?: AgentDraft) =>
    onChange({
      ...draft,
      agents: [
        ...draft.agents,
        {
          ...(source ?? createAgentDraft({ kind: 'bot' })),
          key: `agent-${Math.random().toString(36).slice(2, 10)}`,
          name: uniqueAgentName(),
        },
      ],
    });

  const duplicateNames = new Set(
    draft.agents.map((agent) => agent.name.trim()).filter((name, index, all) => name && all.indexOf(name) !== index),
  );
  const manifestIssue = issueFor('agents')?.message;

  return (
    <form
      className="flex min-h-0 flex-1 flex-col"
      onSubmit={(event) => {
        event.preventDefault();
        setTouched(true);
        if (issues.length > 0) return;
        onSubmit();
      }}
    >
      <fieldset disabled={submitting} className="min-h-0 flex-1 space-y-6 overflow-y-auto px-5 py-4">
        <section className="space-y-2">
          <div className="flex items-center justify-between gap-2">
            <Label htmlFor="compute-goal">
              计算目标 <span className="text-destructive">*</span>
            </Label>
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    className="h-6 shrink-0 px-2 text-[11px]"
                    onClick={() => {
                      const example = AGENT_EXAMPLES[0];
                      if (!example) return;
                      onChange({
                        ...draft,
                        goal: example.goal,
                        agents: example.agents.map((agent) => createAgentDraft({ kind: 'bot', ...agent })),
                      });
                    }}
                  >
                    填入示例任务
                  </Button>
                </TooltipTrigger>
                <TooltipContent>{AGENT_EXAMPLES[0]?.summary ?? '载入一个可直接运行的示例'}</TooltipContent>
              </Tooltip>
            </TooltipProvider>
          </div>
          <Textarea
            id="compute-goal"
            rows={4}
            value={draft.goal}
            placeholder="描述批量计算任务、处理范围，以及最终希望得到的结果。"
            aria-invalid={(showFieldErrors && Boolean(issueFor('goal'))) || undefined}
            onChange={(event) => onChange({ ...draft, goal: event.target.value })}
          />
          {showFieldErrors && issueFor('goal') ? (
            <p role="alert" className="text-xs text-destructive">
              {issueFor('goal')?.message}
            </p>
          ) : (
            <p className="text-xs leading-5 text-muted-foreground">
              仅使用这里明确填写的目标和 Agent 定义，不自动继承当前聊天、附件，也不默认授予 Bot 自身能力。
            </p>
          )}
        </section>

        <section className="space-y-3 border-t border-border pt-4">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold">Agent 定义 · {draft.agents.length}</h3>
            <Button type="button" size="sm" variant="outline" onClick={() => addAgent()}>
              <Plus className="mr-1 h-3.5 w-3.5" />
              添加 Agent
            </Button>
          </div>
          <p className="text-xs leading-5 text-muted-foreground">
            定义可用的 Agent 池。平台按需拉起容器，并非每个定义都必定执行或只执行一次。
          </p>

          {draft.agents.map((agent, index) => (
            <AgentFieldCard
              key={agent.key}
              agent={agent}
              index={index}
              showErrors={showFieldErrors}
              duplicate={showFieldErrors && duplicateNames.has(agent.name.trim())}
              manifestError={
                showFieldErrors && manifestIssue?.includes(agent.name.trim() || '未命名') ? manifestIssue : undefined
              }
              canRemove={draft.agents.length > 1}
              onChange={(patch) => updateAgent(agent.key, patch)}
              onDuplicate={() => addAgent(agent)}
              onRemove={() =>
                onChange({ ...draft, agents: draft.agents.filter((item) => item.key !== agent.key) })
              }
            />
          ))}
        </section>

        <section className="space-y-3 border-t border-border pt-4">
          <RunOptionsPanel options={draft.options} onChange={(options) => onChange({ ...draft, options })} />
        </section>

        {/* Only rendered when the run provisions Bots — an llm-only draft has no
            source to choose, so the control would be a dead affordance. */}
        {botRelevant ? (
          <section className="space-y-2 border-t border-border pt-4">
            <IdentityBotPicker
              options={identityOptions}
              value={draft.identityBotId}
              onChange={(identityBotId) => onChange({ ...draft, identityBotId })}
              loading={identityLoading}
              error={identityError}
            />
            {showFieldErrors && issueFor('identity') ? (
              <p role="alert" className="text-xs text-destructive">
                {issueFor('identity')?.message}
              </p>
            ) : null}
          </section>
        ) : null}
      </fieldset>

      <footer className="shrink-0 space-y-3 border-t border-border bg-card px-5 py-4">
        {showFieldErrors && issues.length > 0 ? (
          <p role="alert" className="text-sm text-destructive">
            {issues[0].message}
          </p>
        ) : null}
        {/* Never disable on validation failure: the user has to be able to retry
            after fixing a field, and after a gateway refusal. */}
        <Button className="w-full" type="submit" disabled={submitting}>
          {submitting ? '正在提交…' : '提交计算任务'}
        </Button>
      </footer>
    </form>
  );
}