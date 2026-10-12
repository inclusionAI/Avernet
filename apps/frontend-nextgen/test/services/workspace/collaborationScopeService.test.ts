import { collaborationScopeService } from '@/services/workspace/collaborationScopeService';
import { groupService } from '@/services/workspace/groupService';
import { sessionService } from '@/services/workspace/sessionService';
import { scopeGroup, scopeSession } from '../../mocks/collaborationScope';
jest.mock('@/services/workspace/groupService');
jest.mock('@/services/workspace/sessionService');
beforeEach(() => {
  jest.clearAllMocks();
  jest.mocked(groupService.loadGroupDetailOrBcs).mockResolvedValue({ ok: true, data: scopeGroup });
  jest.mocked(sessionService.getSessionDetail).mockResolvedValue({ ok: true, data: scopeSession });
});
it('loads only the requested group and session, passing the viewing identity', async () => {
  const result = await collaborationScopeService.load('g', 'human_test', 'g:s');
  expect(result).toEqual({ ok: true, data: { group: scopeGroup, session: scopeSession } });
  expect(groupService.loadGroups).not.toHaveBeenCalled();
  expect(groupService.loadGroupDetailOrBcs).toHaveBeenCalledWith('g', 'human_test');
});
it('rejects a session from another group', async () => {
  jest
    .mocked(sessionService.getSessionDetail)
    .mockResolvedValue({ ok: true, data: { ...scopeSession, groupId: 'other' } });
  expect(await collaborationScopeService.load('g', 'human_test', 'g:s')).toMatchObject({
    ok: false,
    error: { code: 'SESSION_GROUP_MISMATCH' },
  });
});
it('rejects dissolved or mismatched groups', async () => {
  jest
    .mocked(groupService.loadGroupDetailOrBcs)
    .mockResolvedValue({ ok: true, data: { ...scopeGroup, status: 'dissolved' } });
  expect(await collaborationScopeService.load('g', 'human_test')).toMatchObject({ ok: false });
});
it('propagates permission/load failures without falling back', async () => {
  const failure = { ok: false as const, error: { code: 'DENIED', friendlyMessage: '无权限', canRetry: false } };
  jest.mocked(groupService.loadGroupDetailOrBcs).mockResolvedValue(failure);
  expect(await collaborationScopeService.load('g', 'human_test', 'g:s')).toEqual(failure);
  expect(sessionService.getSessionDetail).not.toHaveBeenCalled();
});
