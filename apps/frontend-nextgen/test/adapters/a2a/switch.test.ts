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

  it('默认 off：未设置时 false', () => {
    expect(isA2aClientEnabled()).toBe(false);
  });

  it("localStorage = '1' 才开（显式 opt-in，与 AICODING_RESUME 的 !== '0' 相反方向）", () => {
    store.set(A2A_SWITCH_KEY, '1');
    expect(isA2aClientEnabled()).toBe(true);
  });

  it('任何其他值视为 off', () => {
    store.set(A2A_SWITCH_KEY, '0');
    expect(isA2aClientEnabled()).toBe(false);
  });

  it('localStorage 访问抛错时按 off（不抛错上浮）', () => {
    localStorageStub.getItem = () => { throw new Error('denied'); };
    try {
      expect(isA2aClientEnabled()).toBe(false);
    } finally {
      localStorageStub.getItem = (k: string) => (store.has(k) ? store.get(k)! : null);
    }
  });
});