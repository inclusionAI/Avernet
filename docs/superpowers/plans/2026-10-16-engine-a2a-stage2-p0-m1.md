# A2A 阶段 2 P0+M1 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地 P0（引擎侧 resolve 能力真实声明）与 M1（ocb 前端 A2A wire 层/探测/开关，默认 off 不接线），为 M2 接线备好全部零件。

**Architecture:** P0 在 Avernet 仓把 `make_agent_card` 变为接受 `resolve_supported` 的条件声明，声明真值由活动引擎 ChatService 的 resolve 方法族在场性推导（`_KIND_RESOLVE_METHOD` 单源）。M1 在 ocb `apps/frontend/src/adapters/a2a/` 落四个纯模块（urls/switch/card 校验/wire），零接线、零运行时消费，TDD 覆盖。

**Tech Stack:** Python/FastAPI（Avernet engine adapter，pytest）；TypeScript（ocb apps/frontend，ts-jest node 环境——Node 18+ 全局 Response/ReadableStream 可真流测试）。

**上游 spec（approved）:** `docs/superpowers/specs/2026-10-16-engine-a2a-stage2-frontend-a2a-client-design.md`

**两仓提交规范:** `<type>(a2a): <desc>`；关闭归属 footer（本机全局禁用）。两仓各自分支：Avernet 在 `feat/engine-a2a-protocol-replacement`（或 dev 基线新分支）；ocb 在 `feat/a2a-frontend-client`（**用 git worktree 隔离，基线分支按 B0 核验结果定**）。

---

## P0 前置门槛：能力声明契约（编码前定稿，计划批准即视为确认）

- **URI 词汇**：`EXTENSION_URIS["resolve"] = "https://avernet.local/a2a/ext/resolve/v1"`，与其余五项（inject/context/replay/retry/cancel）同源单定义于 `card.py`。
- **粒度**：**单一 URI 代表完整 resolve 能力集**——声明 iff 活动引擎 ChatService 实现
  `_KIND_RESOLVE_METHOD.values()` **全部三方法**（`resolve_exec_approval` / `resolve_interaction` / `resolve_mode_transition`）。任一缺失即不声明（保守：部分支持=不声明，对应会话留 v3）。
- **真值来源**：`engine_bridge._chat_plugin()` 在场且可取；取插件抛异常 → 不声明。声明是**请求时计算**（GET card 即时判），无缓存——与 `_forward_resolve` 运行期门控同源，避免声明与实现漂移。
- `required: false`，与现有五项一致。
- 前端消费侧（M1 `capability.ts` 常量）以字符串字面量对齐本 URI；两侧不对齐会导致场景校验永远失败，M3 集成时以引擎 card 输出做 golden 对拍。

---

## Part A — P0（Avernet 仓）

### Task A1: `make_agent_card` 条件声明（纯函数层）

**Files:**
- Modify: `engine/adapter/src/engine/community/api/transport/a2a/card.py`
- Test: `engine/adapter/src/engine/community/api/tests/test_a2a_transport.py`

- [ ] **Step 1: 写失败测试**（追加到 `test_a2a_transport.py`，紧邻既有 `test_agent_card_declares_cancel_extension` 所在层级）

```python
# ── P0: resolve 能力条件声明（spec stage2 §5）──
# 粒度=单一 URI 代表完整能力集：三方法全在场才声明；部分支持=不声明。
# URI 字面量 = 契约定稿值（计划批准即确认），不用模块属性——测试与 card.py
# 的实现漂移时字面量会红。

RESOLVE_URI = "https://avernet.local/a2a/ext/resolve/v1"

def test_agent_card_default_excludes_resolve_uri():
    c = make_agent_card("http://127.0.0.1:20003")
    urls = [e["url"] for e in c["capabilities"]["extensions"]]
    assert RESOLVE_URI not in urls


def test_agent_card_with_resolve_support_declares_uri_last():
    c = make_agent_card("http://127.0.0.1:20003", resolve_supported=True)
    urls = [e["url"] for e in c["capabilities"]["extensions"]]
    assert RESOLVE_URI in urls
    # 既有五项不受影响（retry 被 router:232 retry-only 校验消费，不能动）
    assert "https://avernet.local/a2a/ext/retry/v1" in urls
    assert "https://avernet.local/a2a/ext/cancel/v1" in urls
    assert len(urls) == 6
    # 声明必须 required:false，与其余五项一致
    resolve_entry = [e for e in c["capabilities"]["extensions"]
                     if e["url"] == RESOLVE_URI][0]
    assert resolve_entry["required"] is False
```

- [ ] **Step 2: 运行验证失败**

Run: `python -m pytest engine/adapter/src/engine/community/api/tests/test_a2a_transport.py -v -k "agent_card_with_resolve or agent_card_default_excludes"`
Expected: FAIL——`make_agent_card` 不接受 `resolve_supported` 关键字（TypeError）

- [ ] **Step 3: 最小实现**（card.py）

```python
EXTENSION_URIS = {
    "inject": f"{EXT_BASE}/inject/v1",
    "context": f"{EXT_BASE}/context/v1",
    "replay": f"{EXT_BASE}/replay/v1",
    "retry": f"{EXT_BASE}/retry/v1",
    "cancel": f"{EXT_BASE}/cancel/v1",
    # P0/stage2 §5：resolve 词汇进入单一定义；card 是否声明由
    # resolve_supported 决定（真值=活动引擎 resolve 方法族在场）。
    "resolve": f"{EXT_BASE}/resolve/v1",
}


def make_agent_card(base_url: str, *,
                    agent_name: str = "OpenClaw Engine",
                    description: str = "Engine adapter with A2A v1.0 JSON-RPC binding",
                    version: str = "0.1.0",
                    resolve_supported: bool = False) -> dict[str, Any]:
    # 声明列表 = 基础五项 + 条件 resolve；resolved 粒度=完整能力集单 URI。
    card_uris = [u for k, u in EXTENSION_URIS.items() if k != "resolve"]
    if resolve_supported:
        card_uris.append(EXTENSION_URIS["resolve"])
    out: dict[str, Any] = {
        # ……（既有字段不动）……
        "capabilities": {
            "streaming": True,
            "pushNotifications": False,
            "extensions": [{"url": u, "required": False} for u in card_uris],
        },
        # 其余字段不变
    }
    return out
```

