# WebSocket 协议增强实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补齐协议文档缺失（skills/commands/mcp 方法）+ 实现 Plugin Reload 功能

**Architecture:** 从协议文档补齐开始 → 添加 reload 方法到 Store → 添加 handlers → 拦截 slash 命令 → 事件通知 → 更新协议文档

**Tech Stack:** TypeScript, ws (WebSocket), egg-bin/mocha (testing)

---

## Phase 1: 协议文档补齐

### Task P1-1: 文档化 skills.* 方法

**Files:**
- Modify: `docs/websocket-protocol.md`

- [ ] **Step 1: 在方法表中添加 skills 方法**

在协议文档的"方法一览"表中添加：

```markdown
| `skills.list` | 列出所有 skills | — |
| `skills.get` | 获取单个 skill | `skillId` |
| `skills.install` | 安装 skill | `skillId`, `source`, `enabled?` |
| `skills.uninstall` | 卸载 skill | `skillId` |
| `skills.update` | 更新 skill | `skillId`, `enabled?`, `source?` |
| `skills.reload` | 重载 skills 目录 | `scope?` |
| `skills.reloadOne` | 重载指定 skill | `skillId` |
```

- [ ] **Step 2: 添加 skills 方法详细章节**

在协议文档末尾添加新章节：

```markdown
## X.X Skills 方法

### skills.list

列出所有已安装的 skills。

**请求参数：** 无

**响应：**
```json
{
  "ok": true,
  "payload": {
    "skills": [
      {
        "skillId": "my-skill",
        "name": "My Skill",
        "description": "A sample skill",
        "skillType": "symlink",
        "source": "/path/to/skill",
        "enabled": true,
        "status": "enabled"
      }
    ]
  }
}
```

### skills.get

获取单个 skill 的详细信息。

**请求参数：**
| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| skillId | string | 是 | Skill ID |

### skills.install

安装一个新 skill（symlink 方式）。

**请求参数：**
| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| skillId | string | 是 | Skill ID |
| source | string | 是 | Skill 源路径 |
| enabled | boolean | 否 | 是否启用，默认 true |

### skills.uninstall

卸载一个 skill。

**请求参数：**
| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| skillId | string | 是 | Skill ID |

### skills.update

更新 skill 配置。

**请求参数：**
| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| skillId | string | 是 | Skill ID |
| enabled | boolean | 否 | 是否启用 |
| source | string | 否 | 新的源路径 |

### skills.reload

重新扫描 skills 目录，返回差异。

**请求参数：**
| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| scope | string | 否 | 作用域：`all`, `user`, `project` |

### skills.reloadOne

重新加载指定 skill。

**请求参数：**
| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| skillId | string | 是 | Skill ID |
```

- [ ] **Step 3: 提交**

```bash
git add docs/websocket-protocol.md
git commit -m "docs: add skills.* methods to protocol"
```

---

### Task P1-2: 文档化 commands.* 方法

**Files:**
- Modify: `docs/websocket-protocol.md`

- [ ] **Step 1: 在方法表中添加 commands 方法**

```markdown
| `commands.list` | 列出所有 commands | `cwd?` |
| `commands.get` | 获取单个 command | `id` 或 `name` |
| `commands.reload` | 重载 commands 目录 | `scope?` |
```

- [ ] **Step 2: 添加 commands 方法详细章节**

```markdown
## X.X Commands 方法

### commands.list

列出所有可用的 slash commands。

**请求参数：**
| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| cwd | string | 否 | 工作目录，包含项目级 commands |

### commands.get

获取单个 command 的详细信息。

**请求参数：**
| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| id | string | 否* | Command ID |
| name | string | 否* | Command 名称（含 `/`） |
| cwd | string | 否 | 工作目录 |

### commands.reload

重新扫描 commands 目录，返回差异。

**请求参数：**
| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| scope | string | 否 | 作用域：`all`, `user`, `project`, `plugin` |
```

- [ ] **Step 3: 提交**

```bash
git add docs/websocket-protocol.md
git commit -m "docs: add commands.* methods to protocol"
```

---

### Task P1-3: 文档化 mcp.* 方法

**Files:**
- Modify: `docs/websocket-protocol.md`

