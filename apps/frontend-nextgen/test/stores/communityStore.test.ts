import { useCommunityStore } from '../../src/stores/communityStore';

const topic = {
  id: 't1',
  title: '主题',
  body: '正文',
  status: 'open' as const,
  author: { type: 'human' as const, id: 'u1', displayName: '我' },
  replyCount: 1,
  latestActivityAt: '2026-10-06T10:00:00Z',
  createdAt: '2026-10-06T09:00:00Z',
  isMine: true,
  canClose: true,
};

describe('community store', () => {
  beforeEach(() => useCommunityStore.getState().reset());

  test('stores topics and updates a closed topic consistently', () => {
    const store = useCommunityStore.getState();
    store.setTopics([topic], 1);
    store.setSelectedTopic(topic);
    store.markTopicClosed('t1');

    expect(useCommunityStore.getState().topics[0]).toMatchObject({ status: 'closed', canClose: false });
    expect(useCommunityStore.getState().selectedTopic).toMatchObject({ status: 'closed', canClose: false });
  });

  test('changing scope and query resets pagination', () => {
    useCommunityStore.setState({ offset: 20 });
    useCommunityStore.getState().setQuery('bot');
    expect(useCommunityStore.getState()).toMatchObject({ query: 'bot', offset: 0 });
    useCommunityStore.setState({ offset: 20 });
    useCommunityStore.getState().setScope('mine');
    expect(useCommunityStore.getState()).toMatchObject({ scope: 'mine', offset: 0 });
  });
});
