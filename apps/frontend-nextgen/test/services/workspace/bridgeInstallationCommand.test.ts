import { withBridgeInstallation } from '@/services/workspace/bridgeInstallationCommand';
import { spawnSync } from 'node:child_process';
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

test.each(['path', 'local', 'install', 'fail', 'missing'])('bootstrap %s safely before onboarding', (scenario) => {
  const root = mkdtempSync(join(tmpdir(), 'bridge-bootstrap-'));
  const bin = join(root, 'bin');
  const home = join(root, 'home with spaces');
  const local = join(home, '.local/bin');
  const log = join(root, 'calls');
  mkdirSync(bin);
  mkdirSync(local, { recursive: true });
  const bridge = `#!${process.execPath}\nrequire('fs').appendFileSync(process.env.BOOT_LOG,'bridge:'+process.argv.slice(2).join(' ')+'\\n');`;
  const install = `#!${process.execPath}\nconst fs=require('fs'); fs.appendFileSync(process.env.BOOT_LOG,'install\\n');
    if(process.env.BOOT_SCENARIO==='fail')process.exit(7);
    if(process.env.BOOT_SCENARIO!=='missing')fs.writeFileSync(process.env.BOOT_LOCAL+'/bcs-bridge',${JSON.stringify(
      bridge,
    )},{mode:0o700});`;
  writeFileSync(join(bin, 'acli'), install, { mode: 0o700 });
  if (scenario === 'path' || scenario === 'local') {
    const target = join(scenario === 'path' ? bin : local, 'bcs-bridge');
    writeFileSync(target, bridge);
    chmodSync(target, 0o700);
    rmSync(join(bin, 'acli')); // Existing Bridge must work without acli.
  }
  try {
    const result = spawnSync(
      '/bin/sh',
      ['-c', withBridgeInstallation('bcs-bridge register && bcs-bridge status', 'acli install bcs-bridge')],
      {
        env: { PATH: bin, HOME: home, BOOT_LOG: log, BOOT_LOCAL: local, BOOT_SCENARIO: scenario },
        encoding: 'utf8',
      },
    );
    const calls = existsSync(log) ? readFileSync(log, 'utf8').trim().split('\n') : [];
    const failed = scenario === 'fail' || scenario === 'missing';
    expect(result.status === 0).toBe(!failed);
    const expectedCalls = failed
      ? ['install']
      : [...(scenario === 'install' ? ['install'] : []), 'bridge:register', 'bridge:status'];
    expect(calls).toEqual(expectedCalls);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
