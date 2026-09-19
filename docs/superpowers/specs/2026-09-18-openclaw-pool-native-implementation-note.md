# OpenClaw Pool-native 实施接线记录

日期：2026-09-18
状态：PR1 兼容 consumer 与 Engine 能力已开始实现；Native 创建生产方尚未启用。

本文闭环正式 Spec §8 的代码映射；它记录实现事实和仍需交付的依赖，不改变
[正式 Spec](2026-09-18-openclaw-pool-native-startup-spec.md) 的产品取舍。

## 基线

| 仓库 | 实施基线 |
| --- | --- |
| Avernet | `origin/dev@03d4b7875bdf38f10540819c5f6a38bd5bd39028` |
| OCB | `origin/dev@d1ddc1c0e68b44d7eac27e9bdc1c96de614ec4d0` |
| OCB `ocb-public` | `f3ebe7bf978c39223129b55690e5475ab0de01e6` |
| agentclaw-daas-scripts | `origin/dev@e9380be7d09d996e78671ff1efe9d1309001a468` |

三个主 checkout 均有用户未提交内容；实施使用独立 worktree。没有读取线上模板、
部署镜像或运行数据库，因此本记录不是部署或 Rollout 验证。

## 1. 当前启动身份

- ARCA status callback 当前用 `device_id + callback_token` 鉴权；binding 的
  `device_props.sandbox_id` 是可复用的当前 sandbox 身份。普通 ARCA Bot restart
  会释放旧 binding 并重新分配，不能只依赖未核验的 Bot ACTIVE 状态。
- BaaS create 在 binding 上持久化 `publish_id`。`run_create_init_once` 校验该值后
  派发容器初始化，但它随后主动调用 alive；该动作不证明根级布局已经完成。
- BaaS 原地 restart 复用 binding、device id、callback token 与 bot UUID；因此旧
  callback 仅凭这些字段仍可能命中当前记录。现有恢复链已经持久化
  `restart_request_id`，发布接受后再持久化 `restart_publish_id`。布局回报必须携带
  并匹配其中当前优先身份，不能新增随机租约或把旧 `SUCCEEDED` 当证据。
- PR1 callback consumer 接受可选 `startup_identity + layout_initialization`，并按
  `startup_identity → restart_publish_id → restart_request_id → publish_id → sandbox_id`
  的当前 binding 事实验证。无布局证据的旧 callback 完全保留旧生命周期语义。

## 2. alive、status 与 BaaS 顺序

- daas `starting_watchdog.sh` 从 `.starting_done` 发送 `STARTING/SUCCEEDED/FAILED`；
  `ready_callback.sh` 独立发送 alive。两者不存在稳定先后关系。
- `DeviceService.report_device_alive()` 会把 PENDING binding/Bot 推到 ACTIVE 并发布
  后续 Skill/MCP 投影事件；`report_device_status(SUCCEEDED)` 只在 Bot 已 ACTIVE 时
  触发 data-init。BaaS create-init 还会在启动命令派发后主动 alive。
- 因此布局确认在 status callback 鉴权和当前启动身份验证后独立执行，不等待
  Bot ACTIVE，不等待 data-init 或 Skill/MCP 投影。CAS/数据库失败向上返回，不能
  由生命周期状态写入吞掉。

## 3. Desktop 独立链路

- Desktop 创建使用 `_build_desktop_bot_payload()`，逻辑配置位于
  `config.deploy_config`，VM 凭证由 BDC 写入 host box 的 `.credentials`；它不经过
  云端 daas `start_service.sh`。
- OCB `dockers/desktop-openclaw/entrypoint.sh` 是本地 VM 的最早初始化点。它当前只
  建基础 OpenClaw 目录并启动 supervisord，没有消费 Pool layout 或发送布局证据。
- 创建 request id 在 BaaS 请求前已知，可以与 layout/contract 一起进入 credentials；
  创建成功后 Backend 也能保存同一现有 request/publish 身份用于 callback 比对。
- 当前 Desktop `restart()` 只调用 BaaS restart 并轮询 publish，不刷新 VM
  credentials/deploy config。PR3 必须实现本地最早初始化与回报；若 BaaS restart
  仍无法把本次既有 request/publish 身份交给 VM，则“Desktop 重启布局回报”保持明确
  的客户端交付依赖，不能用旧 credentials 或云镜像更新伪造完成。

## 4. 分阶段生效

1. PR1 只增加新 phase 读取、可选 callback schema、Native 确认 CAS、精简 active
   marker 读取与 OpenClaw Native 初始化能力；不创建 `pool_initializing` 行。
2. PR2/PR3 更新 daas 与 OCB/desktop producer 和镜像装配，并保持旧 callback 兼容。
3. PR4 才在 Bot 创建事务中写入 Pool-native 选择并透传部署参数。它必须等待 PR1
   合并或以隔离集成 worktree 验证，目标 base 始终是 `dev`。

已开启 owner Rollout 在 PR4 前仍只影响既有 Legacy migration claim。没有部署或
切流授权；不能在兼容 consumer 与新镜像未核验时启用 Native 写入。
