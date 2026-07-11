import { getExt } from '@/capabilities';
import { AppExt } from '@/shell/extension';
import {
  DEFAULT_BOT_ACCESS_ENGINE,
  getBotAccessMethods,
  getVisibleBotAccessEngines,
  replaceBotAccessToken,
} from './botAccess';

const resources = {
  bcnConnectCmdTemplate: 'openclaw install --token {token}',
  bcnAutoConnectCmdTemplate: 'Tell OpenClaw to use {token}',
  bcnHermesConnectCmdTemplate:
    "printf '%s\\n' '{token}' | bash install-hermes.sh --human-token-stdin",
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
      {
        id: 'manual',
        template:
          "printf '%s\\n' '{token}' | bash install-hermes.sh --human-token-stdin",
      },
      { id: 'automatic', template: 'Tell Hermes to use {token}' },
    ]);
    expect(
      replaceBotAccessToken(methods[0].template, 'registration-token'),
    ).toBe(
      "printf '%s\\n' 'registration-token' | bash install-hermes.sh --human-token-stdin",
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

  it('keeps the Hermes token out of installer argv', () => {
    const template = getExt(AppExt).resources.bcnHermesConnectCmdTemplate;
    expect(template).not.toBeNull();
    expect(template).not.toMatch(/(^|\s)--token(?:\s|$)/);
    expect(template).toContain('--human-token-stdin');
    expect(template).toContain(
      'mktemp "${TMPDIR:-/tmp}/install-hermes.XXXXXX"',
    );
    expect(template).toContain('trap \'rm -f "$installer"\' EXIT');
    expect(template).not.toContain('/tmp/install-hermes.sh');
    expect(template?.trim().startsWith('(')).toBe(true);
    expect(template?.trim().endsWith(')')).toBe(true);
    expect(template?.indexOf('mktemp ')).toBeLessThan(
      template?.indexOf("trap 'rm -f") ?? -1,
    );
    expect(template?.indexOf("trap 'rm -f")).toBeLessThan(
      template?.indexOf('curl -fsSL') ?? -1,
    );
    expect(template?.indexOf('curl -fsSL')).toBeLessThan(
      template?.indexOf("printf '%s\\n'") ?? -1,
    );

    const rendered = replaceBotAccessToken(
      template ?? '',
      'registration-token',
    );
    const pipeline = rendered.split('|');
    const installerArgv = pipeline[pipeline.length - 1] ?? '';
    expect(installerArgv).not.toContain('registration-token');
    expect(installerArgv).not.toMatch(/(^|\s)--token(?:\s|$)/);
  });
});
