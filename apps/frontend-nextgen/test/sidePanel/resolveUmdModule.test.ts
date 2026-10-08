// @sdd: UMD 模块导出智能解析单测——锁定"manifest 库名与 UMD 实际全局名不一致"兜底逻辑。
import { pickExport } from '@/services/bcs/resolveUmdModule';

describe('pickExport：库对象上取导出', () => {
  it('末段直取（named export 常见形态）：BcnPanelAsset.StateMachineRunView', () => {
    const lib = { StateMachineRunView: () => null, Foo: () => null } as unknown as Record<string, unknown>;
    expect(pickExport(lib, 'bcsPanel.StateMachineRunView')).toBeTruthy();
    expect(pickExport(lib, 'StateMachineRunView')).toBeTruthy();
  });

  it('点路径逐层取：bcsPanel.X.Y', () => {
    const lib = { bcsPanel: { Sub: { Deep: () => null } } } as unknown as Record<string, unknown>;
    expect(pickExport(lib, 'bcsPanel.Sub.Deep')).toBeTruthy();
  });

  it('library 为 null/undefined：返回 null', () => {
    expect(pickExport(null, 'X.Y')).toBeNull();
    expect(pickExport(undefined, 'X.Y')).toBeNull();
  });

  it('导出不存在：返回 null', () => {
    const lib = { Foo: () => null } as unknown as Record<string, unknown>;
    expect(pickExport(lib, 'bcsPanel.StateMachineRunView')).toBeNull();
  });

  it('对象型导出也命中（非函数组件，如 namespace）', () => {
    const lib = { Components: { DagView: () => null } } as unknown as Record<string, unknown>;
    expect(pickExport(lib, 'Components.DagView')).toBeTruthy();
  });
});
