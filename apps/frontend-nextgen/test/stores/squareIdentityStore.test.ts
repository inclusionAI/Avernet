/** @jest-environment jsdom */
import { useSquareIdentityStore } from '@/stores/squareIdentityStore';

const STORAGE_KEY = 'teamclaw:square:identityId';

describe('squareIdentityStore', () => {
  beforeEach(() => {
    window.localStorage.clear();
    useSquareIdentityStore.getState().reset();
  });

  it('无持久化时初始为登录用户身份（null）', () => {
    expect(useSquareIdentityStore.getState().selectedIdentityId).toBeNull();
  });

  it('选择 Bot 身份后写入状态并持久化', () => {
    useSquareIdentityStore.getState().selectIdentity('bot-1:900003');

    expect(useSquareIdentityStore.getState().selectedIdentityId).toBe('bot-1:900003');
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe('bot-1:900003');
  });

  it('选择用户身份（null）后清除持久化', () => {
    useSquareIdentityStore.getState().selectIdentity('bot-1:900003');
    useSquareIdentityStore.getState().selectIdentity(null);

    expect(useSquareIdentityStore.getState().selectedIdentityId).toBeNull();
    expect(window.localStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it('reset 清除状态与持久化', () => {
    useSquareIdentityStore.getState().selectIdentity('bot-1:900003');
    useSquareIdentityStore.getState().reset();

    expect(useSquareIdentityStore.getState().selectedIdentityId).toBeNull();
    expect(window.localStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it('模块初始化时从 localStorage 恢复前序身份（刷新恢复语义）', () => {
    window.localStorage.setItem(STORAGE_KEY, 'bot-2:900003');

    let restored: string | null = null;
    jest.isolateModules(() => {
      const fresh = require('@/stores/squareIdentityStore') as typeof import('@/stores/squareIdentityStore');
      restored = fresh.useSquareIdentityStore.getState().selectedIdentityId;
    });

    expect(restored).toBe('bot-2:900003');
  });

  it('localStorage 读取异常时静默回退 null，不抛错', () => {
    const original = window.localStorage;
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      value: {
        getItem: () => {
          throw new Error('storage blocked');
        },
        setItem: () => {
          throw new Error('storage blocked');
        },
        removeItem: () => {
          throw new Error('storage blocked');
        },
        clear: () => {},
      },
    });

    let restored: unknown = 'unset';
    expect(() => {
      jest.isolateModules(() => {
        const fresh = require('@/stores/squareIdentityStore') as typeof import('@/stores/squareIdentityStore');
        restored = fresh.useSquareIdentityStore.getState().selectedIdentityId;
      });
    }).not.toThrow();
    expect(restored).toBeNull();

    Object.defineProperty(window, 'localStorage', { configurable: true, value: original });
  });

  it('localStorage 写入异常时状态仍生效，不抛错', () => {
    const original = window.localStorage;
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      value: {
        getItem: () => null,
        setItem: () => {
          throw new Error('storage blocked');
        },
        removeItem: () => {
          throw new Error('storage blocked');
        },
        clear: () => {},
      },
    });

    expect(() => {
      useSquareIdentityStore.getState().selectIdentity('bot-1:900003');
    }).not.toThrow();
    expect(useSquareIdentityStore.getState().selectedIdentityId).toBe('bot-1:900003');

    Object.defineProperty(window, 'localStorage', { configurable: true, value: original });
  });
});