注意：`EXTENSION_URIS` 是 router:232 `EXTENSION_URIS["retry"]` 的既有键，六键词汇表不破坏该用法。

- [ ] **Step 4: 运行验证通过**

Run: `python -m pytest engine/adapter/src/engine/community/api/tests/test_a2a_transport.py -v -k "agent_card"`
Expected: PASS（新旧 card 用例全绿）

- [ ] **Step 5: Commit**

```bash
git add engine/adapter/src/engine/community/api/transport/a2a/card.py engine/adapter/src/engine/community/api/tests/test_a2a_transport.py
git commit -m "feat(a2a): card conditionally declares resolve extension (P0)"
```

### Task A2: 引擎真值判定 + card 路由接线

**Files:**
- Modify: `engine/adapter/src/engine/community/api/transport/a2a/router.py`（`agent_card` 路由，:90-94 附近；新增 `_engine_resolve_supported`）
- Test: `engine/adapter/src/engine/community/api/tests/test_a2a_transport.py`

- [ ] **Step 1: 写失败测试**

```python
class _FullResolveChat:
    """三方法全有——声明满足。"""
    def resolve_exec_approval(self, **kw): ...
    def resolve_interaction(self, **kw): ...
    def resolve_mode_transition(self, **kw): ...

class _PartialResolveChat:
    """缺 mode_switch——部分支持=不声明。"""
    def resolve_exec_approval(self, **kw): ...
    def resolve_interaction(self, **kw): ...


RESOLVE_URI = "https://avernet.local/a2a/ext/resolve/v1"   # 同 A1 字面量

def test_card_endpoint_declares_resolve_for_full_engine(
        app_client, monkeypatch):
    from engine.community.api.transport.a2a import engine_bridge as bridge
    monkeypatch.setattr(bridge, "_chat_plugin", lambda: _FullResolveChat())
    resp = app_client.get("/.well-known/agent-card.json")
    assert resp.status_code == 200
    urls = [e["url"] for e in resp.json()["capabilities"]["extensions"]]
    assert RESOLVE_URI in urls


def test_card_endpoint_no_resolve_decl_for_partial_engine(
        app_client, monkeypatch):
    from engine.community.api.transport.a2a import engine_bridge as bridge
    monkeypatch.setattr(bridge, "_chat_plugin", lambda: _PartialResolveChat())
    resp = app_client.get("/.well-known/agent-card.json")
    urls = [e["url"] for e in resp.json()["capabilities"]["extensions"]]
    assert RESOLVE_URI not in urls
    # 基础五项仍在，老消费方（BaaS abort 判读）不受影响
    assert "https://avernet.local/a2a/ext/cancel/v1" in urls


def test_card_endpoint_no_resolve_decl_when_plugin_unavailable(
        app_client, monkeypatch):
    from engine.community.api.transport.a2a import engine_bridge as bridge
    def _raise(): raise RuntimeError("no active engine")
    monkeypatch.setattr(bridge, "_chat_plugin", _raise)
    resp = app_client.get("/.well-known/agent-card.json")
    assert resp.status_code == 200  # card 永可答，声明保守缺席
    urls = [e["url"] for e in resp.json()["capabilities"]["extensions"]]
    assert RESOLVE_URI not in urls
```

（`app_client` fixture 与测试文件既有 `test_agent_card_declares_cancel_extension(app_client)` 用法相同。）

- [ ] **Step 2: 运行验证失败**

Run: `python -m pytest engine/adapter/src/engine/community/api/tests/test_a2a_transport.py -v -k "card_endpoint"`
Expected: FAIL——resolve URI 未出现在任何 card（路由未接线）

- [ ] **Step 3: 最小实现**（router.py）

`_KIND_RESOLVE_METHOD` 定义之后追加：

```python
def _engine_resolve_supported() -> bool:
    """P0/stage2 §5：resolve 声明真值——活动引擎 ChatService 实现
    `_KIND_RESOLVE_METHOD.values()` 全部三方法才为 True。

    与 `_forward_resolve` 运行期门控同源（同一词汇表），不声明即免于
    "card 声明≠实际能力"；取插件失败按不声明处理（保守）。请求时计算，
    无缓存——避免声明与实现漂移。"""
    try:
        from engine.community.api.transport.a2a.engine_bridge import _chat_plugin
        chat = _chat_plugin()
    except Exception:
        return False
    return all(getattr(chat, m, None) is not None
               for m in set(_KIND_RESOLVE_METHOD.values()))
```

`agent_card` 路由改为：

```python
@router.get("/.well-known/agent-card.json")
async def agent_card() -> dict[str, Any]:
    from engine.community.config import get_local_base_url
    return make_agent_card(get_local_base_url(),
                           resolve_supported=_engine_resolve_supported())
```

- [ ] **Step 4: 运行验证通过**

Run: `python -m pytest engine/adapter/src/engine/community/api/tests/test_a2a_transport.py -v -k "card_endpoint"`
Expected: PASS ×3

- [ ] **Step 5: Commit**

```bash
git add engine/adapter/src/engine/community/api/transport/a2a/router.py engine/adapter/src/engine/community/api/tests/test_a2a_transport.py
git commit -m "feat(a2a): engine-truth resolve declaration on agent card (P0)"
```

### Task A3: P0 退出门（全量回归）

- [ ] **Step 1: 全量 a2A 测试模块**

Run: `python -m pytest engine/adapter/src/engine/community/api/tests/test_a2a_transport.py -v 2>&1 | tail -3`
Expected: 全 PASS（既有 24+ 新 5 用例）

- [ ] **Step 2: 引擎既有回归核验**

Run: `python -m pytest engine/adapter/src/engine/community/api/tests/ -v 2>&1 | tail -3`
Expected: 与基线数一致零失败（PR#2743 合入后基线 3011 passed）

