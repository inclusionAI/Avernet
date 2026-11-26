/** @jest-environment jsdom */
import { getManifest, type BcsManifest } from '@/services/bcs/bcsManifestController';
import {
  clearCdnConfig,
  getLibraryCdn,
  queryAndRegisterManifestLibraryCdn,
  storeBotCdnConfigs,
  storeManifestBundles,
} from '@/services/bcs/libraryCdnInjector';
import { resolveBusinessEntry } from '@tc-chat/ui/es/MarkdownRender/plugins/components/businessUtils';

jest.mock('@/services/bcs/bcsManifestController', () => ({ getManifest: jest.fn() }));

const mockGetManifest = jest.mocked(getManifest);
const oldUrl = '/assets/state-machine-v1.umd.js';
const newUrl = '/assets/state-machine-v2.umd.js';
const manifest = (url: string): BcsManifest => ({
  schema_version: 1,
  env: 'test',
  bundles: [{ name: 'bcsPanel', url }],
});

function pendingManifest() {
  let resolve!: (value: BcsManifest) => void;
  const promise = new Promise<BcsManifest>((done) => { resolve = done; });
  mockGetManifest.mockReturnValueOnce(promise);
  return resolve;
}

beforeEach(() => {
  clearCdnConfig();
  mockGetManifest.mockReset();
});

test('刷新期间现有状态机副屏仍能通过 SDK 解析 CDN，成功后原子替换', async () => {
  storeManifestBundles(manifest(oldUrl).bundles);
  const resolve = pendingManifest();
  const refresh = queryAndRegisterManifestLibraryCdn();
  const entry = resolveBusinessEntry({
    component: 'bcsPanel.StateMachineRunView',
    businessData: { runId: 'r1' },
  });
  // 先完成请求再断言，避免回归失败时共享的 pending Promise 泄漏到下一用例。
  resolve(manifest(newUrl));
  await refresh;
  expect(entry.finalPayload.cdn).toBe(oldUrl);
  expect(getLibraryCdn('bcsPanel')).toBe(newUrl);
});

test('刷新失败保留上次成功映射，后续请求仍可重试', async () => {
  storeManifestBundles(manifest(oldUrl).bundles);
  mockGetManifest.mockRejectedValueOnce(new Error('network unavailable'));
  await expect(queryAndRegisterManifestLibraryCdn()).resolves.toBe(0);
  expect(getLibraryCdn('bcsPanel')).toBe(oldUrl);

  mockGetManifest.mockResolvedValueOnce(manifest(newUrl));
  await expect(queryAndRegisterManifestLibraryCdn()).resolves.toBe(1);
  expect(getLibraryCdn('bcsPanel')).toBe(newUrl);
});

test('成功的空清单会清除已移除的 manifest 库，不影响 Bot 配置', async () => {
  storeManifestBundles(manifest(oldUrl).bundles);
  storeBotCdnConfigs('bot1', [{ name: 'botPanel', url: '/assets/bot.umd.js' }]);
  mockGetManifest.mockResolvedValueOnce({ ...manifest(newUrl), bundles: [] });
  await queryAndRegisterManifestLibraryCdn();
  expect(getLibraryCdn('bcsPanel')).toBeUndefined();
  expect(getLibraryCdn('botPanel')).toBe('/assets/bot.umd.js');
});

test('并发刷新复用同一请求，成功后保留 Bot 配置的覆盖优先级', async () => {
  storeManifestBundles(manifest(oldUrl).bundles);
  storeBotCdnConfigs('bot1', [{ name: 'bcsPanel', url: '/assets/bot-override.umd.js' }]);
  const resolve = pendingManifest();
  const first = queryAndRegisterManifestLibraryCdn();
  const second = queryAndRegisterManifestLibraryCdn();
  expect(first).toBe(second);
  expect(mockGetManifest).toHaveBeenCalledTimes(1);
  resolve(manifest(newUrl));
  await first;
  expect(getLibraryCdn('bcsPanel')).toBe('/assets/bot-override.umd.js');
});

test('首次加载失败不注册虚构 CDN 地址', async () => {
  mockGetManifest.mockRejectedValueOnce(new Error('network unavailable'));
  await expect(queryAndRegisterManifestLibraryCdn()).resolves.toBe(0);
  expect(getLibraryCdn('bcsPanel')).toBeUndefined();
});
