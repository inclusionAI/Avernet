# Phase 2 Skill Contract-to-Code Gap Matrix

> 基线：`github/dev@b68ec64f1698a931585612801f2db6529c8ec4aa`（2026-08-30）。
>
> 目标合同：正式领域 Spec PR #1686 +
> `src/gateway/specs/2026-08-20-skill-capability-upgrade/phase2-openapi-contract.md`。
>
> 用途：Backend 内部审计、旧 Ticket 治理和新 Ticket 拆分；不提供给前端，不代替生成 OpenAPI。

## 1. 状态定义

| 状态 | 定义 |
| --- | --- |
| `DONE` | Router、DTO、Service、权限、测试和正式 artifact 均符合目标 |
| `ADJUST` | 已有实现，但 wire、语义、artifact 或测试仍有缺口 |
| `NEW` | 目标能力尚未实现 |
| `FROZEN` | 既有 Phase 1/其他 Owner 合同，Phase 2 只消费 |
| `REMOVE` | 旧骨架或旧语义不得继续作为目标实现 |
| `OUT_OF_SCOPE` | 本期明确不建设 |

只有 `DONE` 才能被新 Ticket 视为无需开发。Router 存在但未进入正式
`bots.openapi.json` 仍是 `ADJUST`。

## 2. 证据方法

本矩阵同时检查：

1. `openapi_v1` Router 与 Pydantic DTO；
2. Core/Application Service、Repository、Store、Task Handler；
3. Authorization/Admission inventories；
4. 窄单测/合同测试；
5. Backend `dump_openapi.py` 候选；
6. 已提交的 `src/gateway/configs/schemas/bots.openapi.json`。

在本基线运行：

```bash
DEPLOY_PROFILE=test uv run --project src/backend \
  python src/backend/scripts/dump_openapi.py /tmp/candidate.json \
  --path-prefix /openapi/v1/bots
```

候选能看到 Grant/Lease/Editor Request Router；正式 pinned artifact 只含 SC market/tags 与旧
Space Skill list，说明当前代码与 Gateway 发布文档存在 drift。

## 3. Operation Gap

### 3.1 市场与目录

| ID | Router/DTO | Service/存储 | Auth/测试 | 正式 OpenAPI | 结论 |
| --- | --- | --- | --- | --- | --- |
| P2-MKT-001 | `openapi_v1/market/router.py::search_skill_center_skills` + DTO 已有 | `SkillCenterGatewayService` 已有 | inventory/测试已有 | 已发布 | DONE |
| P2-MKT-002 | `list_skill_center_tags` 已有 | Gateway 已有 | inventory/测试已有 | 已发布 | DONE |
| P2-MKT-003 | 无 consumable Router/DTO | 无 Space Published consumable query | 无 | 无 | NEW |
| P2-MKT-004 | 无 canonical sync Router/DTO | 当前 `SkillCenterSyncService` 是 deprecated latest/current 旧语义，不可复用实现 | 无 | 无 | NEW + REMOVE OLD |

Phase 1 `repository` 与 Membership PUT/DELETE 为 `FROZEN`，实现 Ticket 只做集成回归。

### 3.2 Space Skill、Draft 与 Version

| ID | Router/DTO | Service/存储 | Auth/测试 | 正式 OpenAPI | 结论 |
| --- | --- | --- | --- | --- | --- |
| P2-SKL-001 | list Router 已有；仍返回旧 `SpaceSkillItem(status,draft_status)`，query 是 `page_no` | Query Service 已有，但未投影最终 Summary/Offline/Attempt | inventory 已有；需更新合同测试 | 已发布旧 wire | ADJUST |
| P2-SKL-002 | 无 POST folder Router/DTO | `SkillPackageValidator`、`DraftContentStore` 已有；缺创建 Application Service/UoW | 无 | 无 | NEW，复用基础模块 |
| P2-SKL-003 | 无 import Router/DTO | 缺 deterministic Git snapshot Application Service | 无 | 无 | NEW |
| P2-SKL-004 | 无 detail Router/DTO | 缺聚合 Detail Query | 无 | 无 | NEW |
| P2-DRF-001 | 无 upgrade Router/DTO | Draft Store 已有；缺 exact version→Draft Application Service | 无 | 无 | NEW |
| P2-DRF-002..004 | 无 file Router/DTO | Store 只有 immutable ZIP 原语；缺 tree/read/mutation orchestration 与 DB CAS | 无 | 无 | NEW |
| P2-DRF-005 | 无 refresh Router/DTO | 缺 frozen Git source refresh + CAS | 无 | 无 | NEW |
| P2-DRF-006 | 无 delete Router/DTO | 缺 lineage precondition/UoW 与 OSS best-effort cleanup | 无 | 无 | NEW |
| P2-VER-001..004 | 无 Version HTTP 读取 | ORM skeleton 已有；缺 Published-only query 与 Canonical Store file read facade | 无 | 无 | NEW |

