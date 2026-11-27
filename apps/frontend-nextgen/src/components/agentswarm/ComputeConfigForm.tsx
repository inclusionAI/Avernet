import type { ReactNode } from 'react';
import { useState } from 'react';
import { ExternalLink, Plus } from 'lucide-react';
import { AgentEditor, type AgentTab } from './AgentFieldCard';
import { useDefinitions } from '@/hooks/useDefinitions';
import { DefinitionPicker, type DefinitionSource } from './DefinitionPicker';
import { IdentityBotPicker } from './IdentityBotPicker';
import { RunOptionsPanel } from './RunOptionsPanel';
import { Button } from '@/components/ui/Button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/DropdownMenu';
import { Label } from '@/components/ui/Label';
import { Textarea } from '@/components/ui/Textarea';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import {
  autoReferenceByBodyName,
  spawnName,
  createNewAgentRef,
  validateDraft,
  type AgentRefDraft,
  type ComputeDraft,
  type DraftIssue,
} from '@/domain/agentswarm/draft';
import { AGENT_EXAMPLES } from '@/domain/agentswarm/examples';
import type { IdentityOption } from '@/domain/agentswarm/identityOptions';


export interface ComputeConfigFormProps {
  draft: ComputeDraft;
  onChange: (draft: ComputeDraft) => void;
  identityOptions: IdentityOption[];
  /** The execution Bot's model catalogue; absent falls back to the static list. */
  models?: readonly string[];
  modelsHint?: string;
  identityLoading?: boolean;
  identityError?: string | null;
  submitting?: boolean;
  /** Issues from a refused submit, shown alongside local validation. */
  submitIssues?: DraftIssue[];
  /** Scopes the definition library; absent leaves the picker unusable. */
  tenant?: string;
  /** Navigate to the definition library page; owned by the page, not the form. */
  onManageDefinitions?: () => void;
  /**
   * A refusal that belongs beside the submit action — currently a definition
   * name that already exists — rendered by the caller where the user is looking.
   */
  collisionNotice?: ReactNode;
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
  models,
  modelsHint,
  identityLoading = false,
  identityError = null,
  submitting = false,
  submitIssues = [],
  tenant,
  onManageDefinitions,
  collisionNotice,
  onSubmit,
}: ComputeConfigFormProps) {
  const [touched, setTouched] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [addMenuOpen, setAddMenuOpen] = useState(false);
  // The library is read here as well as in the picker: a typed name has to be
  // matchable the moment it is entered, not only when the modal opens.
  const { definitions } = useDefinitions(tenant);
  const [pickerSource, setPickerSource] = useState<DefinitionSource>('library');
  // PRD §7: the middle column edits one agent, with the left nav switching it.
  const [selectedAgent, setSelectedAgent] = useState(0);
  const [agentTab, setAgentTab] = useState<AgentTab>('basic');
  const issues = [...validateDraft(draft), ...submitIssues];
  const issueFor = (field: DraftIssue['field']) => issues.find((issue) => issue.field === field);
  // Only a roster that provisions Bots needs an execution-permission Bot; an
// llm-only run has none, so the control would be a dead affordance.
  const botRelevant = draft.refs.some((ref) => ref.body.kind === 'bot');
  // Field errors appear once the user has tried to submit, so an untouched form
  // is not a wall of red.
  const showFieldErrors = touched || submitIssues.length > 0;

  const addRefs = (refs: AgentRefDraft[]) => {
    const taken = new Set(draft.refs.map((ref) => spawnName(ref)));
    const next = refs.map((ref) => {
      // A definition may be used twice, but the spawn names must differ.
      let alias = ref.alias.trim() || ref.body.name;
      let n = 2;
      while (taken.has(alias)) {
        alias = `${ref.body.name}_${n}`;
        n += 1;
      }
      taken.add(alias);
      return { ...ref, alias };
    });
    onChange({ ...draft, refs: [...draft.refs, ...next] });
  };

  const spawnNames = draft.refs.map((ref) => spawnName(ref));
  const duplicateNames = new Set(
    spawnNames.filter((name, index, all) => name && all.indexOf(name) !== index),
  );
  const manifestIssue = issueFor('agents')?.message;
  // 提交前检查 counts entries that can actually be submitted: a name and a role.
  const rosterSize = draft.refs.length;
  const readyCount = draft.refs.filter((ref) => ref.body.name.trim() && ref.body.role.trim()).length;

  return (
    <form
      className="flex min-h-0 flex-1 flex-col"
      onSubmit={(event) => {
        event.preventDefault();
        setTouched(true);
        if (issues.length > 0) {
          // PRD §7: locate the first entry whose name/role is missing or whose
          // spawn name repeats, select it, and switch to 基本信息.
          const offender = draft.refs.findIndex(
            (ref, index) =>
              !ref.body.name.trim() ||
              !ref.body.role.trim() ||
              draft.refs.some(
                (other, otherIndex) => otherIndex < index && spawnName(other) === spawnName(ref),
              ),
          );
          if (offender >= 0) {
            setSelectedAgent(offender);
            setAgentTab('basic');
          }
          return;
        }
        onSubmit();
      }}
    >
      {/* PRD §7: full-width config. The form is the main column; 运行设置 sits
          beside it and stays reachable while the form scrolls. */}
      <div className="grid min-h-0 flex-1 grid-cols-1 gap-6 overflow-y-auto px-5 py-4 xl:grid-cols-[minmax(0,1fr)_320px]">
      <fieldset disabled={submitting} className="min-h-0 space-y-6">
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
                      // A previous submit already saved these agents, so an agent
                      // whose name is in the library must become a reference again
                      // rather than a fresh blank entry that collides on submit.
                      const refs = example.agents.map((agent) =>
                        createNewAgentRef({ kind: 'bot', ...agent }),
                      );
                      onChange(autoReferenceByBodyName({ ...draft, goal: example.goal, refs }, definitions));
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
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-sm font-semibold">Agent 定义 · {draft.refs.length}</h3>
            <div className="flex items-center gap-2">
              {/* PRD §3: one 添加 Agent menu offering 空白创建 / 我的 Agent 库 / 空间
                  Bot / 专家市场. The latter three open one shared picker; the two
                  catalogues are listed but disabled in this milestone rather
                  than omitted, so the shape stays visible. */}
              <DropdownMenu open={addMenuOpen} onOpenChange={setAddMenuOpen}>
                <DropdownMenuTrigger asChild>
                  <Button type="button" size="sm" variant="outline">
                    <Plus className="mr-1 h-3.5 w-3.5" />
                    添加 Agent
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent>
                  <DropdownMenuItem
                    onClick={() => {
                      setAddMenuOpen(false);
                      onChange({ ...draft, refs: [...draft.refs, createNewAgentRef()] });
                      setSelectedAgent(draft.refs.length);
                      setAgentTab('basic');
                    }}
                  >
                    空白创建
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    onClick={() => {
                      setAddMenuOpen(false);
                      setPickerSource('library');
                      setPickerOpen(true);
                    }}
                  >
                    我的 Agent 库
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    disabled
                    title="空间 Bot 本期暂未开放"
                  >
                    空间 Bot
                  </DropdownMenuItem>
                  <DropdownMenuItem disabled title="专家市场本期暂未开放">
                    专家市场
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
              {onManageDefinitions ? (
                <Button type="button" size="sm" variant="ghost" onClick={onManageDefinitions}>
                  <ExternalLink className="mr-1 h-3.5 w-3.5" />
                  管理 Agent 库
                </Button>
              ) : null}
            </div>
          </div>

          <p className="text-xs leading-5 text-muted-foreground">
            Agent 定义构成可用池，实际启动数由编排决定。空白新建的 Agent 会在提交时先保存到「我的 Agent 库」，再以定义引用提交。
          </p>

          <AgentEditor
            refs={draft.refs}
            index={Math.min(selectedAgent, draft.refs.length - 1)}
            tab={agentTab}
            onTabChange={setAgentTab}
            onSelect={setSelectedAgent}
            showErrors={showFieldErrors}
            duplicateNames={showFieldErrors ? duplicateNames : new Set()}
            manifestError={showFieldErrors ? manifestIssue : undefined}
            canRemove={draft.refs.length > 1}
            onChange={(patch) => {
              const current = draft.refs[Math.min(selectedAgent, draft.refs.length - 1)];
              if (!current) return;
              // Renaming a referenced entry detaches it: the user is no longer
              // pointing at the stored definition, so the entry becomes a new
              // one that saves under the new name instead of borrowing the old.
              const detach = patch.name !== undefined && current.origin === 'library';
              const updated = draft.refs.map((item) => {
                if (item.key !== current.key) return item;
                if (detach) {
                  return {
                    ...item,
                    origin: 'new' as const,
                    definitionId: '',
                    body: { ...item.body, ...patch },
                  };
                }
                return { ...item, body: { ...item.body, ...patch } };
              });
              // Typing the name of an agent that already exists resolves this
              // entry to it, so the roster shows it as 来自定义库 right away.
              onChange(autoReferenceByBodyName({ ...draft, refs: updated }, definitions));
            }}
            onDuplicate={() => {
              const current = draft.refs[Math.min(selectedAgent, draft.refs.length - 1)];
              if (!current) return;
              // A copy carries the source's body — for a library ref that is the
              // definition's content, so the copy shows what was duplicated.
              // Its spawn name is suffixed: two rows spawning as `hello_writer`
              // would be refused, and the copy is meant to run as its own agent.
              const copy = createNewAgentRef({ ...current.body });
              const taken = new Set(draft.refs.map((ref) => spawnName(ref)));
              const base = spawnName(current) || current.body.name.trim();
              let alias = base;
              let n = 2;
              while (taken.has(alias)) {
                alias = `${base}_${n}`;
                n += 1;
              }
              onChange(
                autoReferenceByBodyName(
                  { ...draft, refs: [...draft.refs, { ...copy, alias }] },
                  definitions,
                ),
              );
              setSelectedAgent(draft.refs.length);
              setAgentTab('basic');
            }}
            onRemove={() => {
              const index = Math.min(selectedAgent, draft.refs.length - 1);
              onChange({ ...draft, refs: draft.refs.filter((_, i) => i !== index) });
              // Select the neighbour that slides into place.
              setSelectedAgent(Math.max(0, index - 1));
            }}
            onAdd={() => {
              onChange({ ...draft, refs: [...draft.refs, createNewAgentRef()] });
              setSelectedAgent(draft.refs.length);
              setAgentTab('basic');
            }}
          />

          {showFieldErrors && issueFor('agents') ? (
            <p role="alert" className="text-xs text-destructive">
              {issueFor('agents')?.message}
            </p>
          ) : null}
        </section>

        </fieldset>

        <aside
          aria-label="运行设置"
          className="min-w-0 space-y-6 border-t border-border pt-5 xl:border-l xl:border-t-0 xl:pl-6 xl:pt-0 xl:sticky xl:top-0 xl:self-start"
        >
          <section className="space-y-4">
            <h2 className="m-0 text-base font-semibold">运行设置</h2>
            {/* Only rendered when the run provisions Bots — an llm-only draft has
                no source to choose, so the control would be a dead affordance. */}
            {botRelevant ? (
              <div className="space-y-2">
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
              </div>
            ) : null}
            <RunOptionsPanel
              options={draft.options}
              onChange={(options) => onChange({ ...draft, options })}
              models={models}
              modelsHint={modelsHint}
            />
          </section>

          <section className="space-y-3 border-t border-border pt-5">
            <h3 className="m-0 text-sm font-semibold">提交前检查</h3>
            <dl className="m-0 space-y-3 text-xs">
              <div className="flex justify-between gap-3">
                <dt className="text-muted-foreground">计算目标</dt>
                <dd className="m-0">{draft.goal.trim() ? '已填写' : '待填写'}</dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-muted-foreground">Agent 基础信息</dt>
                <dd className="m-0">
                  {readyCount} / {rosterSize} 已填写
                </dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-muted-foreground">执行限制</dt>
                <dd className="m-0">{draft.options ? '自定义' : '平台默认'}</dd>
              </div>
            </dl>
            <p className="m-0 text-xs leading-5 text-muted-foreground">
              Agent 定义构成可用池，实际启动数由编排决定。不自动继承聊天、附件或 Bot 能力。
            </p>
          </section>
        </aside>
      </div>

      <footer className="flex shrink-0 flex-wrap items-end justify-between gap-4 border-t border-border bg-card px-5 py-4">
        <div className="min-w-0 flex-1 space-y-2">
          {showFieldErrors && issues.length > 0 ? (
            <p role="alert" className="m-0 text-sm text-destructive">
              {issues[0].message}
            </p>
          ) : null}
          {/* A collision belongs next to the action it blocks, not at the top of
              a long form the user has already scrolled past. */}
          {collisionNotice}
        </div>
        {/* Never disable on validation failure: the user has to be able to retry
            after fixing a field, and after a gateway refusal. */}
        <Button type="submit" disabled={submitting}>
          {submitting ? '正在提交…' : '提交计算任务'}
        </Button>
      </footer>

      <DefinitionPicker
        open={pickerOpen}
        tenant={tenant}
        initialSource={pickerSource}
        onOpenChange={setPickerOpen}
        onAdd={addRefs}
      />
    </form>
  );
}