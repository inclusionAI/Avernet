import {
  buildCollaborationParticipantDefinitions,
  formatCollaborationValidationErrors,
  getCollaborationParticipantLabel,
} from './collaborationValidation';

describe('collaborationValidation', () => {
  it('uses a trimmed display name before the binding name', () => {
    const [participant] = buildCollaborationParticipantDefinitions([
      {
        binding: 'editor',
        display_name: '  内容主编  ',
        required: true,
        assigned: true,
      },
    ]);

    expect(participant).toEqual({
      key: 'editor',
      name: 'editor',
      displayName: '内容主编',
      description: undefined,
      required: true,
      assigned: true,
    });
    expect(getCollaborationParticipantLabel(participant)).toBe('内容主编');
  });

  it('falls back to the binding name when display_name is blank', () => {
    const [participant] = buildCollaborationParticipantDefinitions([
      {
        binding: 'analyst',
        display_name: ' ',
        description: '  核查赛事数据  ',
        required: false,
        assigned: true,
      },
    ]);

    expect(getCollaborationParticipantLabel(participant)).toBe('analyst');
    expect(participant.description).toBe('核查赛事数据');
  });

  it('formats backend diagnostics with their paths', () => {
    expect(
      formatCollaborationValidationErrors([
        {
          path: '$.runtime.nodes',
          message: 'must contain at least one node',
        },
        {
          path: '$',
          message: 'definition is invalid',
        },
      ]),
    ).toBe(
      '$.runtime.nodes: must contain at least one node；definition is invalid',
    );
  });
});
