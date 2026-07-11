import {
  DEFAULT_BOT_ACCESS_ENGINE,
  getBotAccessMethods,
  getVisibleBotAccessEngines,
  replaceBotAccessToken,
} from './botAccess';

const resources = {
  bcnConnectCmdTemplate: 'openclaw install --token {token}',
  bcnAutoConnectCmdTemplate: 'Tell OpenClaw to use {token}',
  bcnHermesConnectCmdTemplate: 'hermes install --token {token}',
  bcnHermesAutoConnectCmdTemplate: 'Tell Hermes to use {token}',
};

describe('bot access resources', () => {
  it('keeps OpenClaw selected by default', () => {
    expect(DEFAULT_BOT_ACCESS_ENGINE).toBe('openclaw');
    expect(getVisibleBotAccessEngines(resources)).toEqual([
      { id: 'openclaw', label: 'OpenClaw' },
      { id: 'hermes', label: 'Hermes' },
    ]);
  });

  it('selects Hermes manual and automatic templates and replaces tokens', () => {
    const methods = getBotAccessMethods(resources, 'hermes');

    expect(methods.map(({ id, template }) => ({ id, template }))).toEqual([
      { id: 'manual', template: 'hermes install --token {token}' },
      { id: 'automatic', template: 'Tell Hermes to use {token}' },
    ]);
    expect(replaceBotAccessToken(methods[0].template, 'registration-token')).toBe(
      'hermes install --token registration-token',
    );
  });

  it('hides an engine when both of its templates are unavailable', () => {
    expect(
      getVisibleBotAccessEngines({
        ...resources,
        bcnHermesConnectCmdTemplate: null,
        bcnHermesAutoConnectCmdTemplate: null,
      }),
    ).toEqual([{ id: 'openclaw', label: 'OpenClaw' }]);
  });
});
