import { backendRequest } from '@/services/backendApi/httpClient';
import { getExpert, listExpertCategories, listExperts } from '@/services/expertMarketService';

jest.mock('@/services/backendApi/httpClient', () => ({ backendRequest: jest.fn(), BackendRequestError: class extends Error {} }));
const request = jest.mocked(backendRequest);

describe('expert catalog client', () => {
  beforeEach(() => request.mockReset());

  test('uses the shared authenticated catalog contract without a caller supplied tenant', async () => {
    request.mockResolvedValue({ code: 200000, data: { total: 0, items: [] } });
    const signal = new AbortController().signal;
    await expect(listExperts({ keyword: '产品', category: '设计', sort: 'newest', page_num: 2, page_size: 20 }, signal))
      .resolves.toEqual({ total: 0, items: [] });
    expect(request).toHaveBeenCalledWith('/openapi/v1/experts', {
      method: 'GET', params: { keyword: '产品', category: '设计', sort: 'newest', page_num: 2, page_size: 20 },
      injectUserId: false, signal,
    });
  });
  test('keeps permission and malformed responses as errors instead of an empty catalog', async () => {
    request.mockResolvedValue({ code: 403000, message: '无权浏览', data: { total: 0, items: [] } });
    await expect(listExperts({ keyword: '', category: '', sort: 'hot', page_num: 1, page_size: 20 })).rejects.toThrow('无权浏览');
    request.mockResolvedValue({ code: 200000 });
    await expect(listExpertCategories()).rejects.toThrow('专家目录响应无效');
  });
});

test('rejects malformed successful pages rather than crashing the catalog view', async () => {
  request.mockResolvedValue({ code: 200000, data: { total: 2 } });
  await expect(listExperts({ keyword: '', category: '', sort: 'hot', page_num: 1, page_size: 20 })).rejects.toThrow('专家目录分页响应无效');
});


test('categories and detail use the same authenticated source and encode the identifier', async () => {
  request.mockResolvedValueOnce({ code: 200000, data: ['产品', '研发'] });
  await expect(listExpertCategories()).resolves.toEqual(['产品', '研发']);
  request.mockResolvedValueOnce({ code: 200000, data: { expert_id: 'product-manager', identity: [], skills: [] } });
  await getExpert('product/manager');
  expect(request).toHaveBeenLastCalledWith('/openapi/v1/experts/product%2Fmanager', {
    method: 'GET', params: undefined, injectUserId: false, signal: undefined,
  });
});