- [ ] **Step 3: spec 基线回填**（若有漂移）：核验 `git log --oneline -1 origin/dev` 与 spec §1 表格，Avernet 行写实际 commit。

---

## Part B — M1（ocb 仓 `apps/frontend`）

### Task B0: worktree 建立 + 双仓基线核验回填

- [ ] **Step 1: 核验 ocb 前端基线与契约包版本**

```bash
cd ~/IdeaProjects/ocb && git fetch origin && git rev-parse origin/dev
grep -A1 '"@alipay/aix-chat-adapters"' apps/frontend/package.json
grep '"@alipay/aix-chat-adapters"' apps/frontend/pnpm-lock.yaml | head -3   # 或对应 lock 文件；无 lock 则记录 package.json 档
git log --oneline -1 -- apps/frontend/src/hooks/useMultiSessionChat.ts   # 注入点仍在树
grep -n "specialProvider ?? new OpenClawProvider" apps/frontend/src/hooks/useMultiSessionChat.ts
```
Expected: 注入点行存在；adapter 版本回填 spec §1 表。

- [ ] **Step 2: worktree 隔离分支**

```bash
cd ~/IdeaProjects/ocb && git worktree add ../ocb-a2a-stage2 -b feat/a2a-frontend-client origin/dev
cd ../ocb-a2a-stage2/apps/frontend && npx jest src/adapters/aicoding --silent 2>&1 | tail -3
```
Expected: 既有 adapter 测试在干净树上是绿的（不绿先停，回报环境问题）。

- [ ] **Step 3: spec §1 回填实际基线**（Avernet/ocb commit + adapter 版本），commit spec。

### Task B1: `urls.ts`——ws→http 地址派生（直连形态）

**Files:**
- Create: `apps/frontend/src/adapters/a2a/urls.ts`
- Test: `apps/frontend/src/adapters/a2a/__tests__/urls.test.ts`

- [ ] **Step 1: 写失败测试**

```ts
import { a2aUrlsFromWsUrl } from '@/adapters/a2a/urls';

describe('a2aUrlsFromWsUrl（spec §2 地址映射）', () => {
  it('直连 v3 ws url 派生根级 A2A 面（丢弃 /api/{engine}/ws 路径）', () => {
    expect(a2aUrlsFromWsUrl('ws://localhost:20003/api/claude_code/ws'))
      .toEqual({
        cardUrl: 'http://localhost:20003/.well-known/agent-card.json',
        rpcUrl: 'http://localhost:20003/rpc',
      });
  });

  it('wss 派生 https，保留端口与主机', () => {
    const urls = a2aUrlsFromWsUrl('wss://lab.internal:20003/api/aicoding/ws');
    expect(urls.cardUrl).toBe('https://lab.internal:20003/.well-known/agent-card.json');
    expect(urls.rpcUrl).toBe('https://lab.internal:20003/rpc');
  });

  it('无效 URL 抛错（fail-fast，不静默回退）', () => {
    expect(() => a2aUrlsFromWsUrl('not-a-url')).toThrow();
  });
});
```

企业代理链的地址映射 **M4 定稿**（spec §2：正式验收项）——本模块只做直连形态，文件头注明。

- [ ] **Step 2: 运行验证失败**

Run: `cd ~/IdeaProjects/ocb-a2a-stage2/apps/frontend && npx jest src/adapters/a2a/__tests__/urls.test.ts`
Expected: FAIL——模块不存在

- [ ] **Step 3: 最小实现**

```ts
/**
 * A2A 面地址派生（spec stage2 §2）。
 *
 * 直连形态：v3 `ws://host:port/api/{engine}/ws` → A2A 根级路径
 * （引擎 a2a router 无前缀挂载，@app.py:293）。
 * 企业代理链前缀规则 M4 定稿——本模块不含代理链翻译。
 * 前端永不消费 card 自报 url（引擎本机视角，非浏览器可达承诺）。
 */
export interface A2aUrls { cardUrl: string; rpcUrl: string }

const CARD_PATH = '/.well-known/agent-card.json';
const RPC_PATH = '/rpc';

export function a2aUrlsFromWsUrl(wsUrl: string): A2aUrls {
  const httpUrl = wsUrl.replace(/^ws(s?):/, 'http$1:');
  return {
    cardUrl: `${new URL(httpUrl).origin}${CARD_PATH}`,
    rpcUrl: `${new URL(httpUrl).origin}${RPC_PATH}`,
  };
}
```

- [ ] **Step 4: 运行验证通过** → PASS
- [ ] **Step 5: Commit** `git commit -m "feat(a2a): derive a2a card/rpc urls from v3 ws url (M1)"`（add 两文件）

### Task B2: `capability.ts` 之一——开关唯一读取口

**Files:**
- Create: `apps/frontend/src/adapters/a2a/switch.ts`
- Test: `apps/frontend/src/adapters/a2a/__tests__/switch.test.ts`

（开关与探测都归 `capability.ts` 职责域；拆 `switch.ts` 单文件承载"唯一读取口"便于 grep 审计。）

- [ ] **Step 1: 写失败测试**

```ts
import { isA2aClientEnabled, A2A_SWITCH_KEY } from '@/adapters/a2a/switch';

// jest 主配置 testEnvironment='node'（无 window/localStorage）——本测试组
// 用最小 stub 驱动 switch 的读取路径；产品码真实跑在浏览器。
const store = new Map<string, string>();
const localStorageStub = {
  getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
  setItem: (k: string, v: string) => { store.set(k, v); },
  clear: () => store.clear(),
};
(global as any).window = { localStorage: localStorageStub };

