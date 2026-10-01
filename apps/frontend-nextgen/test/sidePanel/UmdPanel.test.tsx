// @sdd: UmdPanel 导出点路径重建单测——锁定引擎 resolveBusinessEntry entry 丢前缀缺陷的 teamclaw 侧修复。
//
// 引擎 @tc-chat/ui 为 ESM 产物，jest 不转换 node_modules，故本测试只覆盖纯逻辑 resolveExportName
// （组件加载/注册的引擎交互由 /panel-self-test 自测页 + 预发联调端到端验证）。
import { resolveExportName } from '@/services/bcs/resolveUmdExport';

describe('resolveExportName：UMD 导出点路径重建（修复 entry 丢 libraryName 前缀）', () => {
  it('声明式 component=lib.Comp：按 _componentKey 重建点路径', () => {
    // 模拟引擎 resolveBusinessEntry finalPayload：entry=componentName（丢前缀），_componentKey 完整。
    const params = {
      cdn: 'https://gw.alipayobjects.com/x/index.umd.js',
      entry: 'StateMachineRunView',
      _libraryName: 'bcsPanel',
      _componentKey: 'bcsPanel.StateMachineRunView',
      data: { runId: 'r1' },
    };
    expect(resolveExportName(params)).toBe('bcsPanel.StateMachineRunView');
  });

  it('命令式 _libraryName+entry 无 _componentKey：拼接前缀', () => {
    const params = { cdn: 'https://x/index.umd.js', entry: 'StateMachineRunView', _libraryName: 'bcsPanel' };
    expect(resolveExportName(params)).toBe('bcsPanel.StateMachineRunView');
  });

  it('命令式 entry 已是完整点路径（无 _libraryName）：原样使用', () => {
    const params = { cdn: 'https://x/index.umd.js', entry: 'bcsPanel.StateMachineRunView' };
    expect(resolveExportName(params)).toBe('bcsPanel.StateMachineRunView');
  });

  it('entry 与 libraryName 相同：不拼接成 lib.lib', () => {
    const params = { cdn: 'https://x/index.umd.js', entry: 'asfui', _libraryName: 'asfui' };
    expect(resolveExportName(params)).toBe('asfui');
  });

  it('无 entry 仅 _libraryName：用库名作默认导出', () => {
    const params = { cdn: 'https://x/index.umd.js', _libraryName: 'asfui' };
    expect(resolveExportName(params)).toBe('asfui');
  });

  it('仅有 entry（无库上下文）：原样使用', () => {
    const params = { cdn: 'https://x/index.umd.js', entry: 'RiskSummary' };
    expect(resolveExportName(params)).toBe('RiskSummary');
  });

  it('无任何线索：降级 default', () => {
    expect(resolveExportName({ cdn: 'https://x/index.umd.js' })).toBe('default');
  });

  it('_componentKey 优先级最高（即使 entry 也给完整点路径）', () => {
    const params = {
      cdn: 'https://x/index.umd.js',
      entry: 'bcsPanel.StateMachineRunView',
      _libraryName: 'bcsPanel',
      _componentKey: 'bcsPanel.StateMachineRunView',
    };
    expect(resolveExportName(params)).toBe('bcsPanel.StateMachineRunView');
  });
});