### 3.3 Grant、Editor Request 与 Lease

| ID | Router/DTO | Service/存储 | Auth/测试 | 正式 OpenAPI | 结论 |
| --- | --- | --- | --- | --- | --- |
| P2-GRT-001 | Router 已有；`SkillActorPermissions.retire_skill` 需改为 `offline_skill` | Grant Service/UoW 已有 | inventory/单测已有 | pinned artifact 缺失 | ADJUST |
| P2-GRT-002..004 | Router/DTO 已有 | Grant/Owner transfer 已有 | inventory/单测已有 | pinned artifact 缺失 | ADJUST（发布 drift） |
| P2-GRT-005 | Router/DTO 已有 | Editor Request + Work Order approval handler 已有 | inventory/测试已有 | pinned artifact 缺失 | ADJUST（发布 drift） |
| P2-LSE-001..004 | Router/DTO 已有 | 永久 Lease/fencing/takeover 已有 | inventory/测试已有 | pinned artifact 缺失 | ADJUST（发布 drift） |

这里不应重新实现领域服务；对应 Ticket 只允许 DTO 最终命名、生成 artifact 和兼容测试收口。

### 3.4 Publication 与 Offline

| ID | Router/DTO | Service/存储 | Task/外部 | 正式 OpenAPI | 结论 |
| --- | --- | --- | --- | --- | --- |
| P2-PUB-001 | 无 | 缺 Track Latest 候选 impact query | 不触发 Task | 无 | NEW |
| P2-PUB-002..005 | 无 | `SkillPublicationAttempt` ORM skeleton 有；缺 Publication Application Service/UoW | SC Gateway 已有；缺阶段 Worker/recovery handlers | 无 | NEW |
| P2-OFF-001 | 无 | 缺统一 lineage reader；现有 Artifact meta 可作为 source | 缺 inline/offloaded Artifact 完整扫描测试 | 无 | NEW |
| P2-OFF-002 | 无 | 现有 `retired_at` skeleton 与最终 recoverable Offline 语义不符；缺 Offline UoW + upgrade reuse | 不调用 SC delete | 无 | NEW + ADJUST SCHEMA |

### 3.5 SC Public Reference

| ID | Router/DTO | Service/存储 | Task/控制面 | 正式 OpenAPI | 结论 |
| --- | --- | --- | --- | --- | --- |
| P2-REF-001..003 | 全部无 | 缺持久 Operation/Item 表、Repository 和 exact lazy materialization orchestrator | 缺 Task handler；最终 add 必须复用 `SkillSetManagementService.add_skills()` | 无 | NEW |

不得复用旧 `market_source + identifier` 通用 Router，也不得在物化前写 Membership。

## 4. 跨接口基础模块 Gap

| 模块 | 当前 dev | 目标 | 状态/后续 |
| --- | --- | --- | --- |
| `SkillPackageValidator` | #1676 已合入，Local upload 已使用 | Space folder/Git/Materializer 共享严格校验 | DONE，禁止放宽为全局 Local legacy 模式 |
| `DraftContentStore` | #1680 已合入，OSS + Local Fake + DI | Application Service 写 immutable revision | DONE foundation；缺业务编排 |
| `CanonicalCenterVersionStore` | #1678 已合入 | exact write/read/verify | DONE foundation；缺 Materializer |
| `SkillCenterGateway` | public search/tags 与 team-scoped protocol 已有 | Publication/下载/状态查询复用 transport seam | ADJUST/补 conformance 场景，不另造 Gateway |
| `SkillVersionResolver` | dev 不存在；PR #1674 open | Reader 后唯一 exact-version read seam | NEW IN DEV；先 Review/合入 #1674 |
| Publication Materializer | 无 | SC exact download→validate→scan→Canonical Stores→PUBLISHED | NEW |
| Reference Operation | 无 | 持久 batch/items + Task + final Membership | NEW |
| Track Latest | 旧 propagation 不是最终 Reader/Resolver/Task 合同 | PUBLISHED 后 fanout，按执行时 latest 收敛 | NEW/REPLACE OLD PATH |
| Runtime Center mapping | Engine 已有 `ensure_center_skills` 与 pool_center path | Backend Resolver/Projector 必须消费 exact RegisteredSkillAsset | PARTIAL；需 Consumer-first E2E |
| 文件型 Service Artifact | 现有 manifest 不含最终 additive `center_skills` | build 前 project everything；冻结 exact refs；历史重放不 resolve latest | NEW |
| Teclaw v4 | v4 基础存在 | additive `skill-center` Store/Ref + offload/re-inline | NEW/CONSUMER GATE |
| Offline lineage | 无统一 reader | 扫描现有 inline/offloaded Artifact meta，未知 fail closed | NEW；不建新索引表/backfill |
| TaskQueue 原子写 | 普通 enqueue | 本期接受业务事务后 enqueue 有限窗口 | OUT_OF_SCOPE；#1679 已关闭 |

