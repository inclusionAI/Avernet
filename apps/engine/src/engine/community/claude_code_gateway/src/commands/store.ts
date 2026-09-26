// Slash-command store — discovers all `/` commands available to a session.
//
// Sources are merged in priority order (later overrides earlier):
//   1. BUILTIN_COMMANDS    — hardcoded list mirroring Claude Code's CLI
//                            (https://docs.claude.com/en/docs/claude-code/slash-commands)
//   2. user commands        — `~/.claude/commands/*.md`
//   3. plugin commands      — `~/.claude/plugins/<plugin>/commands/*.md`
//   4. project commands     — `<cwd>/.claude/commands/*.md`
//
// Each `.md` file is one command:
//   filename `deploy.md` → command `/deploy`
//   YAML frontmatter (---…---) supplies `description` and optional
//   `argument-hint` / `allowed-tools`. Body is the prompt template.
//
// Returns a flat list of `SlashCommand` records; the consumer (frontend)
// filters / sorts as needed. Discovery is pure filesystem + frontmatter
// parsing — same approach as SkillsStore so we can reuse the parser.

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createLogger } from '../debug.js';
import { parseFrontmatter } from '../skills/store.js';

const log = createLogger('server');

export type SlashCommandSource = 'builtin' | 'user' | 'project' | 'plugin';

export type SlashCommand = {
  /** Stable id (== name without leading `/`). */
  id: string;
  /** Render text including `/`, e.g. `/compact`. */
  name: string;
  /** One-line description. */
  description: string;
  /** Where it came from. */
  source: SlashCommandSource;
  /** For non-builtin: absolute path of the source `.md` file. */
  filePath?: string;
  /** For plugin commands: the owning plugin id. */
  pluginId?: string;
  /** Optional hint for arguments, e.g. `<env>` or `[file]`. */
  argumentHint?: string;
  /** Optional emoji / icon (only set for built-ins; custom files don't ship icons). */
  icon?: string;
};

// ── Built-in commands (Claude Code) ────────────────────────────────────────
//
// Source of truth: official docs page above. Update when bumping the Claude
// Code SDK / CLI version. Each entry's `id` matches the slash name minus `/`.
// We intentionally keep this in code (not a JSON file) so version-skew lands
// as a TypeScript change reviewers can spot.
const BUILTIN_COMMANDS: SlashCommand[] = [
  { id: 'add-dir', name: '/add-dir', description: '把额外目录加入工作区', source: 'builtin' },
  { id: 'agents', name: '/agents', description: '管理自定义 subagent', source: 'builtin' },
  { id: 'bug', name: '/bug', description: '向 Anthropic 反馈对话问题', source: 'builtin' },
  { id: 'clear', name: '/clear', description: '清空当前对话历史', source: 'builtin', icon: '🗑️' },
  { id: 'compact', name: '/compact', description: '压缩对话上下文（保留摘要）', source: 'builtin', icon: '📝' },
  { id: 'config', name: '/config', description: '查看 / 修改 Claude Code 配置', source: 'builtin' },
  { id: 'cost', name: '/cost', description: '查看本次会话累计 token 消耗', source: 'builtin' },
  { id: 'doctor', name: '/doctor', description: '检查 Claude Code 安装健康度', source: 'builtin' },
  { id: 'exit', name: '/exit', description: '退出 REPL', source: 'builtin' },
  { id: 'help', name: '/help', description: '显示可用命令列表', source: 'builtin', icon: '❓' },
  { id: 'hooks', name: '/hooks', description: '管理工具调用 hooks', source: 'builtin' },
  { id: 'init', name: '/init', description: '在当前项目生成 CLAUDE.md', source: 'builtin' },
  { id: 'login', name: '/login', description: '切换 Anthropic 账号', source: 'builtin' },
  { id: 'logout', name: '/logout', description: '登出当前账号', source: 'builtin' },
  { id: 'mcp', name: '/mcp', description: '管理 / 鉴权 MCP server', source: 'builtin' },
  { id: 'memory', name: '/memory', description: '编辑长期记忆（CLAUDE.md）', source: 'builtin' },
  { id: 'model', name: '/model', description: '切换底层 Claude 模型', source: 'builtin' },
  { id: 'output-style', name: '/output-style', description: '切换输出风格', source: 'builtin' },
  { id: 'permissions', name: '/permissions', description: '查看 / 修改工具白名单', source: 'builtin' },
  { id: 'plugin', name: '/plugin', description: '管理已安装插件', source: 'builtin' },
  { id: 'pr-comments', name: '/pr-comments', description: '总结当前 PR 的 review 评论', source: 'builtin' },
  { id: 'release-notes', name: '/release-notes', description: '显示最近的 release notes', source: 'builtin' },
  { id: 'resume', name: '/resume', description: '继续之前的对话', source: 'builtin' },
  { id: 'review', name: '/review', description: '审查当前代码变更', source: 'builtin', icon: '🔍' },
  { id: 'skill', name: '/skill', description: '管理已安装的 Skill', source: 'builtin' },
  { id: 'status', name: '/status', description: '显示账户 / 工作区 / 模型状态', source: 'builtin' },
  { id: 'terminal-setup', name: '/terminal-setup', description: '在终端绑定 Shift+Enter', source: 'builtin' },
  { id: 'vim', name: '/vim', description: '切换到 vim 模式', source: 'builtin' },
];

