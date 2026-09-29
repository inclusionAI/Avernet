# Pool-native Spec：代码定位与实现核验附录

本文件是可更新的代码导航，不是另一份业务合同。
正式需求以 [Spec](2026-09-18-openclaw-pool-native-startup-spec.md) 为准。

## 调研基线

| 仓库 | 本轮读取的本地 ref/SHA |
| --- | --- |
| Avernet `origin/dev` | `03d4b7875bdf38f10540819c5f6a38bd5bd39028` |
| OCB `origin/dev` | `d1ddc1c0e68b44d7eac27e9bdc1c96de614ec4d0` |
| 该 OCB 的 `ocb-public` gitlink | `f3ebe7bf978c39223129b55690e5475ab0de01e6` |
| agentclaw-daas-scripts `origin/dev` | `e9380be7d09d996e78671ff1efe9d1309001a468` |

以上 SHA 已在 2026-09-18 实施开始时重新 fetch；仍不是正在部署镜像或运行环境
的证明。OCB gitlink 落后于独立 Avernet `dev`，企业集成必须通过后续精确 gitlink
更新与 OCB CI 验证，不能以公共仓测试代替。

实现前接线核验结论见
[implementation note](2026-09-18-openclaw-pool-native-implementation-note.md)。

## Avernet

以下路径相对 Avernet 根；`backend-core` 表示
`src/backend/src/agentclaw/community/core/`，`engine-community` 表示
`src/engine/src/engine/community/`。

| 入口 | 当前事实 / 实现检查 |
| --- | --- |
| `backend-core/skills_pool/types.py` | 无 `pool_initializing`；无行默认 Legacy；Runtime 已按 active_layout/切流阶段选权威 |
| `backend-core/skills_pool/repository/models.py` | phase 是字符串列，读取转 enum；旧 Backend 不能天然解析新值 |
| `backend-core/skills_pool/reconcile_task.py` | Pool 分支当前有迁移身份要求；需要 Native 与迁移清理分流 |
| `backend-core/bot_management/services/bot_service.py` | 创建、ARCA stop/start、BaaS upgrade、模板 image 透传的主要 consumer |
| `backend-core/devices/services/device_service.py` | alive 鉴权依赖 device_id/token；ACTIVE 与 status SUCCEEDED 是不同处理路径 |
| `backend-core/devices/services/baas_device_service.py` | `run_create_init_once` 主动 alive，不等于容器已完成根级布局 |
| `backend-core/devices/services/baas_publish_task_handlers.py` | BaaS publish ACTIVE 后初始化，后续事件会唤醒原迁移链路 |
| `src/backend/src/agentclaw/community/adapters/http/devices/schemas.py` | 回报 DTO `extra=forbid`，新增可选字段必须先兼容部署 |
| `src/backend/src/agentclaw/community/adapters/http/devices/router.py` | 复用 `/callback/status`；不新增布局确认 URL |
| `backend-core/desktop_bot/services/desktop_bot_service.py` | 独立 `_build_desktop_bot_payload`、deploy_config 与 credentials，不能只改云端构造器 |
| `backend-core/service_bot/services/arca_image_pin.py` | Draft default-image 与 Published record pin 分离；模板实际 image 仍需核验 |
| `engine-community/plugins/skills_pool/active_marker_validation.py` | active 仍校验 preparation/generation；finalizing 有 mappings 恢复检查 |
| `engine-community/plugins/skills_pool/layout_probe.py` | 稳态/迁移与挂载/链接检查有耦合，不能整套复用为 Native 小门禁 |

实施时还需沿 Service Skills manifest、Artifact Build、部署 env consumer 完整回溯，
确认每个稳态入口都不因 Native 无迁移身份而拒绝，且没有放松未完成迁移。

## OCB

- `dockers/arca-openclaw/entrypoint.sh`：当前 mount 后调用 Pool preparation；需在最早准备前选择 Native/稳态/迁移路线。
- `dockers/arca-openclaw/prepare_skills_pool.py`：当前存在 ready+active 联合校验、Legacy bridge/local copy；Native 不应经过这些步骤。
- `dockers/desktop-openclaw/entrypoint.sh`：本地独立入口，Pool 信任根配置不代表 Native 初始化已完成。
- `dockers/arca-openclaw/tests/test_prepare_skills_pool.py`：现有启动准备测试入口。
- Repo mount selector、OSS mount 脚本和镜像复制/打包规则需一起检查；保留 Center/Repo non-critical 挂载策略。

## agentclaw-daas-scripts

- `bootstrapping/setup_engine_dirs.py`：现有 engine sandbox_setup 可能创建 Legacy 入口；后续 setup 也必须服从布局。
- `bootstrapping/start_service.sh`：启动健康与 ready callback 不等价于 Pool 布局完成。
- `bootstrapping/ready_callback.sh`：当前 alive 只带 device_id。
- `bootstrapping/starting_watchdog.sh`：当前 status 一次发送，无本次新增的可靠投递承诺。

## 文档交付边界

本轮仅整理 Spec 与核验附录、更新决策文档指向；未修改业务代码，未执行业务测试，
未创建 Issue/PR，未修改任何运行配置或启动实例。测试矩阵全部是待执行的验收要求。
