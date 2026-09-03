import type { CollaborationBotDto } from '@/services/backendApi';
import { mapBotDtoToDomain, mapOrgDeptToEntry } from '@/services/collaborationPrivacy/mappers';
import { describe, expect, it } from '@jest/globals';

function createBot(overrides: Partial<CollaborationBotDto> = {}): CollaborationBotDto {
  return {
    kind: 'bot',
    bot_id: 'bot-1',
    name: '测试 Bot',
    visibility: 'protected',
    status: 'online',
    env: 'pre',
    descriptor: { summary: '', domains: [], scopes: [], skills: [] },
    reachability: 'reachable',
    created_at: 1,
    updated_at: 2,
    ...overrides,
  };
}

describe('collaboration privacy service mappers', () => {
  it('preserves the original department name delimiters from search results', () => {
    expect(
      mapOrgDeptToEntry({
        dept_no: 'A4195',
        dept_name: '示例集团-技术事业部-平台团队',
        dept_path: 'ROOT/A4195',
      }),
    ).toEqual({ deptNo: 'A4195', path: ['示例集团-技术事业部-平台团队'] });
    expect(
      mapOrgDeptToEntry({
        dept_no: 'A5000',
        dept_name: '示例集团 / 技术事业部 / 平台团队',
        dept_path: 'ROOT/A5000',
      }),
    ).toEqual({ deptNo: 'A5000', path: ['示例集团 / 技术事业部 / 平台团队'] });
  });

  it('prefers the joined top-level engine and restores the display label', () => {
    expect(
      mapBotDtoToDomain(createBot({ engine: 'openclaw', provider: { name: 'legacy', provider_id: 'p1' } })).engine,
    ).toBe('OpenClaw');
    expect(mapBotDtoToDomain(createBot({ engine: 'claude_code' })).engine).toBe('Claude Code');
  });

  it('maps the current user and Bot publication states from the real /mine fields', () => {
    const mapped = mapBotDtoToDomain(
      createBot({
        bot_id: '20260715_vl4oht43:447147',
        visibility: 'public',
        user_visibility: 'private',
        friend_ext: {},
      }),
    );

    expect(mapped.publication).toEqual({
      user: { scope: 'none', organizationPaths: [] },
      bot: { scope: 'all', organizationPaths: [] },
    });
  });

  it('maps effective department scopes for both publication audiences', () => {
    const mapped = mapBotDtoToDomain(
      createBot({
        visibility: 'protected',
        user_visibility: 'public',
        friend_ext: {
          view_scope_user_friend_deps: [{ deptNo: 'TECH', deptName: '蚂蚁集团-技术部' }],
          view_scope_agent_friend_deps: ['AI_PLATFORM'],
        },
      }),
    );

    expect(mapped.publication).toEqual({
      user: {
        scope: 'restricted',
        organizationPaths: [['蚂蚁集团-技术部']],
        organizationEntries: [{ deptNo: 'TECH', path: ['蚂蚁集团-技术部'] }],
      },
      bot: { scope: 'restricted', organizationPaths: [] },
    });
  });

  it('reads friend_check_in_strategy and no_check_scope_friend_deps when the DTO carries them', () => {
    expect(
      mapBotDtoToDomain(
        createBot({
          friend_check_in_strategy: 'DEPT_FREE',
          friend_ext: { no_check_scope_friend_deps: ['TECH', 'AI_PLATFORM'] },
        }),
      ).friendApproval,
    ).toEqual({
      mode: 'partial_exempt',
      exemptOrganizationPaths: [],
      exemptDepartmentNos: ['AI_PLATFORM', 'TECH'],
    });
  });

  it('defaults missing friend strategy to the approval-required mode', () => {
    expect(mapBotDtoToDomain(createBot()).friendApproval).toEqual({
      mode: 'all',
      exemptOrganizationPaths: [],
    });
  });

  it('restores user and Bot publication approvals from friend_ext on page load', () => {
    const mapped = mapBotDtoToDomain(
      createBot({
        friend_ext: {
          public_user_approval: {
            puid: 'user-puid',
            status: 'PROCESSING',
            visibility: 'public',
            approval_url: 'https://approval.example.com/ticket/dispatch/user-puid',
            view_friend_deps: [{ deptNo: 'TECH', deptName: '技术部' }],
          },
          public_agent_approval: {
            puid: 'agent-puid',
            status: 'CREATED',
            visibility: 'private',
            approval_url: '/ticket/dispatch/agent-puid',
            view_friend_deps: [],
          },
        },
      }),
    );

    expect(mapped.pendingPublications).toEqual({
      user: {
        id: 'user-puid',
        audience: 'user',
        target: { scope: 'restricted', organizationPaths: [] },
        submittedAt: '',
        approvalUrl: 'https://approval.example.com/ticket/dispatch/user-puid',
      },
      bot: {
        id: 'agent-puid',
        audience: 'bot',
        target: { scope: 'none', organizationPaths: [] },
        submittedAt: '',
        approvalUrl: '/ticket/dispatch/agent-puid',
      },
    });
  });

  it('does not restore completed approvals or expose unsafe approval links', () => {
    const mapped = mapBotDtoToDomain(
      createBot({
        friend_ext: {
          public_user_approval: {
            puid: 'agreed-puid',
            status: 'AGREE',
            visibility: 'public',
            approval_url: 'https://approval.example.com/ticket/dispatch/agreed-puid',
            view_friend_deps: [],
          },
          public_agent_approval: {
            puid: 'pending-puid',
            status: 'PENDING',
            visibility: 'public',
            approval_url: 'javascript:alert(1)',
            view_friend_deps: [],
          },
        },
      }),
    );

    expect(mapped.pendingPublications.user).toBeUndefined();
    expect(mapped.pendingPublications.bot).toEqual({
      id: 'pending-puid',
      audience: 'bot',
      target: { scope: 'all', organizationPaths: [] },
      submittedAt: '',
    });
  });
});
