// @vitest-environment node
import { expect, it, vi } from 'vitest';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { createServer } from 'node:http';
import { once } from 'node:events';
import type { AddressInfo } from 'node:net';
import { getMaxDeployNumberFromGitTags } from '../clawflows-git-client.js';

it('serves HTTP while AutoHeal Git is waiting for its result', async () => {
  const root = await mkdtemp(path.join(tmpdir(), 'autoheal-git-delay-'));
  await writeFile(path.join(root, 'git'), `#!${process.execPath}\nsetTimeout(() => process.stdout.write('deploy/wf/#7\\n'), 800);\n`, { mode: 0o700 });
  vi.stubEnv('PATH', root + path.delimiter + process.env.PATH);
  const logs = vi.spyOn(console, 'info').mockImplementation(() => {});
  const server = createServer((_req, res) => res.end('ok'));
  server.listen(0, '127.0.0.1'); await once(server, 'listening');
  let settled = false;
  const pending = Promise.resolve(getMaxDeployNumberFromGitTags(root, 'wf')).then(value => { settled = true; return value; });
  try {
    const response = await fetch(`http://127.0.0.1:${(server.address() as AddressInfo).port}/health`);
    expect(await response.text()).toBe('ok');
    expect(settled).toBe(false);
    expect(await pending).toBe(7);
  } finally {
    await pending;
    await new Promise<void>(resolve => { server.close(() => resolve()); server.closeAllConnections(); });
    vi.unstubAllEnvs(); logs.mockRestore(); await rm(root, { recursive: true, force: true });
  }
});
