# Backend 接入 BCS caller 配置同步 — 设计

- **状态**:已确认设计(2026-10-29 brainstorming 会话,等待实施)
- **模块**:`apps/backend`
- **关联**: `docs/specs/2026-09-16-caller-identity-session-container/spec.md`(§10.2/§10.3/§10.7)、`apps/bcs/docs/caller-identity-configuration.md`

## 1. 问题

Backend 是 Bot caller 身份的权威源:`CallerIdentityService`(开/关 = `McpCallType.CALLER/OWNER`)。BCS 的 caller 运行期功能(`CallerConfigurationDeliveryGate`、caller ingress)依赖 BCN 侧 per-bot 的 `CallerIdentitySnapshot`(`bcs_bots.bot_info.$.caller`)。BCS 已暴露权威同步端点

```
GET   /providers/{provider_id}/bots/{provider_bot_ref}/caller
PATCH /providers/{provider_id}/bots/{provider_bot_ref}/caller
```

但 Backend 目前没有任何调用方——后端开关落库后只同步 Agent Principal(Engine),BCS 的 caller 快照停在自己最后一次值上,状态永久漂移。

## 2. 关键代码事实(设计依据)

| 事实 | 位置 |
|---|---|
| `get_bot_call_type` 无视 stage/publish_id,`ac_bots.call_type` 即生效聚合,落库即生效 | `core/caller_identity/service.py:428-437`(`del stage, publish_id`) |
| per-bot 单调 revision 已存在:`ac_bots.caller_config_revision`,每次 `replace_draft_*` +1 | `core/repository/implementations/identity/caller_identity.py:182,289` |
| 开关的两个服务入口(均已 async,已含 Agent Principal 同步+补偿) | `CallerIdentityService.update_mcp_call_type` / `update_cli_call_type`(`core/caller_identity/service.py:104,226`) |
| 现成 BCN 传输层:`BcnService`(Bearer `provider_admin_token`,`provider_bot_ref = "{teamclaw_bot_uuid}:{owner_workno}"`,无凭据 env-skip 模式) | `core/bot_management/services/bcn_service.py`;凭据来自 `bcn` user_config 块(`di/config.py:78-95 BcnConfig`) |
| BCS PATCH wire 契约:body `{"snapshot": {"source_revision": N, "caller_identity": {"enabled": bool, "minimum_isolation": "caller_container"}}}`,响应 `sync_status ∈ {applied, unchanged, stale}`(按 `source_revision` CAS) | `bcs-domain/src/caller_identity.rs`;契约测试 `bcs-http/tests/provider_caller_config_routes_contract.rs:218` |
| BCN 既有失败语义严格:`switch_bot` 失败中断 upgrade 流程 | `bot_publish_service.py:1440` |

## 3. 决策记录

1. **触发点 = 开关落库后**,不挂 publish。`call_type` 无 draft/online 分态,落库即生效;publish 流程没有 caller 概念。
2. **失败传播,不降级**:BCS 同步失败 → 抛 `CallerBcsSyncError` 打回整个操作。仓库纪律(write failure 不吞)+ 无 repair loop 时静默漂移最危险。重试幂等:再次提交同值 → `caller_config_revision` 再 +1 → BCS 看到严格更高 revision → `applied`,自然收敛。
3. **复用 `BcnService` 作传输层**,不自建 httpx 客户端(复用凭据/超时/skip 模式),也不引入事件表+异步 worker(YAGNI)。

## 4. 架构

```
CallerIdentityService.update_mcp_call_type / update_cli_call_type
  1. 落库(replace_draft_* → call_type + caller_config_revision+1)   [现有]
  2. Agent Principal 同步;失败→补偿回滚 + CallerMcpSyncError          [现有]
  3. BCS caller snapshot 同步 [新增,本设计] ;失败→CallerBcsSyncError
```

补偿路径不推 BCS:回滚值 = BCS 上一已知值。

### 4.1 组件

- **端口协议** `BcsCallerConfigSyncProtocol`,放 `core/caller_identity/protocols.py`(与 `CallerMcpSyncProtocol` 同处):

  ```python
  def sync_caller_config(
      self, *, bot_id: str, owner_id: str, enabled: bool, source_revision: int
  ) -> None
  ```