export type CommandsStoreOptions = {
  /** Override `~/.claude` root for tests. */
  claudeHome?: string;
};

const CMD_FILE_EXT = '.md';

// ── Filesystem scanning ───────────────────────────────────────────────────

function scanDir(dir: string, source: SlashCommandSource, pluginId?: string): SlashCommand[] {
  if (!fs.existsSync(dir)) return [];
  let entries: string[];
  try {
    entries = fs.readdirSync(dir);
  } catch (err) {
    log.warn('commands.scan:readdir-failed', { dir, error: (err as Error).message });
    return [];
  }
  const out: SlashCommand[] = [];
  for (const name of entries) {
    if (!name.endsWith(CMD_FILE_EXT)) continue;
    if (name.startsWith('.')) continue;
    const filePath = path.join(dir, name);
    let stat: fs.Stats;
    try { stat = fs.statSync(filePath); } catch { continue; }
    if (!stat.isFile()) continue;

    const id = name.slice(0, -CMD_FILE_EXT.length);
    let description = '';
    let argumentHint: string | undefined;
    try {
      const raw = fs.readFileSync(filePath, 'utf8');
      const fm = parseFrontmatter(raw);
      if (typeof fm.description === 'string') description = fm.description;
      const hint = fm['argument-hint'] ?? fm.argument_hint ?? fm.arguments;
      if (typeof hint === 'string') argumentHint = hint;
    } catch (err) {
      log.warn('commands.scan:read-failed', { filePath, error: (err as Error).message });
    }
    out.push({
      id,
      name: `/${id}`,
      description,
      source,
      filePath,
      pluginId,
      argumentHint,
    });
  }
  return out;
}

function scanPluginsCommands(claudeHome: string): SlashCommand[] {
  const pluginsRoot = path.join(claudeHome, 'plugins');
  if (!fs.existsSync(pluginsRoot)) return [];
  let pluginDirs: string[];
  try {
    pluginDirs = fs.readdirSync(pluginsRoot);
  } catch (err) {
    log.warn('commands.scan:plugins-readdir-failed', { pluginsRoot, error: (err as Error).message });
    return [];
  }
  const out: SlashCommand[] = [];
  for (const pluginId of pluginDirs) {
    if (pluginId.startsWith('.')) continue;
    const cmdsDir = path.join(pluginsRoot, pluginId, 'commands');
    out.push(...scanDir(cmdsDir, 'plugin', pluginId));
  }
  return out;
}

// ── Store ─────────────────────────────────────────────────────────────────

export class CommandsStore {
  private readonly claudeHome: string;

  constructor(opts: CommandsStoreOptions = {}) {
    this.claudeHome = opts.claudeHome ?? path.join(os.homedir(), '.claude');
  }

  /** All commands visible to a session running with `cwd`.
   *  De-duplicated by `name` — last writer wins so project > plugin > user > builtin. */
  list(cwd?: string): SlashCommand[] {
    const merged = new Map<string, SlashCommand>();
    const put = (cmd: SlashCommand) => merged.set(cmd.name, cmd);

    for (const c of BUILTIN_COMMANDS) put(c);
    for (const c of scanDir(path.join(this.claudeHome, 'commands'), 'user')) put(c);
    for (const c of scanPluginsCommands(this.claudeHome)) put(c);
    if (cwd) {
      for (const c of scanDir(path.join(cwd, '.claude', 'commands'), 'project')) put(c);
    }

    return [ ...merged.values() ].sort((a, b) => a.name.localeCompare(b.name));
  }

  /** Look up a single command by id (`compact`) or full name (`/compact`). */
  get(idOrName: string, cwd?: string): SlashCommand | null {
    const wanted = idOrName.startsWith('/') ? idOrName : `/${idOrName}`;
    return this.list(cwd).find(c => c.name === wanted) ?? null;
  }
}
