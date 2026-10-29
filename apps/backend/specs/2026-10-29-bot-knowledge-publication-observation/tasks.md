# Tasks: 服务 Bot 知识库开关的分版本尽力记录

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked
>
> 用户已批准实现；按 Groups 顺序执行，记录各任务实际验证结果。
> Source paths 相对 `apps/backend/src/agentclaw/community/`；test paths 相对 `apps/backend/`。

## Task 1 [x]: Define and persist bounded publication observations
- **Goal:** 用一个普通 Module 提供独立保存、读取最近成功记录的 Interface，不改当前选择或原生发布状态。
- **Files:** `core/bot_knowledge/protocols.py`, `api/bot_knowledge_service.py`, `core/bot_knowledge/publication_selection.py` (new), `tests/community/core/bot_knowledge/test_publication_selection.py` (new).
- **Done when:**
  - [x] 先增加行为测试，再实现 protocol、构造注入与有界 payload；不增加第三方依赖、表、字段或索引。
  - [x] 存储键包含租户、不可变源 PK、环境、发布单；payload 校验空间、版本、ID 数量和字节限制。
  - [x] 采集只在 BUILDING、可信源映射及有效空间下发生；源/权限异常不可保存成空选择。
  - [x] last_recorded + []、unrecorded + null、unavailable + null 可区分；正常 Repository 异常不伪装成持久化成功。
  - [x] 读取先完成当前权限与源归属校验；授权失败不被附属容错吞掉，错域或损坏记录不回退读取草稿。
  - [x] 同一版本重建可覆盖最近记录；失败保留旧值时返回原采集时间，不假称本次构建成功记录。
- **Depends on:** —
- **Verification:** 新用例先因缺少实现导入失败；实现后 41 passed。Context Boundary 增补真实 Repository contract 依赖随本任务提交。
- **Acceptance mapping:** AC-02, AC-05, AC-07, AC-09.

## Task 2 [x]: Wire one failure-isolated build observation
- **Goal:** 在既有异步构建入口接入尽力记录，原生构建和发布结果不受附属失败控制。
- **Files:** `core/service_bot/services/publish_flow/tasks.py`, `di/modules/bot_knowledge_module.py`, `di/modules/service_bot_module.py`, `tests/community/core/service_bot/services/test_publish_tasks.py`, `tests/community/di/test_bot_knowledge_publication_selection.py` (new).
- **Done when:**
  - [x] 只有 PublishVerifyFlowHandler 在 BUILDING 分支、原生 build 前调用记录；通过已有 lifecycle 传递惰性普通 Service API provider。
  - [x] 不更改 PublishFlowService、BuildStageRunner、原生 Repository、状态 CAS、事务或上线成功/恢复处理。
  - [x] 注入采集、保存、编码与惰性 provider 异常后，原生 build、verify release 与 poll 调度按原分支继续。
  - [x] 真实 build/release 失败仍返回原生 Fail；观察容错范围不包住原生阶段方法。
  - [x] BUILT/VALIDATE_PUB/VALIDATING/ONLINE_PUB/SUCCESS 的重放、上线、重启和回滚不新增记录写入。
  - [x] 真 DI 可解析，构造时没有 DB/远程 I/O；无需新平台凭证，不改变其他任务 handler。
  - [x] 核实连接/驱动等待配置并保留证据；不能把捕获异常当作硬耗时上限，不能暗增超时线程或全局数据库改造。
- **Depends on:** Task 1.
- **Verification:** 构建任务/真实 DI 48 passed；公有 DB 插件未提供逐调用硬超时，内部 DB 适配器也未声明可供本功能复用的短截止时间。实际生产驱动配置与故障耗时未验证，不作硬时限保证。
- **Acceptance mapping:** AC-03, AC-06, AC-09, AC-10.

## Task 3 [x]: Expose publication reads without changing draft APIs
- **Goal:** 提供可按原生 publication_id 查询的只读端点，保持当前草稿开关及个人 Bot 行为。
- **Files:** `adapters/http/bot_knowledge/router.py`, `tests/community/endpoints/test_bot_knowledge_router.py`, `tests/community/e2e/test_bot_knowledge_publication_selection.py` (new).
- **Done when:**
  - [x] 新 GET 使用统一协议、认证与错误映射；旧 GET/PUT、CAS、分页及既有 enablement 别名不变。
  - [x] HTTP/DI 测试通过真实组装解析实现，不手工重绑被测业务服务。
  - [x] 覆盖草稿 A → 构建记录 A → 原生预发版本 A → 新草稿 B；读取旧发布版本仍为 A。
  - [x] 同一版本上线查询不写记录；返回记录不得改变原生部署状态，失败版本不得被包装成有效阶段。
  - [x] 选择为空、历史无记录、损坏、辅助读取失败、跨 Bot/租户/环境/空间以及旧 Owner 均有明确响应。
  - [x] 原生回滚所选历史版本有记录可读、无记录为 unrecorded；不自动改草稿选择，不查询全历史猜测版本。
  - [x] 前端接入未实现的范围写清楚，不把后端受控流程当作真实预发/生产验收。
