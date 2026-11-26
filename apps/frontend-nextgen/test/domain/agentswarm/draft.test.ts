import {
  canSubmit,
  createLibraryAgentRef,
  autoReferenceByBodyName,
  createNewAgentRef,
  findDefinitionCollisions,
  IDENTITY_REQUIRED_MESSAGE,
  resolveRosterRefs,
  spawnName,
  toSubmitDTO,
  validateDraft,
  type ComputeDraft,
} from '@/domain/agentswarm/draft';

/**
 * A draft whose roster is one *new* entry — the blank-agent case, which is now
 * the only way to author an agent in the form. New entries are persisted at
 * submit, so payload assertions pass explicit resolved ids.
 */
const draft = (partial: Partial<ComputeDraft> = {}): ComputeDraft => ({
  goal: '整理这批数据',
  refs: [createNewAgentRef({ key: 'a', name: 'worker', role: 'works', kind: 'bot' })],
  identityBotId: 'bot-1',
  ...partial,
});

const SAVED = { a: 'saved-def' };

test('a complete draft with an identity source is submittable', () => {
  expect(validateDraft(draft())).toEqual([]);
  expect(canSubmit(draft())).toBe(true);
});

test('a draft without an identity source is refused', () => {
  const issues = validateDraft(draft({ identityBotId: null }));
  expect(issues).toContainEqual({ field: 'identity', message: IDENTITY_REQUIRED_MESSAGE });
});

test('a blank identity source counts as absent', () => {
  expect(canSubmit(draft({ identityBotId: '   ' }))).toBe(false);
});

test('an empty roster is refused', () => {
  expect(validateDraft(draft({ refs: [] }))).toContainEqual({
    field: 'agents',
    message: '请至少定义一个执行 Agent',
  });
});

test('a new entry without a name or role is refused', () => {
  expect(
    validateDraft(draft({ refs: [createNewAgentRef({ key: 'a', name: ' ', role: 'works' })] })),
  ).toContainEqual({ field: 'agents', message: '每个 Agent 都需要名称' });
  expect(
    validateDraft(draft({ refs: [createNewAgentRef({ key: 'a', name: 'worker', role: '  ' })] })),
  ).toContainEqual({ field: 'agents', message: '每个 Agent 都需要角色说明' });
});

test('an empty goal is refused', () => {
  expect(validateDraft(draft({ goal: '   ' }))).toContainEqual({
    field: 'goal',
    message: '请填写工作目标',
  });
});

// ---- the roster is always definition refs ----

test('every roster entry is submitted as an agent_ref, never as inline agents', () => {
  const dto = toSubmitDTO(draft(), 'acme', SAVED);
  expect(dto.agent_refs).toEqual([{ definition_id: 'saved-def' }]);
  expect(dto).not.toHaveProperty('agents');
});

test('a library entry references its own definition without re-saving it', async () => {
  const library = createLibraryAgentRef({ definition_id: 'lib-1', name: 'greeter', role: 'greets' });
  const save = jest.fn();
  const resolved = await resolveRosterRefs(draft({ refs: [library] }), save);
  expect(save).not.toHaveBeenCalled();
  expect(resolved[library.key]).toBe('lib-1');
});

test('a new entry is saved under exactly the name the user typed', async () => {
  // No `_2` suffixing: a collision is surfaced upstream, not renamed here.
  const save = jest.fn().mockResolvedValue('new-def');
  await resolveRosterRefs(draft({ refs: [createNewAgentRef({ key: 'a', name: 'worker', role: 'w' })] }), save);
  expect(save).toHaveBeenCalledWith(expect.objectContaining({ key: 'a' }), 'worker');
});

test('submitting an unsaved new entry throws instead of sending a missing id', () => {
  expect(() => toSubmitDTO(draft(), 'acme', {})).toThrow(/还没有保存为定义/);
});

test('an alias is sent when set, and omitted so the definition name applies', () => {
  const renamed = { ...createNewAgentRef({ key: 'a', name: 'worker', role: 'w' }), alias: 'boss' };
  expect(toSubmitDTO(draft({ refs: [renamed] }), 'acme', SAVED).agent_refs?.[0]).toEqual({
    definition_id: 'saved-def',
    alias: 'boss',
  });
});

test('spawn names default to the agent name so a blank alias is not a gap', () => {
  const ref = createNewAgentRef({ name: 'worker', role: 'w' });
  expect(spawnName(ref)).toBe('worker');
  expect(spawnName({ ...ref, alias: 'boss' })).toBe('boss');
});

