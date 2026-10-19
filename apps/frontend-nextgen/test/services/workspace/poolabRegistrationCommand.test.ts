import {
  buildPoolabEnvironmentPrefix,
  buildPoolabInstallCommand,
  buildPoolabOneClickCommand,
  buildPoolabRegisterCommand,
  buildPoolabStartCommand,
  buildPoolabStatusCommand,
} from '@/services/workspace/poolabRegistrationCommand';

describe('buildPoolabEnvironmentPrefix', () => {
  it('只在 PRE 环境添加 SERVER_ENV 前缀，其他环境（含 PROD/LOCAL/DEV/未知）保持为空', () => {
    expect(buildPoolabEnvironmentPrefix({ environment: 'PRE' })).toBe('SERVER_ENV=pre ');
    expect(buildPoolabEnvironmentPrefix({ environment: 'PROD' })).toBe('');
    expect(buildPoolabEnvironmentPrefix({ environment: 'LOCAL' })).toBe('');
    expect(buildPoolabEnvironmentPrefix({ environment: 'DEV' })).toBe('');
    expect(buildPoolabEnvironmentPrefix({ environment: 'UNKNOWN' })).toBe('');
    expect(buildPoolabEnvironmentPrefix({ environment: '' })).toBe('');
  });
});

describe('buildPoolabInstallCommand / Start / Status', () => {
  it('返回固定命令字符串', () => {
    expect(buildPoolabInstallCommand()).toBe('acli install bcs-bridge');
    expect(buildPoolabStartCommand()).toBe('bcs-bridge start --daemon');
    expect(buildPoolabStatusCommand()).toBe('bcs-bridge status');
  });
});

describe('buildPoolabRegisterCommand', () => {
  const base = { token: 'test-token-abc', botName: 'AlphaBot', environment: 'PROD' };

  it('PROD 不添加 SERVER_ENV 前缀', () => {
    const command = buildPoolabRegisterCommand(base);
    expect(command).toBe('bcs-bridge register --token test-token-abc --bot-name AlphaBot');
    expect(command.startsWith('bcs-bridge register')).toBe(true);
    expect(command).not.toContain('SERVER_ENV');
  });

  it('PRE 自动添加 SERVER_ENV=pre 前缀', () => {
    const command = buildPoolabRegisterCommand({ ...base, environment: 'PRE' });
    expect(command.startsWith('SERVER_ENV=pre bcs-bridge register')).toBe(true);
    expect(command).toContain('--bot-name AlphaBot');
  });

  it('token 或 botName 为空时返回空串（由 UI 拦截复制）', () => {
    expect(buildPoolabRegisterCommand({ ...base, token: '' })).toBe('');
    expect(buildPoolabRegisterCommand({ ...base, token: '  ' })).toBe('');
    expect(buildPoolabRegisterCommand({ ...base, botName: '' })).toBe('');
    expect(buildPoolabRegisterCommand({ ...base, botName: ' ' })).toBe('');
  });

  it('非法 botName（过长 / 过短）返回空串', () => {
    expect(buildPoolabRegisterCommand({ ...base, botName: 'a' })).toBe('');
    expect(buildPoolabRegisterCommand({ ...base, botName: 'x'.repeat(65) })).toBe('');
  });

  it('token 与 botName 经 quoteShellArg 转义，单引号/美元符不会注入', () => {
    const command = buildPoolabRegisterCommand({
      ...base,
      token: "tok'en$(printf INJ)",
      botName: 'Bot With Space',
    });
    // quoteShellArg 用单引号包裹并对内部单引号特殊转义
    expect(command).toContain('--token');
    expect(command).toContain('--bot-name');
    // 不应直接拼接原始 token
    expect(command).not.toContain("--token tok'en$(printf INJ) ");
    // 'Bot With Space' 含空格，必然被单引号包裹
    expect(command).toContain("'Bot With Space'");
  });

  it('中文 botName 被 quoteShellArg 单引号包裹', () => {
    const command = buildPoolabRegisterCommand({
      ...base,
      botName: '元歌的Poolab',
    });
    expect(command).toContain("--bot-name '元歌的Poolab'");
  });
});

describe('buildPoolabOneClickCommand', () => {
  const base = {
    token: 'test-token-abc',
    botName: 'AlphaBot',
    environment: 'PROD',
    scriptUrl: 'http://example.com/bcn/dist/poolab-onboard.sh',
  };

  it('PROD 不添加 SERVER_ENV 前缀', () => {
    const command = buildPoolabOneClickCommand(base);
    expect(command.startsWith('bash -c')).toBe(true);
    expect(command).not.toContain('SERVER_ENV');
  });

  it('PRE 添加 SERVER_ENV=pre 前缀', () => {
    const command = buildPoolabOneClickCommand({ ...base, environment: 'PRE' });
    expect(command.startsWith('SERVER_ENV=pre bash -c')).toBe(true);
  });

  it('包含 poolab-onboard.sh 调用与 --token/--name 参数', () => {
    const command = buildPoolabOneClickCommand(base);
    expect(command).toContain('poolab-onboard.sh');
    expect(command).toContain(' --token test-token-abc ');
    expect(command).toContain(' --name AlphaBot');
  });

  it('curl 失败时通过 echo exit 1 退出', () => {
    const command = buildPoolabOneClickCommand(base);
    expect(command).toContain("|| echo 'exit 1'");
  });

  it('scriptUrl 为空 / token 为空 / botName 无效时返回空串', () => {
    expect(buildPoolabOneClickCommand({ ...base, scriptUrl: '' })).toBe('');
    expect(buildPoolabOneClickCommand({ ...base, scriptUrl: '  ' })).toBe('');
    expect(buildPoolabOneClickCommand({ ...base, token: '' })).toBe('');
    expect(buildPoolabOneClickCommand({ ...base, botName: 'a' })).toBe('');
    expect(buildPoolabOneClickCommand({ ...base, botName: 'x'.repeat(65) })).toBe('');
  });

  it('scriptUrl、token、botName 中的特殊字符经 quoteShellArg 转义', () => {
    const command = buildPoolabOneClickCommand({
      ...base,
      token: "tok'en",
      botName: 'Bot With Space',
      scriptUrl: 'https://example.com/script with space.sh',
    });
    expect(command).toContain('--token');
    expect(command).toContain('--name');
    // 不应直接拼接原始 botName（含空格必被引号包裹）
    expect(command).toContain("'Bot With Space'");
    expect(command).toContain("'https://example.com/script with space.sh'");
  });
});
