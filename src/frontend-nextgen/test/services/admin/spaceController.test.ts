// spaceController.initializePersonalSpace body 契约：部署形态差异由 Service 经 capability 注入
// （Open Core=阿里云 → skipSC:true；内部版 → 不传/空对象）。Controller 只按 body 是否有值
// 决定是否落 data 键，本身不感知形态。
import { initializePersonalSpace } from '@/services/backendApi/admin/spaceController';

jest.mock('@/services/backendApi/httpClient', () => ({
  backendRequest: jest.fn(),
}));

import { backendRequest } from '@/services/backendApi/httpClient';

const mockedRequest = backendRequest as jest.Mock;

describe('spaceController.initializePersonalSpace body 契约', () => {
  beforeEach(() => mockedRequest.mockReset());

  it('阿里云形态：body 携带 skipSC:true 时随 request data 下发', () => {
    mockedRequest.mockResolvedValue({ code: 200000, data: {} });
    initializePersonalSpace({ user_id: '146836' }, { skipSC: true });
    expect(mockedRequest).toHaveBeenCalledWith('/openapi/v1/bots/spaces/personal/initialize', {
      method: 'POST',
      params: { user_id: '146836' },
      data: { skipSC: true },
    });
  });

  it('内部版：不传 body / 传空对象均不落 data 键（保持历史空 body 契约）', () => {
    mockedRequest.mockResolvedValue({ code: 200000, data: {} });
    const options = () => mockedRequest.mock.calls[mockedRequest.mock.calls.length - 1][1];

    initializePersonalSpace({ user_id: '146836' });
    expect(mockedRequest).toHaveBeenCalledWith('/openapi/v1/bots/spaces/personal/initialize', {
      method: 'POST',
      params: { user_id: '146836' },
    });

    initializePersonalSpace({ user_id: '146836' }, {});
    expect(options()).not.toHaveProperty('data');
  });
});
