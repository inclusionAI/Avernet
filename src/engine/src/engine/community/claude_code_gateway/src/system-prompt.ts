import { createHash } from 'node:crypto';
import { realpathSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { createLogger } from './debug.js';

const log = createLogger('server');
const MAX_IMPORT_DEPTH = 5;
const MAX_PROMPT_CHARS = 64_000;
const IMPORT_LINE = /^\s*@([A-Za-z0-9._/-]+\.md)\s*$/gm;

export type ResolvedSystemPrompt = {
  prompt: string;
  fileCount: number;
  sha256: string;
};

function resolveWithinRoot(root: string, candidate: string): string {
  const resolved = realpathSync(candidate);
  if (resolved !== root && !resolved.startsWith(`${root}${path.sep}`)) {
    throw new Error('system prompt import escapes the configured profile root');
  }
  return resolved;
}

function expandPromptFile(
  filePath: string,
  root: string,
  depth: number,
  activeFiles: Set<string>,
  loadedFiles: Set<string>,
): string {
  if (depth > MAX_IMPORT_DEPTH) {
    throw new Error(`system prompt import depth exceeds ${MAX_IMPORT_DEPTH}`);
  }
  const resolvedFile = resolveWithinRoot(root, filePath);
  if (activeFiles.has(resolvedFile)) {
    throw new Error('system prompt import cycle detected');
  }
  activeFiles.add(resolvedFile);
  loadedFiles.add(resolvedFile);
  try {
    const source = readFileSync(resolvedFile, 'utf8');
    return source.replace(IMPORT_LINE, (_match, importPath: string) => {
      if (path.isAbsolute(importPath) || importPath.split('/').includes('..')) {
        throw new Error('system prompt import must be a relative path inside the configured profile root');
      }
      return expandPromptFile(path.join(path.dirname(resolvedFile), importPath), root, depth + 1, activeFiles, loadedFiles);
    });
  } finally {
    activeFiles.delete(resolvedFile);
  }
}

export function resolveSystemPromptFile(promptFile: string, promptRoot?: string): ResolvedSystemPrompt {
  const root = realpathSync(promptRoot || path.dirname(promptFile));
  const loadedFiles = new Set<string>();
  const prompt = expandPromptFile(promptFile, root, 0, new Set<string>(), loadedFiles).trim();
  if (!prompt) throw new Error('system prompt file is empty');
  if (prompt.length > MAX_PROMPT_CHARS) {
    throw new Error(`system prompt exceeds ${MAX_PROMPT_CHARS} characters after imports`);
  }
  return {
    prompt,
    fileCount: loadedFiles.size,
    sha256: createHash('sha256').update(prompt).digest('hex'),
  };
}

export function resolveConfiguredSystemPrompt(env: NodeJS.ProcessEnv = process.env): string | undefined {
  const inlinePrefix = env.RELAY_SYSTEM_PROMPT_PREFIX?.trim();
  const promptFile = env.RELAY_SYSTEM_PROMPT_FILE?.trim();
  if (!promptFile) return inlinePrefix || undefined;

  const resolved = resolveSystemPromptFile(promptFile, env.RELAY_SYSTEM_PROMPT_ROOT?.trim() || undefined);
  log.debug('system-prompt:file-loaded', {
    fileCount: resolved.fileCount,
    promptChars: resolved.prompt.length,
    sha256: resolved.sha256,
  });
  return [ inlinePrefix, resolved.prompt ].filter(Boolean).join('\n\n');
}