- **Depends on:** Tasks 1, 2.
- **Verification:** 注册端点及真实 HTTP/DI/DB 场景 11 passed；包含原 GET/PUT、原生状态 CAS/rollback_flip、provider mutation witness 与路由唯一性，不包含设备部署。
- **Acceptance mapping:** AC-01, AC-03, AC-04, AC-05, AC-07, AC-08, AC-10.

## Task 4 [x]: Lock down the small scope and document weak consistency
- **Goal:** 把非阻断和无事务联动变成可检查的约束，避免重新长成旧方案。
- **Files:** `core/bot_knowledge/README.md`, `core/service_bot/README.md`, `tests/community/architecture/test_bot_knowledge_publication_observation.py` (new), `apps/backend/specs/2026-10-29-bot-knowledge-publication-observation/{spec,plan,tasks}.md` (repo-relative).
- **Done when:**
  - [x] Context Boundary 与真实 API 依赖一致；文档解释 last_recorded/unrecorded/unavailable、空选择及权限边界。
  - [x] 架构回归证明记录未接入原生完成、上线、恢复或事务路径，且未引入 online_enabled、发布 ext 写入、新任务或新 Plugin。
  - [x] 同一发布单取消预发/回滚退回草稿后重建的弱一致限制、采集时间及无自动恢复语义可读且有对应测试。
  - [x] 明确整体 DB 故障、DB 等待、文件同步落后不在“辅助异常隔离成功”的保证范围内。
  - [x] 检查实际生产代码 diff；若超出计划中的普通 Module、一个任务集成入口、DI 和一个只读端点，先回到方案 review。
- **Depends on:** Tasks 1, 2, 3.
- **Verification:** 新增观察调用边界/只读/无原生写入 guard + 现有模块/API 架构测试 15 passed；文档明确历史、重建、等待和运行文件限制。
- **Acceptance mapping:** AC-04, AC-06, AC-08, AC-09, AC-10.

## Task 5 [x]: Tests & Verification
- **Goal:** 检查全部 acceptance criteria，并形成可用于后续独立新 PR 的真实验证记录。
- **Files:** 上述测试、SDD checklist；不以修改 unrelated CI/Singlebox 阈值消除失败。
- **Done when:**
  - [x] 运行 plan 列出的定向 unit/HTTP/DI/e2e/architecture 测试，保留真实结果；未跑的不勾选。
  - [x] 检查辅助存储按完整唯一键读写、启用集合最多 101 行，无历史/累计禁用绑定扫描；对 1/1000/10000 额外禁用绑定验证查询形态不放大。
  - [x] 运行 Backend SAST、完整 Backend CI 与 changed-line gate；如失败，区分新增失败与干净基线失败。
  - [x] 检查每个新增/修改源文件不超过 1000 行，运行 diff whitespace 和静态检查，核对正常启动及路由唯一性。
  - [x] 独立检查源码/契约与 AC 映射；不从“单测通过”推断真实设备、数据库或部署通过。
  - [x] 保持 Singlebox pending/阈值原样，按交付门禁运行或披露未运行；不伪造 AGENTS.md 设备验收。
  - [x] 更新 spec/tasks 中实际完成项并汇总 DB 等待与真实环境未验证边界。提交/推送/新 PR 按后续明确授权处理，不合并、不部署。
- **Depends on:** Tasks 1, 2, 3, 4.
- **Verification:** 完整 Backend CI 27650 passed / 40 skipped / 2 subtests；line 90.65%、changed 99.19%，门禁通过；实际证据与未验收范围见 verification.md。三路分组评审及修复后的增量复审已收敛，无新的确认缺陷。
- **Acceptance mapping:** AC-01 through AC-10.

---

## Groups
- **Group A — Independent recording:** Tasks 1, 2
  - Theme: 真实构建入口接入独立记录，故障注入证明不控制原生发布。
- **Group B — Read-only consumption and guardrails:** Tasks 3, 4
  - Theme: 提供按版本读取闭环并固定弱一致、权限与无事务联动范围。
- **Group C — Verification:** Task 5
  - Theme: 完成 AC 核对与门禁，列明未验收范围，准备后续新 PR。
