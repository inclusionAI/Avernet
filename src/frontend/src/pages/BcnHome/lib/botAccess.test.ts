import { getExt } from '@/capabilities';
import { AppExt } from '@/shell/extension';
import {
  DEFAULT_BOT_ACCESS_ENGINE,
  deriveHermesProfile,
  getBotAccessMethods,
  getVisibleBotAccessEngines,
  HERMES_MULTI_PROFILE_NOTICE,
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
  it('keeps the Hermes multi-profile notice explicit', () => {
    expect(HERMES_MULTI_PROFILE_NOTICE).toBe(
      '支持接入多个 Hermes Bot。Avernet 会根据 Bot 名称自动创建独立 Profile；相同名称将恢复原 Bot。',
    );
  });

  it('derives deterministic Hermes profiles from Bot names', () => {
    expect(deriveHermesProfile('Hermes Reviewer')).toBe(
      'avernet-hermes-reviewer',
    );
    expect(deriveHermesProfile('  Hermes   Reviewer  ')).toBe(
      'avernet-hermes-reviewer',
    );
    expect(deriveHermesProfile('Hermes Réviewer')).toBe(
      'avernet-hermes-reviewer',
    );
    expect(deriveHermesProfile('产品经理')).toBe('avernet-bot-397dc3e8');
    expect(deriveHermesProfile('a'.repeat(100))).toHaveLength(64);
    expect(deriveHermesProfile('')).toBe('');
  });

  it('validates Hermes Bot names', () => {
    expect(validateHermesBotConfig({ botName: '' })).toEqual({
      botNameError: '请输入 Bot 名称',
      valid: false,
    });
    expect(validateHermesBotConfig({ botName: 'Hermes Reviewer' })).toEqual({
      botNameError: null,
      valid: true,
    });
  });

  it('quotes Hermes configuration and renders it into the command', () => {
    expect(quoteShellArg("Hermes O'Brien")).toBe("'Hermes O'\\''Brien'");
    expect(
      renderBotAccessCommand(
        'run {token} --bot-name {bot_name} --profile {profile} --create-profile',
        'registration-token',
        { botName: "Hermes O'Brien" },
      ),
    ).toBe(
      "run registration-token --bot-name 'Hermes O'\\''Brien' --profile 'avernet-hermes-o-brien' --create-profile",
    );
    expect(
      renderBotAccessCommand(
        'automatic {token} bot={bot_name} profile={profile}',
        'registration-token',
        { botName: '产品经理' },
      ),
    ).toBe(
      "automatic registration-token bot='产品经理' profile='avernet-bot-397dc3e8'",
    );
    expect(
      renderBotAccessCommand('{token}:{token}', 'registration-token'),
    ).toBe('registration-token:registration-token');
  });

  it('refuses to render Hermes configuration without a Bot name', () => {
    expect(
      renderBotAccessCommand(
        'run {token} --bot-name {bot_name} --profile {profile}',
        'registration-token',
        { botName: '' },
      ),
    ).toBe('');
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
      template?.indexOf('curl --ipv4') ?? -1,
    );
    expect(template?.indexOf('curl --ipv4')).toBeLessThan(
      template?.indexOf("printf '%s\\n'") ?? -1,
    );

    const rendered = renderBotAccessCommand(
      template ?? '',
      'registration-token',
      { botName: 'Hermes Reviewer' },
    );
    const pipeline = rendered.split('|');
    const installerArgv = pipeline[pipeline.length - 1] ?? '';
    expect(installerArgv).not.toContain('registration-token');
    expect(installerArgv).not.toMatch(/(^|\s)--token(?:\s|$)/);
    expect(installerArgv).toContain("--bot-name 'Hermes Reviewer'");
    expect(installerArgv).toContain("--profile 'avernet-hermes-reviewer'");
    expect(installerArgv).toContain('--create-profile');
  });

  it('renders Bot name and Hermes Profile in the automatic instructions', () => {
    const template = getExt(AppExt).resources.bcnHermesAutoConnectCmdTemplate;
    expect(template).not.toBeNull();
    const rendered = renderBotAccessCommand(
      template ?? '',
      'registration-token',
      { botName: 'Hermes Reviewer' },
    );
    expect(rendered).toContain("--bot-name 'Hermes Reviewer'");
    expect(rendered).toContain("--profile 'avernet-hermes-reviewer'");
  });

  it('uses a resilient installer command for both Hermes access methods', () => {
    const manual = getExt(AppExt).resources.bcnHermesConnectCmdTemplate ?? '';
    const automatic =
      getExt(AppExt).resources.bcnHermesAutoConnectCmdTemplate ?? '';

    expect(manual).toContain('curl --ipv4');
    expect(manual).toContain('--retry 3');
    expect(manual).toContain('--retry-all-errors');
    expect(manual).toContain('--connect-timeout 10');
    expect(manual).toContain('--max-time 30');
    expect(manual).toContain('--china-mirror');
    expect(automatic).toContain('Execute this exact command');
    expect(automatic).toContain(manual);
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
    expect(template).toContain('install-hermes.sh?ref=dev');
  });
});
