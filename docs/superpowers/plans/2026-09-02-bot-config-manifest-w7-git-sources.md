# W7 — 命名源与 git 源 Implementation Plan

> **已执行完毕的存档。**本 wave 经 [#1829](https://github.com/inclusionAI/Avernet/pull/1829) 合入 dev,评审修复经 [#1835](https://github.com/inclusionAI/Avernet/pull/1835) 交付。原计划曾按 writing-plans 模板在每一步内嵌完整代码——交付后那些快照与仓库本体重复,且 #1835 改动了其中一部分(凭证通道、限额时点、失败文本、基线语义),留着即是过期副本。本文件因此收编为**决策与不变量的记录**:每条任务写清交付了什么、为什么这样裁、落在哪个模块,不再复制源码。执行期的逐日志见文末(append-only,原始保留)。

**Goal:** manifest 条目通过 `from` 引用命名源，git 仓库成为一等公民源——一次浅层单 ref fetch 把整份配置解析到同一个 commit，strict 模式在 ref 漂移时拒绝下发。

**Architecture:** 三层增量，不动既有契约——schema（W1）已全部就绪；`EntryFetcher` 加 `fetch_declared` 完成 `from` 解析与 URL/git 分派；新模块 `fetch/git_source.py`（subprocess git CLI）+ `apply/source_session.py`（per-apply 状态，挂 `ApplyContext`，同 `budget` 先例）。语法严格依据 spec：`docs/superpowers/specs/2026-09-02-bot-config-manifest-w7-git-sources-design.md`。

**Tech Stack:** Python 3.12、pytest、subprocess git（沿用 `skill_center/services/git_sync.py` 先例，零新依赖）。

**执行前取证事实（写代码前不需要重查，存档供回溯）：**

- `EntryFetcher` 是 DI 单例——per-apply 状态一律走 `ctx.source_session`，绝不放 fetcher 实例上。
- `ApplyReport.sources` 与 `outcomes.SourceResolution(name, ref, resolved_sha, auth)` 是 W5 预留给 W7 的空位；服务已有 `last_apply()`——strict 基线零迁移（#1835 后改为 `recent(limit)` 有界回走，见 Task 8）。
- `EntryFetcher.fetch(...)` 是现有 URL 路；materialiser 用 `asyncio.to_thread` 调它。
- `FakeGuardedFetcher / FakeManifestContent / FakeCredentials / make_context / fetched_object` 全在 `apply/_fakes.py`。
- 测试命令在 `src/backend/` 下跑；测试路径相对 `src/backend/tests/community/core/bot_config_manifest/`。

---

## File Structure（全部已落地）

| 文件 | 职责 | 落点 |
| --- | --- | --- |
| `fetch/limits.py` | git checkout 常量四枚 | `Test 1` |
| `fetch/git_source.py` | `GitSourceSpec` / `GitSourceClient` / `SubprocessGitClient` / `GitCheckout` / `git_receipt_url` | `Test 2–3` |
| `apply/source_session.py` | per-apply `SourceSession` | `Test 4` |
| `apply/context.py` | `source_session` 字段 | `Test 5` |
| `apply/entry_fetch.py` | `fetch_declared` / `file_bytes` / `GitEntrySource` | `Test 5` |
| `apply/orchestrator.py` | 报告填 `sources` | `Test 8` |
| `apply/materialisers/identity.py` | git 单文件路 | `Test 6` |
| `apply/materialisers/skills.py` | git 树路 `_git_package` | `Test 7` |
| `services/config_manifest_apply_service.py` | session 构造/关闭、基线读取 | `Test 8` |
| `di/modules/manifest_fetch_module.py` | `GitSourceClient` provider + 延迟工厂 | `Test 8` |
| `fetch/test_git_source.py`、`apply/test_source_session.py` | 新建测试 | 各自 Task |
| `apply/test_entry_fetch.py` 等五份既有测试 | git 路扩展 | 各自 Task |

（路径前缀 `<R> = src/backend/src/agentclaw/community/core/bot_config_manifest`，`<T> = src/backend/tests/community/core/bot_config_manifest`。）

---

### Task 1: limits 的 git checkout 常量

`<R>/fetch/limits.py` 追加四枚常量：`GIT_CHECKOUT_UNPACKED_LIMIT`（对齐 `FETCH_ENTRY_LIMITS["resources_unpacked"]`）、`GIT_CHECKOUT_MEMBER_LIMIT`（对齐 `ARCHIVE_MEMBER_LIMIT`）、`GIT_SINGLE_FILE_LIMIT`（对齐 skills 条目限额）、`GIT_FETCH_TIMEOUT_S`（120s，网络+盘双层时长的折中）。

**不变量:** checkout 与解包档案是同一类危害，数字对齐既有档案词汇，不造第二套会漂移的方言——由 `test_git_limits_align_with_the_archives_they_generalise` 钉住。

Commit: `feat(w7): git checkout limits, aligned with the archive numbers`

---

### Task 2: `SubprocessGitClient`——fetch、解析、凭证注入、拒绝面

`<R>/fetch/git_source.py` 新建模块头三件套：`GitSourceSpec`（url/ref/subpath/mode/auth，auth 是凭证**名**）、`git_receipt_url(url, sha, subpath)`（W11 收据身份，按 resolved SHA 键控——移动的 ref 是不同地址，keep_last 无法拿旧记录顶新 commit）、`SubprocessGitClient.fetch`（init → remote → `fetch --depth=1 origin <ref>` → `rev-parse` → `ls-tree -r -l` → `checkout --detach`）。

**不变量（部分经 #1835 修订为现行形态）:**

- **拒绝先于子进程**:非 https scheme、URL 带控制字符（会注入 `.git/config`）、ref 以 `-` 开头（会被读成 `--upload-pack` 类选项）都在任何 spawn 之前拒——`FetchRefusedError`,keep_last 不得掩饰的类。
- **只读按构造**:fetch 不执行任何服务端可控输入（无 hook、无 filter);树在读取之前先枚举,`_ALLOWED_MODES` 只收 `100644/100755`，symlink（120000）与 gitlink（160000）在任意字节进平台前拒绝。
- **凭证通道**（#1835 修订）:W3 header 凭证经 `GIT_CONFIG_KEY_n=http.extraHeader` / `GIT_CONFIG_VALUE_n` **env** 注入——绝不进 URL（会持久进收据）、绝不进 argv（`ps` 对所有本机用户可见）、绝不进整份父环境。原计划走 `-c` argv，#1835 依评审改道,本节以现行形态为准。
- **环境来源**（#1835 修订）:base env 由 DI 组合根读取（repo 的 raw-env 规则），核心侧剥掉全部 `GIT_*` 再加封闭覆盖（`GIT_TERMINAL_PROMPT=0`、`GIT_ASKPASS`、`GIT_CONFIG_GLOBAL/SYSTEM=devnull`）。
- **remote 不走 argv**（#1835 修订）:origin 直接写进 0700 mkdtemp 目录的 `.git/config`，URL 不出现在任何子进程 argv。
- **失败即清理**：任何异常路径 `rmtree` 临时目录再抛。
- **SHA 严格**：`rev-parse` 结果必须匹配 40 位小写 hex,否则 `FetchFailedError`。

