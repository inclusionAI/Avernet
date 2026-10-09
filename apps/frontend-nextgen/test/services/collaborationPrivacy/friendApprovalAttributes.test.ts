import type { FriendApprovalConfig } from '@/domain/collaborationPrivacy/types';
import {
  buildFriendApprovalAttributesPatch,
  mapFriendApprovalAttributesToDomain,
  mergeFriendExtNoCheckScope,
  readFriendApprovalAttributes,
  toFriendCheckInStrategy,
} from '@/services/collaborationPrivacy/friendApprovalAttributes';
import { describe, expect, it } from '@jest/globals';

describe('friend approval attributes contract', () => {
  it('maps backend strategies and department codes without treating codes as display paths', () => {
    expect(
      mapFriendApprovalAttributesToDomain({
        friend_check_in_strategy: 'DEPT_FREE',
        friend_ext: { no_check_scope_friend_deps: [' TECH ', 'AI_PLATFORM', 'TECH'] },
      }),
    ).toEqual({
      mode: 'partial_exempt',
      exemptOrganizationPaths: [],
      exemptDepartmentNos: ['AI_PLATFORM', 'TECH'],
    });
    expect(readFriendApprovalAttributes({ friend_check_in_strategy: 'OPEN', friend_ext: {} })).toEqual({
      strategy: 'OPEN',
      noCheckScopeFriendDeps: [],
    });
  });

  it('fails closed to approval when the strategy is missing or unknown', () => {
    expect(readFriendApprovalAttributes({ friend_check_in_strategy: 'UNKNOWN' })).toEqual({
      strategy: 'APPROVAL',
      noCheckScopeFriendDeps: [],
    });
    expect(mapFriendApprovalAttributesToDomain({})).toMatchObject({ mode: 'all' });
  });

  it('maps page modes back to the backend enum', () => {
    const configs: FriendApprovalConfig[] = [
      { mode: 'none', exemptOrganizationPaths: [] },
      { mode: 'all', exemptOrganizationPaths: [] },
      { mode: 'partial_exempt', exemptOrganizationPaths: [], exemptDepartmentNos: ['TECH'] },
    ];
    expect(configs.map(toFriendCheckInStrategy)).toEqual(['OPEN', 'APPROVAL', 'DEPT_FREE']);
  });

  it('keeps every existing friend_ext key while replacing only the department allowlist', () => {
    const current = {
      public_user_approval: { puid: 'user-puid', status: 'AGREE' },
      public_public_approval: { puid: 'agent-puid', status: 'PROCESSING' },
      view_scope_user_friend_deps: ['OLD_USER'],
      view_scope_agent_friend_deps: ['OLD_AGENT'],
    };

    expect(mergeFriendExtNoCheckScope(current, [' TECH ', 'TECH'])).toEqual({
      ...current,
      no_check_scope_friend_deps: ['TECH'],
    });
    expect(current).not.toHaveProperty('no_check_scope_friend_deps');
  });

  it('builds a complete friend_ext replacement patch and clears allowlist for non-dept-free modes', () => {
    const current = {
      public_user_approval: { puid: 'user-puid' },
      public_public_approval: { puid: 'agent-puid' },
    };

    expect(
      buildFriendApprovalAttributesPatch(
        { friend_ext: current },
        {
          mode: 'all',
          exemptOrganizationPaths: [['不应提交']],
        },
      ),
    ).toEqual({
      friend_ext: {
        public_user_approval: { puid: 'user-puid' },
        public_public_approval: { puid: 'agent-puid' },
        no_check_scope_friend_deps: [],
      },
      friend_check_in_strategy: 'APPROVAL',
    });

    expect(() =>
      buildFriendApprovalAttributesPatch(
        { friend_ext: current },
        {
          mode: 'partial_exempt',
          exemptOrganizationPaths: [['技术部']],
        },
      ),
    ).toThrow('缺少部门编码');

    expect(() =>
      buildFriendApprovalAttributesPatch(
        { friend_ext: current },
        {
          mode: 'partial_exempt',
          exemptOrganizationPaths: [],
          exemptDepartmentNos: ['  '],
        },
      ),
    ).toThrow('缺少部门编码');
  });
});
