# Tasks: the Two Fetching Categories (`skills` + `identity`, W5)

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked

Groups run in order. Within a group, tasks are independent.

Two invariants, as in W4's task list:

- **No existing assertion is edited.** Scaffolding may move (construction
  sites gain arguments; one fake absorbs its skill-pair twin; the
  no-materialiser demo names the still-sparse `resources`), but every
  pre-existing assertion keeps its meaning. Each deliberate adjustment is
  recorded beside its task.
- **Nothing existing is destroyed by a failure.** Every fetch, package and
  legality refusal lands in `resolve`, before any write — the §3.2
  all-or-nothing rule holds by construction.

---

## Group A — Seams

## [x] Task 1: W11 `latest_receipt`
- **Goal:** the per-source receipt lookup; the audit read bounds at
  DEFAULT_RECORD_LIMIT and returns every source, so it cannot answer the
  pipeline's question on a busy bot.
- **Files:** repository protocol + implementation, service protocol +
  implementation, their tests.
- **Done when:** newest-first for one source URL; exact-equality source
  matching (a sibling path is another source); other bots and other tenants
  answer `None`; repository exercised over real SQLite.

## [x] Task 2: refuse `content` on a skills entry
- **Goal:** a skill is a package (SKILL.md + what it names); inline text
  cannot be one, and W1's rule forbids accepting an inappliable construct.
- **Files:** `schema/entries.py::validate_skill_entry`,
  `manifest-schema.zh-CN.md` §3.3 (the same-PR doc rule), the schema tests.
- **Done when:** `manifest.skills[0].content` / `content_not_a_skill_package`;
  identity keeps both forms (pinned by the same test); the schema doc gains
  the note in the same change.

---

## Group B — The pipeline

## [x] Task 3: `apply/entry_fetch.py`
- **Goal:** one funnel — substitute, consult the platform's copy, fetch
  under the named credential, file the receipt.
- **Done when:** each policy branch is its own test — substitution reaches
  the transport first; a pinned entry with a matching receipt is served from
  the store with zero network; a mismatched receipt refetches (sources
  rotate); unpinned re-fetches; `keep_last` reads the receipt only when it
  may (no receipt, or one disagreeing with the pin, refuses); transport and
  credential errors carry names, never values; `store()` receives the
  substituted URL, the credential NAME, and the actor as modifier.

---

## Group C — The materialisers