describe('a2a 客户端总开关（spec §1：默认 off，唯一读取入口）', () => {
  beforeEach(() => store.clear());

  it('默认 off：未设置时 false', () => {
    expect(isA2aClientEnabled()).toBe(false);
  });

  it("localStorage = '1' 才开（显式 opt-in，与 AICODING_RESUME 的 !== '0' 相反方向）", () => {
    store.set(A2A_SWITCH_KEY, '1');
    expect(isA2aClientEnabled()).toBe(true);
  });

  it('任何其他值视为 off', () => {
    store.set(A2A_SWITCH_KEY, '0');
    expect(isA2aClientEnabled()).toBe(false);
  });

  it('localStorage 访问抛错时按 off（不抛错上浮）', () => {
    localStorageStub.getItem = () => { throw new Error('denied'); };
    try {
      expect(isA2aClientEnabled()).toBe(false);
    } finally {
      localStorageStub.getItem = (k: string) => (store.has(k) ? store.get(k)! : null);
    }
  });
});
```

- [ ] **Step 2: 验证失败**（模块不存在）

- [ ] **Step 3: 最小实现**

```ts
/**
 * A2A 客户端总开关——spec stage2 §1：默认 off；本文件是**唯一**读取入口，
 * 灰度放量/整体回退都从这里生效。开发override走 localStorage（仓内先例
 * AICODING_RESUME），正式放量载体（@umijs/max defineConfig 契约）M2 接线
 * 时在此文件内扩展，不另开读取口。
 */
export const A2A_SWITCH_KEY = 'A2A_CHAT_CLIENT';

export function isA2aClientEnabled(): boolean {
  try {
    return typeof window !== 'undefined'
      && window.localStorage.getItem(A2A_SWITCH_KEY) === '1';
  } catch {
    return false; // storage 不可用（隐私模式等）→ 保守 off
  }
}
```

- [ ] **Step 4: 验证通过；Step 5: Commit** `feat(a2a): a2a client master switch, default off (M1)`

### Task B3: `capability.ts` 之二——card 探测与分场景校验

**Files:**
- Create: `apps/frontend/src/adapters/a2a/card.ts`
- Test: `apps/frontend/src/adapters/a2a/__tests__/card.test.ts`

- [ ] **Step 1: 写失败测试**

```ts
import { fetchCard, validateScenarios, Scenario } from '@/adapters/a2a/card';
import { makeEngineCard } from './helpers';

describe('card 探测（spec §2/§5：card 通过=基础面；场景缺项→该场景 v3）', () => {
  it('HTTP 非 2xx → card null + reason（probe-v3 不锁定）', async () => {
    const fetchImpl = async () => new Response(null, { status: 404 });
    const r = await fetchCard('http://x/.well-known/agent-card.json', fetchImpl as any);
    expect(r.card).toBeNull();
    expect(r.reason).toBe('http_404');
  });

  it('网络异常 → card null + transport reason', async () => {
    const fetchImpl = async () => { throw new TypeError('network down'); };
    const r = await fetchCard('http://x/.well-known/agent-card.json', fetchImpl as any);
    expect(r.card).toBeNull();
    expect(r.reason).toBe('network');
  });

  it('非 JSON → card null + bad_json', async () => {
    const fetchImpl = async () => new Response('not json', { status: 200 });
    const r = await fetchCard('http://x/.well-known/agent-card.json', fetchImpl as any);
    expect(r.reason).toBe('bad_json');
  });

  it('结构缺基础面（非 JSONRPC/1.0）→ 场景全 fail', () => {
    const card = makeEngineCard({ binding: 'HTTP+JSON' });
    const rs = validateScenarios(card, [Scenario.STREAM, Scenario.RECOVERY, Scenario.HITL]);
    expect(rs.every((x: any) => !x.ok)).toBe(true);
    expect(rs[0].reason).toBe('no_jsonrpc_interface');
  });

  it('全量引擎 card（六扩展+streaming）→ 三场景全过', () => {
    const rs = validateScenarios(makeEngineCard(), [
      Scenario.STREAM, Scenario.RECOVERY, Scenario.HITL,
    ]);
    expect(rs.every((x: any) => x.ok)).toBe(true);
  });

  it('缺 resolve 声明（部分支持引擎）→ 仅 hitl fail', () => {
    const rs = validateScenarios(makeEngineCard({ resolve: false }), [Scenario.HITL]);
    expect(rs[0].ok).toBe(false);
    expect(rs[0].reason).toBe('missing_resolve_extension');
  });

  it('streaming=false → stream 场景 fail（reason not_streaming）', () => {
    const rs = validateScenarios(makeEngineCard({ streaming: false }), [Scenario.STREAM]);
    expect(rs[0].reason).toBe('not_streaming');
  });
});
```

同目录建 `helpers.ts`（测试卡片工厂，形状对齐 Avernet `card.py` 输出）：

```ts
/** 金样对齐 engine/adapter/.../a2a/card.py 的输出形状（P0 落地后含 resolve）。 */
export const EXT_BASE = 'https://avernet.local/a2a/ext';

export function makeEngineCard(opts: {
  binding?: string; streaming?: boolean; resolve?: boolean;
} = {}): any {
  const exts = ['inject', 'context', 'replay', 'retry', 'cancel']
    .map((k) => ({ url: `${EXT_BASE}/${k}/v1`, required: false }));
  const base = { streaming: opts.streaming ?? true, pushNotifications: false,
                 extensions: exts };
  if (opts.resolve !== false) {
    base.extensions.push({ url: `${EXT_BASE}/resolve/v1`, required: false });
  }
  return {
    supportedInterfaces: [{
      url: 'http://127.0.0.1:20003/rpc',
      protocolBinding: opts.binding ?? 'JSONRPC',
      protocolVersion: '1.0',
    }],
    capabilities: base,
  };
}
```

- [ ] **Step 2: 验证失败**

- [ ] **Step 3: 最小实现**

```ts
/**
 * card 探测与分场景能力校验（spec §2 传输选择 / §5 能力校验）。
 * 探测请求不携带任何 token（card 无鉴权）。
 */
export const EXT_BASE = 'https://avernet.local/a2a/ext';
const REPLAY_URI = `${EXT_BASE}/replay/v1`;
const RESOLVE_URI = `${EXT_BASE}/resolve/v1`;

export enum Scenario { STREAM = 'stream', RECOVERY = 'recovery', HITL = 'hitl' }

export interface CardShape {
  capabilities?: { streaming?: boolean;
                   extensions?: { url: string; required: boolean }[] };
}
export interface ProbeResult { card: any | null; reason?: string }
export interface ScenarioResult { scenario: Scenario; ok: boolean; reason?: string }

