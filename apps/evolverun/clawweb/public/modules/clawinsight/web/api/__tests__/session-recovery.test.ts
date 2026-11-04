import { expect, it, vi } from 'vitest';
const fetchJson = vi.hoisted(() => vi.fn().mockResolvedValue({ ok: true }));
vi.mock('@avernet/clawweb-shared/web/api/client', () => ({ fetchJson }));
import { sessionRecoveryApi } from '../session-recovery';

it('confirms the encoded Owner request using the authenticated shared client without a plan decision', async () => {
  await sessionRecoveryApi.confirmOwnerRequest('task/a', 'request/b', '已处理文档权限');
  expect(fetchJson).toHaveBeenCalledWith('/api/insight/v1/monitoring/session-recoveries/task%2Fa/owner-requests/request%2Fb/confirm', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ feedback: '已处理文档权限' }),
  });
});
