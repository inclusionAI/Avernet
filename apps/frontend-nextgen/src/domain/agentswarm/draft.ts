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
import { normalizeModelId } from './models';

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
  /**
   * The roster, always submitted as `agent_refs`.
   *
   * A blank agent the user writes in the form is indistinguishable on the wire
   * from a stored one: it is saved to the definition library at submit and then
   * referenced like any other. That keeps one submit path and means a run's
   * agents are always auditable, versioned definitions rather than inline text
   * that exists only inside the run.
   */
  refs: AgentRefDraft[];
  /** Run-level, because the gateway carries the identity once per run. */
  identityBotId?: string | null;
  options?: RunOptionsDTO | null;
}

/**
 * Where a roster entry came from. Kept on the draft so the list can say so —
 * a user who just wrote an agent should see that it will be saved, not have it
 * look identical to one picked from the library.
 */
export type AgentRefOrigin = 'library' | 'new';

/** One roster entry: a stored definition (or one about to be stored) plus its alias. */
export interface AgentRefDraft {
  /** Stable client-side key for list rendering; never sent. */
  key: string;
  origin: AgentRefOrigin;
  /** Empty for a `new` entry until it is persisted at submit. */
  definitionId: string;
  /** The editable body of a `new` entry; ignored for a library entry. */
  body: AgentDraft;
  /** Sent as `alias`; blank means "use the definition's own name". */
  alias: string;
}

export interface DraftIssue {
  /** `submit` carries a gateway refusal, which belongs to no single field. */
  field: 'goal' | 'agents' | 'identity' | 'options' | 'submit';
  message: string;
}

export const IDENTITY_REQUIRED_MESSAGE = '请选择执行权限 Bot：包含 Bot 类型 Agent 的运行必须指定执行权限 Bot';

/**
 * The UI only creates `bot` agents — `llm` is a test-only kind with no entry
 * point. The predicate stays kind-aware rather than assuming bot, because the
 * gateway genuinely exempts an llm-only run and a draft can still carry one.
 */
export function hasBotAgent(agents: readonly { kind?: string | null }[]): boolean {
  return agents.some((agent) => agent.kind === 'bot');
}

/**
 * The name this entry spawns under: its alias, or — for a library entry the
 * user did not rename — the definition's own name. A `new` entry always has a
 * real name because it is saved from `body.name`.
 */