const INTERFACE_FAIL = 'no_jsonrpc_interface';

export async function fetchCard(
  cardUrl: string, fetchImpl: typeof fetch = fetch,
): Promise<ProbeResult> {
  let resp: Response;
  try { resp = await fetchImpl(cardUrl, { headers: { accept: 'application/json' } }); }
  catch { return { card: null, reason: 'network' }; }
  if (!resp.ok) return { card: null, reason: `http_${resp.status}` };
  try {
    const raw = await resp.json();
    return { card: (raw && typeof raw === 'object') ? raw : null,
             reason: raw && typeof raw === 'object' ? undefined : 'bad_json' };
  } catch { return { card: null, reason: 'bad_json' }; }
}

export function validateScenarios(
  rawCard: any, scenarios: Scenario[],
): ScenarioResult[] {
  const exts = rawCard?.capabilities?.extensions ?? [];
  const urls = new Set<string>(exts.map((e: any) => e?.url).filter(Boolean));
  return scenarios.map((s) => {
    const fail = (reason: string): ScenarioResult =>
      ({ scenario: s, ok: false, reason });
    const ok = (): ScenarioResult => ({ scenario: s, ok: true });
    if (!Array.isArray(rawCard?.supportedInterfaces)
        || !rawCard.supportedInterfaces.some((i: any) =>
          i?.protocolBinding === 'JSONRPC' && i?.protocolVersion === '1.0')) {
      return fail(INTERFACE_FAIL);
    }
    if (s === Scenario.STREAM) {
      return rawCard.capabilities?.streaming === true ? ok() : fail('not_streaming');
    }
    if (s === Scenario.RECOVERY) {
      return urls.has(REPLAY_URI) ? ok() : fail('missing_replay_extension');
    }
    return urls.has(RESOLVE_URI) ? ok() : fail('missing_resolve_extension');
  });
}
```

（测试里的 `rs.every((x: any) => !x.ok)` 这类断言照抄即可——`ScenarioResult` 是结构化对象，避免把断言写成对枚举值断言。）

- [ ] **Step 4: 验证通过；Step 5: Commit** `feat(a2a): card probe and per-scenario capability validation (M1)`

### Task B4: `capability.ts` 之三——传输选择缓存（含 locked 优先级）

**Files:**
- Create: `apps/frontend/src/adapters/a2a/choiceCache.ts`
- Test: `apps/frontend/src/adapters/a2a/__tests__/choiceCache.test.ts`

- [ ] **Step 1: 写失败测试**（spec §2 状态机 + §3.3 sticky 规则的纯逻辑面）

```ts
import { TransportChoiceCache, type EntryKind } from '@/adapters/a2a/choiceCache';

const makeCache = () => new TransportChoiceCache();

describe('传输选择缓存（spec §2：probe-v3 可翻，locked 不翻）', () => {
  it('缓存身份=二元组：同 sessionKey 不同 endpoint 互不串', () => {
    const c = makeCache();
    c.set('sess', 'http://a:20003', 'a2a');
    expect(c.get('sess', 'http://b:20003')?.kind).toBeUndefined();
    expect(c.get('sess', 'http://a:20003')?.kind).toBe('a2a');
  });

  it('probe 结果收敛：探测通过→a2a；缺项→probe-v3', () => {
    const c = makeCache();
    expect(c.decide('sess', 'http://a:20003', true).kind).toBe('a2a');
    expect(c.decide('sess', 'http://b:20003', false).kind).toBe('probe-v3');
  });

  it('普通 probe-v3 的 reconnect 重探可翻（再次 decide 即重算）', () => {
    const c = makeCache();
    c.decide('sess', 'http://a:20003', false);               // probe-v3
    expect(c.decide('sess', 'http://a:20003', true).kind).toBe('a2a');
  });

  it('locked 本页面会话内优先于探测结果：locked 后 decide 不翻案', () => {
    const c = makeCache();
    const base = 'http://a:20003';
    c.decide('sess', base, true);
    c.markLocked('sess', base);
    expect(c.decide('sess', base, true).kind).toBe('locked-v3');
  });

  it('清空=页面会话边界（新会话 re-probe）', () => {
    const c = makeCache();
    const base = 'http://a:20003';
    c.decide('sess', base, true);
    c.markLocked('sess', base);
    c.clear();
    expect(c.decide('sess', base, true).kind).toBe('a2a');
  });
});
```

- [ ] **Step 2: 验证失败**

- [ ] **Step 3: 最小实现**

```ts
/**
 * 传输选择缓存（spec stage2 §2/§3.3）。页内会话级 Map——不持久化，
 * 页面刷新即天然重探。probe-v3 可由后续探测翻案；locked-v3 由运行期
 * 硬失败设置（§3.3 窄定义），本页面会话内最高优先级，自动清除无效。
 */
export type EntryKind = 'a2a' | 'probe-v3' | 'locked-v3';
export interface ChoiceEntry { kind: EntryKind; at: number }

export class TransportChoiceCache {
  private readonly map = new Map<string, ChoiceEntry>();

  private key(sessionKey: string, endpointBase: string): string {
    // endpoint 在前——防 sessionKey 跨 endpoint 复用时的键位歧义
    return `${endpointBase}::${sessionKey}`;
  }

  get(sessionKey: string, endpointBase: string): ChoiceEntry | undefined {
    return this.map.get(this.key(sessionKey, endpointBase));
  }

  /** 探测收敛（locked 优先——已 locked 的会话探测结果不翻案）。 */
  decide(sessionKey: string, endpointBase: string, probeOk: boolean): ChoiceEntry {
    const k = this.key(sessionKey, endpointBase);
    const existing = this.map.get(k);
    if (existing?.kind === 'locked-v3') return existing;
    const entry: ChoiceEntry = { kind: probeOk ? 'a2a' : 'probe-v3', at: Date.now() };
    this.map.set(k, entry);
    return entry;
  }

