/** @jest-environment jsdom */
import type { BcsManifestBundle, BotRenderScreen } from '@/services/bcs/bcsManifestController';
import { clearDevPanelCdnFixture, injectDevPanelCdnFixture } from '@/services/bcs/devPanelCdnFixture';
import {
  clearBotCdnConfig,
  clearCdnConfig,
  clearManifestCdnConfig,
  getLibraryCdn,
  getLibraryNames,
  hasLibraryCdn,
  storeCdnConfigs,
  storeManifestBundles,
} from '@/services/bcs/libraryCdnInjector';

function botScreen(botId: string, name: string, url: string): BotRenderScreen {
  return { id: Math.random(), bot_id: botId, name, cdn_url: url } as BotRenderScreen;
}

function manifestBundle(name: string, url: string): BcsManifestBundle {
  return { name, url };
}

describe('libraryCdnInjector', () => {
  beforeEach(() => {
    clearCdnConfig();
    clearDevPanelCdnFixture();
  });

  test('storeManifestBundles 写 aixLibraryCdnMap + aixGetLibraryCdn', () => {
    storeManifestBundles([manifestBundle('asfui', 'https://cdn/asfui.js')]);
    expect(hasLibraryCdn('asfui')).toBe(true);
    expect(getLibraryCdn('asfui')).toBe('https://cdn/asfui.js');
    const w = window as unknown as {
      aixLibraryCdnMap: Map<string, string>;
      aixGetLibraryCdn: (n: string) => string | undefined;
    };
    expect(w.aixLibraryCdnMap.get('asfui')).toBe('https://cdn/asfui.js');
    expect(w.aixGetLibraryCdn('asfui')).toBe('https://cdn/asfui.js');
  });

  test('合并优先级：bot 配置覆盖同库名的 manifest 配置', () => {
    storeManifestBundles([manifestBundle('lib', 'https://cdn/manifest.js')]);
    storeCdnConfigs([botScreen('bot-1', 'lib', 'https://cdn/bot.js')]);
    expect(getLibraryCdn('lib')).toBe('https://cdn/bot.js');
  });

  test('合并优先级：不同 bot 与 manifest 共存，bot 不覆盖 manifest 未涉库', () => {
    storeManifestBundles([manifestBundle('onlyManifest', 'https://cdn/m.js')]);
    storeCdnConfigs([botScreen('bot-A', 'botLib', 'https://cdn/a.js')]);
    expect(getLibraryCdn('onlyManifest')).toBe('https://cdn/m.js');
    expect(getLibraryCdn('botLib')).toBe('https://cdn/a.js');
    expect(getLibraryNames().sort()).toEqual(['botLib', 'onlyManifest']);
  });

  test('clearBotCdnConfig(botId) 仅清该 bot，保留 manifest 与其他 bot', () => {
    storeManifestBundles([manifestBundle('manifestLib', 'https://cdn/m.js')]);
    storeCdnConfigs([botScreen('bot-1', 'b1', 'https://cdn/b1.js'), botScreen('bot-2', 'b2', 'https://cdn/b2.js')]);
    clearBotCdnConfig('bot-1');
    expect(hasLibraryCdn('b1')).toBe(false);
    expect(hasLibraryCdn('b2')).toBe(true);
    expect(hasLibraryCdn('manifestLib')).toBe(true);
  });

  test('clearManifestCdnConfig 清空 manifest 但保留 bot', () => {
    storeManifestBundles([manifestBundle('m', 'https://cdn/m.js')]);
    storeCdnConfigs([botScreen('bot-1', 'b', 'https://cdn/b.js')]);
    clearManifestCdnConfig();
    expect(hasLibraryCdn('m')).toBe(false);
    expect(hasLibraryCdn('b')).toBe(true);
  });

  test('跳过缺 name / url 的不完整条目', () => {
    storeManifestBundles([
      manifestBundle('', 'https://cdn/x.js'),
      { name: 'noUrl', url: '' },
      manifestBundle('good', 'https://cdn/good.js'),
    ]);
    expect(getLibraryNames()).toEqual(['good']);
  });

  test('bcsPanel 走 CDN 优先:manifest 返回则入 aixLibraryCdnMap,引擎加载远程 UMD', () => {
    storeManifestBundles([
      manifestBundle('bcsPanel', 'https://cdn/bcsPanel.js'),
      manifestBundle('asfui', 'https://cdn/asfui.js'),
    ]);
    expect(hasLibraryCdn('bcsPanel')).toBe(true);
    expect(getLibraryCdn('bcsPanel')).toBe('https://cdn/bcsPanel.js');
    expect(hasLibraryCdn('asfui')).toBe(true);
  });

  test('clearCdnConfig 清空后 window 映射为空（不留旧库）', () => {
    storeManifestBundles([manifestBundle('old', 'https://cdn/old.js')]);
    clearCdnConfig();
    const w = window as unknown as { aixLibraryCdnMap: Map<string, string> };
    expect(w.aixLibraryCdnMap.size).toBe(0);
  });
});

describe('devPanelCdnFixture', () => {
  beforeEach(() => {
    clearCdnConfig();
    clearDevPanelCdnFixture();
  });

  test('injectDevPanelCdnFixture 写 window.aixLibraryCdnMap + aixGetLibraryCdn', () => {
    const snap = injectDevPanelCdnFixture({ devLib: 'https://cdn/dev.js' });
    expect(snap.get('devLib')).toBe('https://cdn/dev.js');
    const w = window as unknown as { aixGetLibraryCdn: (n: string) => string | undefined };
    expect(w.aixGetLibraryCdn('devLib')).toBe('https://cdn/dev.js');
  });

  test('重复注入合并入既有 Map（渐进注入多库）', () => {
    injectDevPanelCdnFixture({ libA: 'https://cdn/a.js' });
    injectDevPanelCdnFixture({ libB: 'https://cdn/b.js' });
    const w = window as unknown as { aixLibraryCdnMap: Map<string, string> };
    expect(w.aixLibraryCdnMap.get('libA')).toBe('https://cdn/a.js');
    expect(w.aixLibraryCdnMap.get('libB')).toBe('https://cdn/b.js');
  });

  test('clearDevPanelCdnFixture 清空 window 侧映射', () => {
    injectDevPanelCdnFixture({ tmp: 'https://cdn/tmp.js' });
    clearDevPanelCdnFixture();
    const w = window as unknown as { aixLibraryCdnMap: Map<string, string> };
    expect(w.aixLibraryCdnMap.size).toBe(0);
  });
});
