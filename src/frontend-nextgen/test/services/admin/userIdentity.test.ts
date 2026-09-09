/** @jest-environment node */
import { getCapabilities } from '@/capabilities';
import { ensureUserId, ensureUserName, readUserId, readUserName } from '@/services/admin/userIdentity';
import { identityService } from '@/services/workspace/identityService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';

// auto-mock identityService，避免真实 bots/mine；stub @tc-chat/adapters ESM transitive
// （identityService→testUser→supportProvider）。
jest.mock('@/services/workspace/identityService');
jest.mock('@tc-chat/adapters', () => ({}));
// 数据源已改为 getCapabilities().getHumanIdentity()；auto-mock 出可控能力访问器（避免在 hoisted factory 内引用 jest）。
jest.mock('@/capabilities');

const getCapabilitiesMock = getCapabilities as unknown as jest.Mock;
const loadIdentities = identityService.loadIdentities as unknown as jest.Mock<any>;

type HumanIdentityValue = { userId: string; displayName: string } | null;

/** 镜像 resolveOpenApiUserId：'human_327325'/'human:327325'/'me' → '327325'/'327325'/'me'。 */
function humanIdOf(id: string): string {
  const colon = id.indexOf(':');
  const afterColon = colon >= 0 ? id.slice(colon + 1) : id;
  return afterColon.replace(/^human_/, '');
}

/** 动态能力：每次调用读 workspaceStore.identities，镜像 Open Core 默认 getHumanIdentity 的 me 解析
 *  （测试态不注入 externalAuthStore，等价只看 me）。用于 ensureUserId/ensureUserName 的 loadIdentities 兜底路径。 */
function setHumanIdentityDynamic(): void {
  getCapabilitiesMock.mockImplementation(() => ({
    getHumanIdentity: () => {
      const { identities } = useWorkspaceStore.getState();
      const me = identities.find((i) => i.kind === 'user') ?? identities[0] ?? null;
      const value = me ? { userId: humanIdOf(me.id), displayName: me.displayName } : null;
      return { status: 'available', value };
    },
  }));
}

/** 固定能力：恒返回给定身份（null = 未就绪）。用于命中 / 冲突覆盖 / 内部 staffNo 同步等单测。 */
function setHumanIdentity(value: HumanIdentityValue): void {
  getCapabilitiesMock.mockReturnValue({
    getHumanIdentity: () => ({ status: 'available', value }),
  });
}

beforeEach(() => {
  jest.resetAllMocks();
  useWorkspaceStore.setState({ activeIdentityId: null, identities: [] });
  setHumanIdentityDynamic(); // 默认动态（与 Open Core 默认契约一致；单测可覆盖为固定）
});

describe('readUserId', () => {
  it('经 canonical getHumanIdentity 能力得当前操作者 user_id', () => {
    setHumanIdentity({ userId: '327325', displayName: '风太' });
    expect(readUserId()).toBe('327325');
  });

  it('即便 activeIdentityId 存在也忽略——以 capability 为唯一源（内部 staffNo 覆盖 BCS-id 形 store 值）', () => {
    // 模拟内部部署：store 的 activeIdentityId 为 BCS-id 形（human_<BCS>），但能力返回 staffNo
    useWorkspaceStore.setState({ activeIdentityId: 'human_gYJSGDajzYPV', identities: [] });
    setHumanIdentity({ userId: '327325', displayName: '风太' });
    expect(readUserId()).toBe('327325');
  });

  it('阿里云 BCS id 经 resolveUserId 幂等透传', () => {
    setHumanIdentity({ userId: 'gYJSGDajzYPV', displayName: '廖胜平' });
    expect(readUserId()).toBe('gYJSGDajzYPV');
  });

  it('capability 偶尔下发带前缀 id 时 resolveUserId 仍剥前缀兜底', () => {
    setHumanIdentity({ userId: 'human_327325', displayName: '风太' });
    expect(readUserId()).toBe('327325');
  });

  it('capability 未就绪返回 null（不读 activeIdentityId）', () => {
    useWorkspaceStore.setState({ activeIdentityId: 'human_327325' });
    setHumanIdentity(null);
    expect(readUserId()).toBeNull();
  });
});

