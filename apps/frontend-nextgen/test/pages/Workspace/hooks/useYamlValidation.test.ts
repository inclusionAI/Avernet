/** @jest-environment jsdom */
import { useYamlValidation } from '@/pages/Workspace/hooks/useYamlValidation';
import { collaborationDefinitionService, type DefinitionValidation } from '@/services/workspace/collaborationDefinitionService';
import { beforeEach, expect, it, jest } from '@jest/globals';
import { act, renderHook } from '@testing-library/react';

beforeEach(() => {
  jest.restoreAllMocks();
});

it('validate returns false for empty yaml', async () => {
  const { result } = renderHook(() => useYamlValidation());
  let ok: boolean | undefined;
  await act(async () => {
    ok = (await result.current.validate('  ')).ok;
  });
  expect(ok).toBe(false);
  expect(result.current.validationError).toBe('请输入自定义协作 YAML');
});

it('validate succeeds and sets participantDefinitions', async () => {
  jest.spyOn(collaborationDefinitionService, 'validate').mockResolvedValue({
    ok: true,
    data: {
      valid: true,
      summary: { participants: 1, nodes: 1, initial_nodes: [] },
      participants: [{ binding: 'assistant', display_name: '助手', required: true, assigned: false }],
      errors: [],
      warnings: [],
    },
  });

  const { result } = renderHook(() => useYamlValidation());

  await act(async () => {
    await result.current.validate('name: yaml');
  });

  expect(result.current.isValidated).toBe(true);
  expect(result.current.participantDefinitions).toHaveLength(1);
  expect(result.current.validatedYaml).toBe('name: yaml');
});

it('validate handles invalid yaml', async () => {
  jest.spyOn(collaborationDefinitionService, 'validate').mockResolvedValue({
    ok: true,
    data: {
      valid: false,
      summary: { participants: 0, nodes: 0, initial_nodes: [] },
      participants: [],
      errors: [{ code: 'E1', path: '$', message: 'bad' }],
      warnings: [],
    },
  });

  const { result } = renderHook(() => useYamlValidation());

  await act(async () => {
    await result.current.validate('bad');
  });

  expect(result.current.isValidated).toBe(false);
  expect(result.current.validationError).toContain('bad');
});

it('invalidate clears validated state', async () => {
  jest.spyOn(collaborationDefinitionService, 'validate').mockResolvedValue({
    ok: true,
    data: {
      valid: true,
      summary: { participants: 1, nodes: 1, initial_nodes: [] },
      participants: [{ binding: 'a', required: true, assigned: false }],
      errors: [],
      warnings: [],
    },
  });

  const { result } = renderHook(() => useYamlValidation());

  await act(async () => {
    await result.current.validate('yaml');
  });
  expect(result.current.isValidated).toBe(true);

  act(() => result.current.invalidate());
  expect(result.current.isValidated).toBe(false);
});

it('reset clears all state', async () => {
  jest.spyOn(collaborationDefinitionService, 'validate').mockResolvedValue({
    ok: true,
    data: {
      valid: true,
      summary: { participants: 1, nodes: 1, initial_nodes: [] },
      participants: [{ binding: 'a', required: true, assigned: false }],
      errors: [],
      warnings: [],
    },
  });

  const { result } = renderHook(() => useYamlValidation());

  await act(async () => {
    await result.current.validate('yaml');
  });

  act(() => result.current.reset());

  expect(result.current.isValidated).toBe(false);
  expect(result.current.participantDefinitions).toEqual([]);
});

const validLoop: DefinitionValidation = {
  valid: true,
  summary: { participants: 1, nodes: 1, initial_nodes: ['work'] },
  participants: [{ binding: 'writer', required: true, assigned: false }],
  errors: [], warnings: [],
};

it.each([
  ['VALIDATION_ONLY_FEATURE', false],
  ['NOTICE', true],
])('keeps %s warnings visible and sets execution eligibility', async (code, canExecute) => {
  const warnings = [{ code, path: '$.runtime.state_machine.version', message: 'server diagnostic', hint: 'server hint' }];
  jest.spyOn(collaborationDefinitionService, 'validate').mockResolvedValue({ ok: true, data: { ...validLoop, warnings } });
  const { result } = renderHook(() => useYamlValidation());
  await act(async () => { await result.current.validate('loop yaml'); });
  expect(result.current.isValidated).toBe(true);
  expect(result.current.warnings).toEqual(warnings);
  expect(result.current.canExecute).toBe(canExecute);
  act(() => result.current.reset());
  expect(result.current.warnings).toEqual([]);
  expect(result.current.canExecute).toBe(false);
});

it('does not restore execution eligibility from a response arriving after YAML was edited', async () => {
  let complete!: (value: { ok: true; data: DefinitionValidation }) => void;
  jest.spyOn(collaborationDefinitionService, 'validate').mockImplementation(() => new Promise((resolve) => { complete = resolve; }));
  const { result } = renderHook(() => useYamlValidation());
  let validation!: ReturnType<typeof result.current.validate>;
  act(() => { validation = result.current.validate('old loop yaml'); });
  act(() => result.current.invalidate());
  await act(async () => { complete({ ok: true, data: validLoop }); await validation; });
  expect(result.current.isValidated).toBe(false);
  expect(result.current.canExecute).toBe(false);
  expect(result.current.isValidating).toBe(false);
});

it('clears previous eligibility when revalidation fails or the YAML is empty', async () => {
  const validate = jest.spyOn(collaborationDefinitionService, 'validate').mockResolvedValue({ ok: true, data: validLoop });
  const { result } = renderHook(() => useYamlValidation());
  await act(async () => { await result.current.validate('loop yaml'); });
  expect(result.current.canExecute).toBe(true);
  await act(async () => { await result.current.validate(''); });
  expect(result.current.canExecute).toBe(false);
  await act(async () => { await result.current.validate('loop yaml'); });
  validate.mockResolvedValueOnce({ ok: false, error: { code: 'FAILED', friendlyMessage: 'failed', canRetry: false } });
  await act(async () => { await result.current.validate('loop yaml'); });
  expect(result.current.canExecute).toBe(false);
  expect(result.current.warnings).toEqual([]);
});