- [ ] **Step 1: 在方法表中添加 mcp 方法**

```markdown
| `mcp.config.list` | 列出 MCP 配置 | — |
| `mcp.config.get` | 获取 MCP 配置 | `name` |
| `mcp.config.create` | 创建 MCP 配置 | `name`, `config` |
| `mcp.config.update` | 更新 MCP 配置 | `name`, `config` |
| `mcp.config.delete` | 删除 MCP 配置 | `name` |
| `mcp.tools.list` | 列出 MCP 工具 | `serverName?` |
| `mcp.tools.call` | 调用 MCP 工具 | `serverName`, `toolName`, `arguments` |
| `mcp.reload` | 重载 MCP 配置 | — |
```

- [ ] **Step 2: 添加 mcp 方法详细章节**

```markdown
## X.X MCP 方法

### mcp.config.list

列出所有 MCP 服务器配置。

**请求参数：** 无

### mcp.config.get

获取单个 MCP 服务器配置。

**请求参数：**
| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| name | string | 是 | MCP 服务器名称 |

### mcp.config.create

创建新的 MCP 服务器配置。

**请求参数：**
| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| name | string | 是 | MCP 服务器名称 |
| config | object | 是 | MCP 服务器配置 |

### mcp.config.update

更新 MCP 服务器配置。

**请求参数：**
| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| name | string | 是 | MCP 服务器名称 |
| config | object | 是 | 新的 MCP 服务器配置 |

### mcp.config.delete

删除 MCP 服务器配置。

**请求参数：**
| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| name | string | 是 | MCP 服务器名称 |

### mcp.tools.list

列出 MCP 服务器提供的工具。

**请求参数：**
| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| serverName | string | 否 | MCP 服务器名称，不填则列出所有 |

### mcp.tools.call

调用 MCP 服务器的工具。

**请求参数：**
| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| serverName | string | 是 | MCP 服务器名称 |
| toolName | string | 是 | 工具名称 |
| arguments | object | 是 | 工具参数 |

### mcp.reload

重新加载 MCP 配置，返回差异。

**请求参数：** 无
```

- [ ] **Step 3: 提交**

```bash
git add docs/websocket-protocol.md
git commit -m "docs: add mcp.* methods to protocol"
```

---

## Phase 2: Plugin Reload 功能实现

### Task P2-1: 扩展类型定义

**Files:**
- Modify: `src/types.ts`
- Test: `test/plugin-reload.test.ts`

- [ ] **Step 1: 写类型测试**

创建 `test/plugin-reload.test.ts`:

```typescript
import assert from 'node:assert/strict';

describe('Plugin Reload Types', () => {
  describe('ReloadDiff', () => {
    it('should define added, removed, updated fields', () => {
      const diff = {
        added: ['new-skill'],
        removed: ['old-skill'],
        updated: ['modified-skill'],
      };
      assert.equal(diff.added.length, 1);
      assert.equal(diff.removed.length, 1);
      assert.equal(diff.updated?.length, 1);
    });
  });

  describe('ReloadedEvent types', () => {
    it('should define skills.reloaded event', () => {
      const event = {
        event: 'skills.reloaded' as const,
        payload: { added: [], removed: [], updated: [] },
      };
      assert.equal(event.event, 'skills.reloaded');
    });
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `npx egg-bin test test/plugin-reload.test.ts 2>&1 | head -30`
Expected: 编译错误 — 新类型尚未定义

- [ ] **Step 3: 在 types.ts 中新增 ReloadDiff 类型**

在 `src/types.ts` 末尾添加：

```typescript
// -- Plugin Reload types --

export type ReloadDiff = {
  added: string[];
  removed: string[];
  updated?: string[]; // 仅 skills 需要
};

export type SkillsReloadedEvent = {
  event: 'skills.reloaded';
  payload: ReloadDiff;
};

export type CommandsReloadedEvent = {
  event: 'commands.reloaded';
  payload: { added: string[]; removed: string[] };
};