测试正面（真实 git 跑 on-disk bare repo，scheme 放宽到 `file`）落在 `fetch/test_git_source.py`。

Commit: `feat(w7): subprocess git client with refusal-first scheme guard`

---

### Task 3: `GitCheckout` 读者——包含性、限额、subpath

`GitCheckout` 的 `files(subpath, *, file_limit)` 与 `read_file(subpath, *, file_limit)` 两条读者路,加 `_require_safe` / `_is_plain_name` / `_under_subpath` 三个模块级守卫。

**不变量（限额时点经 #1835 前移）:**

- 读者只走枚举过的成员、只从 checkout 自己的 root 下解析、`resolve()` 结果必须在 root 内——树作者留什么都走不出去。
- **限额按声明尺寸裁决**（#1835 修订）:`ls-tree -r -l` 带 blob 尺寸,unpacked 总额在 `git checkout` 落盘**之前**拒绝、单文件按声明尺寸在读取**之前**拒绝（不再先读 6GiB 再拒）;`file_limit` 由类目侧传入（`FETCH_ENTRY_LIMITS` 同词汇),不再一枚 skills 数字通吃。
- **quotepath 反转义**（#1835 修订）:`core.quotepath` 把非 ASCII 名转 C 引号转义,枚举时反转义（中文文件名可匹配可读）；反转义后非 UTF-8 的名按纯 ASCII 引号形式干净拒绝。原计划无此步——评审实测「仓库里一个中文文件名即让 skills 路整类目中断」后补。
- subpath 安全(PUT 规则在 apply 重问一遍,因存量文档可能漂移);空选择、目录当单文件都成为该条目的 `FetchRefusedError`。
- `tree_bytes` 属性:整树的声明字节数,apply 账本按它记账（#1835）。

