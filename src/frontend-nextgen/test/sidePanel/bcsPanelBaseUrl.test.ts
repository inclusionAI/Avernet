// @sdd: bcsPanelBaseUrl 注入纯逻辑单测——锁定 UmdPanel 对 BCS 状态机 panel 注入
// /api/v1/collaboration 取数基址的行为，避免远程 CDN UMD 回退 /bcnproxy 默认在部署态被 CORB 拦截。
import { BCS_STATE_MACHINE_API_BASE_URL } from '@/services/bcs/bcsManifestController';
import { isBcsStateMachinePanel, resolveBcsStateMachineApiBaseUrl } from '@/services/bcs/bcsPanelBaseUrl';

describe('isBcsStateMachinePanel', () => {
  it('libraryName=bcsPanel 命中', () => {
    expect(isBcsStateMachinePanel('bcsPanel', undefined)).toBe(true);
  });

  it('导出末段为 StateMachineRunView 命中（含点路径前缀）', () => {
    expect(isBcsStateMachinePanel(undefined, 'bcsPanel.StateMachineRunView')).toBe(true);
  });

  it('导出裸名 StateMachineRunView 命中', () => {
    expect(isBcsStateMachinePanel(undefined, 'StateMachineRunView')).toBe(true);
  });

  it('非 BCS panel 不命中', () => {
    expect(isBcsStateMachinePanel('asfui', 'RiskSummary')).toBe(false);
    expect(isBcsStateMachinePanel(undefined, undefined)).toBe(false);
  });
});

describe('resolveBcsStateMachineApiBaseUrl', () => {
  const EXPECTED = BCS_STATE_MACHINE_API_BASE_URL;

  it('BCS 状态机 panel 且未提供 base：注入 /api/v1/collaboration', () => {
    expect(resolveBcsStateMachineApiBaseUrl('bcsPanel', 'bcsPanel.StateMachineRunView', undefined, undefined)).toBe(
      EXPECTED,
    );
  });

  it('已显式提供 apiBaseUrl：不覆盖（返回 undefined）', () => {
    expect(
      resolveBcsStateMachineApiBaseUrl('bcsPanel', 'bcsPanel.StateMachineRunView', '/custom/api', undefined),
    ).toBeUndefined();
  });

  it('已显式提供 baseUrl（无 apiBaseUrl）：不干预，避免更高优先级的 apiBaseUrl 盖掉调用方意图', () => {
    expect(
      resolveBcsStateMachineApiBaseUrl('bcsPanel', 'bcsPanel.StateMachineRunView', undefined, '/custom/base'),
    ).toBeUndefined();
  });

  it('空串视为未提供（注入默认）', () => {
    expect(resolveBcsStateMachineApiBaseUrl('bcsPanel', 'bcsPanel.StateMachineRunView', '', '')).toBe(EXPECTED);
  });

  it('非 BCS panel：不注入（返回 undefined）', () => {
    expect(resolveBcsStateMachineApiBaseUrl('asfui', 'RiskSummary', undefined, undefined)).toBeUndefined();
  });

  it('注入值与 manifest 同源反代基址一致', () => {
    expect(EXPECTED).toBe('/api/v1/collaboration');
  });
});
