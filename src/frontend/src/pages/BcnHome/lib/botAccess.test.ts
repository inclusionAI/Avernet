import { getExt } from '@/capabilities';
import { AppExt } from '@/shell/extension';
import {
  DEFAULT_BOT_ACCESS_ENGINE,
  getBotAccessMethods,
  getVisibleBotAccessEngines,
  quoteShellArg,
  renderBotAccessCommand,
  replaceBotAccessToken,
  validateHermesBotConfig,
} from './botAccess';

const resources = {
  bcnConnectCmdTemplate: 'openclaw install --token {token}',
  bcnAutoConnectCmdTemplate: 'Tell OpenClaw to use {token}',
  bcnHermesConnectCmdTemplate:
    "printf '%s\\n' '{token}' | bash install-hermes.sh --human-token-stdin",
  bcnHermesAutoConnectCmdTemplate: 'Tell Hermes to use {token}',
};

describe('bot access resources', () => {
  it('validates Hermes Bot names and profiles', () => {
    expect(validateHermesBotConfig({ botName: '', profile: '' })).toEqual({
      botNameError: '请输入 Bot 名称',
      profileError: '请输入 Profile 名称',
      valid: false,
    });
    expect(
      validateHermesBotConfig({
        botName: 'Hermes Reviewer',
        profile: 'review_bot-2',
      }),
    ).toEqual({ botNameError: null, profileError: null, valid: true });

    for (const profile of [
      'default',
      'hermes',
      'test',
      'tmp',
      'root',
      'sudo',
    ]) {
      expect(
        validateHermesBotConfig({ botName: 'Reviewer', profile }).valid,
      ).toBe(false);
    }
  });

  it('quotes Hermes configuration and renders it into the command', () => {
    expect(quoteShellArg("Hermes O'Brien")).toBe("'Hermes O'\\''Brien'");
    expect(
      renderBotAccessCommand(
        'run {token} --bot-name {bot_name} --profile {profile} --create-profile',
        'registration-token',
        { botName: "Hermes O'Brien", profile: 'review_bot-2' },
      ),
    ).toBe(
      "run registration-token --bot-name 'Hermes O'\\''Brien' --profile 'review_bot-2' --create-profile",
    );
  });

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

    const rendered = renderBotAccessCommand(
      template ?? '',
      'registration-token',
      { botName: 'Hermes Reviewer', profile: 'review_bot-2' },
    );
    const pipeline = rendered.split('|');
    const installerArgv = pipeline[pipeline.length - 1] ?? '';
    expect(installerArgv).not.toContain('registration-token');
    expect(installerArgv).not.toMatch(/(^|\s)--token(?:\s|$)/);
    expect(installerArgv).toContain("--bot-name 'Hermes Reviewer'");
    expect(installerArgv).toContain("--profile 'review_bot-2'");
    expect(installerArgv).toContain('--create-profile');
  });

  it('uses the same Git ref for the Hermes installer and connector', () => {
    const template = getExt(AppExt).resources.bcnHermesConnectCmdTemplate ?? '';
    const sourceRefs = [
      ...template.matchAll(
        /https:\/\/raw\.githubusercontent\.com\/inclusionAI\/Avernet\/(.+?)\/src\//g,
      ),
    ].map((match) => match[1]);

    expect(template).toContain('AVERNET_RAW_BASE_URL=');
    expect(template).toContain('BCS_INSTALLER_URL=');
    expect(sourceRefs).toHaveLength(2);
    expect(new Set(sourceRefs).size).toBe(1);
    expect(sourceRefs[0]).toBe('refs/heads/dev');
  });
});
