import type { CollaborationSquareGateway } from '@/services/collaborationSquare/collaborationSquareGateway';
import { UnsupportedCollaborationSquareAdapter } from '@/services/collaborationSquare/unsupportedCollaborationSquareAdapter';
import { describe, expect, it } from '@jest/globals';

describe('UnsupportedCollaborationSquareAdapter task methods', () => {
  // 按网关契约访问，验证不支持能力显式抛 unsupported（不伪成功）。
  const adapter: CollaborationSquareGateway = new UnsupportedCollaborationSquareAdapter();

  it('listPublicTasks 显式返回 unsupported，不伪成功', async () => {
    await expect(adapter.listPublicTasks()).rejects.toMatchObject({ code: 'unsupported' });
  });

  it('getPublicTask 显式返回 unsupported，不伪成功', async () => {
    await expect(adapter.getPublicTask('task-1')).rejects.toMatchObject({ code: 'unsupported' });
  });
});