  /** 运行期硬失败锁定（spec §3.3：传输不兼容/鉴权持续拒绝）。 */
  markLocked(sessionKey: string, endpointBase: string): ChoiceEntry {
    const entry = { kind: 'locked-v3' as const, at: Date.now() };
    this.map.set(this.key(sessionKey, endpointBase), entry);
    return entry;
  }

  clear(): void { this.map.clear(); }
}
```

（`Date.now` 在生产码里合法；测试注入不做，M2 接线时如需时序断言再抽 clock。）

- [ ] **Step 4: 验证通过；Step 5: Commit** `feat(a2a): transport choice cache with sticky locked-v3 (M1)`

### Task B5: `wire.ts` 之一——JSON-RPC 信封与失败分类

**Files:**
- Create: `apps/frontend/src/adapters/a2a/rpc.ts`
- Test: `apps/frontend/src/adapters/a2a/__tests__/rpc.test.ts`

契约依据（引擎实锤）：信封 `{jsonrpc:"2.0", id, params}`；响应 `data: {jsonrpc:"2.0", id, result|error}`；错误 `{code, message, data:{reason?}}`；`-32602` 参数错、`-32000`+reason 业务错误、`unknown_outcome`/`replay refused: <reason>`/`not_found_or_expired` 等实字已核。

- [ ] **Step 1: 写失败测试**

```ts
import { buildRpcRequest, parseRpcMessage, classifyFailure } from '@/adapters/a2a/rpc';

describe('JSON-RPC 信封（engine sse.py 口径：data 行=完整响应信封）', () => {
  it('请求信封带递增 id', () => {
    const a = buildRpcRequest('SendStreamingMessage', {});
    const b = buildRpcRequest('SendStreamingMessage', {});
    expect(a.jsonrpc).toBe('2.0');
    expect(typeof a.id).toBe('number');
    expect(b.id).toBe(a.id + 1);
  });

  it('result 信封解析', () => {
    const m = parseRpcMessage('{"jsonrpc":"2.0","id":7,"result":{"seq":1}}');
    expect(m?.id).toBe(7);
    expect(m?.result).toEqual({ seq: 1 });
    expect(m?.error).toBeUndefined();
  });

  it('error 信封解析（含 reason）', () => {
    const m = parseRpcMessage(
      '{"jsonrpc":"2.0","id":7,"error":{"code":-32000,"message":"x","data":{"reason":"not_found_or_expired"}}}');
    expect(m?.error?.code).toBe(-32000);
    expect((m?.error?.data as any)?.reason).toBe('not_found_or_expired');
  });

  it('非 JSON / 缺 id → null（协议级问题）', () => {
    expect(parseRpcMessage('not json')).toBeNull();
    expect(parseRpcMessage('{"jsonrpc":"2.0"}')).toBeNull();
  });
});

describe('失败分类（spec §3.3：仅 auth/protocol 进 lock 候选；abort 不是失败）', () => {
  it('主动 abort → null', () => {
    expect(classifyFailure({ aborted: true })).toBeNull();
  });

  it('HTTP 401/403 → auth（lock 候选）', () => {
    expect(classifyFailure({ httpStatus: 401 })?.kind).toBe('auth');
  });

  it('其他 HTTP 非 2xx → transport（不锁）', () => {
    expect(classifyFailure({ httpStatus: 502 })?.kind).toBe('transport');
  });

  it('-32602 参数错 → rpc（业务上浮）', () => {
    expect(classifyFailure({ rpcError: { code: -32602, message: 'x' } })?.kind).toBe('rpc');
  });

  it('-32000 业务 reason → rpc（恢复流程吃掉，不锁）', () => {
    expect(classifyFailure({ rpcError: { code: -32000, message: 'x',
      data: { reason: 'cache_limit' } } })?.kind).toBe('rpc');
  });

  it('无任何已知特征 → protocol（lock 候选）', () => {
    expect(classifyFailure({})?.kind).toBe('protocol');
  });
});
```

- [ ] **Step 2: 验证失败**

- [ ] **Step 3: 最小实现**

```ts
/**
 * A2A JSON-RPC 信封与失败分类（spec stage2 §3.3）。
 * LOCK_CANDIDATE：仅"传输面不兼容/鉴权面持续拒绝"进 locked-v3；
 * 普通 transport 断流走恢复正路，业务 rpc 错误上浮 UI。
 */
export interface RpcRequest { jsonrpc: '2.0'; id: number; method: string; params: unknown }
export interface RpcError { code: number; message: string; data?: Record<string, unknown> }
export interface ParsedRpc { id: number | string; result?: unknown; error?: RpcError }
export type FailureKind = 'auth' | 'protocol' | 'rpc' | 'transport';
export interface WireFailure { kind: FailureKind; httpStatus?: number; rpcError?: RpcError }

export const LOCK_CANDIDATE_KINDS: ReadonlySet<FailureKind> = new Set<FailureKind>(['auth', 'protocol']);

let nextId = 1;
export function buildRpcRequest(method: string, params: unknown): RpcRequest {
  return { jsonrpc: '2.0', id: nextId++, method, params };
}

export function parseRpcMessage(raw: string): ParsedRpc | null {
  let parsed: any;
  try { parsed = JSON.parse(raw); } catch { return null; }
  if (parsed == null || typeof parsed !== 'object') return null;
  if (parsed.id == null) return null;
  if (parsed.result === undefined && parsed.error === undefined) return null;
  return parsed;
}

