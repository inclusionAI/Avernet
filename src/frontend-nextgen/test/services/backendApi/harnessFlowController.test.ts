import { listFacadeBindings } from '@/services/backendApi/harnessFlow/harnessFlowController';
import * as httpClient from '@/services/backendApi/httpClient';

jest.mock('@/services/backendApi/httpClient');

const backendRequest = httpClient.backendRequest as jest.MockedFunction<typeof httpClient.backendRequest>;

describe('harnessFlowController', () => {
  beforeEach(() => {
    backendRequest.mockReset();
  });

  test('loads facade bindings through the public HarnessFlow gateway', async () => {
    backendRequest.mockResolvedValue([]);

    await listFacadeBindings();

    expect(backendRequest).toHaveBeenCalledWith('/openapi/v1/harnessflow/api/facades', {
      method: 'GET',
    });
  });
});