export type McpServersReloadedEvent = {
  event: 'mcp.servers.reloaded';
  payload: { added: string[]; removed: string[] };
};
```

- [ ] **Step 4: 运行测试确认通过**

Run: `npx egg-bin test test/plugin-reload.test.ts`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/types.ts test/plugin-reload.test.ts
git commit -m "feat: add ReloadDiff and reloaded event types"
```

---

### Task P2-2: 扩展 Skills Store

**Files:**
- Modify: `src/skills/store.ts`

- [ ] **Step 1: 添加 reload() 方法到 SkillsStore 类**

在 `SkillsStore` 类中添加：

```typescript
/** 重新扫描 skills 目录，返回差异 */
reload(): ReloadDiff {
  const previous = this.list();
  const previousIds = new Set(previous.map(s => s.skillId));
  const previousMap = new Map(previous.map(s => [s.skillId, s]));

  // 重新扫描
  const current = this.list();
  const currentIds = new Set(current.map(s => s.skillId));
  const currentMap = new Map(current.map(s => [s.skillId, s]));

  // 计算差异
  const added = current.filter(s => !previousIds.has(s.skillId)).map(s => s.skillId);
  const removed = previous.filter(s => !currentIds.has(s.skillId)).map(s => s.skillId);
  const updated = current.filter(s => {
    if (!previousIds.has(s.skillId)) return false;
    const prev = previousMap.get(s.skillId)!;
    const curr = currentMap.get(s.skillId)!;
    return prev.version !== curr.version || prev.description !== curr.description;
  }).map(s => s.skillId);

  return {
    added,
    removed,
    updated: updated.length > 0 ? updated : undefined
  };
}
```

- [ ] **Step 2: 提交**

```bash
git add src/skills/store.ts
git commit -m "feat: add reload() method to SkillsStore"
```

---

### Task P2-3: 扩展 Commands Store

**Files:**
- Modify: `src/commands/store.ts`

- [ ] **Step 1: 添加 reload() 方法到 CommandsStore 类**

在 `CommandsStore` 类中添加：

```typescript
/** 重新扫描 commands 目录，返回差异 */
reload(scope?: 'all' | 'user' | 'project' | 'plugin'): { added: string[]; removed: string[] } {
  const previous = this.list();
  const previousIds = new Set(previous.map(c => c.id));

  // 重新扫描
  const current = this.list();
  const currentIds = new Set(current.map(c => c.id));

  const added = current.filter(c => !previousIds.has(c.id)).map(c => c.id);
  const removed = previous.filter(c => !currentIds.has(c.id)).map(c => c.id);

  return { added, removed };
}
```

- [ ] **Step 2: 提交**

```bash
git add src/commands/store.ts
git commit -m "feat: add reload() method to CommandsStore"
```

---

### Task P2-4: 扩展 MCP Store

**Files:**
- Modify: `src/mcp/store.ts`

- [ ] **Step 1: 添加 reload() 方法到 McpStore 类**

在 `McpStore` 类中添加：

```typescript
/** 重新加载 MCP 配置，返回差异 */
reload(): { added: string[]; removed: string[] } {
  this.load();

  const currentServers = Object.keys(this.root.mcpServers ?? {});
  const currentIds = new Set(currentServers);

  const previousIds = this._previousServerIds ?? new Set<string>();
  this._previousServerIds = currentIds;

  const added = currentServers.filter(s => !previousIds.has(s));
  const removed = [...previousIds].filter(s => !currentIds.has(s));

  return { added, removed };
}

private _previousServerIds?: Set<string>;
```

- [ ] **Step 2: 提交**

```bash
git add src/mcp/store.ts
git commit -m "feat: add reload() method to McpStore"
```

---

### Task P2-5: 添加 Reload Handlers

**Files:**
- Modify: `src/skills/handlers.ts`
- Modify: `src/commands/handlers.ts`
- Modify: `src/mcp/handlers.ts`

- [ ] **Step 1: 添加 skills.reload handler**

在 `src/skills/handlers.ts` 中添加：

```typescript
export async function handleReload(
  store: SkillsStore,
  params: unknown,
): Promise<SkillResult<ReloadDiff>> {
  try {
    const diff = store.reload();
    return ok(diff);
  } catch (e) {
    return err('RELOAD_FAILED', e instanceof Error ? e.message : String(e));
  }
}
```

