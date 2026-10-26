import { backendRequest } from '@/services/backendApi/httpClient';
import {
  createExpertBot,
  getExpert,
  getExpertBotCreationStatus,
  listExpertCategories,
  listExperts,
} from '@/services/expertMarketService';

jest.mock('@/services/backendApi/httpClient', () => ({
  backendRequest: jest.fn(),
  BackendRequestError: jest.requireActual('@/services/backendApi/httpClient').BackendRequestError,
}));
const request = jest.mocked(backendRequest);

describe('expert catalog client', () => {
  beforeEach(() => request.mockReset());

  test('uses the shared authenticated catalog contract without a caller supplied tenant', async () => {
    request.mockResolvedValue({ code: 200000, data: { total: 0, items: [] } });
    const signal = new AbortController().signal;
    await expect(
      listExperts({ keyword: '产品', category: '设计', sort: 'newest', page_num: 2, page_size: 20 }, signal),
    ).resolves.toEqual({ total: 0, items: [] });
    expect(request).toHaveBeenCalledWith('/openapi/v1/experts', {
      method: 'GET',
      params: { keyword: '产品', category: '设计', sort: 'newest', page_num: 2, page_size: 20 },
      injectUserId: false,
      signal,
    });
  });
  test('keeps permission and malformed responses as errors instead of an empty catalog', async () => {
    request.mockResolvedValue({ code: 403000, message: '无权浏览', data: { total: 0, items: [] } });
    await expect(listExperts({ keyword: '', category: '', sort: 'hot', page_num: 1, page_size: 20 })).rejects.toThrow(
      '无权浏览',
    );
    request.mockResolvedValue({ code: 200000 });
    await expect(listExpertCategories()).rejects.toThrow('专家目录响应无效');
  });
});

test('rejects malformed successful pages rather than crashing the catalog view', async () => {
  request.mockResolvedValue({ code: 200000, data: { total: 2 } });
  await expect(listExperts({ keyword: '', category: '', sort: 'hot', page_num: 1, page_size: 20 })).rejects.toThrow(
    '专家目录分页响应无效',
  );
});

test('categories and detail use the same authenticated source and encode the identifier', async () => {
  request.mockResolvedValueOnce({ code: 200000, data: ['产品', '研发'] });
  await expect(listExpertCategories()).resolves.toEqual(['产品', '研发']);
  request.mockResolvedValueOnce({ code: 200000, data: { expert_id: 'product-manager', identity: [], skills: [] } });
  await getExpert('product/manager');
  expect(request).toHaveBeenLastCalledWith('/openapi/v1/experts/product%2Fmanager', {
    method: 'GET',
    params: undefined,
    injectUserId: false,
    signal: undefined,
  });
});

test('the all-categories query omits the category filter instead of requesting an empty category', async () => {
  request.mockResolvedValue({ code: 200000, data: { total: 1, items: [] } });
  await listExperts({ keyword: '', category: '', sort: 'hot', page_num: 1, page_size: 20 });
  expect(request).toHaveBeenLastCalledWith('/openapi/v1/experts', {
    method: 'GET',
    params: { keyword: '', category: undefined, sort: 'hot', page_num: 1, page_size: 20 },
    injectUserId: false,
    signal: undefined,
  });
});

test('creates from an expert using the original BotCreate body and polls the original manifest task', async () => {
  const body = {
    bot_name: '我的产品助手',
    bot_desc: '需求分析',
    engine: 'openclaw',
    cluster_name: 'ACRA' as const,
    bot_type: 'personal' as const,
    space_id: '10',
  };
  request.mockResolvedValueOnce({ code: 200000, data: { bot_id: 'bot-1', iframe_url: '', redirect_url: '' } });
  await expect(createExpertBot('product/manager', body)).resolves.toMatchObject({ bot_id: 'bot-1' });
  expect(request).toHaveBeenLastCalledWith('/openapi/v1/experts/product%2Fmanager/bots', {
    method: 'POST',
    data: body,
    injectUserId: true,
  });
  request.mockResolvedValueOnce({ code: 200000, data: { bot_id: 'bot-1', state: 'APPLYING' } });
  await expect(getExpertBotCreationStatus('bot-1')).resolves.toMatchObject({ state: 'APPLYING' });
  expect(request).toHaveBeenLastCalledWith('/openapi/v1/bots/bot-1/with-manifest/status', {
    method: 'GET',
    injectUserId: true,
  });
});


test('Service classifies definite rejection separately from unknown submission outcomes', () => {
  const { classifyExpertSubmissionFailure } = jest.requireActual('@/services/expertMarketService');
  const { BackendRequestError } = jest.requireActual('@/services/backendApi/httpClient');
  expect(classifyExpertSubmissionFailure(new BackendRequestError('duplicate', { status: 409, apiPath: '/experts' }))).toBe('rejected');
  expect(classifyExpertSubmissionFailure(new BackendRequestError('upstream failed', { status: 500, apiPath: '/experts' }))).toBe('unknown');
  expect(classifyExpertSubmissionFailure(new Error('connection lost'))).toBe('unknown');
});
