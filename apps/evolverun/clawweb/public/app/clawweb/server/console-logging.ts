import { formatWithOptions } from "node:util";
import { isMainThread } from "node:worker_threads";

const installed = Symbol.for("clawweb.console-logging.installed");
const target = console as Console & { [installed]?: boolean };
const levels = { error: "ERROR", warn: "WARN", log: "INFO", info: "INFO", debug: "DEBUG" } as const;

function formatLine(level: string, args: unknown[]): string {
  const now = new Date();
  const pad = (value: number, width = 2) => String(value).padStart(width, "0");
  const time = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())},${pad(now.getMilliseconds(), 3)}`;
  return `${time} ${level} ${formatWithOptions({ colors: false }, ...args)}`;
}

function writeFormatted(write: (...args: unknown[]) => void, level: string, args: unknown[]): void {
  let line: string;
  try {
    line = formatLine(level, args);
  } catch {
    // Formatting failures must not introduce new application failures.
    write(...args);
    return;
  }
  write(line);
}

/** Opt-in: importing this module never changes the console. */
export function installConsoleLogging(): void {
  if (!isMainThread || typeof process.send === "function" || target[installed]) return;
  for (const method of Object.keys(levels) as Array<keyof typeof levels>) {
    const original = console[method].bind(console);
    console[method] = (...args: unknown[]) => writeFormatted(original, levels[method], args);
  }
  Object.defineProperty(target, installed, { value: true });
}

/** Format an internal CLI failure before bootstrap, without installing globally. */
export function logStartupError(internal: boolean, ...args: unknown[]): void {
  if (!internal || target[installed]) {
    console.error(...args);
    return;
  }
  writeFormatted(console.error.bind(console), "ERROR", args);
}