## [x] Task 4: `IdentityMaterialiser`
- **Done when:** the router's own write path (`identity_coords_from_record`
  + `update_bot_file`); both source forms; legality re-asked per the bot's
  engine; reserved names refused in `resolve` AND subtracted from removals
  (both halves of the guarantee, each in its own test); removals are empty
  writes (absent ≡ empty is the domain's own contract); convergence is zero
  further writes; one failed fetch aborts the category, nothing written; a
  structural test holds the module off restart/device plumbing.

## [x] Task 5: `SkillsMaterialiser`
- **Done when:** declaration conflicts with the area are asked *before* the
  fetch; no-subpath zips travel the exact manual-upload road (the same
  validator, `upload_local_skill`, direct activation); tar.gz/subpath go
  through the guarded unpacker and re-pack canonically; the package's
  front-matter name must equal the entry's; oversized packages fail in
  `resolve`; the area is the active set with Set-governed members neither
  declarable nor removable; `skills: []` removes the directly-active
  skills, sorted; re-apply converges with zero uploads, zero activations,
  zero fetches; a structural import test keeps the module off the storage
  and repository internals of the upload flow.

## [x] Task 6: registration + engine integration
- **Done when:** `build_materialisers` returns four; the W4 safety nets
  (bar dominance, admission width) re-run over the new categories unchanged;
  a four-category document applies in `APPLY_ORDER` order (identity before
  skills), one category's fetch outage aborts only that category, and the
  report summarises `PARTIAL`. Deliberate adjustment recorded: the
  no-materialiser demo names `resources` (skills were its W4-era subject;
  W5 materialised them — the move the wave always implied).

---

## Group D — Wiring and docs

## [x] Task 7: composition
- **Done when:** `ManifestFetchModule` owns the config cluster (through
  config_module's one public seam — the sofa read stays in its sanctioned
  file), guarded fetcher, content store, the one `EntryFetcher`, and the
  five lazy factories (identity keyed by the narrow port, so the device
  graph is never imported eagerly); the apply service's five new providers
  are distinct typed keys; a full-graph smoke resolves every key to its real
  singleton; the architecture gates run green (module boundaries + the
  1000-line cap — the cap is what placed the wiring in its own module).

## [x] Task 8: README, flow coverage, this spec set
- **Done when:** the module README's narrative, `provides`, `consumes` and
  `internal_dependencies` cover the W5 surface (three new declared imports);
  the flow-coverage exemption names the W5 machine parts; the spec/plan/task
  docs exist in this directory.

---

## 附录（2026-09-03 追加）：终审整改轮

> 10 条终审 finding（4×P0、4×P1、P2 若干）全部落地于 #1795 的新提交；此处台账对应
> 代码提交，便于复审对照。

## [x] P0-1 skills 的 UNCHANGED 按「已安装内容」判定
- 判据换血：receipt 只证取过、不证装过（干跑落收据、中止残留收据两个陷阱都可达）。
  `LocalSkillUploadService.installed_package_digest(bot, bot, bot_id, owner_id, name)`
  读 stable locator 上真实发布的包并给 canonical digest；plan 以
  `installed_digest == package.content_digest` 判 UNCHANGED；读不回=未知=全量重写。
- fake 按「只有完成的 upload 才发布」建模 installed；四个场景钉住（干跑收据陷阱、
  中止残留、不可读未知、正常收敛零写入）。

## [x] P0-2 store 异常穿透已翻译
- lookup/read/store 四处交互全部译成 EntryFetchError（含 400/500 两族）；
  pinned 缺 blob→落网络自愈；损坏→500 语义大声失败；redirect 超列宽→该条目失败
  而非全类目；keep_last 读失败报双因（源失败+回退不可读）。

## [x] P0-3 keep_last 回退可见
- FetchedEntry.fallback_reason 携带原因；Intent.note 贯穿 resolve→write；
  EntryResult.note 呈现（§9.6 承诺兑现）；钉住命中与回退被 fallback 标志区分。

## [x] P0-4 start_apply 线程启动失败不再泄锁
- ctx 构造与 Thread.start 收进守卫；launch 失败→FAILED 终态行 + 锁释放 +
  原错误上抛；_terminate_on_launch_failure 是 _run finally 的镜像。生命周期测试
  含爆炸线程→终态→立即重 apply（真线程）。

## [x] P1-5 apply 级取数预算成真
- apply/budget.ApplyFetchBudget（时间 deadline + 字节账本，每 apply 一个，
  挂 ctx）；fetch 前查账、fetch 后计费；干跑同账本；两个测试钉两半；
  limits.py 注释改为陈述已存在的接线（原先谎称 W4 thread 了它）。

## [x] P1-6 dry_run 契据改真
- orchestrator/协议/路由描述三处改口：不物化不激活不移除；可能真取源（同账本
  封顶）；获取要落审计（§2.8 是「获取」的事实）。

## [x] P1-7 回退资格按类分级
- FetchRefusedError/CredentialError 不进 keep_last（配置类，非可用性）；
  FetchFailedError 才可回退。模块 docstring 拍板 + 测试钉住。

## [x] P1-8 两个类型收口
- category/entry_identity 必填 keyword-only（调用点已全部在传，零成本）；
  FetchCategory(StrEnum) ↔ FETCH_ENTRY_LIMITS 模块级 assert 绑死。

## [x] P2 顺手项
- _build_package 进 to_thread；ManifestIdentityPort 方法加 @abstractmethod +
  DI 工厂运行时 isinstance 护栏 + 真服务反射测试；ManifestContentService 继承
  自家 protocol；文档清了 8 处失实（含 README consumes 指 capability_state_contract）；
  skills partially_written 角落测试；factories.py 的 TYPE_CHECKING 块补上它声称
  的四个 import（预存债，因本 PR 触碰该文件而被 SAST 揭出）。