describe('ensureUserId', () => {
  it('能力命中直接返回，不调 loadIdentities（内部 staffNo 同步命中）', async () => {
    setHumanIdentity({ userId: '327325', displayName: '风太' });
    const id = await ensureUserId();
    expect(id).toBe('327325');
    expect(loadIdentities).not.toHaveBeenCalled();
  });

  it('能力未就绪 + 补拉成功 → 写回 store 并返回 me 派生 user_id（Open Core first paint，BCS id）', async () => {
    setHumanIdentityDynamic(); // identities 空 → null → 触发兜底；setIdentities 后再读得 me
    loadIdentities.mockResolvedValue({
      ok: true,
      data: {
        identities: [{ id: 'human_gYJSGDajzYPV', kind: 'user', displayName: '廖胜平', online: true }],
        defaultActiveId: 'human_gYJSGDajzYPV',
      },
    });
    const id = await ensureUserId();
    expect(loadIdentities).toHaveBeenCalledTimes(1);
    expect(id).toBe('gYJSGDajzYPV'); // BCS id（open-core 网关签名主体空间）
    expect(useWorkspaceStore.getState().activeIdentityId).toBe('human_gYJSGDajzYPV');
  });

  it('补拉失败 → 返回 null', async () => {
    setHumanIdentity(null);
    loadIdentities.mockResolvedValue({ ok: false, error: { code: 'IDENTITY_LOAD_FAILED' } });
    expect(await ensureUserId()).toBeNull();
  });

  it('补拉成功但 me 为占位（无 human 结构）→ 返回 me 派生（不退化）', async () => {
    setHumanIdentityDynamic();
    loadIdentities.mockResolvedValue({
      ok: true,
      data: {
        identities: [{ id: 'me', kind: 'user', displayName: '我', online: true }],
        defaultActiveId: 'me',
      },
    });
    expect(await ensureUserId()).toBe('me');
  });
});

describe('readUserName', () => {
  it('固定形态：返回能力内 displayName（内部 __TERN__ 花名）', () => {
    setHumanIdentity({ userId: '327325', displayName: '风太' });
    expect(readUserName()).toBe('风太');
  });

  it('动态形态：从 identities 解析当前 human 花名', () => {
    setHumanIdentityDynamic();
    useWorkspaceStore.setState({
      activeIdentityId: 'human_327325',
      identities: [{ id: 'human_327325', kind: 'user', displayName: '风太', online: true }],
    });
    expect(readUserName()).toBe('风太');
  });

  it('identities 未就绪返回 null', () => {
    setHumanIdentityDynamic();
    expect(readUserName()).toBeNull();
  });
});

describe('ensureUserName', () => {
  it('能力命中直接返回花名，不调 loadIdentities', async () => {
    setHumanIdentity({ userId: '327325', displayName: '风太' });
    expect(await ensureUserName()).toBe('风太');
    expect(loadIdentities).not.toHaveBeenCalled();
  });

  it('能力未就绪 + 补拉成功 → 写回 store 并返回花名', async () => {
    setHumanIdentityDynamic();
    loadIdentities.mockResolvedValue({
      ok: true,
      data: {
        identities: [{ id: 'human_327325', kind: 'user', displayName: '风太', online: true }],
        defaultActiveId: 'human_327325',
      },
    });
    expect(await ensureUserName()).toBe('风太');
    expect(loadIdentities).toHaveBeenCalledTimes(1);
    expect(useWorkspaceStore.getState().activeIdentityId).toBe('human_327325');
  });

  it('补拉失败 → 返回 null', async () => {
    setHumanIdentityDynamic();
    loadIdentities.mockResolvedValue({ ok: false, error: { code: 'IDENTITY_LOAD_FAILED' } });
    expect(await ensureUserName()).toBeNull();
  });
});