## 5. Schema/模型 Gap

当前 additive skeleton 不能直接视为最终合同：

| 当前事实 | 问题 | 最终处理 |
| --- | --- | --- |
| `Skill.retired_at/retired_by` | 名称和旧查询表达不可逆 retirement | 迁移为 recoverable `offline_at/offline_by` 语义；全部 query/DTO 同步 |
| `SpaceSkillItem.status/draft_status` | 把多个状态机压平 | 替换为 lifecycle + Draft + Attempt summary |
| `SkillActorPermissions.retire_skill` | 产品最终是 recoverable Offline | 改为 `offline_skill`；无真实调用方，不保留双字段 |
| `SkillPublicationAttempt` skeleton | 旧状态/字段可能与最终状态机不一致 | 按 #1686 逐字段审计后 additive DDL/ORM 修订 |
| `SkillVersion` skeleton | 需 exact SC version、metadata/MCP dependency、PUBLISHED Ready | 由 Materializer Ticket收口，不增加第二 Ready marker |
| SC Public external identity | `skillCode` 可能非 UUID且与其他 corpus 同名 | `git_path=center://<external_code>` 幂等；内部生成 UUID；不按 name 复用 |

## 6. Authorization、Admission 与 OpenAPI Drift

### 6.1 已确认

- `spaces/router.py` 使用 `PublicAPIRoute`。
- Grant/Lease/Editor Request 已进入 Authorization/Admission inventory。
- Backend candidate OpenAPI 能生成这些路径，说明 Router assembly 正常。

### 6.2 待修复

当前 pinned `bots.openapi.json` 未包含已合入的 Grant/Lease/Editor Request 路径。对应收口 PR 必须：

1. 从最新 dev 生成 candidate；
2. 运行 compatibility gate；
3. 提交生成 artifact；
4. 增加 test 证明 candidate 与 pinned 不再 drift；
5. 禁止手工编辑 JSON。

所有 NEW 路径实现时必须同时加入两个 inventory；不能先注册再补权限。

## 7. 建议纵向 Ticket 输入

Gap 不应按 Router、DTO、Repository 横切拆票，而应按可独立验收的产品闭环：

| 建议闭环 | 包含 Operation | 主要依赖 |
| --- | --- | --- |
| Contract drift 收口 | P2-SKL-001、P2-GRT/LSE | #1686 + 当前 dev |
| Space Skill Create/Detail | P2-SKL-002..004 | Validator + Draft Store |
| Draft/Version Editor | P2-DRF、P2-VER | Create/Detail + Version Resolver |
| Publication | P2-PUB | SC Gateway + Materializer + TaskQueue |
| SC Public Reference | P2-REF + MKT-003 | Materializer + SkillSetManagementService |
| SC Public Sync/Track Latest | MKT-004 + fanout | Materializer + Reader + Version Resolver |
| Runtime/Artifact Consumers | 非新增页面 Router | Resolver + Engine/Teclaw contract |
| Offline | P2-OFF | Artifact consumers + lineage reader + upgrade |
| Gateway Enable/E2E | 全部已实现路径 | 所有 Producer/Consumer + generated OpenAPI |

必须保留以下 blocking edges，不能只按页面接口并行合入：

```text
SkillVersionResolver
→ Runtime Center mapping + Engine/Pool consumer 验收
→ 文件型 Artifact + Teclaw v4 历史重放验收
→ Publication / SC Public Reference Producer
→ Gateway OpenAPI Enable + 产品真实联调
```

Publication/Reference Router 可以先在隔离分支开发，但在 Consumer-first 门禁通过前不得作为
可用生产合同发布。Offline 还额外依赖 Artifact lineage reader；否则无法证明历史 Service Bot
引用不存在。

旧 Phase 2 Tickets 必须逐条映射到上述闭环；不能继续按旧 ZIP、retirement、同步 Reference 或旧
Installation 语义直接领取。

## 8. 完成判定

当且仅当下列条件同时成立，某一行才能从 NEW/ADJUST 改为 DONE：

- 真实 Router、DTO、领域服务和持久化/Task 完成；
- 权限与 Admission 完成；
- narrow tests 与 contract tests 通过；
- 自动生成 OpenAPI 与 compatibility gate 通过；
- `bots.openapi.json` 已更新；
- 前端手册示例与最终 DTO 一致；
- Singlebox/Gateway 可真实调用，不存在 stub；
- 对应产品 User Story 可端到端验收。
