import { isSilentAssistantReply } from './silentReply';

describe('isSilentAssistantReply', () => {
  it('recognizes canonical silent completion fields', () => {
    expect(
      isSilentAssistantReply({
        role: 'assistant',
        content: '',
        stopReason: 'silent',
      }),
    ).toBe(true);
    expect(
      isSilentAssistantReply({
        role: 'assistant',
        content: '',
        metadata: { stop_reason: 'SILENT' },
      }),
    ).toBe(true);
    expect(
      isSilentAssistantReply({
        role: 'assistant',
        content: '',
        extra: { payload: { stop_reason: 'silent' } },
      }),
    ).toBe(true);
    expect(
      isSilentAssistantReply({
        role: 'assistant',
        content: '',
        stopReason: 'end_turn',
        stop_reason: 'silent',
      }),
    ).toBe(true);
  });

  it('recognizes only an exact legacy token or sole JSON action wrapper', () => {
    expect(
      isSilentAssistantReply({ role: 'assistant', content: '  no_reply\n' }),
    ).toBe(true);
    expect(
      isSilentAssistantReply({
        role: 'assistant',
        content: [
          { type: 'text', text: 'NO_' },
          { type: 'text', text: 'REPLY' },
        ],
      }),
    ).toBe(true);
    expect(
      isSilentAssistantReply({
        role: 'assistant',
        content: '{"action":"no_reply"}',
      }),
    ).toBe(true);
  });

  it('does not hide substantive text or non-assistant messages', () => {
    expect(
      isSilentAssistantReply({
        role: 'assistant',
        content: 'NO_REPLY is an OpenClaw silent token',
      }),
    ).toBe(false);
    expect(
      isSilentAssistantReply({
        role: 'assistant',
        content: '{"action":"NO_REPLY","reason":"test"}',
      }),
    ).toBe(false);
    expect(isSilentAssistantReply({ role: 'user', content: 'NO_REPLY' })).toBe(
      false,
    );
  });
});
