// File-backed mirror of relay console output.
//
// Called from server.ts when invoked as the entry point. Tees
// `console.log` / `warn` / `error` into both the original handle and
// `${RELAY_LOG_DIR ?? cwd/logs}/relay-YYYY-MM-DD.log`.
//
// Knobs:
//   - RELAY_LOG_DIR=...   override the directory (absolute or cwd-relative)
//   - RELAY_LOG_DISABLED=1  disable file logging entirely (stdout only)
//
// Failures during setup degrade silently to stdout-only — startup must
// not fail on a log-path issue (e.g. read-only fs in some deploys).

import { createWriteStream, mkdirSync, type WriteStream } from 'node:fs';
import path from 'node:path';

let activeStream: WriteStream | null = null;

function format(args: unknown[]): string {
  return args
    .map(a => {
      if (typeof a === 'string') return a;
      if (a instanceof Error) return a.stack ?? a.message;
      try {
        return JSON.stringify(a);
      } catch {
        return String(a);
      }
    })
    .join(' ');
}

function todayLogName(): string {
  const d = new Date();
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  return `relay-${yyyy}-${mm}-${dd}.log`;
}

export function setupFileLogger(): { path: string } | null {
  if (process.env.RELAY_LOG_DISABLED === '1') return null;
  if (activeStream) return null;
  try {
    const dir = process.env.RELAY_LOG_DIR
      ? path.resolve(process.env.RELAY_LOG_DIR)
      : path.resolve(process.cwd(), 'logs');
    mkdirSync(dir, { recursive: true });
    const filePath = path.join(dir, todayLogName());
    activeStream = createWriteStream(filePath, { flags: 'a' });

    const origLog = console.log.bind(console);
    const origWarn = console.warn.bind(console);
    const origError = console.error.bind(console);

    const tee = (orig: (...args: unknown[]) => void, level: string) => (...args: unknown[]) => {
      orig(...args);
      try {
        activeStream?.write(`${new Date().toISOString()} ${level} ${format(args)}\n`);
      } catch {
        // Never let file write errors propagate to the caller.
      }
    };
    console.log = tee(origLog, 'INFO');
    console.warn = tee(origWarn, 'WARN');
    console.error = tee(origError, 'ERROR');
    return { path: filePath };
  } catch (err) {
    process.stderr.write(
      `[claude-code-gateway] log file setup failed: ${(err as Error).message}\n`,
    );
    return null;
  }
}
