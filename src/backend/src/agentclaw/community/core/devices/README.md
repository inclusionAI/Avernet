# `agentclaw.community.core.devices`

Device / engine domain — engine binding, health probing, readiness, sandbox provisioning, OSS↔NAS migration.

## Context Boundary

```yaml
purpose: "Device / engine domain — engine binding, health probing, readiness, sandbox provisioning, OSS↔NAS migration."
provides:
  - "DeviceService"
  - "build_baas_bootstrap_guard_command"
  - "DeviceBindingRepository protocol"
  - "Engine health & readiness services"
  - "Device error types"
  - "Device event types"
consumes:
  - "BotManagement repo + service"
  - "EventBus"
  - "StoragePath"
  - "SystemConfig"
  - "BaasService"
  - "PassportPlugin"
  - "DeviceAccessor (models only)"
  - "DaaS image bootstrap lifecycle contract"
internal_dependencies:
  - agentclaw.community.core.bot_management
  - agentclaw.community.core.bot_collaborator
  - agentclaw.community.core.config
  - agentclaw.community.core.config_compose    # teclaw_paths — namespaces/mappers used by the device-fs dispatcher (B6)
  - agentclaw.community.core.events
  - agentclaw.community.core.service_bot
  - agentclaw.community.core.services    # {identity,resource,config}_addressing mapper builders for dispatch_addressed (B6)
  - agentclaw.community.core.storage
  - agentclaw.community.core.system_config
  - agentclaw.community.core.task_queue
  - agentclaw.community.core.utils
  - agentclaw.community.core.workspace
  - agentclaw.community.di
  - agentclaw.corp.di    # corp-only device services (ARCA) — relocate to corp/ in B11 T3.4
  - agentclaw.community.kernel.lifecycle    # BaaS publish task lifecycle registers task_queue handlers
  - agentclaw.community.kernel.device_dto    # neutral CommandResult / ResourceSpecification / OutBound DTOs (B6)
  - agentclaw.community.log
  - agentclaw.community.core.devices.services.device_filesystem    # DeviceFileSystem — return type of the core device-fs dispatcher (B6)
  - agentclaw.community.plugin_api.device_sync    # DeviceSyncPlugin — return type of the core device-sync dispatcher (B6)
  - agentclaw.community.plugin_api.devices    # DeviceAccessor — injected into DeviceFilesystemDispatcher (B6)
  - agentclaw.community.plugin_api.drm    # DRM dynamic-config reader plugin (B6)
  - agentclaw.community.plugin_api.models
  - agentclaw.community.plugin_api.outbound_rules    # OutboundRuleProvider (kernel rule) injected into ArcaDeviceService (B6)
  - agentclaw.community.plugin_api.passport
  - agentclaw.community.plugin_api.sandbox_runtime    # SandboxRuntimeClient — ARCA SDK I/O seam (B6)
  - agentclaw.community.plugin_api.secret_resolver    # build_outbound_operation_rule 接 SecretResolver, 收口 Mist→layotto 硬依赖
  - agentclaw.community.utils
  - agentclaw.community.utils.env_utils
```

### Change impact

Owns the engine/device binding lifecycle. Schema changes (binding table, engine config dir) require migration. Health-probe contract is consumed by Prom-style monitoring outside the repo.

## BaaS bootstrap readiness contract

本节是 Backend 与 DaaS 容器镜像之间启动契约的规范性事实源。

- **生产者**：镜像 `entrypoint.sh` / `root_init.sh` 完成依赖安装后创建
  `/var/run/agentclaw/.install_dependency_file`；旧镜像若没有 marker，则以
  PID 1 已进入 `supervisord` 作为兼容 ready 信号。
- **消费者**：`BaasContainerInitializer` 和服务 Bot 的 BaaS 启动命令在调用
  `install_engine.sh` 前共同执行 `baas_bootstrap_guard`。
- **版本身份**：checkout 必须位于精确 Git tag；合法基础版本为
  `v0.<minor>=3+.<patch>` 或 `v<major>=1+.<minor>.<patch>`。`dev` 只接受
  `_dev`、`pre` 只接受 `_pre`、`prod` 只接受无环境后缀的 release tag。
  空值或未知 `AGENTCLAW_ENV`/`env` 非法并 fail closed。
- **完整性**：release properties、checkout 中的 install/start 脚本必须存在，
  且与 `/home/admin/bin` 的镜像脚本逐字节一致。完整 checkout 直接复用。
- **并发控制**：Backend 以 root 在 `/var/run/agentclaw/.baas-bootstrap.lock`
  获取进程锁，但 Git 检查和补偿 bootstrap 以 checkout owner `admin` 执行。
  活锁可在持有进程消失后回收；未知占用不被覆盖。
- **超时与失败**：等待镜像 ready 和等待 bootstrap lock 各最多 120 秒。
  不完整 checkout 只允许在锁内补偿一次并再次验证。超时、环境/tag 不匹配、
  补偿失败或复验失败均返回非零；Backend 必须停止后续 install/start，不能把
  发布误报为成功。

修改 marker、tag 命名、checkout 内容、脚本复制位置、锁或超时语义时，必须同时
更新两个消费者及其兼容测试。