更新 `SKILLS_METHODS` 添加 `'skills.reload': handleReload`。

- [ ] **Step 2: 添加 commands.reload handler**

类似添加 `handleReload` 到 commands handlers。

- [ ] **Step 3: 添加 mcp.reload handler**

类似添加 `handleReload` 到 mcp handlers。

- [ ] **Step 4: 提交**

```bash
git add src/skills/handlers.ts src/commands/handlers.ts src/mcp/handlers.ts
git commit -m "feat: add reload handlers for skills, commands, mcp"
```

---

### Task P2-6: Slash 命令拦截

**Files:**
- Modify: `src/gateway/handlers/chat.ts`

- [ ] **Step 1: 在 handleSend 中添加 slash 命令拦截逻辑**

在 `startChatRun` 调用之前添加：

```typescript
// 检查是否为 plugin reload 命令
if (message.startsWith('/plugin-reload')) {
  const args = message.slice('/plugin-reload'.length).trim().split(/\s+/).filter(Boolean);
  const target = args[0] || 'all';

  const results: Record<string, unknown> = {};

  const skillsStore = (deps as { skillsStore?: SkillsStore }).skillsStore;
  const commandsStore = (deps as { commandsStore?: CommandsStore }).commandsStore;
  const mcpStore = (deps as { mcpStore?: McpStore }).mcpStore;

  if (!target || target === 'all' || target === 'skills') {
    if (skillsStore) results.skills = skillsStore.reload();
  }
  if (!target || target === 'all' || target === 'commands') {
    if (commandsStore) results.commands = commandsStore.reload();
  }
  if (!target || target === 'all' || target === 'mcp') {
    if (mcpStore) results.mcp = mcpStore.reload();
  }

  // 发送事件通知前端
  if (results.skills) {
    ctx.send({ type: 'event', event: 'skills.reloaded', payload: results.skills, seq: ++ctx.seq });
  }
  if (results.commands) {
    ctx.send({ type: 'event', event: 'commands.reloaded', payload: results.commands, seq: ++ctx.seq });
  }
  if (results.mcp) {
    ctx.send({ type: 'event', event: 'mcp.servers.reloaded', payload: results.mcp, seq: ++ctx.seq });
  }

  ctx.response(frame.id, true, { message: `Reloaded: ${Object.keys(results).join(', ')}`, results });
  return;
}
```

- [ ] **Step 2: 提交**

```bash
git add src/gateway/handlers/chat.ts
git commit -m "feat: intercept /plugin-reload slash command"
```

---

### Task P2-7: 更新协议文档（reload 事件）

**Files:**
- Modify: `docs/websocket-protocol.md`

- [ ] **Step 1: 添加 reload 事件到事件表**

```markdown
| `skills.reloaded` | skills 目录重载完成 |
| `commands.reloaded` | commands 目录重载完成 |
| `mcp.servers.reloaded` | MCP 服务重载完成 |
```

- [ ] **Step 2: 添加 reload 事件详细章节**

```markdown
## X.X Reload 事件

### skills.reloaded

重载 skills 目录后发送。

```json
{
  "type": "event",
  "event": "skills.reloaded",
  "payload": {
    "added": ["new-skill"],
    "removed": ["old-skill"],
    "updated": ["modified-skill"]
  }
}
```
```

- [ ] **Step 3: 提交**

```bash
git add docs/websocket-protocol.md
git commit -m "docs: add reload events to protocol"
```

---

## Phase 3: 运行完整 CI 流程

**Files:** None (verification only)

- [ ] **Step 1: 运行 lint**

Run: `tnpm run lint`
Expected: 无错误

- [ ] **Step 2: 运行测试**

Run: `tnpm run test-local`
Expected: 所有测试通过

- [ ] **Step 3: 运行构建**

Run: `tnpm run prepublishOnly`
Expected: 构建成功

- [ ] **Step 4: 最终提交**

```bash
git add -A
git commit -m "chore: fix lint issues from protocol enhancement"
```

---

## Plan Complete

**Plan complete and saved to `docs/superpowers/plans/2026-05-07-protocol-enhancement-plan.md`.**

Two execution options:

1. **Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

2. **Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?