import { searchAntCodeProjects } from '@/services/backendApi/legacyAICodingController';
import { backendRequest } from '@/services/backendApi/httpClient';

jest.mock('@/capabilities', () => ({
  getCapabilities: jest.fn(),
}));
jest.mock('@/services/backendApi/httpClient', () => ({
  backendRequest: jest.fn(),
}));

import { getCapabilities } from '@/capabilities';

const capabilityMock = getCapabilities as jest.Mock;
const requestMock = backendRequest as jest.Mock;

beforeEach(() => {
  capabilityMock.mockReturnValue({
    getAgentCodingInternalResources: () => ({
      value: {
        antCodeProjectsApiUrl: 'https://ant-code.test/webapi/projects/list',
        antCodeProjectBaseUrl: 'https://ant-code.test',
        xiaojinCodeProjectsApiUrl: 'https://xiaojin-code.test/webapi/projects/list',
        xiaojinCodeProjectBaseUrl: 'https://xiaojin-code.test',
      },
    }),
  });
  requestMock.mockReset().mockResolvedValue({
    list: [
      {
        name: '消费金融仓库',
        path_with_namespace: 'xiaojin/demo',
        description: 'demo',
        access_level: 30,
      },
    ],
  });
});

describe('searchAntCodeProjects', () => {
  it('搜索消金仓库时使用消金接口并拼接消金浏览地址', async () => {
    await expect(searchAntCodeProjects('demo', { tenant: 'xiaojin' })).resolves.toEqual([
      {
        name: '消费金融仓库',
        web_url: 'https://xiaojin-code.test/xiaojin/demo',
        description: 'demo',
        accessLevel: 30,
      },
    ]);

    expect(requestMock).toHaveBeenCalledWith(
      'https://xiaojin-code.test/webapi/projects/list',
      expect.objectContaining({
        method: 'GET',
        params: { search: 'demo', page: 1, per_page: 20 },
        injectUserId: false,
        operation: 'search-antcode-projects',
      }),
    );
  });

  it('缺省租户仍搜索主站仓库', async () => {
    await searchAntCodeProjects('demo');

    expect(requestMock.mock.calls[0][0]).toBe('https://ant-code.test/webapi/projects/list');
  });
});
