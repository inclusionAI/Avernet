# W7 — 命名源与 git 源：实现设计

日期：2026-09-02
分支：`feat/bot-config-manifest-w7-git-sources`（基于 origin/dev，W5 已合入）
上游 spec：`src/backend/docs/bot-config-manifest/work-items.zh-CN.md` §W7（#1475）——**本文不重复其验收标准；只记录把它落到这个代码库的实现决策。**

## 0. 背景与已定前提

W1 已交付 schema 层的全部形状契约：

- `sources` 顶层声明、`from` 引用、`from`/`source`/`content` 三选一互斥、
  未引用源的警告位（`schema/_support.py` 的 `declared_sources` /
  `referenced_sources`）；
- git 源键集 `_GIT_SOURCE_KEYS = {git, ref, subpath, auth, mode}`、
  `mode ∈ {strict, non_strict}` 缺省 `non_strict`（`schema/entries.py`）。

W5 已交付取源骨架：`apply/entry_fetch.py` 的 `EntryFetcher` 目前只消费
**内联 URL 源**。W7 补上 `from` 的解析与 git 源的获取——这是 W7 在代码上的
全部空间。

两项用户拍板的实现决策（2026-09-02）：

1. **分支策略** —— git worktree 基于 origin/dev 开新分支（W7 只依赖 W5，
   不依赖在途的 W6）。
2. **git 客户端** —— subprocess 调 git CLI。沿用
   `skill_center/services/git_sync.py` 的既有先例（`git fetch --depth=1`
   子进程），零新依赖、不动 corp 依赖镜像清单；凭证经
   `-c http.<url>.extraHeader=` 注入。拒绝 dulwich：新依赖需要 corp 侧同步，
   成本不成比例。

## 1. 架构

```text
schema (W1：from / sources / mode 校验已在，不改)
   │
apply/entry_fetch.py  (W5 模块内扩展)
   │  entry.source_url → 既有 URL 路径  → GuardedFetcher (W2，不动)
   │  entry.from       → 命名源解析 → 源声明分派
   │                                    ├─ url 源 → 同上 URL 路径
   │                                    └─ git 源 → fetch/git_source.py (新)
   ▼
content store (W11)：git 条目的字节照常入库存证
```

**git 条目在 W11 里的 receipt 身份**：不是 URL，而是一个规范形
`git+<repo-url>@<resolved-sha>:<path-in-repo>` 的伪 URL。它以解析后的 SHA
为键，所以同一个 ref 移动后天然是新地址（keep_last 不会被移动的 ref 骗到重用
旧记录），同一 SHA 内多个条目互不冲突。

## 2. 新模块 `fetch/git_source.py`

W7 的核心。一个模块、一个公开协议、一个 subprocess 实现。

### 2.1 协议与实现

- **`GitSourceClient`**（protocol）：两个操作——
  - `resolve(url, ref, auth) -> str`：把 ref 解析为 commit SHA（不取内容）；
  - `fetch(url, ref, subpath, auth) -> GitCheckout`：浅层单 ref fetch，
    返回限定在 `subpath` 的本地 checkout 路径 + 已解析 SHA。
- **`SubprocessGitClient`**：唯一实现。**不用 `clone --branch`**（它只吃
  branch/tag， 而 ref 还可能是 40-hex SHA 形式——schema 对 SHA ref 是
  「接受但 mode 无效」）。统一走 `git init`（临时目录）→ `git remote add
  origin <url>` → `git fetch --depth=1 origin <ref>`：branch/tag/SHA 一条
  代码路径；SHA 直取依赖服务端 `allowAnySHA1InWant`（拒了就按 ref 不存在
  报 `FetchFailedError` 语义，不区别对待）。SHA 用 `git rev-parse
  FETCH_HEAD` 取。`resolve` 单独存在只为 dry_run 与报告需要，实现上就是
  一次 fetch——协议留着两个操作，实现可以共用一条路径。

### 2.2 凭证注入

- 源声明的 `auth` 指向 W3 凭证服务的 `header` 类型凭证
  （X1 已关闭的决定：专用机器账号 + read_repository 令牌，HTTP Basic）。
- 注入方式（`fix/w7-review-fixes` 修订）：`GIT_CONFIG_KEY_n=http.extraHeader` / `GIT_CONFIG_VALUE_n` **env** 注入。
  **凭证绝不进 URL、不进 argv（ps 对所有本机用户可见）、不进日志**——错误信息里只出现
  凭证名，沿用 `EntryFetchError` 的安全属性。
- 凭证在 fetch 前经 W3 前缀授权（`git` URL 就是授权对象），与 URL 源同一套政策。

### 2.3 只读与包含性（在内容进 W11 之前）

fetch 天然不执行仓库 hook；不加 `--filter`；此外对 checkout 强制：

- 拒绝 symlink（任何成员是符号链接即拒——`payload -> /etc` 正是 W2 防的那类危险物）；
- 拒绝 gitlink/submodule 条目与设备/特殊条目；
- 检查发生在 **checkout 落盘之后、字节进入 EntryFetcher/W11 之前**。

### 2.4 解开后的限额（不只线上字节）

一个小 pack 能展开成一棵巨大的树。对 checkout 施加（复用 `fetch/limits.py`
的量纲，数值独立配置并与 schema §5 对齐）：