Commit: `feat(w7): guarded tree readers for the git checkout`

---

### Task 4: `SourceSession`——per-apply 缓存、resolution 记录、基线、关闭

新建 `<R>/apply/source_session.py`:四样东西——文档 `sources` 冻结快照、strict 基线 map、`(url, ref)` checkout 缓存、报告将携带的 `SourceResolution` 记录。挂在 `ApplyContext`,同 `budget` 的「frozen context 里显式可变」先例。

**不变量（采纳时点经 #1835 修订）:**

- 同一 `(url, ref)` 每个 apply 只 fetch 一次;`checkout()` 返回 `(checkout, fresh)`,只有首次调用的调用者被标记为「真的动了网络」。
- **采纳与取出是两件事**:checkout 只取回树;`adopt()` 在 strict 门**之后**由 fetch 层调用才记录 resolution——被拒的移动不进报告,基线不会毒化(「refuse 一次后照发」即此修复挡掉的)。逐 display 幂等。
- fetch 失败不采纳任何东西（报告对该源无记录,配合 Task 8 的历史回走,基线得以幸存)。
- `close()` 幂等移除全部 checkout 树;服务在所有终态路径调用（见 Task 8）。

Commit: `feat(w7): per-apply source session with once-per-ref checkout cache`

---

### Task 5: `fetch_declared` / `file_bytes` / `GitEntrySource`

`<R>/apply/entry_fetch.py` 与 `<R>/apply/context.py` 的增量。`fetch_declared(ctx, *, entry, category, entry_identity)` 是新前端:**分派**——字符串 `source` 走旧 `fetch` 不变;`from` 名/内联 Mapping 取声明;声明无 `git` 键则为 URL 路折入源上的 `auth`;有 `git` 键走 git 路。`GitEntrySource` 是 git 路返回的「已证明安全的树」,由类目解释(单文件?包?)——fetch 层不替类目回答。

**不变量:**

- 纯 URL 路不经 session——**只有真正读 session 的路(`from` 查找+git 路)才要求 session 在场**(执行日志 18:1x 的跨轨教训:门槛放分派前会拦死 W5 存量 URL 路)。
- git 路先取凭证 binding（W3 injector 语义）→ session.checkout → 记账（fresh 才 charge `tree_bytes`,charge 后查 expired）→ strict 门 → adopt → 返回携带 `auth`/`file_limit`/`moved_from` 的 `GitEntrySource`。
- v1 收窄的拒绝消息（digest 与 #1835 增补的 entry 级 `subpath`/`auth`):"not supported on a git source in v1" 式精确措辞,指明该写哪（SHA ref 钉 pin/subpath 声明在源上/auth 声明在源对象里）。
- strict:基线非空且异于 resolved SHA → 条目级拒绝（bot 继续跑旧树, report 带理由);non-strict 移动 → `moved_note()` 报告行。
- `_git_keep_last`:fetch 失败时按 **baseline-SHA 收据**回落（`git_receipt_url(url, baseline, subpath)`),无基线则无回落资格;note 一句话（#1835:不再嵌错误原文——错误文本本身已报告安全）。
- `file_bytes(...)`:把类目自己的 canonical 字节（identity 单文件/skills canonical zip)按收据身份入 W11 库,`credential_name` 一并穿线（#1835）——审计与 keep_last 读同一份库。
- `FetchedEntry.source_url`:URL 路补携带，skills 的 archive-kind 推断用。

Commit: `feat(w7): declared-source resolution and the git entry road`

---

### Task 6: identity materialiser 的 git 路

`<R>/apply/materialisers/identity.py` 的 resolve 循环加 `GitEntrySource` 分支:声明无 subpath → 条目级拒绝（identity 恰好读一个文件,「哪份文件」住在源的 subpath 上）;否则 `decl.read_file()` → `file_bytes` 入库 → 既有 UTF-8 解码 → `Intent(file_type, text, note=decl.moved_note())`。阻塞 I/O 照旧 `asyncio.to_thread`（dry_run 在请求线程上跑,hung source 不得把并发请求全部按住）。

Commit: `feat(w7): identity entries can read one file from a git source`

---

### Task 7: skills materialiser 的 git 路

`<R>/apply/materialisers/skills.py`:resolve 换走 `fetch_declared`;`GitEntrySource` → `_git_package(ctx, decl, name)`——`decl.files()` 取子树文件集 → 现有 directory validator 走真检验（与手工上传同一条验证路)→ canonical zip 经 `file_bytes（credential_name=decl.auth, #1835)` 入 W11 → `_SkillPackage(note=decl.moved_note())`。

