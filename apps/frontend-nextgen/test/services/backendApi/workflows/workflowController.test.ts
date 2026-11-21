import {
  listExecutableWorkflows,
  listWorkflows,
} from '@/services/backendApi/workflows/workflowController';
import * as httpClient from '@/services/backendApi/httpClient';

jest.mock('@/services/backendApi/httpClient');

const backendRequest = httpClient.backendRequest as jest.MockedFunction<typeof httpClient.backendRequest>;

describe('workflowController', () => {
  beforeEach(() => {
    backendRequest.mockReset();
  });

  test('keeps the management list on the legacy view query', async () => {
    backendRequest.mockResolvedValue([]);

    await listWorkflows('146836', 'default:146836');

    expect(backendRequest).toHaveBeenCalledWith('/api/workflows', {
      method: 'GET',
      params: { botOwnerId: '146836', botId: 'default' },
    });
  });

  test('requests only executable workflows for the task menu', async () => {
    backendRequest.mockResolvedValue([]);

    await listExecutableWorkflows('146836', 'default:146836');

    expect(backendRequest).toHaveBeenCalledWith('/api/workflows', {
      method: 'GET',
      params: { botOwnerId: '146836', botId: 'default', permission: 'execute' },
    });
  });
});