export function classifyFailure(input: {
  httpStatus?: number; rpcError?: RpcError; aborted?: boolean;
}): WireFailure | null {
  if (input.aborted) return null;                       // 主动取消不是失败
  if (input.rpcError) return { kind: 'rpc', rpcError: input.rpcError };
  const s = input.httpStatus;
  if (s === 401 || s === 403) return { kind: 'auth', httpStatus: s };
  if (s != null) return { kind: 'transport', httpStatus: s };
  return { kind: 'protocol' };                          // 无已知特征=协议问题
}
```

- [ ] **Step 4: 验证通过；Step 5: Commit** `feat(a2a): json-rpc envelope and failure taxonomy (M1)`

### Task B6: `wire.ts` 之二——SSE 流解析（真 ReadableStream）

**Files:**
- Create: `apps/frontend/src/adapters/a2a/stream.ts`
- Test: `apps/frontend/src/adapters/a2a/__tests__/stream.test.ts`

线上格式实锤（router `_sse()`/`_stream_response()`）：`POST /rpc` 响应 `text/event-stream`，每个 `data: {json}\n\n` 是**完整 JSON-RPC 响应信封**；`result` 为 wire 事件 `{seq, type, ev: {statusUpdate|artifactUpdate|...}}`；流在 `statusUpdate.metadata.final == "true"` 后由服务端关闭。

- [ ] **Step 1: 写失败测试**

```ts
import { streamRpc } from '@/adapters/a2a/stream';
import { buildRpcRequest } from '@/adapters/a2a/rpc';

const enc = new TextEncoder();
function sseResponse(chunks: string[], status = 200): Response {
  const stream = new ReadableStream({
    start(c) { chunks.forEach((ch) => c.enqueue(enc.encode(ch))); c.close(); },
  });
  const resp = new Response(stream, { status });
  return resp;
}
const evt = (body: any) =>
  `data: ${JSON.stringify({ jsonrpc: '2.0', id: 1, result: body })}\n\n`;
const artifactEvt = (seq: number, text: string) => evt({
  seq, type: 'artifactUpdate',
  ev: { artifactUpdate: { taskId: 't1', contextId: 'c1',
    artifact: { parts: [{ text }] }, append: true, lastChunk: false,
    metadata: { seq } } },
});
const finalEvt = (seq: number) => evt({
  seq, type: 'statusUpdate',
  ev: { statusUpdate: { taskId: 't1', contextId: 'c1',
    status: { state: 'TASK_STATE_COMPLETED' },
    metadata: { seq, final: 'true', lastSeq: seq } } },
});

async function collect(gen: AsyncGenerator<any>) {
  const out: any[] = [];
  for await (const e of gen) out.push(e);
  return out;
}

describe('SSE 流解析（engine _sse() 口径）', () => {
  it('逐事件产出：task/statusUpdate/artifactUpdate 转成扁平 wire 事件', async () => {
    const fetchImpl = async () => sseResponse([
      evt({ seq: 0, type: 'task', ev: { task: { id: 't1', status: {} } } }),
      artifactEvt(1, '你'),
      finalEvt(2),
    ]);
    const events = await collect(streamRpc('http://x/rpc', buildRpcRequest('SendStreamingMessage', {}), { fetchImpl: fetchImpl as any }));
    expect(events.map((e) => [e.seq, e.type])).toEqual([[0, 'task'], [1, 'artifactUpdate'], [2, 'statusUpdate']]);
    expect(events[1].metadata).toEqual({ seq: 1 });
    expect(events[2].metadata.final).toBe('true');
  });

  it('final marker 到达即停（不再读流尾）', async () => {
    const stream = new ReadableStream({
      start(c) { c.enqueue(enc.encode(artifactEvt(1, 'a') + finalEvt(2)
        + artifactEvt(99, 'late'))); c.close(); },
    });
    const fetchImpl = async () => new Response(stream, { status: 200 });
    const events = await collect(streamRpc('http://x/rpc', buildRpcRequest('m', {}), { fetchImpl: fetchImpl as any }));
    expect(events.map((e) => e.seq)).toEqual([1, 2]);   // late 事件不被消费
  });

  it('分块跨界（半行）正确拼接', async () => {
    const whole = artifactEvt(1, 'x').split('\n\n')[0] + '\n\n';
    const [head, tail] = [whole.slice(0, 20), whole.slice(20)];
    const fetchImpl = async () => sseResponse([head, tail, finalEvt(2)]);
    const events = await collect(streamRpc('http://x/rpc', buildRpcRequest('m', {}), { fetchImpl: fetchImpl as any }));
    expect(events.map((e) => e.seq)).toEqual([1, 2]);
  });

  it('SSE 注释行/非 data 行被忽略', async () => {
    const fetchImpl = async () => sseResponse([
      ': keepalive\n\n', artifactEvt(1, 'a'), finalEvt(2),
    ]);
    const events = await collect(streamRpc('http://x/rpc', buildRpcRequest('m', {}), { fetchImpl: fetchImpl as any }));
    expect(events.map((e) => e.seq)).toEqual([1, 2]);
  });

  it('流中 error 信封 → 抛 rpc 失败（typed）', async () => {
    const fetchImpl = async () => sseResponse([
      `data: ${JSON.stringify({ jsonrpc: '2.0', id: 1,
        error: { code: -32000, message: 'x', data: { reason: 'not_found_or_expired' } } })}\n\n`,
    ]);
    await expect(collect(streamRpc('http://x/rpc', buildRpcRequest('m', {}), { fetchImpl: fetchImpl as any })))
      .rejects.toMatchObject({ failure: { kind: 'rpc' } });
  });

  it('HTTP 非 2xx → 抛 failure（kind=transport/auth 由 classify 定）', async () => {
    const fetchImpl = async () => sseResponse([], 502);
    await expect(collect(streamRpc('http://x/rpc', buildRpcRequest('m', {}), { fetchImpl: fetchImpl as any })))
      .rejects.toMatchObject({ failure: { kind: 'transport', httpStatus: 502 } });
  });

  it('iamToken 进 x-iam-token 头；accept=text/event-stream', async () => {
    let seen: HeadersInit | undefined;
    const fetchImpl = async (_u: string, init?: RequestInit) => {
      seen = init?.headers as HeadersInit;
      return sseResponse([finalEvt(2)]);
    };
    await collect(streamRpc('http://x/rpc', buildRpcRequest('m', {}), { iamToken: 'IAM-T', fetchImpl: fetchImpl as any }));
    const h = new Headers(seen);
    expect(h.get('x-iam-token')).toBe('IAM-T');
    expect(h.get('accept')).toBe('text/event-stream');
  });
});
```

- [ ] **Step 2: 验证失败**

- [ ] **Step 3: 最小实现**

```ts
/**
 * A2A SSE 流解析（engine router _sse()/_stream_response() 口径）。
 * wire 事件形态：result = {seq, type, ev: {statusUpdate|artifactUpdate|task|message}}；
 * 终态以 statusUpdate.metadata.final==="true" 判定（服务端随后关流）。
 * M1 不含恢复/取消——这里只有单向流消费。
 */
