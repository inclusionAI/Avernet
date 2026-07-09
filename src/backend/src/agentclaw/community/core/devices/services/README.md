# `agentclaw.community.core.devices.services` — DeviceContextResolver

全仓**唯一** provider 解析点。caller 用 `(bot_id, user_id)` 入参，
resolver 查 `binding.device_provider` 拿 provider，委托对应
`ConnInfoBuilder` 算 `conn_info`，包成 typed `DeviceContext` 出口；
下游 dispatcher 只看 `ctx.provider` 选 plugin 实例。

## 架构

```
        caller (bot_management / cron / expert_chat / yuque / sync_bot_config / ...)
                │  (bot_id, user_id)
                ▼
        ┌───────────────────────────┐
        │  DeviceContextResolver    │  ← 唯一 provider 解析点
        │  (device_context_resolver │     binding.device_provider 单源
        │   .py)                    │     委托 builder + schema 归一
        └────────────┬──────────────┘
                     │  DeviceContext(provider, conn_info, binding_id, bot_id, user_id)
                     ▼
        ┌───────────────────────────┐
        │  Dispatcher               │
        │  ├─ FilesystemDispatcher  │  ← di/modules/skill_center_module.py
        │  └─ DeviceSyncDispatcher  │  ← plugins/prod/device_sync.py
        └────────────┬──────────────┘
                     │  ctx.provider → 选 plugin 实例
                     ▼
        Baas / Arca / Teclaw / Local Plugin → 拨号
```

详见 spec §6.1 `docs/superpowers/specs/2026-06-15-device-context-resolver-design.md`。

## 入参规范

```python
ctx = resolver.resolve_for_bot(bot_id: str, user_id: str) -> DeviceContext
```

- `bot_id` — 业务 bot ID。
- `user_id` — 操作者身份（非 owner 时 caller 须自行权限校验，
  resolver 不做权限判断）。

可能抛出：

- `DeviceNotBoundError` — bot 无 active binding（未 apply / 已 release）。
- `UnknownProviderError` — `binding.device_provider` 是未知值（DB 异常）。
- `ConnInfoBuildError` — 底层 conn_info 计算失败。

## `DeviceContext` 字段

| 字段          | 含义                                       |
| ------------- | ------------------------------------------ |
| `provider`    | `arca` / `baas` / `teclaw` / `local`       |
| `conn_info`   | 拨号字段 dict（命名经 resolver schema 归一）|
| `binding_id`  | `ac_entity_device_binding.id`              |
| `bot_id`      | caller 透传                                |
| `user_id`     | caller 透传（用于 device_affinity / 审计）|

frozen dataclass，不可变。

## 4 个 ConnInfoBuilder（`conn_info_builders/`）

| Builder                | 复用底层逻辑                                                       |
| ---------------------- | ------------------------------------------------------------------ |
| `ArcaConnInfoBuilder`  | 复用 `ArcaDeviceService` proxy 配置                                |
| `BaasConnInfoBuilder`  | 委托 `baas_conn_info.build_baas_conn_info_for_http`（baas /http-info）|
| `TeclawConnInfoBuilder`| 复用现 teclaw plugin 内 OSS 装配逻辑                                |
| `LocalConnInfoBuilder` | 兼容期 fallback（无 binding 场景；含 desktop pathlib path）         |

builder 仅算 conn_info，不做权限 / 不做存活检查。

## 2 个 Dispatcher

| Dispatcher                  | 入口                              | 出口                       |
| --------------------------- | --------------------------------- | -------------------------- |
| `DeviceFilesystemDispatcher`| `dispatch(ctx) -> DeviceFileSystem` | filesystem plugin 实例 |
| `DeviceSyncDispatcher`      | `dispatch(ctx) -> DeviceSyncPlugin`       | device-sync plugin 实例 |

dispatcher 是纯机械工厂 — 只看 `ctx.provider` 选实例，不做 provider
判断之外的任何业务逻辑。

## 使用模式

```python
# caller 注入 resolver + dispatcher（DI 已配齐）

ctx = resolver.resolve_for_bot(bot_id, user_id)
plugin = dispatcher.dispatch(ctx)
plugin.<method>(ctx.conn_info, ...)   # 例：plugin.list_files / plugin.sync_bot_config
```

caller 必须走 `resolver.resolve_for_bot + dispatcher.dispatch(ctx)`
显式两行模式，无 `for_bot` 兼容入口。

## 已知限制（本期边界）

- **`DeviceFilesystemDispatcher.for_bot` 残留 3 处 caller** —
  `core/skill_center/factories.py` 通过 `SkillService` 消费
  `device_fs_factory` callable + `adapters/http/bot_management/router.py`
  2 处 endpoint，本期范围内未迁；handoff 续期跟进，见
  `docs/superpowers/handoffs/2026-06-15-device-sync-supplier-for-bot-cleanup-handoff.md`。
- **desktop `url` 字段语义历史债** — `conn_info["url"]` 在 desktop
  bot 链路上语义与其它 provider 不一致（历史延续），单独 handoff
  跟进（待写）。
- **`bind_id` ↔ `binding_id` alias** — `BaasConnInfoBuilder` 输出
  `bind_id`，v2 desktop/local 出 `binding_id`。resolver 在
  `_normalize_schema` 加 alias 让两端 caller 都生效；终态应让
  baas_builder 直接写 `binding_id`，但涉及多处 plugin 测试，留独立 task。

## 链接

- Spec: [`docs/superpowers/specs/2026-06-15-device-context-resolver-design.md`](../../../../../../docs/superpowers/specs/2026-06-15-device-context-resolver-design.md)
- Plan: [`docs/superpowers/plans/2026-06-15-device-context-resolver.md`](../../../../../../docs/superpowers/plans/2026-06-15-device-context-resolver.md)
- 上游 spec（六边形）: [`docs/superpowers/specs/2026-06-14-baas-hexagonal-container-access-design.md`](../../../../../../docs/superpowers/specs/2026-06-14-baas-hexagonal-container-access-design.md)
- Handoff（for_bot 38 caller 治理）: [`docs/superpowers/handoffs/2026-06-15-device-sync-supplier-for-bot-cleanup-handoff.md`](../../../../../../docs/superpowers/handoffs/2026-06-15-device-sync-supplier-for-bot-cleanup-handoff.md)
