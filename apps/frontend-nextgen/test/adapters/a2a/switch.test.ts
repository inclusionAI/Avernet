import { isA2aClientEnabled, A2A_SWITCH_KEY } from '@/adapters/a2a/switch';

// jest 主配置 testEnvironment='node'（无 window/localStorage）——本测试组
// 用最小 stub 驱动 switch 的读取路径；产品码真实跑在浏览器。
const store = new Map<string, string>();
const localStorageStub = {
  getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
  setItem: (k: string, v: string) => { store.set(k, v); },
  clear: () => store.clear(),
};
(global as any).window = { localStorage: localStorageStub };

describe('a2a 客户端总开关（spec §1：默认 off，唯一读取入口）', () => {
  beforeEach(() => store.clear());

  it('默认 off：未设置且构建期默认未注入时 false', () => {
    expect(isA2aClientEnabled()).toBe(false);
  });

  it("localStorage = '1' 开（显式 opt-in 优先级最高）", () => {
    store.set(A2A_SWITCH_KEY, '1');
    expect(isA2aClientEnabled()).toBe(true);
  });

  it("localStorage = '0' 视为运行期显式关（现场回退通道）", () => {
    store.set(A2A_SWITCH_KEY, '0');
    expect(isA2aClientEnabled()).toBe(false);
  });

  it('localStorage 访问抛错时回落构建期默认而非武断全关', () => {
    localStorageStub.getItem = () => { throw new Error('denied'); };
    try {
      // jest 构建无 define 注入 → 构建期默认 undefined → off（不抛错上浮）
      expect(isA2aClientEnabled()).toBe(false);
    } finally {
      localStorageStub.getItem = (k: string) => (store.has(k) ? store.get(k)! : null);
    }
  });
});

// 构建期 define 注入面（镜像构建 A2A_CLIENT_DEFAULT=true / 主线 build 为 'false'）。
// jest 不走 define 注入，用 globalThis 直接模拟 define 常量的两类取值。
describe('a2a 开关构建期默认（define 注入面）', () => {
  const g = globalThis as any;
  beforeEach(() => { store.clear(); delete g.__A2A_CLIENT_DEFAULT__; });

  it("define='true' 且 localStorage 未设 → 开（验证镜像整包默认开）", () => {
    g.__A2A_CLIENT_DEFAULT__ = 'true';
    expect(isA2aClientEnabled()).toBe(true);
  });

  it("define='true' 但 localStorage='0' → 关（运行期显式关优先于构建期默认）", () => {
    g.__A2A_CLIENT_DEFAULT__ = 'true';
    store.set(A2A_SWITCH_KEY, '0');
    expect(isA2aClientEnabled()).toBe(false);
  });

  it("define='true' 且 localStorage='1' → 开（两级叠加仍开）", () => {
    g.__A2A_CLIENT_DEFAULT__ = 'true';
    store.set(A2A_SWITCH_KEY, '1');
    expect(isA2aClientEnabled()).toBe(true);
  });

  it("define='false'（主线构建语义）→ off", () => {
    g.__A2A_CLIENT_DEFAULT__ = 'false';
    expect(isA2aClientEnabled()).toBe(false);
  });

  it('define 未注入（non-define 环境）→ off', () => {
    expect(isA2aClientEnabled()).toBe(false);
  });
});

describe('构建期默认双形态判定（review P2：define 值类型统一）', () => {
  const cases: Array<[boolean | string | undefined, boolean]> = [
    [true, true],            // config/a2aBuildDefault 正路：布尔
    ['true', true],          // 防御：异构 define 管线的字符串形态
    [false, false],
    ['false', false],
    [undefined, false],
    ["'true'", false],       // 旧坏产物（带引号字面量）恒关——修复前陷阱
  ];
  it.each(cases)('__A2A_CLIENT_DEFAULT__=%p → %s', (injected, expected) => {
    (global as any).__A2A_CLIENT_DEFAULT__ = injected;
    try {
      expect(isA2aClientEnabled()).toBe(expected);
    } finally {
      delete (global as any).__A2A_CLIENT_DEFAULT__;
    }
  });
});
