/** @jest-environment jsdom */
import { useYamlValidation } from '@/pages/Workspace/hooks/useYamlValidation';
import { collaborationDefinitionService } from '@/services/workspace/collaborationDefinitionService';
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
