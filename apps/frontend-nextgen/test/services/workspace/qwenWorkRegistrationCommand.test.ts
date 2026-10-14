import {
  buildBridgeDownloadCommand,
  buildBridgeInstallCommand,
  buildQwenWorkRegistrationCommand,
  buildQwenWorkOnboardingCommand,
  buildQwenWorkInitCommand,
  buildQwenWorkStartCommand,
  QWEN_INIT_COMMAND,
  QWEN_START_COMMAND,
} from '@/services/workspace/qwenWorkRegistrationCommand';
import { execFileSync, spawnSync } from 'node:child_process';

it('quotes user input literally and registers without prematurely starting an unconfigured engine', () => {
  const command = buildQwenWorkRegistrationCommand({
    token: "token'$(printf INJECTED)",
    botName: 'Qwen "test"',
    apiUrl: 'https://api.example.com/bcs',
    bcsEndpoint: 'https://bcs.example.com',
  });
  expect(command).toContain('--engine qwenwork');
  expect(command).toContain('--provider-auth static-bearer');
  expect(command).toContain('--upstream-url wss://bcs.example.com/ws/bot');
  expect(command).not.toContain('--cwd');
  expect(command).not.toContain('--provider-bot-ref');
  expect(command).not.toContain('openssl');
  expect(command).not.toContain('start --daemon');
  expect(spawnSync('sh', ['-n'], { input: command }).status).toBe(0);
  const capture = command.replace(
    '"$HOME/.local/bin/bcs-bridge"',
    `node -e 'process.stdout.write(JSON.stringify(process.argv.slice(1)))' --`,
  );
  const args = JSON.parse(execFileSync('sh', ['-c', capture], { encoding: 'utf8' }));
  expect(args[args.indexOf('--token') + 1]).toBe("token'$(printf INJECTED)");
  expect(args[args.indexOf('--bot-name') + 1]).toBe('Qwen "test"');
  expect(command).toContain("'token'\"'\"'$(printf INJECTED)'");
});
it('rejects invalid endpoints and Bot names', () => {
  const input = {
    token: 'token',
    botName: 'Qwen',
    apiUrl: 'https://api.example.com',
    bcsEndpoint: 'https://bcs.example.com',
  };
  expect(buildQwenWorkRegistrationCommand({ ...input, botName: ' ' })).toBe('');
  expect(buildQwenWorkRegistrationCommand({ ...input, bcsEndpoint: 'not a url' })).toBe('');
});
it('downloads separately and checks checksum before executing the downloaded installer', () => {
  const command = buildBridgeInstallCommand({
    version: '0.1.4-beta.1',
    url: 'https://example.com/release',
    installerUrl: 'https://example.com/installer',
    checksumUrl: 'https://example.com/sha256.sum',
  });
  expect(command.indexOf('shasum -a 256 -c')).toBeLessThan(command.indexOf('\nsh '));
  expect(spawnSync('sh', ['-n'], { input: command }).status).toBe(0);
  const download = buildBridgeDownloadCommand({
    version: '0.1.4-beta.1',
    url: 'https://example.com/release',
    installerUrl: 'https://example.com/installer',
    checksumUrl: 'https://example.com/sha256.sum',
  });
  expect(download).toContain('curl --fail --location https://example.com/installer');
  expect(download).not.toContain('\nsh ');
  expect(spawnSync('sh', ['-n'], { input: download }).status).toBe(0);
  expect(QWEN_INIT_COMMAND).toContain('init --engine qwenwork');
  expect(QWEN_INIT_COMMAND).not.toContain('secret');
});

it.each(['https://bcs.example.com/bcn', 'https://bcs.example.com/bcn/'])(
  'preserves the reverse proxy prefix %s',
  (endpoint) => {
    expect(
      buildQwenWorkRegistrationCommand({
        token: 'token',
        botName: 'Qwen',
        apiUrl: endpoint,
        bcsEndpoint: endpoint,
      }),
    ).toContain('--upstream-url wss://bcs.example.com/bcn/ws/bot');
  },
);

it('does not probe a fixed port when initialization may choose a different listener', () => {
  expect(QWEN_START_COMMAND).toContain('status');
  expect(QWEN_START_COMMAND).not.toContain('curl');
  expect(QWEN_START_COMMAND).not.toContain('52491');
});


it('selects a named profile consistently and stops the active instance before switching', () => {
  const input = { token: 'token', botName: 'Bot', apiUrl: 'https://api.example.com', bcsEndpoint: 'https://bcs.example.com', binary: 'bcs-bridge', profile: 'pre' };
  const command = buildQwenWorkRegistrationCommand(input);
  expect(command).toContain('bcs-bridge --profile pre register');
  expect(command).not.toContain('--config');
  expect(buildQwenWorkInitCommand('bcs-bridge', 'pre')).toContain('--profile pre init --engine qwenwork');
  expect(buildQwenWorkStartCommand('bcs-bridge', 'pre')).toContain('bcs-bridge stop &&\nbcs-bridge --profile pre start --daemon');
  for (const profile of ['../pre', 'a b', '/tmp', '']) {
    expect(buildQwenWorkRegistrationCommand({ ...input, profile })).toBe('');
  }
});

const onboardingInput = {
  token: 'test-token', botName: 'Qwen', apiUrl: 'https://api.example.com',
  bcsEndpoint: 'https://bcs.example.com', binary: 'bridge_stub', profile: 'pre',
};

function runOnboarding(failure = '') {
  const command = buildQwenWorkOnboardingCommand(onboardingInput);
  const stub = `bridge_stub() {
    if [ "$1" = "--profile" ]; then shift 2; fi
    stage="$1"
    if [ "$stage" = "init" ] && [ "$2" = "--help" ]; then return 0; fi
    printf '%s\\n' "$stage"
    [ "$stage" != "${failure}" ]
  }`;
  return spawnSync('sh', ['-c', `${stub}\n${command}`], { encoding: 'utf8' });
}

it('runs registration, initialization, switching and status in one command', () => {
  const result = runOnboarding();
  expect(result.status).toBe(0);
  expect(result.stdout.trim().split('\n')).toEqual(['register', 'init', 'stop', 'start', 'status']);
  expect(buildQwenWorkOnboardingCommand({ ...onboardingInput, botName: '' })).toBe('');
});

it.each(['register', 'init', 'stop', 'start'])('stops onboarding when %s fails', (failure) => {
  const result = runOnboarding(failure);
  expect(result.status).not.toBe(0);
  const stages = ['register', 'init', 'stop', 'start', 'status'];
  expect(result.stdout.trim().split('\n')).toEqual(stages.slice(0, stages.indexOf(failure) + 1));
});
