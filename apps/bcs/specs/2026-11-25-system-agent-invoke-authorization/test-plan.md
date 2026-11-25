# 后续实现回归与验收计划

状态：**计划，未执行**。本次文档检查单独记在 [validation.md](validation.md)。每个测试须绑定实际执行 SHA、provider/dialect、通过/总数、失败与 skip；示例命令不能算通过。

## 验收矩阵

| 层 / 所属 | 必测场景 | 必须观察的结果 |
| --- | --- | --- |
| Domain unit / bcs-domain | 新旧 5 kind 序列化；未知值；SystemInvoke shape；同 ref0 不同 kind；RequestKind；case/space | exact DB/JSON 字符串；旧四类型无 drift；坏 shape 拒绝 |
| Policy unit / bcs-edge-permission | 各角色矩阵；A→B/逆向/多跳/委托；隐藏、退役、个人/生产域；qualification generation | allow 只来自正确 operation 和 originator，owner/manager 不变运行权 |
| Identity consumer conformance | trusted credential→unique bot_uuid、code 多实例/缺失、Provider/tenant/env 不符、issuer/audience/时效失败、读失败 | 受信 resolver 真命中，不能 first-match、自报或空值兜底；local fake 离线可运行 |
| Repo shared conformance / bcs-test-support | list_active_grants/is_authorized 加新 kind；metadata 排除；has_friend_edge/list 不变；checked read；pair/kind coexistence；精确 revoke/regrant | 同一 harness 在 Memory/SQLite/MySQL，fake/noop 只验证其声明的隔离语义，不替代真实存储 |
| Management Service API conformance | create/request/approve/reject/cancel/revoke/read/list/degree；权限丢失再审批；幂等/状态冲突/审计失败 | 通过 application/core 契约调用，记录真实 repo/资格 port 命中，原子状态可查询 |
| HTTP contract / registered router | D6 每个 endpoint 各自 happy + reject；401/403/404/400/409/429/503；未知字段/ID/无权限/跨域；pagination/error envelope | 创建、GET、审批、撤销、列表独立验证；OpenAPI mirror 如启用需同组验证，不以 PATCH 一条代替 |
| Legacy HTTP/consumer | friends/friendships/friend-connections、friend inbox 含 system request 时过滤、admission、authority、AgentCard management | 不接受 kind 参数；system-only 不变好友、不生成 default grant、不写 legacy friendship；Card 原契约保留 |
| DB store / SQLite | 完整 fresh/upgrade migration；坏 enum/JSON/shape；唯一约束、审计/receipt 原子、CAS、计数与排序 | real SQLite transactions，不只 assertion 生成的 SQL 文本 |
| DB store / MySQL | 同组 real MySQL、NO PAD、大小写/尾空格区别、复合键长度、unknown commit、deadlock、locks、EXPLAIN | 明确实际 MySQL 版本、strict mode、migration chain；不能把 SQLite/MariaDB 结果冒充 MySQL/OceanBase |
| Host DB/provider acceptance | 真实宿主 compatible DB、资格/身份 provider/namespace 及 generation invalidation | 单独记录宿主版本与集成结果；公共契约过测不代表 OCB 已接通 |
| E2E singlebox | synthetic prod namespace + real local HTTP/app/DB + loopback A2A fake receiver | 真实调用计数与存储审计，不仅直接调用 policy 函数或 mock bool |
| Architecture / boundaries | domain 纯类型、application-only delivery、repo/store SQL、composition root/central hooks、config validation | 独立 conformance 映射与 existing gate；不得用 Python suite 发现代替 Rust runtime tests |

## E2E 最小故事（全部未来门禁）

1. 创建受信 synthetic 系统 A/B/C 和非系统 D，目标 protected，关闭 public/collaboration/friend 等独立放行来源；基线 A→B 真实调用被拒，fake B 未收到请求。
2. source manager 申请 A→B：pending、无 edge；无 target 权限的审批被拒；target owner 批准后精确一条边；真实 A→B 成功、B→A 拒绝。
3. friend lists/degree/owner roles 断言：不是好友、不具管理权，system 出入度各增加一，不影响旧 profile/rules 计数；重复 approve 不重复加度数。
4. B→C 批准也不允许 A→C，A-originator 经 B 的委托不能借 B→C；伪造 body originator、code、env 不成功。
5. revoke A→B 后同一认证发起的新调用被拒、无新增 fake B delivery；历史 receipt 可查，旧 key replay 不复活；新 key+审批才能恢复并提升 generation。
6. 分别共存 default-profile、non-default profile、Rules 和反向 system 边，撤销各资源只影响本边；若独立授权仍可调用，必须报告命中来源，不误判撤销失效。
7. B hidden/retired/rebound/失去系统资格立即阻止新调用；恢复不复活旧批准；相同 code 新 Bot 不继承；不存在/资格库或 DB 错误 fail-closed。
8. 验证申请 reject/cancel、权限代次变化、各 GET/list/cursor/degree、quota boundary 和主错误分类。以真实 store 证明非成功响应没有部分状态/审计残留。

## 并发与失败注入

- real SQLite + MySQL 独立连接，Notify/barrier 固定交错；不使用 sleep 作为正确性依据。
- approve vs cancel/reject：至多一个终态，失败者 409；approve vs revoke：以 version fence 线性化。
- revoke vs old create-key replay vs new regrant：旧 key 永不复活，只有新的授权动作改变状态。
- 两条入/出审批竞争最后 quota slot：只一个成功，counts 与 rows 一致。
- 持久化审计/edge/request/receipt 任一步失败：同事务回滚；commit acknowledgement 丢失：同 key 读回已提交或仍不确定，不重复写。
- 资格 generation 在 check→commit 之间改变：CAS 拒绝；权限 generation/ownership transfer 同理；跨节点后续 admission 不用旧 positive cache。
- Query/坏行失败不能变成功空页、计数 0 或 default-profile fallback；max-page、empty-page 和 high-degree 扫描都有计划/成本数据。

## 执行入口（未来实施后核实）

从 dedicated worktree 执行，以 `apps/bcs/Cargo.toml` workspace 为准；以下目前仅是计划：

```sh
cd apps/bcs
cargo test -p bcs-domain
cargo test -p bcs-edge-permission
cargo test -p bcs-edge-permission-store
cargo test -p bcs-test-support
bash scripts/ci/arch-check.sh
bash scripts/ci/check-store-boundaries.sh
```

新共享 harness 的 Memory/SQLite/MySQL drivers、新 HTTP router suite 和 E2E story 需在实现时注册，记录精确 test names / 命令；不能因为已有 conformance_bot_authority/authority_mysql 通过就宣称新 kind 被测。真实 MySQL URL 仅从 test bootstrap 注入，不记录秘密；缺依赖须明确 skip，并作为对应 provider 的未完成验收，不报告通过。

按风险先运行定向 gates，再运行所有受影响 crate/HTTP/legacy tests，最后按仓库策略执行 `singlebox/ci/singlebox_coverage.sh` 等全栈门禁及 artifacts verifier。保持当前 CI 阈值与分母，不为本变更放宽 coverage/architecture；wide gate 因既有问题失败时记录基线归因，不替代失败为通过。

最终验收报告必须分开：spec 文档有效、定向单测、conformance drivers、真实 DB、HTTP、E2E、current-SHA CI、host integration、部署/获准环境 live proof。每一层未跑就是未跑。
