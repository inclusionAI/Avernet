import { evolutionController } from '@/services/backendApi/harnessFlow/evolutionController';
import { backendRequest } from '@/services/backendApi/httpClient';
jest.mock('@/services/backendApi/httpClient', () => ({ backendRequest: jest.fn() }));
it('serializes apply task ids as repeated query keys, never comma joins through generic backend params', async () => {
  (backendRequest as jest.Mock).mockResolvedValue({ tasks: [] });
  await evolutionController.applyTasks(['s/a', 's b'], 'actual-user', new AbortController().signal);
  const [path, options] = (backendRequest as jest.Mock).mock.calls[0];
  const url = new URL(path, 'https://local.invalid');
  expect(url.searchParams.getAll('suggestionIds')).toEqual(['s/a', 's b']);
  expect(options.params).toBeUndefined();
  expect(url.searchParams.get('user_id')).toBe('actual-user');
  expect(options.injectUserId).toBe(false);
});
