<!--
本 PR 模板与 src/bcs/scripts/ci/check-pr-gate.sh 联动。
修改 src/bcs/crates/service-api/** 或 src/bcs/crates/plugin-api/** 时必须填写"架构合规自检"小节。
-->

## 改动说明



## 架构合规自检（修改 BCS service-api 或 plugin-api 时必填）

- [ ] 契约方向分类：[ ] Service API   [ ] Plugin API   [ ] Protocol Definition
- [ ] 改动类型：[ ] additive   [ ] breaking

### Propagation analysis（breaking 必填）

- 影响的 consumer：
- 影响的 implementation：
- 影响的部署 / 配置：
- 迁移 / deprecation 方案：

### 新增 Outbound Port（仅引入新 Port 时必填）

通过了 `src/bcs/docs/arch/refactor-arch-proposal.md` "Outbound Port Design Criteria" 三条检验：

- **De-domain test**：签名仅依赖 std / 3rd-party / types，论证：
- **Infrastructure swap test**：给出至少两种基础设施实现方案：
- **Business reuse test**：业务动词单一性论证：

### 新增偏离（仅在确实必要时）

- [ ] 否
- [ ] 是 → 已在 `src/bcs/crates/service-api/CONTEXT.md §5` 登记；waiver 编号：`src/bcs/docs/waivers/...md`
