/**
 * The draft configuration a user builds before submitting an Agent Compute run.
 *
 * Kept separate from the wire DTO because a draft has fields the gateway must
 * never receive (it applies `extra="forbid"`), and because validation has to run
 * before a request exists.
 */
import type { AgentKind, AgentSpecDTO, RunOptionsDTO, SubmitRunDTO } from '@/services/agentswarm/types';
import { normalizeManifest, summarizeIssues, validateManifest } from './manifest';
import { normalizeOptions, validateOptions } from './options';

export interface AgentDraft {
  /** Stable client-side key for list rendering; never sent. */
  key: string;
  name: string;
  role: string;
  kind: AgentKind;
  model?: string | null;
  instructions?: string;
  /** Full manifest YAML for a bot-kind agent, provisioned verbatim. */
  config_manifest?: string;
}

export interface ComputeDraft {
  goal: string;
  agents: AgentDraft[];
  /** Run-level, because the gateway carries the identity once per run. */
  identityBotId?: string | null;
  options?: RunOptionsDTO | null;
}

export interface DraftIssue {
  /** `submit` carries a gateway refusal, which belongs to no single field. */
  field: 'goal' | 'agents' | 'identity' | 'options' | 'submit';
  message: string;
}

export const IDENTITY_REQUIRED_MESSAGE = '请选择身份 Bot：包含 Bot 类型 Agent 的运行必须指定身份 Bot';

/**
 * The UI only creates `bot` agents — `llm` is a test-only kind with no entry
 * point. The predicate stays kind-aware rather than assuming bot, because the
 * gateway genuinely exempts an llm-only run and a draft can still carry one.
 */
export function hasBotAgent(agents: readonly { kind?: string | null }[]): boolean {
  return agents.some((agent) => agent.kind === 'bot');
}

/**
 * Mirrors the service's rule: only a run that provisions Bots needs a source;
 * an `llm` agent runs in-process and holds no passport.
 *
 * A manifest is optional, but a manifest that is present must at least parse and
 * carry the five categories — the platform's preflight stays authoritative for the
 * rest, so this refuses obvious breakage without second-guessing the grammar.
 */
export function validateDraft(draft: ComputeDraft): DraftIssue[] {
  const issues: DraftIssue[] = [];
  if (!draft.goal.trim()) issues.push({ field: 'goal', message: '请填写工作目标' });
  if (!draft.agents.length) issues.push({ field: 'agents', message: '请至少定义一个执行 Agent' });
  if (draft.agents.some((agent) => !agent.name.trim())) {
    issues.push({ field: 'agents', message: '每个 Agent 都需要名称' });
  }
  if (draft.agents.some((agent) => !agent.role.trim())) {
    issues.push({ field: 'agents', message: '每个 Agent 都需要角色说明' });
  }
  // Names address the agent after trimming, so duplicates would make two
  // entries indistinguishable in the run detail.
  const names = draft.agents.map((agent) => agent.name.trim()).filter(Boolean);
  const duplicate = names.find((name, index) => names.indexOf(name) !== index);
  if (duplicate) {
    issues.push({ field: 'agents', message: `Agent 名称重复：${duplicate}` });
  }
  for (const agent of draft.agents) {
    const validation = validateManifest(agent.config_manifest ?? '');
    if (!validation.ok) {
      issues.push({
        field: 'agents',
        message: `${agent.name.trim() || '未命名 Agent'} 的配置清单无法提交：${summarizeIssues(validation.issues)}`,
      });
      break;
    }
  }
  const optionIssue = validateOptions(draft.options)[0];
  if (optionIssue) issues.push({ field: 'options', message: optionIssue.message });
  if (hasBotAgent(draft.agents) && !(draft.identityBotId || '').trim()) {
    issues.push({ field: 'identity', message: IDENTITY_REQUIRED_MESSAGE });
  }
  return issues;
}

export function canSubmit(draft: ComputeDraft): boolean {
  return validateDraft(draft).length === 0;
}

/**
 * Engine and bot type are fixed, not user-facing: every agent provisions with
 * `openclaw`/`personal`, and the adapter derives the cluster from the engine.
 */
export const FIXED_ENGINE = 'openclaw';
export const FIXED_BOT_TYPE = 'personal';

/** Drops empty optional values so the gateway's `extra="forbid"` never sees a null placeholder. */
function toAgentSpec(agent: AgentDraft): AgentSpecDTO {
  const spec: AgentSpecDTO = {
    name: agent.name.trim(),
    role: agent.role.trim(),
    kind: agent.kind,
    engine: FIXED_ENGINE,
    bot_type: FIXED_BOT_TYPE,
  };
  if (agent.model?.trim()) spec.model = agent.model.trim();
  if (agent.instructions?.trim()) spec.instructions = agent.instructions.trim();
  // Blank means "let the platform synthesize the persona" — omit rather than send "".
  const manifest = normalizeManifest(agent.config_manifest);
  if (manifest) spec.config_manifest = manifest;
  return spec;
}

/**
 * Builds the submit payload from a draft that already passed validation. The
 * caller's confirmed configuration is transmitted unchanged: no goal editing,
 * no agent invention, no limit substitution.
 *
 * The run-level `model` goes in `options.model_name` — the gateway applies it to
 * agents that do not name their own, which is exactly the inheritance the
 * selector offers.
 */
export function toSubmitDTO(draft: ComputeDraft, tenant: string): SubmitRunDTO {
  const dto: SubmitRunDTO = {
    tenant,
    goal: draft.goal,
    agents: draft.agents.map(toAgentSpec),
  };
  const identity = (draft.identityBotId || '').trim();
  if (identity) dto.identity_bot_id = identity;
  const options = normalizeOptions(draft.options);
  if (options) dto.options = options;
  return dto;
}

export function createAgentDraft(partial: Partial<AgentDraft> = {}): AgentDraft {
  return {
    key: partial.key ?? `agent-${Math.random().toString(36).slice(2, 10)}`,
    name: partial.name ?? '',
    role: partial.role ?? '',
    kind: partial.kind ?? 'bot',
    model: partial.model ?? null,
    instructions: partial.instructions,
    config_manifest: partial.config_manifest,
  };
}