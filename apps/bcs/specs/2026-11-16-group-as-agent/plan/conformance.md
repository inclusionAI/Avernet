# 新增合同的集中 conformance 与 port 纯度矩阵

关联：[主计划](../plan.md)、[接口定义](interfaces.md)。本矩阵是每项任务Files与测试要求的一部分，不是T20才补的检查表。新增合同不申请waiver；存量门禁失败不免除新增接口的验证，也不允许暂时返回Unsupported来假装完成合同。

## 1. DTO 与声明位置规则（T01起）

- Submission/TaskQuery/ListQuery/TaskPage/SubscriptionStart/EventQuery及执行、凭据、Card DTO全部位于domain/group_a2a的中立模块；application/core仅定义trait和业务编排。
- Repo/port不引用application/core的公开类型（包括别名），直接依赖domain或中立types。Plugin API自持认证类型，不引用Service API；bootstrap桥接。
- `GroupInvocationRepo`与相关Service以任务粒度增量添加方法与测试。T07交付admit/get、T08交付claim/begin_dispatch/renew/consume/observation、T11终态和events、T12取消、T13恢复、T14完整查询、T15清理；**同一方法的声明、实现和对应harness断言必须同次交付**，不能T01先声明全方法后以Unsupported默认实现避测。
- 每个生产实现的 `tests/conformance_*.rs` 同时包含真实实现类型名和共享 `<trait_snake>_contract_tests` 调用，满足R25.2并有实际语义断言。插件／application／bootstrap即使不在旧脚本impl扫描范围也执行同等测试，不能利用扫描范围漏洞。

## 2. 完整合同映射

下表声明路径以 `apps/bcs/crates/` 为前缀；harness统一放 `apps/bcs/crates/test-support/bcs-test-support/src/contract/`；runner以被测crate根为准。文件在对应任务创建，并在父mod注册。已存在的测试文件必须追加而不是覆盖。

| 合同 | 声明文件 | 集中harness文件 → 精确函数 | 真实实现与conformance入口 | 交付 |
| --- | --- | --- | --- | --- |
| GroupA2aService | service-api/bcs-service-api/src/application/group_a2a.rs | application/group_a2a.rs → `group_a2a_service_contract_tests` | GroupA2aApplication；application/bcs-app-group-a2a/tests/conformance_group_a2a_service.rs | T08起，T12/14补齐 |
| TaskWaitService | 同上application/group_a2a.rs | application/group_a2a_wait.rs → `task_wait_service_contract_tests` | PersistentTaskWait；application/bcs-app-group-a2a/tests/conformance_wait.rs | T18 |
| GroupInvocationCoreService | service-api/bcs-service-api/src/core/group_invocation.rs | core/group_invocation.rs → `group_invocation_core_service_contract_tests` | GroupInvocationCore；services/bcs-group-a2a/tests/conformance_core.rs | T08起，T11–13扩展 |
| GroupInvocationRepo | service-api/bcs-service-api/src/port/repo/group_invocation.rs | repo/group_invocation.rs → `group_invocation_repo_contract_tests` | SqlGroupInvocationRepo/MemoryGroupInvocationRepo；services/bcs-group-a2a-store/tests/conformance_invocation.rs、conformance_invocation_mysql.rs | T07起逐方法同步 |
| GroupExecutionPort | service-api/bcs-service-api/src/port/group_execution.rs | port/group_execution.rs → `group_execution_port_contract_tests` | GroupExecutionBridge；bootstrap/bcs/tests/conformance_group_execution.rs，分别使用真实MessageFlow/Runtime+fake网络下游 | T09/10/12，T19装配 |
| A2aAuthenticationService | service-api/bcs-service-api/src/application/a2a_auth.rs | application/a2a_auth.rs → `a2a_authentication_service_contract_tests` | A2aAuthenticationApplication；application/bcs-app-a2a-auth/tests/conformance_authentication.rs | T04 |
| A2aCredentialAdminService | 同上application/a2a_auth.rs | application/a2a_credentials.rs → `a2a_credential_admin_service_contract_tests` | A2aCredentialAdmin；application/bcs-app-a2a-auth/tests/conformance_credentials.rs | T05 |
| A2aCredentialVerifierPort | service-api/bcs-service-api/src/port/a2a_credentials.rs | port/a2a_credentials.rs → `a2a_credential_verifier_port_contract_tests` | A2aAuthBridge；bootstrap/bcs/tests/conformance_a2a_auth_bridge.rs | T04，T19接线 |
| A2aTokenRepo | service-api/bcs-service-api/src/port/repo/a2a_token.rs | repo/a2a_token.rs → `a2a_token_repo_contract_tests` | SqlA2aTokenStore/MemoryA2aTokenStore；services/bcs-a2a-token-store/tests/conformance_tokens.rs、conformance_mysql.rs | T03 |
| A2aAuthPlugin | plugin-api/bcs-auth-api/src/a2a.rs | plugin/a2a_auth.rs → `a2a_auth_plugin_contract_tests` | BcsTokenAuthPlugin；plugins/bcs-auth-bcs-token/tests/conformance_plugin.rs | T04 |
| CredentialLookup | 同上plugin-api/bcs-auth-api/src/a2a.rs | plugin/a2a_lookup.rs → `credential_lookup_contract_tests` | A2aCredentialLookupBridge；bootstrap/bcs/tests/conformance_a2a_lookup.rs | T04，T19接线；虽无Port后缀也要求 |
| GroupA2aCardService | service-api/bcs-service-api/src/application/group_a2a_card.rs | application/group_a2a_card.rs → `group_a2a_card_service_contract_tests` | GroupA2aCardApplication；application/bcs-app-group-a2a/tests/conformance_card.rs | T16 |
| GroupA2aPublicationRepo | service-api/bcs-service-api/src/port/repo/group_a2a_publication.rs | repo/group_a2a_publication.rs → `group_a2a_publication_repo_contract_tests` | SqlGroupA2aPublicationRepo/MemoryGroupA2aPublicationRepo；services/bcs-group-a2a-store/tests/conformance_publication.rs | T16 |
| GroupA2aCatalogPort | service-api/bcs-service-api/src/port/group_a2a_catalog.rs | port/group_a2a_catalog.rs → `group_a2a_catalog_port_contract_tests` | FuseGroupA2aCatalogBridge；bootstrap/bcs/tests/conformance_group_a2a_catalog.rs，真实Fuse HTTP client+本地peer | T16，T19接线 |
| GroupA2aAdminService | service-api/bcs-service-api/src/application/group_a2a_admin.rs | application/group_a2a_admin.rs → `group_a2a_admin_service_contract_tests` | GroupA2aAdminApplication；application/bcs-app-group-a2a/tests/conformance_admin.rs | T13 |
| A2aChangeNotifier | service-api/bcs-service-api/src/port/group_a2a_wait.rs | port/group_a2a_wait.rs → `a2a_change_notifier_contract_tests` | LocalA2aChangeNotifier；application/bcs-app-group-a2a/tests/conformance_notifier.rs | T18；不承诺跨实例通知 |
| A2aClock | service-api/bcs-service-api/src/port/group_a2a_clock.rs | port/group_a2a_clock.rs → `a2a_clock_contract_tests` | SystemA2aClock/FakeA2aClock；bootstrap/bcs/tests/conformance_a2a_clock.rs | T01/T08接线 |

