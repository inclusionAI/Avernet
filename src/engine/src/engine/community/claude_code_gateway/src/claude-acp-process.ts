import { Readable, Writable } from 'node:stream';
import { spawn, type ChildProcessByStdio } from 'node:child_process';
import { ClientSideConnection, ndJsonStream, PROTOCOL_VERSION } from '@agentclientprotocol/sdk/dist/acp.js';
import type { SessionNotification } from '@agentclientprotocol/sdk';

export class ClaudeAcpProcess {
  private child: ChildProcessByStdio<Writable, Readable, null>;
  public client: ClientSideConnection;
  private updateListeners = new Set<(notification: SessionNotification) => void | Promise<void>>();

  constructor(cwd: string, extraArgs: string[] = []) {
    this.child = spawn('claude', [ '--input-format', 'stream-json', '--output-format', 'stream-json', ...extraArgs ], {
      cwd,
      stdio: [ 'pipe', 'pipe', 'inherit' ],
      env: process.env,
    });

    const input = Writable.toWeb(this.child.stdin);
    const output = Readable.toWeb(this.child.stdout) as unknown as ReadableStream<Uint8Array>;
    const stream = ndJsonStream(input, output);

    const listeners = this.updateListeners;
    this.client = new ClientSideConnection(
      () => ({
        async sessionUpdate(notification) {
          for (const listener of [ ...listeners ]) {
            await listener(notification);
          }
        },
        async requestPermission() {
          return { outcome: 'allow' } as any;
        },
      }),
      stream,
    );
  }

  onSessionUpdate(listener: (notification: SessionNotification) => void | Promise<void>) {
    this.updateListeners.add(listener);
    return () => this.updateListeners.delete(listener);
  }

  async initialize() {
    return this.client.initialize({
      protocolVersion: PROTOCOL_VERSION,
      clientCapabilities: {},
      clientInfo: { name: 'claude-code-gateway-bridge', version: '0.1.0' },
    });
  }
}