export function spawnName(ref: AgentRefDraft): string {
  return (ref.alias.trim() || ref.body.name.trim()).trim();
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

  // Every roster entry becomes a definition ref, so the rules are the ref
  // rules: a non-empty roster and distinct spawn names. A `new` entry also has
  // to be complete enough to save as a definition.
  if (!draft.refs.length) {
    issues.push({ field: 'agents', message: '请至少定义一个执行 Agent' });
  }
  const newEntries = draft.refs.filter((ref) => ref.origin === 'new');
  if (newEntries.some((ref) => !ref.body.name.trim())) {
    issues.push({ field: 'agents', message: '每个 Agent 都需要名称' });
  }
  if (newEntries.some((ref) => !ref.body.role.trim())) {
    issues.push({ field: 'agents', message: '每个 Agent 都需要角色说明' });
  }
  for (const ref of newEntries) {
    const validation = validateManifest(ref.body.config_manifest ?? '');
    if (!validation.ok) {
      issues.push({
        field: 'agents',
        message: `${ref.body.name.trim() || '未命名 Agent'} 的配置清单无法提交：${summarizeIssues(validation.issues)}`,
      });
      break;
    }
  }

  const aliases = draft.refs.map((ref) => spawnName(ref)).filter(Boolean);
  const duplicateAlias = aliases.find((alias, index) => aliases.indexOf(alias) !== index);
  if (duplicateAlias) {
    issues.push({
      field: 'agents',
      message: `同名 Agent 重复：${duplicateAlias}。可以引用同一定义多次，但每次要用不同名称。`,
    });
  }

  const optionIssue = validateOptions(draft.options)[0];
  if (optionIssue) issues.push({ field: 'options', message: optionIssue.message });
  // Only a roster that provisions Bots needs an execution-permission Bot.
  const needsIdentity = draft.refs.some((ref) => ref.body.kind === 'bot');
  if (needsIdentity && !(draft.identityBotId || '').trim()) {
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

/**
 * Drops empty optional values so the gateway's `extra="forbid"` never sees a
 * null placeholder. Kept as the shape a definition is written from.
 */
export function toAgentSpec(agent: AgentDraft): AgentSpecDTO {
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
 * Builds the submit payload from a draft that already passed validation.
 *
 * The roster is always `agent_refs`: a `new` entry must already have been saved
 * as a definition by `resolveRosterRefs`, which is why this takes the resolved
 * ids rather than inventing them. Sending an unsaved entry would be a lie about
 * what the run references.
 *
 * The run-level `model` goes in `options.model_name` — the gateway applies it to
 * agents that do not name their own, which is exactly the inheritance the
 * selector offers.
 */
export function toSubmitDTO(
  draft: ComputeDraft,
  tenant: string,
  /** definition_id per ref.key, from `resolveRosterRefs`. */
  resolvedIds: Record<string, string> = {},
): SubmitRunDTO {
  const dto: SubmitRunDTO = {
    tenant,
    goal: draft.goal,
  };
  dto.agent_refs = draft.refs.map((ref) => {
    const definitionId = ref.origin === 'new' ? resolvedIds[ref.key] : ref.definitionId;
    if (!definitionId) {
      throw new Error(`${ref.body.name.trim() || '未命名 Agent'} 还没有保存为定义，无法提交`);
    }
    // Blank alias means "use the definition's own name" — omit rather than
    // send "", which the gateway would treat as a name.
    const alias = ref.alias.trim();
    return alias ? { definition_id: definitionId, alias } : { definition_id: definitionId };
  });
  const identity = (draft.identityBotId || '').trim();
  if (identity) dto.identity_bot_id = identity;
  const options = normalizeOptions(draft.options);
  if (options) dto.options = options;
  return dto;
}

/** A `new` entry whose name is already taken by a stored definition. */
export interface DefinitionCollision {
  refKey: string;
  name: string;
  /** The definition that already holds this name. */
  existingDefinitionId: string;
}

/**
 * Turns a blank entry into a reference as soon as its name matches a stored
 * definition.
 *
 * Typing the name of an existing agent almost always means "use that agent", so
 * this resolves it at input time rather than at submit — the roster then shows
 * 来自定义库 immediately and the collision path never has to run.
 */

/** What `autoReferenceByBodyName` needs from the library listing. */
export interface DefinitionSnapshot {
  definition_id: string;
  name: string;
  role?: string;
  instructions?: string;
  manifest_document?: string;
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

/** The fields a definition carries, as a roster body for preview. */
function bodyFromDefinition(definition: DefinitionSnapshot): AgentDraft {
  return createAgentDraft({
    name: definition.name,
    role: definition.role ?? '',
    instructions: definition.instructions ?? '',
    config_manifest: definition.manifest_document ?? '',
    kind: 'bot',
  });
}

export function autoReferenceByBodyName(
  draft: ComputeDraft,
  existing: readonly DefinitionSnapshot[],
): ComputeDraft {
  const byName = new Map(existing.map((definition) => [definition.name, definition]));
  let changed = false;
  const refs = draft.refs.map((ref) => {
    // Any entry can be matched: a reference the user renamed away from its
    // definition must be able to match again by the new name.
    const trimmed = ref.body.name.trim();
    if (!trimmed) return ref;
    const match = byName.get(trimmed);
    if (!match) return ref;
    if (ref.origin === 'library' && ref.definitionId === match.definition_id) return ref;
    changed = true;
    return {
      ...ref,
      origin: 'library' as const,
      definitionId: match.definition_id,
      // The body mirrors the stored definition so the editor can show what the
      // run will actually use instead of blank fields.
      body: bodyFromDefinition(match),
    };
  });
  return changed ? { ...draft, refs } : draft;
}


/**
 * Resolves a collision by using the stored definition instead of writing a new
 * one: the entry stops being a `new` draft and becomes a reference. This is the
 * usual intent — the user typed a name that already exists, which means that
 * definition is what they meant.
 */
export function referenceExistingDefinition(
  draft: ComputeDraft,
  collisions: readonly DefinitionCollision[],
): ComputeDraft {
  const byKey = new Map(collisions.map((item) => [item.refKey, item]));
  return {
    ...draft,
    refs: draft.refs.map((ref) => {
      const collision = byKey.get(ref.key);
      if (!collision) return ref;
      return {
        ...ref,
        origin: 'library',
        definitionId: collision.existingDefinitionId,
      };
    }),
  };
}

/**
 * Finds `new` entries whose name is already in the library.
 *
 * A collision is never auto-resolved: silently saving as `worker_2` would create
 * a duplicate the user did not ask for, and would hide that an agent with this
 * name already exists. The caller surfaces these and lets the user either rename
 * or update the existing definition.
 */
export function findDefinitionCollisions(
  draft: ComputeDraft,
  existing: readonly { definition_id: string; name: string }[],
): DefinitionCollision[] {
  const byName = new Map(existing.map((definition) => [definition.name, definition.definition_id]));
  const collisions: DefinitionCollision[] = [];
  for (const ref of draft.refs) {
    if (ref.origin !== 'new') continue;
    const name = ref.body.name.trim();
    const existingId = byName.get(name);
    if (name && existingId) collisions.push({ refKey: ref.key, name, existingDefinitionId: existingId });
  }
  return collisions;
}

/**
 * Persists every `new` roster entry as a definition and returns its id.
 *
 * `save` receives the entry's own name; collisions are rejected upstream by
 * `findDefinitionCollisions` rather than being renamed here, so what the user
 * typed is what gets stored.
 */
export async function resolveRosterRefs(
  draft: ComputeDraft,
  save: (entry: AgentRefDraft, name: string) => Promise<string>,
): Promise<Record<string, string>> {
  const resolved: Record<string, string> = {};
  for (const ref of draft.refs) {
    if (ref.origin !== 'new') {
      resolved[ref.key] = ref.definitionId;
      continue;
    }
    resolved[ref.key] = await save(ref, ref.body.name.trim());
  }
  return resolved;
}

/** Updates an existing definition from a `new` entry the user chose to apply to it. */
export async function applyToExistingDefinition(
  draft: ComputeDraft,
  collision: DefinitionCollision,
  definitions: readonly {
    definition_id: string;
    definition_row_id: number;
  }[],
  update: (input: {
    definition_id: string;
    definition_row_id: number;
    name: string;
    role: string;
    instructions: string;
    model: string | null;
    manifest_document: string;
  }) => Promise<string>,
): Promise<string> {
  const ref = draft.refs.find((item) => item.key === collision.refKey);
  const existing = definitions.find((item) => item.definition_id === collision.existingDefinitionId);
  if (!ref || !existing) throw new Error(`找不到可更新的定义：${collision.name}`);
  return update({
    definition_id: existing.definition_id,
    definition_row_id: existing.definition_row_id,
    name: collision.name,
    role: ref.body.role,
    instructions: ref.body.instructions ?? '',
    model: normalizeModelId(ref.body.model) ?? null,
    manifest_document: ref.body.config_manifest ?? '',
  });
}



export function createAgentRefDraft(partial: Partial<AgentRefDraft> = {}): AgentRefDraft {
  return {
    key: partial.key ?? `ref-${Math.random().toString(36).slice(2, 10)}`,
    origin: partial.origin ?? 'library',
    definitionId: partial.definitionId ?? '',
    body: partial.body ?? createAgentDraft(),
    alias: partial.alias ?? '',
  };
}

/** A roster entry the user wrote in the form; saved to the library at submit. */
export function createNewAgentRef(partial: Partial<AgentDraft> = {}): AgentRefDraft {
  // `key` identifies the roster entry, not the agent body, so it is lifted out
  // rather than buried in `body.key` where nothing would read it.
  const { key, ...body } = partial;
  return createAgentRefDraft({ key, origin: 'new', body: createAgentDraft(body), alias: '' });
}

/** A roster entry picked from the stored library. */
export function createLibraryAgentRef(definition: {
  definition_id: string;
  name: string;
  role: string;
}): AgentRefDraft {
  return createAgentRefDraft({
    origin: 'library',
    definitionId: definition.definition_id,
    body: createAgentDraft({ name: definition.name, role: definition.role }),
    alias: definition.name,
  });
}