`A2aAuthChain`、GroupA2aWorker/Retention等为具体类型，不另造同名trait；若实现阶段新增Lifecycle/Interceptor等公开trait，必须先扩展本矩阵。已经存在的MessageFlowService、CollaborationRuntimeService、Group/Session/RuntimeBinding Repo更新其原集中harness，并用真实新入口补断言，不因为本表没有重列既有trait就漏测。

## 3. Harness 的实际断言与测试构造

每个harness至少包含一条真实成功和一条失败路径、插件/端口调用记录、范围隔离、DB/网络失败传播；不能只编译trait或断言默认返回值。重要附加行为：

- Repo：真实Memory/SQLite/MySQL跑同套受理/终态/隔离/清理竞争；不能每个实现各写一套不同预期。
- Execution：permit消费与根queue原子登记、重复permit不双Run、取消后旧fence拒绝、Unknown不是Stopped；两种群均通过。
- Auth/Lookup/Verifier：无命中/过期/撤销/依赖故障；Rejected不fallback；secret不进Debug/业务日志；bridge只转换可信字段。
- Card/Catalog：public JSON discovery、六类调用Bearer、LOCAL替换、status资格、旧Group writer退出、超时不假ack。
- Wait/Notifier：同实例通知和无通知双实例DB poll均结束；Notifier的harness只能证明加速，不能把它当持久化证明。
- Clock：单调推进的fake和绝对截止时间解释正确；生产clock差异不重置存储预算。Fake不跨进程模拟未知状态。

示例骨架（T03的实际类型/函数名必须出现在runner中）：

```rust
#[tokio::test]
async fn conformance_sqlite_a2a_tokens() {
    let repo: SqlA2aTokenStore = sqlite_store().await;
    bcs_test_support::contract::repo::a2a_token::a2a_token_repo_contract_tests(&repo).await;
}
```

`sqlite_store()`在对应tests/support创建真实DbPlugin并完整迁移，StoreScope和singleton绑定先校验；harness负责输入fixture与断言，不依赖具体Store crate避免循环依赖。

## 4. Gate 执行与证据

从 `apps/bcs` 运行：

```bash
bash scripts/ci/check-port-purity.sh
bash scripts/ci/check-r25-conformance.sh
cargo test -p bcs-test-support -- --list
```

R25脚本的discoverability步骤会编译workspace，不能将仅grep检查说成完整通过。旧基线失败先单独记录/定位，新接口必须全部满足矩阵，最终门禁仍需通过；不新增CONFORMANCE_WAIVED、不给script降级、不把exit77/0tests视为成功。本次仅文档修订，没有执行这些未来功能门禁，也没有对应新增实现可供证明。