keep_last 的回落 `FetchedEntry(from_store=True, content_type='application/zip')` 落进既有 `_build_package` 的 zip-无-subpath 路:content type 判 zip,git 条目的 subpath 在源上不在条目上,直接 `validate_zip`——一字未改复用整条 zip 路。

Commit: `feat(w7): skills entries can build a package from a git tree`

---

### Task 8: 报告与服务接线——session 桥、基线、DI、dry_run

- `orchestrator.py`:报告构造补 `sources=ctx.source_session.resolution_records()`（session 无 → 空元组)。
- `config_manifest_apply_service.py`:构造表加 `git_client_provider`(与其它 lazy provider 同款);`start_apply` 在 parsed 之后、apply_id 之前建 session(基线在此刻读取——请求线程,worker 不竞写);**所有终态路径**关 session(启动前异常/线程拉起失败/worker finally/dry_run finally);`_last_resolutions` 读基线。
- `manifest_fetch_module.py`:`manifest_git_source_client` provider(构造时读 env、剥 `GIT_*`,#1835)与 `manifest_git_client_factory` 延迟工厂。

**基线语义(#1835 修订):** 原 `_last_resolutions` 只读最新一栏报告——失败 apply 会把基线「冲掉」,评审双洞(毒化/清空)就此打穿。现行:新仓储 `recent(env, entity, bot, limit=10)` 有界回走,逐源取最新携带行;refuse 不采纳(fetch 层保证)+回走,完成「拒绝持续到文档重钉」「断联不清基线」两个修复语义。

Commit: `feat(w7): thread the source session through apply, with strict baselines`

---

### Task 9: 收尾——全套回归、架构守卫、coverage gate、文档

全量 `ci_test.sh` 本地一趟(16661 passed / 行 88.36% / **改动行 83.46%** ≥80%);架构守卫 258;rebase 上含 W6 的新 dev(3 个 keep-both 冲突);work-items 双语完成标记(含「resources 接 git 路是后续工作」的如实修正);PR [#1829](https://github.com/inclusionAI/Avernet/pull/1829)。

> 评审后补的收尾即 [#1835](https://github.com/inclusionAI/Avernet/pull/1835):上漏的功能面——**admission 能力行从未翻转**——与五个被闸门遮蔽的缺陷全部落地修复(翻闸门+resources 收窄进 schema 逐条目拒绝、strict 基线生命周期、报告安全的失败文本、声明尺寸前置的字节护栏+类目限额+`tree_bytes` 记账、quotepath 反转义、entry 级键精确拒绝、收据 `credential_name`、环境与凭证通道)。验证:受影响面 607、架构 258、PR CI 全量门禁。

---

## Self-Review 记录

- **Spec coverage**：W7 验收标准逐条 → ①`sources`/`from`/互斥/未引用警告（W1 已交付，apply 侧缺名拒绝 Task 5）②auth 在源上（Task 5 fetch_declared 读 decl.auth）③atomic 解析、单次拉取复用（Task 4 cache）④ref→SHA 记入报告（Task 4+8）⑤浅层单 ref（Task 2）⑥只读、无 hook/filter（Task 2 docstring + env 隔离）⑦包含性检查在 W11 之前（Task 2 `_enumerate` + Task 3 readers）⑧解开后限额（Task 3）⑨移动 tag 收敛（Task 3 测试）⑩git 目录条目免 unpack（schema 已定，W6 消费；Task 7 单测钉住 zip 路不受影响）⑪mode 字段（schema 已定 + Task 5 strict 执行）——**唯一刻意的 v1 收窄**：git 源 + `digest` 拒绝（Task 5，带文档理由）。清理临时 checkout 由 `GitCheckout` 失败路径 + `SourceSession.close()` 双保险。
- **Placeholder scan**：Task 6/7/8 中标注「展开为完整测试函数」的四处是仅有的弹性位——机制、构造方式、断言目标都已写死，展开时不得增删断言口径。
- **Type consistency**：`GitSourceSpec(url, ref, subpath, mode, auth)` / `SubprocessGitClient.fetch(spec, *, headers)` / `SourceSession.checkout(spec, *, headers, display)` / `fetch_declared(ctx, *, entry, category, entry_identity)` / `file_bytes(ctx, *, content, source_url, category, entry_identity, content_type, credential_name)` 各任务间一致；`git_receipt_url(url, sha, subpath)` 单一定义点。

---

## 双 agent 协作约定(B 会话于 2026-09-02 16:45 追加;单干时本节作废)

两个会共享同一 worktree 与分支,唯一同步介质 = 本文件 + git 历史。任务分工:

| 任务 | 归属 |
| --- | --- |
| Task 1(已完成)、Task 2、Task 3、Task 5、Task 8、Task 9 | **A**(本计划作者,W7 主会话) |
| Task 4、以及 Task 6/7(Task 5 合入后认领) | **B**(协作会话) |

规则(与 superpowers 协作契约一致,浓缩为四条):

1. **claim 先行**:认领写在任务标题下的引用块里(格式见原 Task 4 的 claim 块);开工前先 `git log --oneline -8` 查任务是否已被提交。
2. **按文件 add**:只 `git add` 自己所有权表里的文件,**绝不 `git add -A` / `git add .` / `git stash`**——`git status` 里出现的对方 WIP 是只读信息。
3. **提交 message 带任务号**,沿用本计划各 Task 的 message。
4. **同文件冲突兜底**:本文件是唯一双写点,且约定 append-only;对方 claim 的任务 30 分钟无提交且会话确认死亡,方可在本文件留 takeover 记录后接管。

### 执行日志(append-only)

- 16:45 B claim Task 4 → `f031450b9` 交付 SourceSession(测试 5/5 绿)。
- 17:46 B claim Tasks 6/7(Task 5 已由 A 落盘 `1cb77535e`)。
- 18:1x B 交付 Task 6 identity git 路(`3a48f605e`,B 轨 4 用例绿)。
- 18:18 A 落盘 Task 8(`2e1267dfc`)。
- 18:4x **B 的跨轨前置修复** `3664a729c`:`fetch_declared` 的 no-session 门槛原先进门就抛,把纯内联 URL 路(W5 老,不经 W7)也拦了,导致 identity 套件 9 个存量用例与 engine 的 SKIPPED 邻居用例在 Task 6 重路由后转红。门槛改为只守真正读 session 的路(`from` 查找与 git 路)。entry_fetch.py 属 A 轨文件,B 仅在 A idle(Task 8 已提交、Task 9 未派发)时做了这个前置修复,在此留痕;Task 9 如有冲突以本日志为准。
- 18:45 B 落盘 Task 7 skills git 路(`28f7f921e`)。A 续任会话独立复核中发现的同一批转红与 B 的修复相互印证(门槛下移方向一致),无需返工。
- 18:48 **A 续任会话 claim Task 9**(收尾:全套回归、架构守卫、coverage gate、work-items 标记、PR)。开工前 `git log` 核对:Task 1–8 全部已有提交,工作区干净。
- 19:0x A 的跨轨 lint 清理(Task 9 收尾项,ruff 对全部 W7 改动文件 6 处告警全消)——后两文件属 B 轨已交付面,均为行为无关的机械清理,照 B 的留痕先例备案。全量门禁 `ci_test.sh` 于清理前已跑过(16661 passed / 行 88.36% / **改动行 83.46%** ≥80%)。
- 19:2x **W6 已合入 dev(#1821,3e2b4c024)→ W7 rebase 上新 dev**:三个 keep-both 冲突,其余 14 个提交零冲突自动重放。rebase 后受影响面复验:manifest 463 passed + 架构 258 + openapi bars 8 + endpoint apply 场景 12。
- 19:2x **work-items 标记修正后交付**:「W6 资源目录条目对 git 源的消费随 W6 分支合入」经核对失实——W6 从 W5 分支切出、早于 W7 合入,`resources` 物化器只认字符串 URL 源。标记已改为如实陈述:resources 接 git 路是后续工作。**PR [#1829](https://github.com/inclusionAI/Avernet/pull/1829) 已开**(base=dev),CI 运行中。
- 2026-09-02 深夜 **#1835 评审修复 + 本文档收编**:8 视角复审发现 admission 闸门未翻与五个潜在缺陷,全部修复(详各 Task 的「#1835 修订」注);两轮 rebase 吸收 dev 前进(W6 修复 #1833、W13 #1791)。本文档同 commit 起改为决策/不变量存档,内嵌代码快照整体移除——代码以仓库为准(其中数处已被 #1835 改掉,快照已过期)。收编后自检:goal/architecture/取证事实/文件表/九任务核心不变量/Self-Review/协作日志全数保留,未丢失任何决策性内容;逐项 step/代码/`git add`样板为移除项。
