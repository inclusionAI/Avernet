import { evolutionApi } from '@/services/backendApi/clawevolve/controller';
import { backendRequest } from '@/services/backendApi/httpClient';
import { useSpaceContextStore } from '@/stores/spaceContextStore';
jest.mock('@/services/backendApi/httpClient', () => ({ backendRequest: jest.fn() }));
const request = jest.mocked(backendRequest);
function space(id = 42) {
  useSpaceContextStore.setState({
    initialized: true,
    loading: false,
    error: undefined,
    currentSpaceId: id,
    currentSpace: { spaceId: id } as never,
  });
}
beforeEach(() => {
  request.mockReset();
  space();
});
it('keeps Gateway prefixes with explicit current space for every read', async () => {
  await evolutionApi.task('task/1');
  expect(request).toHaveBeenLastCalledWith('/openapi/v1/clawevolve/teamclaw/tasks/task%2F1', {
    signal: undefined,
    params: { space_id: '42' },
  });
  await evolutionApi.template('author', 'domain', 'case');
  expect(request).toHaveBeenLastCalledWith(
    '/openapi/v1/clawevolve/bench/teamclaw/domains/author/domain/templates/case',
    { params: { space_id: '42' } },
  );
});
it('keeps actual Bot identity and separate Bench owners, and never retries creates', async () => {
  request.mockResolvedValue({ task_id: 'task' });
  const data = { userId: 'runtime-owner', botId: 'bot', benchDomainOwnerUserId: 'author' };
  await evolutionApi.create('benches', data);
  expect(request).toHaveBeenLastCalledWith('/openapi/v1/clawevolve/teamclaw/benches', {
    method: 'POST',
    data,
    retryOnTransient: false,
    params: { space_id: '42' },
  });
});
it('requires a ready real space, rather than downgrading to legacy URLs', async () => {
  useSpaceContextStore.setState({ currentSpaceId: undefined, currentSpace: undefined });
  await expect(evolutionApi.tasks()).rejects.toThrow('空间');
  expect(request).not.toHaveBeenCalled();
});
it('drops late responses including A -> B -> A switches', async () => {
  let resolve!: (value: unknown) => void;
  request.mockReturnValue(
    new Promise((r) => {
      resolve = r;
    }) as never,
  );
  const pending = evolutionApi.tasks();
  space(99);
  space(42);
  resolve({ tasks: [] });
  await expect(pending).rejects.toThrow('空间已切换');
});
it('keeps owner/domain pagination and text Diff contracts', async () => {
  await evolutionApi.runs('author', 'domain', 20);
  expect(request).toHaveBeenLastCalledWith('/openapi/v1/clawevolve/bench/teamclaw/runs', {
    params: { ownerUserId: 'author', domainId: 'domain', limit: 20, offset: 20, space_id: '42' },
  });
  await evolutionApi.diff('task', 'step');
  expect(request).toHaveBeenLastCalledWith('/openapi/v1/clawevolve/teamclaw/tasks/task/steps/step/diff', {
    responseType: 'text',
    params: { space_id: '42' },
  });
});