import { parseRpcMessage, classifyFailure, type RpcRequest, type WireFailure } from './rpc';

export interface WireEvent {
  seq: number;
  type: string;                       // 'task' | 'statusUpdate' | 'artifactUpdate' | ...
  member: Record<string, unknown>;    // ev.* 的内层成员
  metadata: Record<string, unknown>;
}

const SEP = '\n\n';

function memberMetadata(type: string, ev: Record<string, unknown>): Record<string, unknown> {
  const inner = (ev[type] ?? {}) as Record<string, any>;
  return (inner.metadata ?? {}) as Record<string, unknown>;
}

export async function* streamRpc(
  rpcUrl: string, request: RpcRequest,
  opts: { iamToken?: string; signal?: AbortSignal; fetchImpl?: typeof fetch },
): AsyncGenerator<WireEvent> {
  const headers = new Headers({
    'content-type': 'application/json',
    accept: 'text/event-stream',
    ...(opts.iamToken ? { 'x-iam-token': opts.iamToken } : {}),
  });
  let resp: Response;
  try {
    resp = await (opts.fetchImpl ?? fetch)(rpcUrl, {
      method: 'POST', headers, signal: opts.signal,
      body: JSON.stringify(request),
    });
  } catch (err) {
    if (opts.signal?.aborted) return;                  // 主动取消：非失败
    throw Object.assign(new TypeError('a2a transport'), {
      failure: classifyFailure({}) ?? { kind: 'transport' },
    });
  }
  if (!resp.ok) {
    const failure = classifyFailure({ httpStatus: resp.status });
    throw Object.assign(new Error(`a2a http ${resp.status}`), { failure });
  }
  if (!resp.body) {
    throw Object.assign(new Error('a2a empty body'), { failure: { kind: 'protocol' } });
  }

  const reader = (resp.body as ReadableStream<Uint8Array>).getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) return;
      buffer += decoder.decode(value, { stream: true });
      let idx: number;
      while ((idx = buffer.indexOf(SEP)) >= 0) {
        const rawEvent = buffer.slice(0, idx);
        buffer = buffer.slice(idx + SEP.length);
        for (const line of rawEvent.split('\n')) {
          if (!line.startsWith('data:')) continue;    // 注释/空行忽略
          const parsed = parseRpcMessage(line.slice(5).trim());
          if (!parsed) {
            throw Object.assign(new Error('a2a bad envelope'), { failure: { kind: 'protocol' } });
          }
          if (parsed.error) {
            throw Object.assign(new Error(parsed.error.message), {
              failure: classifyFailure({ rpcError: parsed.error })!,
            });
          }
          const result = parsed.result as Record<string, any> | undefined;
          if (!result || typeof result !== 'object' || typeof result.type !== 'string') {
            throw Object.assign(new Error('a2a bad result'), { failure: { kind: 'protocol' } });
          }
          const ev = (result.ev ?? {}) as Record<string, unknown>;
          const event: WireEvent = {
            seq: result.seq, type: result.type,
            member: (ev[result.type] ?? {}) as Record<string, unknown>,
            metadata: memberMetadata(result.type, ev),
          };
          yield event;
          if (event.type === 'statusUpdate' && event.metadata.final === 'true') {
            return;
          }
        }
      }
    }
  } finally {
    reader.releaseLock();
    await (resp.body as ReadableStream<Uint8Array>).cancel().catch(() => undefined);
  }
}
```

- [ ] **Step 4: 验证通过**

Run: `cd ~/IdeaProjects/ocb-a2a-stage2/apps/frontend && npx jest src/adapters/a2a --silent`
Expected: 全 PASS（B1-B6 全部用例）

- [ ] **Step 5: Commit** `feat(a2a): sse stream parsing over real ReadableStream (M1)`

### Task B7: M1 退出门（不接线核验 + 全量）

- [ ] **Step 1: 不接线核验**（spec §7 M1："默认 off、不接线"）

```bash
cd ~/IdeaProjects/ocb-a2a-stage2/apps/frontend
grep -rn "adapters/a2a" src --include="*.ts" --include="*.tsx" -l \
  | grep -v "^src/adapters/a2a"; echo "---应零输出（无任何消费方导入）---"
grep -rn "isA2aClientEnabled\|streamRpc\|validateScenarios" src \
  | grep -v "src/adapters/a2a/"; echo "---应零输出（无接线点）---"
```
Expected: 两段 grep 均零命中（A2A 模块自身目录外零导入）。

- [ ] **Step 2: M1 范围全量**：`npx jest src/adapters/a2a --silent` 全绿；`npx jest src/adapters --silent` 既有测试无回归。

- [ ] **Step 3: 最终提交**（若 B0 spec 回填未提交）+ 推分支：

```bash
git add -A && git commit -m "feat(a2a): M1 wire/probe/switch modules, default off, unwired"
git push -u origin feat/a2a-frontend-client
```

---

## 范围外（下一计划 M2+，spec §7）

A2AProvider 接线、SendStreamingMessage 恢复/取消/禁发规则、HITL 结构化事件、M2 的禁发-恢复预算测试（终审约束 3）、企业代理链映射。

## Self-Review 记录

- **Spec 覆盖**：P0=spec §5 全文；M1=spec §1（开关默认 off/唯一入口）、§2（地址映射/缓存身份/sticky）、§3.3（失败分类）。M1 按定义不含 §3 恢复/§4 流程（M2）✓
- **类型一致**：`WireEvent{seq,type,member,metadata}` B6 定义/测试一致；`classifyFailure` 返回 `WireFailure|null` 与 B5/B6 用例一致；`Scenario` enum 与 `ScenarioName` 字面量在 card.ts 内一致 ✓
- **无占位**：B3 Step3 说明删除 `hasJsonrpc10` 占位（校验内联）；无 TBD ✓