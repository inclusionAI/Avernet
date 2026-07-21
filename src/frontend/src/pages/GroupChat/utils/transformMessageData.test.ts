import type { GroupMessageResponse } from '@/services/backend-api/BcnController';
import { transformMessageData } from './transformMessageData';

function message(
  overrides: Partial<GroupMessageResponse>,
): GroupMessageResponse {
  return {
    id: 'message-1',
    sender: 'bot-1',
    content: 'visible reply',
    timestamp: 1,
    role: 'assistant',
    ...overrides,
  };
}

describe('transformMessageData', () => {
  it('removes canonical and legacy silent assistant history entries', () => {
    const result = transformMessageData([
      message({
        id: 'canonical',
        content: '',
        metadata: { stop_reason: 'silent' },
      }),
      message({ id: 'legacy', content: 'NO_REPLY' }),
      message({ id: 'visible', content: 'NO_REPLY is documented here' }),
      message({ id: 'user', role: 'user', content: 'NO_REPLY' }),
    ]);

    expect(result.map((item) => item.id)).toEqual(['visible', 'user']);
  });
});
