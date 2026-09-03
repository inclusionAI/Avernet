import {
  canRenderComponent,
  clearCdnConfig,
  getLibraryCdn,
  hasLibraryCdn,
  storeManifestBundles,
} from '../registerBotCdnPanels';

describe('BCS manifest panel registration', () => {
  afterEach(() => clearCdnConfig());

  it('discovers the undercover game bundle before opening-message resolution', () => {
    storeManifestBundles([
      { name: 'bcsPanel', url: '/assets/bcsPanel/index.umd.js' },
      { name: 'undercoverGame', url: '/assets/undercoverGame/index.umd.js' },
    ]);

    expect(getLibraryCdn('undercoverGame')).toBe(
      '/bcnproxy/assets/undercoverGame/index.umd.js',
    );
    expect(hasLibraryCdn('undercoverGame')).toBe(true);
    expect(canRenderComponent('undercoverGame.UndercoverGamePanel')).toBe(true);
    expect(canRenderComponent('bcsPanel.StateMachineRunView')).toBe(true);
  });
});
