// @sdd: UmdPanel 导出点路径重建单测——锁定引擎 resolveBusinessEntry entry 丢前缀缺陷的 teamclaw 侧修复。
//
// 引擎 @tc-chat/ui 为 ESM 产物，故本测试只覆盖导出路径与 viewer 解析纯逻辑
// （组件加载/注册的引擎交互由 /panel-self-test 自测页 + 预发联调端到端验证）。
import { resolveExportName } from '@/services/bcs/resolveUmdExport';
import { resolveUndercoverViewerActorId } from '@/services/bcs/resolveUndercoverViewerActorId';

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

describe('resolveUndercoverViewerActorId：每客户端 Human 身份注入', () => {
  it('只向卧底面板注入带 human 前缀的当前登录身份', () => {
    expect(resolveUndercoverViewerActorId('bcsPanel.UndercoverGamePanel', 'alice')).toBe('human_alice');
    expect(resolveUndercoverViewerActorId('UndercoverGamePanel', 'bob')).toBe('human_bob');
  });

  it('保留已经规范化的 actor_id，不重复添加前缀', () => {
    expect(resolveUndercoverViewerActorId('bcsPanel.UndercoverGamePanel', 'human_alice')).toBe('human_alice');
    expect(resolveUndercoverViewerActorId('bcsPanel.UndercoverGamePanel', 'human-alice')).toBe('human-alice');
  });

  it('身份尚未解析时不注入，等待后续重渲染', () => {
    for (const identityId of [undefined, null, '', '   ']) {
      expect(resolveUndercoverViewerActorId('bcsPanel.UndercoverGamePanel', identityId)).toBeUndefined();
    }
  });

  it('不向其他远程 UMD 或同名第三方组件暴露登录身份', () => {
    for (const exportName of ['bcsPanel.StateMachineRunView', 'RiskSummary', 'other.UndercoverGamePanel']) {
      expect(resolveUndercoverViewerActorId(exportName, 'alice')).toBeUndefined();
    }
  });
});
