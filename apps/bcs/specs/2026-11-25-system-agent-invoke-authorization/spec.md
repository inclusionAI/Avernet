# 生产系统 Agent 单向调用授权 — OpenSpec

- 日期：2026-11-25（Asia/Shanghai）。状态：**Draft / reader-first 部分实施；完整授权生命周期尚未实现或发布**。
- Change：`system-agent-invoke-authorization`；owning module：Avernet BCS。
- 当前源码基线：fetch 后 `origin/dev` **`9a9aea2c3b5a0e5a0ed77c85e3790b74dca18bd8`**，不是主 checkout 的旧 dev/WIP。
- dedicated worktree：`Avernet/.worktree/system-agent-invoke-authorization-spec`，branch `codex/system-agent-invoke-authorization-spec`。
- 用户已授权实施并创建 PR；本轮修改 Rust 类型/读端/旧路径隔离及回归，不改 SQL migration、配置/依赖/构建，不修改 OCB/bcngw，不部署或执行生产请求。

## 当前实施状态

实施基线已刷新到 `origin/dev = 4571a50c944ad02f91e0d002b22b73bbdfc2ec80`。目前为 reader-first 安全兼容切片，不是完整功能：4/27 整体任务完成；新管理 HTTP/审批事务/generation/治理/真实 invocation 尚未实现。详见 [implementation.md](implementation.md)。下表仍描述完整目标，不能当作全部已实现。

## 核心提议

新增 **`GrantKind::SystemInvoke` / `system_invoke`**，表示同一受信生产安全域内系统 Bot A 直接调用系统 Bot B 的单向授权。不是好友、不是 Owner/Manager、不是超级权限、不继承到相同 AgentCode 的其他 Bot，不反向或多跳传播。

落库 `from_id/to_id=bot_uuid`，`grant_ref_id=0`，`rules=NULL`，`originator_policy_type=same_as_from`。系统资格、域、Provider/actor 绑定和真实 originator 来自受信边界，不能由 Card、agent_code、展示名或标签自证。

| 议题 | 目标契约 |
| --- | --- |
| 旧 kind / 字符串 | `permission_profile/rules/owner/manager` 全部保留；没有历史边自动改类型 |
| `list_active_grants` | 新 kind 加入运行候选集，管理元数据仍排除；新安全决策使用新增 fallible 读取 |
| `is_authorized` | 同候选集的 bool 存在性，不等于实际 system invocation allow |
| `has_friend_edge` / friends | 不扩展，仍仅 default-profile；撤销互不影响 |
| 一般 admission | 不因新边放行；修正消费者 kind 过滤，绝不转成 default profile |
| 运行鉴权 | 显式 direct-invoke operation、originator==from、资格/绑定代次、approved generation；新 runtime ref 指 edge_id |
| 公共接口 | 拟新增独立 system-invoke 资源和 Service API；不冒充目前已接通，也不复用 friend-connections |
| 申请/审批 | `RequestKind::SystemInvoke`，单方向审批，权限及资格在提交时重新校验 |
| 创建/撤销 | scoped target owner/governance 批准，source 可申请/止损；按 edge_id+version 撤销，不按 pair 广删 |
| 索引/治理 | 按 env/domain+kind+status 的入出边分页/degree/pending count，事务内限额与去重，真实 SQL 计划证据 |
| rules | 首版不复用；未知或未执行的细粒度规则不能当 allow |
| 身份契约 | AgentCard 是描述，不是凭据；AgentCode 可共享，最终必须唯一解析到 bot_uuid |
| 验证 | 单元、共享 conformance、逐 HTTP endpoint、真实 DB store/竞态、singlebox E2E；全部是未来门禁，不是本轮通过项 |

## 文件导航与状态

1. [proposal.md](proposal.md)：动机、能力、影响面。
2. [design.md](design.md)：规范决策 D1–D8、接口、状态机、兼容、存储、治理和部署顺序。
3. [可验收 Requirements / Scenarios](specs/system-agent-invoke-authorization/spec.md)：OpenSpec SHALL / MUST 与 Given/When/Then 等价场景，是拟议目标契约。
4. [research.md](research.md)：固定 SHA 的现状证据与未合入身份 Draft 的边界，不把设计当已实现。
5. [test-plan.md](test-plan.md)：分层验收矩阵、命令与缺口。
6. [tasks.md](tasks.md)：后续实施顺序，全部未完成。
7. [validation.md](validation.md)：本次实际做过的文档检查，不宣称 runtime/CI/部署。

## 评审与实施门禁

待确认：可信系统 inventory/资格 generation 提供者、租户 namespace 隔离、生产限额、审计留存/负责人、实际暴露的 HTTP/OpenAPI/宿主传输面。详见 design 的 Open Questions。默认采用最小权限决策；未明确不得放宽为 anonymous、按 code 任选 Bot、manager 自动批准或 Any originator。

没有已接受 ADR 直接规定本新 kind；遵守既有 BCS contracts/架构，评审如形成跨系统、难逆转决策，再按 ADR 流程记录，不把本 Draft 宣布成已接受 ADR。

## OpenSpec 工具与 Git 可见性

仓库 `.gitignore` 忽略 `openspec`；正式交付保存在本目录，不强改 tracked `.gitignore`。worktree 内的 ignored `openspec/changes/system-agent-invoke-authorization` 仅为指向本目录的兼容链接，不复制第二份真源，也不链接/写入主 checkout 的 planning home。

在此 worktree 运行：

```sh
openspec status --change system-agent-invoke-authorization
openspec validate system-agent-invoke-authorization --type change --strict --no-interactive
```

其他 checkout 需要重建本地链接后才能用 CLI 定位本规范；`spec.md`/requirements 可直接审阅。**不要**从没有本地 planning home 的 `.worktree` 运行 `openspec new`，CLI 可能向上找到主 checkout 的 ignored openspec。

apply-ready 只表示 proposal/design/specs/tasks 存在且满足结构，不自动获得写代码授权。用户已单独确认实施及创建 PR；本 PR 仍不授权生产启用。