test('two entries spawning under the same name are refused', () => {
  const issues = validateDraft(
    draft({
      refs: [
        createNewAgentRef({ key: 'a', name: 'worker', role: 'w' }),
        createNewAgentRef({ key: 'b', name: 'worker', role: 'w' }),
      ],
    }),
  );
  expect(issues.some((issue) => issue.message.includes('同名 Agent 重复'))).toBe(true);
});

// ---- name collisions are reported, not auto-resolved ----

test('a new entry whose name is already in the library is reported, not renamed', () => {
  const collisions = findDefinitionCollisions(
    draft({ refs: [createNewAgentRef({ key: 'a', name: 'worker', role: 'w' })] }),
    [{ definition_id: 'lib-1', name: 'worker' }],
  );
  expect(collisions).toEqual([{ refKey: 'a', name: 'worker', existingDefinitionId: 'lib-1' }]);
});

test('library entries never collide with themselves', () => {
  const library = createLibraryAgentRef({ definition_id: 'lib-1', name: 'worker', role: 'w' });
  expect(
    findDefinitionCollisions(draft({ refs: [library] }), [{ definition_id: 'lib-1', name: 'worker' }]),
  ).toEqual([]);
});

test('an llm-only roster is exempt from the identity requirement', () => {
  expect(
    validateDraft(draft({ refs: [createNewAgentRef({ name: 'planner', role: 'plans', kind: 'llm' })], identityBotId: null })),
  ).toEqual([]);
});

test('draft-only fields never leak into the wire payload', () => {
  const dto = toSubmitDTO(draft(), 'acme', SAVED);
  expect(dto.agent_refs?.[0]).not.toHaveProperty('key');
  expect(dto.agent_refs?.[0]).not.toHaveProperty('origin');
  expect(dto.agent_refs?.[0]).not.toHaveProperty('body');
});
// ---- a typed name that already exists resolves to that definition ----

test('typing an existing name turns the blank entry into a reference', () => {
  const draftWithName = draft({ refs: [createNewAgentRef({ key: 'a', name: 'hello_writer', role: 'w' })] });
  const next = autoReferenceByBodyName(draftWithName, [
    { definition_id: 'def-hello', name: 'hello_writer' },
  ]);
  expect(next.refs[0]).toMatchObject({ origin: 'library', definitionId: 'def-hello' });
});

test('a name with no match stays a blank entry', () => {
  const draftWithName = draft({ refs: [createNewAgentRef({ key: 'a', name: 'unique_name', role: 'w' })] });
  const next = autoReferenceByBodyName(draftWithName, [{ definition_id: 'd', name: 'other' }]);
  expect(next.refs[0].origin).toBe('new');
});

test('a reference already pointing at this definition is left alone', () => {
  const library = createLibraryAgentRef({ definition_id: 'lib-1', name: 'hello_writer', role: 'w' });
  const next = autoReferenceByBodyName(draft({ refs: [library] }), [
    { definition_id: 'lib-1', name: 'hello_writer' },
  ]);
  expect(next.refs[0].definitionId).toBe('lib-1');
});

test('a reference renamed back to a stored name re-references it', () => {
  // Renaming detaches the entry; typing the original name again must re-attach,
  // otherwise the user is stuck editing a copy of what already exists.
  const detached = createLibraryAgentRef({ definition_id: '', name: 'hello_writer', role: 'w' });
  const next = autoReferenceByBodyName(draft({ refs: [{ ...detached, origin: 'new' }] }), [
    { definition_id: 'lib-1', name: 'hello_writer' },
  ]);
  expect(next.refs[0]).toMatchObject({ origin: 'library', definitionId: 'lib-1' });
});

test('referencing fills the body so the editor previews the real definition', () => {
  const next = autoReferenceByBodyName(draft({ refs: [createNewAgentRef({ key: 'a', name: 'greeter' })] }), [
    {
      definition_id: 'def-1',
      name: 'greeter',
      role: 'greets',
      instructions: 'say hi',
      manifest_document: 'schema_version: 1',
    },
  ]);
  expect(next.refs[0].body).toMatchObject({
    name: 'greeter',
    role: 'greets',
    instructions: 'say hi',
    config_manifest: 'schema_version: 1',
  });
});

test('an untouched draft is returned as-is so no needless re-render happens', () => {
  const d = draft();
  expect(autoReferenceByBodyName(d, [])).toBe(d);
});
