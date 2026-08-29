/** @jest-environment node */
import { ensureUserId, ensureUserName, readUserId, readUserName } from '@/services/admin/userIdentity';
import { identityService } from '@/services/workspace/identityService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';

// auto-mock identityService，避免真实 bots/mine；stub @tc-chat/adapters ESM transitive
// （identityService→testUser→supportProvider）。
jest.mock('@/services/workspace/identityService');
jest.mock('@tc-chat/adapters', () => ({}));

const loadIdentities = identityService.loadIdentities as unknown as jest.Mock<any>;

beforeEach(() => {
  jest.resetAllMocks();
  useWorkspaceStore.setState({ activeIdentityId: null, identities: [] });
});

describe('readUserId', () => {
  it('human_ 前缀剥后得工号', () => {
    useWorkspaceStore.setState({ activeIdentityId: 'human_327325' });
    expect(readUserId()).toBe('327325');
  });

  it('复合 id 取冒号后段（兼容 {head}:{staffNo}）', () => {
    useWorkspaceStore.setState({ activeIdentityId: 'human:327325' });
    expect(readUserId()).toBe('327325');
  });

  it('未就绪返回 null', () => {
    expect(readUserId()).toBeNull();
  });
});

describe('ensureUserId', () => {
  it('缓存命中直接返回，不调 loadIdentities', async () => {
    useWorkspaceStore.setState({ activeIdentityId: 'human_327325' });
    const id = await ensureUserId();
    expect(id).toBe('327325');
    expect(loadIdentities).not.toHaveBeenCalled();
  });

  it('缓存未命中 + 补拉成功 → 写回 store 并返回工号', async () => {
    loadIdentities.mockResolvedValue({
      ok: true,
      data: {
        identities: [{ id: 'human_327325', kind: 'user', displayName: '风太', online: true }],
        defaultActiveId: 'human_327325',
      },
    });
    const id = await ensureUserId();
    expect(loadIdentities).toHaveBeenCalledTimes(1);
    expect(id).toBe('327325');
    expect(useWorkspaceStore.getState().activeIdentityId).toBe('human_327325');
  });

  it('补拉失败 → 返回 null', async () => {
    loadIdentities.mockResolvedValue({ ok: false, error: { code: 'IDENTITY_LOAD_FAILED' } });
    const id = await ensureUserId();
    expect(id).toBeNull();
  });

  it('补拉成功但 mine 无 human → fallback me，返回 me（不退化）', async () => {
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
  it('从 identities 解析当前 human 花名', () => {
    useWorkspaceStore.setState({
      activeIdentityId: 'human_327325',
      identities: [{ id: 'human_327325', kind: 'user', displayName: '风太', online: true }],
    });
    expect(readUserName()).toBe('风太');
  });

  it('identities 未就绪返回 null', () => {
    useWorkspaceStore.setState({ activeIdentityId: null, identities: [] });
    expect(readUserName()).toBeNull();
  });
});

describe('ensureUserName', () => {
  it('缓存命中直接返回花名，不调 loadIdentities', async () => {
    useWorkspaceStore.setState({
      activeIdentityId: 'human_327325',
      identities: [{ id: 'human_327325', kind: 'user', displayName: '风太', online: true }],
    });
    expect(await ensureUserName()).toBe('风太');
    expect(loadIdentities).not.toHaveBeenCalled();
  });

  it('缓存未命中 + 补拉成功 → 写回 store 并返回花名', async () => {
    useWorkspaceStore.setState({ activeIdentityId: null, identities: [] });
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
    useWorkspaceStore.setState({ activeIdentityId: null, identities: [] });
    loadIdentities.mockResolvedValue({ ok: false, error: { code: 'IDENTITY_LOAD_FAILED' } });
    expect(await ensureUserName()).toBeNull();
  });
});
