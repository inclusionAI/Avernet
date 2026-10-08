import {
  buildSessionFileContentUrl,
  deleteSessionFile,
  listSessionFiles,
  prepareSessionFile,
} from '@/services/backendApi/collaboration/sessionFileController';
import * as http from '@/services/backendApi/httpClient';
import { beforeEach, expect, it, jest } from '@jest/globals';

jest.mock('@/services/backendApi/httpClient');
const mocked = http as unknown as { backendRequest: jest.Mock<any> };

beforeEach(() => {
  jest.resetAllMocks();
});

it('listSessionFiles builds files path with query params', async () => {
  mocked.backendRequest.mockResolvedValue({ data: { items: [], total: 0 } });
  await listSessionFiles('s1', { status: 'ready', limit: 40, offset: 0 });
  expect(mocked.backendRequest).toHaveBeenCalledWith(
    '/api/v1/collaboration/sessions/s1/files',
    expect.objectContaining({
      method: 'GET',
      params: expect.objectContaining({ status: 'ready', limit: 40, offset: 0 }),
    }),
  );
});

it('prepareSessionFile posts file_name/size/mime_type', async () => {
  mocked.backendRequest.mockResolvedValue({ data: { file_id: 'f1' } });
  await prepareSessionFile('s1', { file_name: 'a.md', size: 12, mime_type: 'text/markdown' });
  expect(mocked.backendRequest).toHaveBeenCalledWith(
    '/api/v1/collaboration/sessions/s1/files',
    expect.objectContaining({ method: 'POST', data: { file_name: 'a.md', size: 12, mime_type: 'text/markdown' } }),
  );
});

it('deleteSessionFile encodes file_id in path', async () => {
  mocked.backendRequest.mockResolvedValue({ data: {} });
  await deleteSessionFile('s1', 'f/1');
  expect(mocked.backendRequest).toHaveBeenCalledWith(
    '/api/v1/collaboration/sessions/s1/files/f%2F1',
    expect.objectContaining({ method: 'DELETE' }),
  );
});

it('buildSessionFileContentUrl appends show query only for preview', () => {
  expect(buildSessionFileContentUrl('s1', 'f1', true)).toBe(
    '/api/v1/collaboration/sessions/s1/files/f1/content?show=true',
  );
  expect(buildSessionFileContentUrl('s1', 'f1', false)).toBe('/api/v1/collaboration/sessions/s1/files/f1/content');
});
