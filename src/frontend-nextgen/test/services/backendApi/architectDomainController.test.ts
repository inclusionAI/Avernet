import { fetchArchitectDomainOptions } from '@/services/backendApi/architectDomainController';
import * as httpClient from '@/services/backendApi/httpClient';
import { afterEach, describe, expect, it, jest } from '@jest/globals';

jest.mock('@/services/backendApi/httpClient');

const requestMock = (httpClient as unknown as { backendRequest: jest.Mock }).backendRequest;

afterEach(() => {
  jest.clearAllMocks();
});

describe('architectDomainController', () => {
  it('查询并转换架构域树，保留层级、负责人并过滤废弃节点', async () => {
    requestMock.mockResolvedValue({
      success: true,
      data: {
        result: [
          {
            archDomainName: '支付架构域',
            archDomainCode: 'pay',
            ownerInfo: { userName: '张三' },
            children: [
              { domainName: '交易架构域', domainCode: 'trade', ownerName: '李四' },
              { domainName: '废弃-历史架构域' },
            ],
          },
        ],
      },
    });

    await expect(fetchArchitectDomainOptions()).resolves.toEqual([
      expect.objectContaining({
        label: '支付架构域',
        value: '支付架构域',
        code: 'pay',
        ownerName: '张三',
        children: [
          expect.objectContaining({
            label: '交易架构域',
            value: '交易架构域',
            code: 'trade',
            ownerName: '李四',
          }),
        ],
      }),
    ]);
    expect(requestMock).toHaveBeenCalledWith(
      '/aixcore/archDomain/tree/searchTree',
      expect.objectContaining({
        method: 'POST',
        data: {
          tntInstId: null,
          buNo: null,
          loadExtraInfo: false,
          loadOwnerInfo: true,
        },
        operation: 'search-architect-domain-tree',
        target: 'legacy-agentclaw',
      }),
    );
  });

  it('对同层相同名称和编码的节点去重', async () => {
    requestMock.mockResolvedValue({
      data: {
        items: [
          { name: '重复架构域', code: 'same' },
          { name: '重复架构域', code: 'same' },
        ],
      },
    });
    await expect(fetchArchitectDomainOptions()).resolves.toHaveLength(1);
  });
});
