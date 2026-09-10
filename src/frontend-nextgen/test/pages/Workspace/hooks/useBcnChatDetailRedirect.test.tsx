/** @jest-environment jsdom */
import { useBcnChatDetailRedirect } from '@/pages/Workspace/hooks/useBcnChatDetailRedirect';
import { groupService } from '@/services/workspace/groupService';
import { renderHook, waitFor } from '@testing-library/react';
import { history, useSearchParams } from '@umijs/max';

jest.mock('@umijs/max', () => ({
  history: { replace: jest.fn() },
  useSearchParams: jest.fn(),
}));
jest.mock('@/services/workspace/groupService');

const mockedUseSearchParams = useSearchParams as jest.MockedFunction<typeof useSearchParams>;
const mockedReplace = history.replace as jest.MockedFunction<typeof history.replace>;
const svc = groupService as unknown as { loadGroupDetail: jest.Mock<any> };

function mountWith(search: string) {
  mockedUseSearchParams.mockReturnValue([new URLSearchParams(search), jest.fn()] as unknown as ReturnType<
    typeof useSearchParams
  >);
  return renderHook(() => useBcnChatDetailRedirect());
}

function groupDetail(participants: Array<{ actorId: string; kind: 'human' | 'bot' }>) {
  return {
    ok: true,
    data: { groupId: 'g1', name: '群', participants } as never,
  };
}

beforeEach(() => {
  jest.clearAllMocks();
  svc.loadGroupDetail.mockResolvedValue(
    groupDetail([
      { actorId: 'human_900003', kind: 'human' },
      { actorId: 'bot-x:2088', kind: 'bot' },
    ]),
  );
});

it('bot_uuid 命中群固定成员名册 → membership=direct 重定向', async () => {
  mountWith('id=g1&bot_uuid=human_900003&session=s1');
  await waitFor(() =>
    expect(mockedReplace).toHaveBeenCalledWith(
      '/workspace?tab=group&group=g1&session=s1&bot=human_900003&membership=direct',
    ),
  );
  expect(svc.loadGroupDetail).toHaveBeenCalledWith('g1');
});

it('bot_uuid 不在名册（仅参与临时会话）→ membership=session_only 重定向', async () => {
  mountWith('id=g1&bot_uuid=human_999999&session=s1');
  await waitFor(() =>
    expect(mockedReplace).toHaveBeenCalledWith(
      '/workspace?tab=group&group=g1&session=s1&bot=human_999999&membership=session_only',
    ),
  );
});

it('bot_uuid 无前缀工号与名册 human_ 前缀归一化匹配 → direct', async () => {
  mountWith('id=g1&bot_uuid=900003&session=s1');
  await waitFor(() => expect(mockedReplace).toHaveBeenCalledWith(expect.stringContaining('membership=direct')));
  expect(mockedReplace).toHaveBeenCalledWith(expect.stringContaining('bot=900003'));
});

it('群详情接口失败 → 降级为不带 membership（由 workspace 自动纠正）', async () => {
  svc.loadGroupDetail.mockResolvedValue({ ok: false, error: { friendlyMessage: 'x' } });
  mountWith('id=g1&bot_uuid=human_900003&session=s1');
  // membership 降级但 bot= 身份透传不受影响（身份定位与参与方式判定相互独立）。
  await waitFor(() =>
    expect(mockedReplace).toHaveBeenCalledWith('/workspace?tab=group&group=g1&session=s1&bot=human_900003'),
  );
});

it('缺 bot_uuid → 不调群详情、不带 membership/bot，仍重定向（workspace 走用户身份路径）', async () => {
  mountWith('id=g1&session=s1');
  await waitFor(() => expect(mockedReplace).toHaveBeenCalledWith('/workspace?tab=group&group=g1&session=s1'));
  expect(svc.loadGroupDetail).not.toHaveBeenCalled();
});

it('bcs_grp_ 前缀群 → 不判定参与方式（交由 workspace BCS 路由），直接重定向', async () => {
  mountWith('id=bcs_grp_abc&bot_uuid=human_900003&session=s1');
  await waitFor(() =>
    expect(mockedReplace).toHaveBeenCalledWith('/workspace?tab=group&group=bcs_grp_abc&session=s1&bot=human_900003'),
  );
  expect(svc.loadGroupDetail).not.toHaveBeenCalled();
});

it('缺 id 或 session → status=invalid 且不发生跳转', async () => {
  const { result } = mountWith('bot_uuid=human_900003&session=s1');
  expect(result.current.status).toBe('invalid');
  const { result: r2 } = mountWith('id=g1&bot_uuid=human_900003');
  expect(r2.current.status).toBe('invalid');
  await waitFor(() => Promise.resolve());
  expect(mockedReplace).not.toHaveBeenCalled();
  expect(svc.loadGroupDetail).not.toHaveBeenCalled();
});

it('参数齐全时进入 redirecting 状态', () => {
  const { result } = mountWith('id=g1&bot_uuid=human_900003&session=s1');
  expect(result.current.status).toBe('redirecting');
});