- 解开后总字节数上限；
- 文件/成员数上限；
- 单文件大小上限。

任一超限即失败并**清理临时目录**（`finally` 路径，不依赖调用方自觉）。

### 2.5 错误语义：归入 W2 的既有二分

| 情形 | 归属 | keep_last |
| --- | --- | --- |
| 非 https、subpath 逃逸、限额拒绝、包含性拒绝 | `FetchRefusedError` 语义（触网前） | **不可**回落 |
| 网络失败、clone 失败、坏对象、ref 不存在 | `FetchFailedError` 语义（触网后） | 可回落 |

理由沿 W5 已定裁决：refusal 是对文档配置的陈述，keep_last 是对源可用性的陈述。

## 3. `EntryFetcher` 扩展（`apply/entry_fetch.py`）

### 3.1 `from` 解析

新增入口：materialiser 现在传 `source_url`，改为可传整个源选择
（`from` 名或内联 source），`EntryFetcher` 完成：

1. `from` 名 → 顶层 `sources` 里的声明（schema 已在 PUT 时校验存在性；
   apply 侧再查一次缺名即 `FetchRefusedError` 语义——防两窗口间文档漂移）；
2. 源上的 `auth` 归属源，不由条目携带（W7 验收标准）；
3. URL 源走既有 `fetch()`；git 源走 §2 的客户端。

### 3.2 单 apply 拉取缓存：`SourceSession` 挂在 `ApplyContext` 上

取证修正：`EntryFetcher` 在 DI 里是 **@singleton**（`manifest_fetch_module.py`
`manifest_entry_fetcher`），不是"每次 apply 新建"——per-apply 状态放它实例上会跨
apply 泄漏。正确位置有现成先例：`ApplyContext.budget`（"mutable by design inside
the frozen context"）。W7 新增 `apply/source_session.py` 的 `SourceSession`：

- 持有：文档的 `sources` 声明、strict 基线（来自上次 apply 报告）、git 客户端；
- 可变状态（per-apply）：`dict[(git_url, ref), GitCheckout]`——同一 `{git, ref}`
  拉一次，全体引用条目复用；SHA 解析同样只做一次；
- 记录 `SourceResolution`，apply 结束时 orchestrator 把它填进 `ApplyReport.sources`；
- `close()` 在 apply 结束（含启动失败路径）清理临时 checkout，幂等；
- 由 apply service 在 `start_apply`/`dry_run` 构造，经 `ApplyContext.source_session`
  下发（测试/手工路径为 `None`，`fetch_declared` 拒绝无 session 的 `from`/git 条目）。

## 4. strict 模式与 SHA 记录（零迁移）

存储问题已由代码库内置答案：`outcomes.py` 的 `SourceResolution`
（name/ref/resolved_sha/auth）与 `ApplyReport.sources` 是 W5 预留给 W7 的，
报告 JSON 存在 `ac_bot_config_manifest_apply.report`（MEDIUMTEXT）。
**不新开表。**

- 每次 apply，所有被引用的命名源各产出一条 `SourceResolution`
  （声明的 ref + 解析出的 SHA + 凭证名），进 `ApplyReport.sources`；
- strict 判定基线 = `last_apply()`（apply 服务已有该方法）报告里同名源的
  `resolved_sha`；无历史记录（首次 apply）不触发 strict；
- `strict`：SHA 不同 → 该**条目** `failed`（bot 继续跑现状），不牵连其他类目
  （§2.7 逐类目 all-or-nothing）；
- `non_strict`（缺省）：应用新内容 + 报告该条目记告警，写明前后 SHA；
- `mode` 写在 SHA 形式的 ref 上是「接受但无效」（schema 已如此；apply 不再警告）。

## 5. 手感细节

- git 源的目录条目不需要 `unpack`/`strip_components`（schema 语义已定，
  物化侧由 W6 分支消费，W7 不碰 `apply/materialisers/resources.py`）；
- 未引用的源：PUT 响应已是警告（W1）；apply 报告不再重复；
- 移动 tag 的收敛：`non_strict` 下 `from` 源每次 apply 重解析，自然收敛。

## 6. 测试

- **`SubprocessGitClient`**：pytest 里用 `git init --bare` 起真实本地仓库
  （`git_sync` 的测试同款思路），覆盖：浅拉取、subpath 限定、symlink 拒绝、
  空间限额、坏 ref、移动 tag、strict/non-strict 两条路径、临时目录清理。
  本地 file:// 远端即可——协议层差异由 §2.2 的注入路径独立用例覆盖
  （对 https URL 构造的 argv 断言，不真发网）。
- **`EntryFetcher` from 解析**：fake `GitSourceClient`，验证单次拉取复用、
  错误二分、`_from` 缺名拒绝。
- **报告**：`SourceResolution` 进 payload 的形状断言 + strict 基线读取
  `last_apply` 的行为。
- coverage 门槛：changed-line ≥80%，本地 `ci_test.sh` 验证后再推。

## 7. 刻意不做

- 不加 dulwich / 任何新依赖；
- 不实现 git-over-http 协议（走 CLI）；
- 不做跨类目下发原子性（§2.7 已明确 v1 没有）；
- 不碰 W6 的 resources 物化器（另一分支在途，git 源对它可用即止）；
- 不给 `DeviceFileSystem` 加原子子树替换（那是 W6 的选项 2，与本项无关）。
