import type { CollaborationPrivacyOverview } from '../src/domain/collaborationPrivacy/types';
import { useCollaborationPrivacyStore } from '../src/stores/collaborationPrivacyStore';

const overview: CollaborationPrivacyOverview = {
  currentUser: { displayName: '示例用户', employeeNumber: 'SAMPLE-001', departmentPath: ['示例集团'] },
  organizationOptions: [],
  bots: [{
    id: 'bot-1', name: '示例 Bot', engine: 'OpenClaw', joinedBcn: true,
    collaborationStatus: 'online', profilePublic: true, taskClaimingEnabled: false, dreamModelEnabled: false,
    publication: { user: { scope: 'all', organizationPaths: [] }, bot: { scope: 'none', organizationPaths: [] } },
    pendingPublications: {}, friendApproval: { mode: 'all', exemptOrganizationPaths: [] },
  }],
};

describe('collaborationPrivacyStore', () => {
  afterEach(() => useCollaborationPrivacyStore.getState().reset());

  test('只通过同步 setter 更新 Bot 并可完整 reset', () => {
    const store = useCollaborationPrivacyStore.getState();
    store.setOverview(overview);
    store.updateBot({ ...overview.bots[0], profilePublic: false });

    expect(useCollaborationPrivacyStore.getState().overview?.bots[0].profilePublic).toBe(false);

    useCollaborationPrivacyStore.getState().reset();
    expect(useCollaborationPrivacyStore.getState()).toMatchObject({
      overview: null, loading: true, error: null, busyAction: null,
    });
  });
});
