// Bot 公开风险前置检测——纯逻辑单测（deriveRiskUiState / resolvePublishChannel）。
// 覆盖 specs/bot-public-risk-check：可见性变更分流门、行在途状态、未知 phase 保守锁定。

import {
  deriveRiskUiState,
  resolvePublishChannel,
  toBotPublicRiskVisibility,
  type BotPublicRiskRecordLike,
} from '@/domain/collaborationPrivacy/botPublicRisk';

const rec = (overrides: Partial<BotPublicRiskRecordLike> = {}): BotPublicRiskRecordLike => ({
  risk_check_id: 'rk-1',
  phase: 'CHECKING',
  security_status: null,
  detail_url: null,
  ...overrides,
});

describe('deriveRiskUiState', () => {
  it('derives idle for null record', () => {
    expect(deriveRiskUiState(null)).toEqual({ kind: 'idle' });
  });

  it('derives checking for PREPARING/CHECKING without a conclusion', () => {
    expect(deriveRiskUiState(rec({ phase: 'PREPARING' })).kind).toBe('checking');
    expect(deriveRiskUiState(rec({})).kind).toBe('checking');
    expect(deriveRiskUiState(rec({ security_status: 'DETECTING' })).kind).toBe('checking');
  });

  it('derives advancing for ADVANCING and for PASS still in CHECKING', () => {
    expect(deriveRiskUiState(rec({ phase: 'ADVANCING' })).kind).toBe('advancing');
    expect(deriveRiskUiState(rec({ security_status: 'PASS' })).kind).toBe('advancing');
  });

  it('derives approving for APPROVING', () => {
    expect(deriveRiskUiState(rec({ phase: 'APPROVING' })).kind).toBe('approving');
  });

  it('derives failed for REJECT regardless of in-flight phase', () => {
    expect(deriveRiskUiState(rec({ security_status: 'REJECT' })).kind).toBe('failed');
  });

  it('derives attention for NEEDS_ATTENTION even with a REJECT conclusion hidden in it', () => {
    expect(deriveRiskUiState(rec({ phase: 'NEEDS_ATTENTION', security_status: 'REJECT' })).kind).toBe('attention');
  });

  it('treats CLOSED and PUBLISHED as no active application', () => {
    expect(deriveRiskUiState(rec({ phase: 'CLOSED' })).kind).toBe('idle');
    expect(deriveRiskUiState(rec({ phase: 'PUBLISHED' })).kind).toBe('idle');
  });

  it('falls back to conservative locking for unknown phases', () => {
    expect(deriveRiskUiState(rec({ phase: 'SOME_FUTURE_PHASE' })).kind).toBe('checking');
  });
});

describe('resolvePublishChannel', () => {
  const initial = { scope: 'none' as const, organizationPaths: [] };
  const current = { scope: 'all' as const, organizationPaths: [] };

  it('routes tightening (target none on a public state) to the direct channel', () => {
    expect(resolvePublishChannel(initial, current, false)).toBe('direct');
  });

  it('routes public-ward changes with an actual diff to the risk-check channel', () => {
    expect(resolvePublishChannel(current, initial, false)).toBe('risk-check');
    expect(
      resolvePublishChannel({ scope: 'restricted' as const, organizationPaths: [['集团', 'A部']] }, current, false),
    ).toBe('risk-check');
  });

  it('keeps external bots fully on the direct channel', () => {
    expect(resolvePublishChannel(current, initial, true)).toBe('direct');
  });

  it('returns none when nothing changed', () => {
    expect(resolvePublishChannel(current, current, false)).toBe('none');
    expect(resolvePublishChannel(initial, initial, false)).toBe('none');
  });
});

describe('toBotPublicRiskVisibility', () => {
  it('maps all to public with empty view_depts', () => {
    expect(toBotPublicRiskVisibility('all')).toEqual({ visibility: 'public', view_depts: [] });
  });

  it('maps restricted to protected with resolved dept entries', () => {
    expect(toBotPublicRiskVisibility('restricted', [{ deptNo: 'D1', deptName: '集团 / A部' }])).toEqual({
      visibility: 'protected',
      view_depts: [{ deptNo: 'D1', deptName: '集团 / A部' }],
    });
  });

  it('falls back to path-joined names when dept codes are missing', () => {
    expect(toBotPublicRiskVisibility('restricted', [{ deptNo: '', deptName: '集团 / A部' }])).toEqual({
      visibility: 'protected',
      view_depts: [{ deptNo: '集团 / A部', deptName: '集团 / A部' }],
    });
  });
});
