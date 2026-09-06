import {
  canRenderComponent,
  clearCdnConfig,
  getLibraryCdn,
  hasLibraryCdn,
  storeManifestBundles,
} from '../registerBotCdnPanels';

describe('BCS manifest panel registration', () => {
  afterEach(() => clearCdnConfig());

  it('discovers both components from the shared bundle before opening-message resolution', () => {
    storeManifestBundles([
      { name: 'bcsPanel', url: '/assets/bcsPanel/index.umd.js' },
    ]);

    expect(getLibraryCdn('bcsPanel')).toBe(
      '/bcnproxy/assets/bcsPanel/index.umd.js',
    );
    expect(hasLibraryCdn('bcsPanel')).toBe(true);
    expect(canRenderComponent('bcsPanel.UndercoverGamePanel')).toBe(true);
    expect(canRenderComponent('bcsPanel.StateMachineRunView')).toBe(true);
  });
});
