/** @jest-environment node */
import * as defController from '@/services/backendApi/collaboration/collaborationDefinitionController';
import {
  buildParticipantDefinitions,
  collaborationDefinitionService,
  formatValidationErrors,
} from '@/services/workspace/collaborationDefinitionService';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';

jest.mock('@/services/backendApi/collaboration/collaborationDefinitionController');

const dc = defController as unknown as Record<string, jest.Mock<any>>;

beforeEach(() => {
  jest.resetAllMocks();
});

describe('collaborationDefinitionService', () => {
  it('validate unwraps envelope data and returns domain model', async () => {
    dc.validateDefinition.mockResolvedValue({
      code: 20000,
      data: {
        valid: true,
        summary: { participants: 1, nodes: 1, initial_nodes: ['start'] },
        participants: [{ binding: 'assistant', display_name: '助手', required: true, assigned: false }],
      },
    });

    const res = await collaborationDefinitionService.validate('yaml');

    expect(dc.validateDefinition).toHaveBeenCalledWith({ definition_yaml: 'yaml' });
    expect(res.ok).toBe(true);
    if (!res.ok) return;
    expect(res.data.valid).toBe(true);
    expect(res.data.participants).toHaveLength(1);
  });

  it('validate returns error on throw', async () => {
    dc.validateDefinition.mockRejectedValue(new Error('boom'));
    const res = await collaborationDefinitionService.validate('yaml');
    expect(res.ok).toBe(false);
    if (res.ok) return;
    expect(res.error.code).toBe('VALIDATE_FAILED');
  });

  it('validate returns error when envelope data missing', async () => {
    dc.validateDefinition.mockResolvedValue({ code: 20000 });
    const res = await collaborationDefinitionService.validate('yaml');
    expect(res.ok).toBe(false);
  });
});

describe('buildParticipantDefinitions', () => {
  it('maps slots filtering empty bindings', () => {
    const defs = buildParticipantDefinitions([
      { binding: 'a', display_name: 'A', required: true, assigned: false },
      { binding: '', required: false, assigned: false },
      { binding: 'b', required: false, assigned: true },
    ]);
    expect(defs).toHaveLength(2);
    expect(defs[0]).toEqual({ key: 'a', displayName: 'A', required: true });
    expect(defs[1]).toEqual({ key: 'b', displayName: undefined, required: false });
  });

  it('returns empty for empty input', () => {
    expect(buildParticipantDefinitions()).toEqual([]);
  });
});

describe('formatValidationErrors', () => {
  it('formats path:message joined by ；', () => {
    const msg = formatValidationErrors([
      { code: 'E1', path: '$.participants', message: 'missing' },
      { code: 'E2', path: '$', message: 'root error' },
    ]);
    expect(msg).toBe('$.participants: missing；root error');
  });

  it('returns default text for empty errors', () => {
    expect(formatValidationErrors(undefined)).toBe('YAML 校验未通过');
  });
});
