/** @jest-environment jsdom */
// 好友申请读取/写操作映射（bcs-standalone-friend-discovery Task 4）：
// 200 业务码失败保留后端 message；envelope 缺失 total 时按满页推断仍有下一页，
// 避免恰好 20 条时把第 21 条判为不可达。
import * as friendConnectionController from '@/services/backendApi/collaboration/collaborationFriendConnectionController';
import type { FriendConnectionRequestDto } from '@/services/backendApi/collaboration/collaborationFriendConnectionController';
import { CollaborationSquareError } from '@/services/collaborationSquare/collaborationSquareError';
import {
  FRIEND_CONNECTION_REQUESTS_PAGE_SIZE,
  listPendingFriendConnectionRequests,
  resolveFriendConnectionRequest,
} from '@/services/collaborationSquare/friendConnectionRequestService';

const mockedList = jest.spyOn(friendConnectionController, 'listFriendConnectionRequests');
const mockedAccept = jest.spyOn(friendConnectionController, 'acceptFriendConnectionRequest');

const actor = { type: 'bot' as const, id: 'bot-7' };

const requestDto = (index: number): FriendConnectionRequestDto => ({
  request_id: `request-${index}`,
  from_actor: { type: 'human', id: '900003' },
  to_actor: actor,
  status: 'pending',
});

const pageEnvelope = (items: FriendConnectionRequestDto[], total?: number) => ({
  code: 20000,
  message: 'ok',
  data: total === undefined ? { items } : { items, total },
});

beforeEach(() => {
  mockedList.mockReset();
  mockedAccept.mockReset();
});

describe('listPendingFriendConnectionRequests 分页', () => {
  it('envelope 无 total 且满页时推断仍可能有一页（第 21 条可达）', async () => {
    const items = Array.from({ length: FRIEND_CONNECTION_REQUESTS_PAGE_SIZE }, (_, index) => requestDto(index));
    mockedList.mockResolvedValue(pageEnvelope(items));

    const page = await listPendingFriendConnectionRequests('received', actor, 1);

    expect(page.items).toHaveLength(FRIEND_CONNECTION_REQUESTS_PAGE_SIZE);
    expect(page.total).toBe(FRIEND_CONNECTION_REQUESTS_PAGE_SIZE + 1);
  });

  it('envelope 无 total 且非满页时没有下一页', async () => {
    const items = [requestDto(0), requestDto(1)];
    mockedList.mockResolvedValue(pageEnvelope(items));

    const page = await listPendingFriendConnectionRequests('received', actor, 1);

    expect(page.total).toBe(2);
  });

  it('非首页无 total 时按页码偏移估算总数', async () => {
    const items = Array.from({ length: FRIEND_CONNECTION_REQUESTS_PAGE_SIZE }, (_, index) => requestDto(index));
    mockedList.mockResolvedValue(pageEnvelope(items));

    const page = await listPendingFriendConnectionRequests('received', actor, 2);

    expect(page.total).toBe(FRIEND_CONNECTION_REQUESTS_PAGE_SIZE * 2 + 1);
  });

  it('envelope 显式 total 时原样使用', async () => {
    mockedList.mockResolvedValue(pageEnvelope([requestDto(0)], 42));

    const page = await listPendingFriendConnectionRequests('received', actor, 1);

    expect(page.total).toBe(42);
  });
});

describe('resolveFriendConnectionRequest 失败信封', () => {
  it('HTTP 200 + 业务码失败时保留后端 message', async () => {
    mockedAccept.mockResolvedValue({ code: 409, message: ' 该申请已被其他人处理 ' });

    await expect(resolveFriendConnectionRequest('accept', 'request-1')).rejects.toThrow(
      new CollaborationSquareError('protocol_error', '该申请已被其他人处理'),
    );
  });

  it('业务码失败但 message 为空时回退通用文案', async () => {
    mockedAccept.mockResolvedValue({ code: 500, message: '   ' });

    await expect(resolveFriendConnectionRequest('accept', 'request-1')).rejects.toThrow(
      new CollaborationSquareError('protocol_error', '好友申请处理接口返回了无法识别的业务码'),
    );
  });
});