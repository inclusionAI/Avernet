# B4: Backend resource addressing compatibility

## Boundary

Build on the deployed B0–B3 address resolver. Keep public parameter names,
response schemas, Frontend and SecBaaS unchanged. Do not add or enable an Owner
handoff operation. `owner_id` request values remain compatibility locators;
authorization always uses the live Bot's current owner and the real actor.

AgentPass execution identity, App Grant contracts and owner-transfer business
transactions belong to later stages. This change does not claim those are ready.

## Implemented paths

| Path | Resource identity | Person identity |
| --- | --- | --- |
| Local Skill list/detail/content/parameters | Resolve Bot first, then entity + bot | Current owner / collaborator and caller |
| Local ZIP/folder upload and delete | Entity for metadata, guard, package location, desired state and security observation | Actor and current owner for authorization; recheck under edit guard |
| OpenAPI permission gate | Resolve the same compatibility locator as domain services | Determine OWNER from the resolved current owner, never from a locator |
| Local Skill security observations | Stable entity for scan/history/digest scope | Actual authenticated reader |
| Device binding resolution | Resolve owner/entity matches to one Bot's existing binding | Preserve the caller passed to connection builders |
| Expert Chat sessions and caller instance ledger | Existing chat-user + entity + bot + env keys; publish artifact query uses entity | Preserve chat user; live Owner for Bot detail and build operator |
| Collaborator roster | Existing bot_pk, with historical owner snapshot retained | Current Owner and actual collaborator user |
| Collaboration and session locks | Existing textual key with stable entity segment | Holder remains the actual user |
| Quality tasks | Existing owner physical column holds entity for Bot-bound rows | operator_id unchanged; unbound tasks unchanged |
| Governance whitelist | Entity in owner physical column; filters accept stable locator or current owner | created_by unchanged; tickets and audit facts not rewritten |
| Dormant whitelist | Resolve imported Bot address to entity; candidate exclusion follows Bot identity | Protected-person policy and notification recipients remain separate |
| Discovery ORM input/read | Canonicalize Bot-bound owner locator to entity | Production discovery resolves current responsible owner for person aggregation and session/notification creation |
| Session Resource target selection | Explicit target_entity_id uses exact entity lookup | Resource owner remains uploader; friend and binding authorization retained |

Tenant guards and environment filters remain in the persistence layer. No global
change to `BotRepository.get_by_id_and_owner`: ownership-sensitive callers retain
its strict semantics. Locator ambiguity is an error, not permission to try a
different Bot.

## Persistence and compatibility

No DDL or automatic data migration. The user-confirmed online invariant is that
normal Bots currently have owner == entity. Existing resource keys therefore do
not change on deployment. Only the source of keys for future owner != entity
reads/writes changes.

The session-local persistence helper is shared by chat, quality, governance and
discovery ORM stores. These stores historically accept detached/offline inputs;
when no live Bot matches, retain the explicit existing locator. This is not an
authorization fallback. Never swallow database failures or ambiguity. Owner-only
administrative list filters use a set-based Bot subquery rather than N queries.

Discovery's explicit offline/manual assembly can omit the Bot resolver. Its
production DI assembly must supply the resolver. This preserves the standalone
reader contract without treating an offline task's historical owner as a new
runtime execution identity.

## Verification and rollout

Use real SQLite Bot and resource rows, write resources under A, then change only
the fixture's current Owner to B. Verify reads/writes through both locators, no
new B resource key, lock contention across both locators, current-owner ACLs,
cross-match rejection and tenant/environment restrictions. Preserve the existing
unit, adapter, endpoint and architecture tests; run the full Backend suite.

Production validation still requires OceanBase preflight and representative
new/legacy platform smoke tests. Local tests cannot establish external runtime,
SecBaaS or enterprise Passport behavior. Keep transfer closed until later-stage
contracts and an actual owner != entity end-to-end exercise pass.

## Mixed-field boundary: Render Screen

The current owner-scoped Render Screen interface permits different people to
save independent records for the same Bot. The online Bot owner/entity equality
invariant does not prove those resource owner columns equal the Bot entity.
B4 therefore preserves Render Screen behavior. Its service-Bot policy, historical
row classification and collision handling require the later mixed-field stage;
do not enable handoff on the strength of this B4 change alone.

## Remaining audit gates

Existing factory entity parameters keep their original meaning; B4 does not
reinterpret them as current-Owner values. The historical Legacy ingress path
parsers, engine-specific physical paths and
resource/file adapters must be checked for direct request-Owner usage before
declaring every Backend resource path transfer-ready. No resource or personnel
field may be reinterpreted solely by its name. Mixed lifecycle state, work-order
assignees and queued-operation policies require the later transfer stage.

## 部署与验收清单

1. 执行同目录 `preflight.sql` 的只读校验；异常逐条确认归属，不自动清理。
   该 SQL 尚未在本次工作中连接线上执行。
2. 保持转交入口关闭，先灰度 Backend。现有正常数据 owner == entity 时，
   新旧资源键应一致；接口入参名、响应结构及前端代码不变。
3. 对新平台 OpenAPI、旧平台 `/api` 各验证列表、详情、Skill 上传/读取/删除、
   协作者和锁、Expert Chat 会话与 caller 实例、白名单、质量任务、任务发现。
   发布/文件与运行时链路也须回归，不能只测 Bot 详情。
4. 观察 404、歧义错误、权限拒绝、上传失败、设备绑定失败及实例重复创建；
   交接未开放且资源键未发生变化时，可回退 Backend 版本，无 DDL 回退。
5. 后续阶段补齐 AgentPass/Caller identity、App Grant 的稳定 Bot 定位、
   Render Screen 等混合字段及历史 Legacy 直接写入链路。App Grant 的物理
   owner_id 按已确认口径视为 entity locator，不视为人员快照。SecBaaS 和前端
   仍按约定后置。

`ac_discovered_tasks` 当前模型没有 env 字段：本次只在 Bot 解析时按当前环境
取稳定地址，不虚构任务表的环境隔离。历史任务跨环境归属、运行中工单的处理策略仍须在正式开放转交前核验。

## 本地验证结果（2026-10-22）

- Backend Community 全量：24,548 passed、40 skipped，退出码 0。
- Backend 总行覆盖率：90%（138,325 行中的 124,413 行命中）。
- 工作区变更行覆盖率：94.17%（210/223），按相对 HEAD 的新增可执行行统计，
  包含新增的生产 Python 文件；这是工作区统计，不替代提交后 CI 的差异门禁。
- 新增交接专项 16 项，包含在全量结果中：真实 SQLite Bot/资源记录、A→B 后的
  读写和权限、Local Skill 上传/删除、caller 实例复用、协作者 PK、锁竞争、
  歧义与租户/环境隔离、任务发现按当前负责人分组和无任务的兼容空操作。
- Ruff、仓库 `python_sast_local.sh apps/backend/src 1`、`git diff --check` 通过。
  所有变更生产 Python 文件均不超过 1,000 行，最大 998 行。
- 全量使用仓库 CI 支持的 `-n 4 --dist loadfile` 和 coverage sysmon。
  测试需要本地 HTTP 监听，已在获准的沙箱外运行；配置文件操作隔离在测试
  临时目录，`AWS_EC2_METADATA_DISABLED=true` 关闭云主机凭证探测。
- 没有在本次工作中执行线上 SQL、真实新老前端实机联调或独立 Singlebox
  全栈验收。因此本地通过不等于已经完成上线验收或允许开放 Owner 转交。
