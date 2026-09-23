import { runtimeResponse } from "../internal/http-response.js";
import { spawn } from "node:child_process";
import type { BotRuntimeProvider, ResolvedBotTarget, ResolvedShellInput, ResolvedHttpInput, ResolvedMessageInput, ShellResult } from "../contracts.js";
import { BotRuntimeError } from "../errors.js";
import { sendEngineMessage } from "../internal/engine-message.js";
export type LocalRuntimeContext = { cwd: string; home: string; stateDirectory: string; engineOrigin: string; engineIdentity: string; env?: NodeJS.ProcessEnv };
/** Context comes from the public host's owner-checked local Bot resolver, never request paths. */
export class LocalRuntimeProvider implements BotRuntimeProvider {
  constructor(private readonly resolve: (target: ResolvedBotTarget) => Promise<LocalRuntimeContext>) {}
  private origin(value: string) {
    const url = new URL(value);
    if (!['http:', 'https:'].includes(url.protocol) || !['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)
      || url.username || url.password || url.pathname !== '/' || url.search || url.hash) throw new BotRuntimeError(503, 'invalid_local_runtime', 'Local Engine origin must be loopback');
    return url;
  }
  async executeShell(input: ResolvedShellInput): Promise<ShellResult> {
    const context = await this.resolve(input.target), startedAt = Date.now();
    return new Promise(resolve => {
      let stdout = '', stderr = '', timedOut = false;
      const child = spawn('/bin/bash', ['--noprofile', '--norc', '-c', input.command], {
        cwd: context.cwd, detached: process.platform !== 'win32',
        env: { ...process.env, ...context.env, HOME: context.home, OPENCLAW_STATE_DIR: context.stateDirectory }, stdio: ['ignore', 'pipe', 'pipe'],
      });
      const terminate = () => { try { if (child.pid && process.platform !== 'win32') process.kill(-child.pid, 'SIGKILL'); else child.kill('SIGKILL'); } catch { /* already exited */ } };
      const timer = setTimeout(() => { timedOut = true; terminate(); }, input.timeoutMs ?? 30000);
      child.stdout.on('data', b => { stdout = (stdout + b.toString()).slice(0, 65536); });
      child.stderr.on('data', b => { stderr = (stderr + b.toString()).slice(0, 65536); });
      child.once('error', () => { clearTimeout(timer); resolve({ status: 'failed', exitCode: null, stdout, stderr: '', durationMs: Date.now() - startedAt, error: 'Local command could not start' }); });
      child.once('close', code => { clearTimeout(timer); resolve({ status: timedOut || code == null ? 'unknown' : code === 0 ? 'success' : 'failed', exitCode: code, stdout, stderr, durationMs: Date.now() - startedAt }); });
    });
  }
  async request(input: ResolvedHttpInput) {
    const context = await this.resolve(input.target), origin = this.origin(context.engineOrigin);
    if (input.port !== undefined && input.port !== Number(origin.port || (origin.protocol === 'https:' ? 443 : 80))) throw new BotRuntimeError(422, 'unsupported_local_port', 'Local request must address the configured Engine');
    const headers = new Headers({ "Content-Type": "application/json" });
    if (context.engineIdentity) headers.set("x-iam-token", context.engineIdentity);
    const response = await fetch(origin.origin + input.path, { method: input.method, headers,
      body: input.body === undefined ? undefined : JSON.stringify(input.body), redirect: 'error', signal: AbortSignal.timeout(input.timeoutMs ?? 30000) });
    return runtimeResponse(response);
  }
  async sendMessage(input: ResolvedMessageInput) {
    const context = await this.resolve(input.target), origin = this.origin(context.engineOrigin);
    const engineUrl = `${origin.protocol === 'https:' ? 'wss:' : 'ws:'}//${origin.host}/ws`;
    return sendEngineMessage(input, { engineUrl, engineAuthToken: context.engineIdentity,
      execute: command => this.executeShell({ target: input.target, command, timeoutMs: 35000 }) });
  }
}