- **传输实现** `BcnService.sync_bot_caller_config(...)`(`bcn_service.py` 新方法):只做传输,不感知业务;
  enabled/source_revision 由调用方折叠:
  - `enabled = (bot.call_type is McpCallType.CALLER)`
  - `source_revision = bot.caller_config_revision`
  - `minimum_isolation` V1 固定 `"caller_container"`(契约目前仅此一档)
- **组装** `di/modules/caller_identity_module.py`:注入已存在的 BcnService singleton,薄适配绑到端口,避免 `core/caller_identity → bot_management` 的直接 import(保持调用方向干净)。

### 4.2 wire 契约

```
PATCH {bcn.base_url}/providers/{bcn.provider_id}/bots/{bot_id}:{owner_id}/caller
Authorization: Bearer {bcn.provider_admin_token}
Content-Type: application/json

{"snapshot": {"source_revision": <caller_config_revision>,
              "caller_identity": {"enabled": <bool>, "minimum_isolation": "caller_container"}}}
```

- 本链路**只传非身份控制面数据**(`enabled`/`minimum_isolation`,两个 bool 级字段)。身份材料(token/session/iam)绝不进入——bcs-domain 契约红线。

## 5. 错误处理

| 情况 | 行为 |
|---|---|
| `sync_status ∈ {applied, unchanged}` | 成功返回(BCS 的 CAS 分歧在 200 响应体里表达,不出 4xx) |
| 网络/5xx/超时 | `CallerBcsSyncError`(本地已生效;文档注明重试收敛) |
| `sync_status=stale`(revision 落后) | `CallerBcsSyncError`,日志指明 BCS 侧 revision 更高——两边计数器分叉,需人工介入 |
| 404(bot 未注册为 provider bot) | `CallerBcsSyncError`,提示先注册 |
| 403/401 | `CallerBcsSyncError`,凭据/权限问题 |
| `bcn` 块缺省(dev/singlebox 无凭据) | 沿用 `register_provider_bot` 的 skip+INFO 日志模式,返回 `{"skipped": true}` |

`CallerBcsSyncError` 与 `CallerMcpSyncError` 同族,注册进 `core/caller_identity/contracts.py`(域错误 code `CALLER_BCS_SYNC_FAILED`),HTTP 路由层沿用现有 DomainError 映射风格。

## 6. 环境与配置

零新增配置:完全复用 `bcn` 块(`base_url` / `provider_id` / `provider_admin_token`)。community 构建默认为空 → 该同步自动降级为 skip(fail-closed 建制一致)。

## 7. 测试

- `BcnService.sync_bot_caller_config` 单测(httpx mock,沿用既有 bcn_service 测试风格):`applied`/`unchanged` 通过、`stale`/404/401/403 抛错、5xx/超时抛错、无凭据 skip。
- `CallerIdentityService` 集成测试(替换端口的 fake):断言成功路径携带折叠后的 `enabled` 与当前 `caller_config_revision`;fake 抛错 → `CallerBcsSyncError`。
- HTTP 路由透传测试:`tests/community/endpoints/test_caller_identity_router.py` 增量。
- CI 门:`DEPLOY_PROFILE=test uv run pytest tests/community -v`;改动行覆盖 ≥80%(仓库门)。

## 8. 风险与待验证项

- **BCS 收敛窗口**(既定接受边界,本设计不触碰):开启方向 ≤5s 内 BCS 仍按 Owner 路径派发;关停方向 ≤30s 内继续按 caller 保护(保守安全)。
- **待验证**:OSS singlebox 的 BCS 默认不装配 `[caller_identity]` 服务器段——PATCH 存储 snapshot 的路由应与插件装配无关仍可用,需在 `singlebox start bcs_bots` E2E 里确认一次 PATCH→GET 读回。
- `ac_bots.call_type` 聚合在 CLI/MCP 两类 update 中都会变更,两个入口都要挂;其他写点(compensate_*)不挂。

## 9. 非目标

- 不改 publish/bot 升级流程;
- 不改 IAM exchange、engine 同步;
- 不做后台对账/重试 worker;
- 不在后端实现 `minimum_isolation` 的可配置化(V1 单档)。
